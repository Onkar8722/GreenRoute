"""Deterministic fixed-stop sequencing over a supplied directed distance matrix.

This module has no Flask, database, OSMnx, or external-service dependencies.
The caller prepares the matrix and remains responsible for declaring its source.

The bounded search keeps the input order and deterministic nearest-neighbor
orders as candidates, then applies best-improvement directed 2-opt within a
shared evaluation budget. Reversal candidates are scored by recomputing the
full directed closed-tour cost; an undirected edge-delta shortcut is invalid
for asymmetric matrices.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any


ROAD_SEQUENCE_MAX_STOPS = 50
MAX_TWO_OPT_PASSES = 8
MAX_TWO_OPT_CANDIDATE_EVALUATIONS = 2500


class RouteSequencingError(ValueError):
    """Base class for invalid sequencing inputs and bounded-search failures."""


class InvalidDistanceMatrix(RouteSequencingError):
    """The supplied matrix is incomplete or contains invalid distances."""


class NoFeasibleTour(RouteSequencingError):
    """No valid closed tour was found among the bounded deterministic candidates."""


def _stop_id(stop: Mapping[str, Any], label: str) -> str:
    if not isinstance(stop, Mapping):
        raise RouteSequencingError(f"{label} must be an object")
    stop_id = stop.get("id")
    if not isinstance(stop_id, str) or not stop_id or stop_id != stop_id.strip():
        raise RouteSequencingError(f"{label} must have a non-empty trimmed string ID")
    return stop_id


def _validate_limits(max_passes: int, max_candidate_evaluations: int) -> None:
    for name, value in (
        ("max_passes", max_passes),
        ("max_candidate_evaluations", max_candidate_evaluations),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")


def _validate_matrix(matrix: Any, node_ids: tuple[str, ...]) -> None:
    if not isinstance(matrix, Mapping):
        raise InvalidDistanceMatrix("road distance matrix must be an object")

    for start_id in node_ids:
        row = matrix.get(start_id)
        if not isinstance(row, Mapping):
            raise InvalidDistanceMatrix(f"road distance matrix is missing row {start_id!r}")
        for end_id in node_ids:
            if start_id == end_id:
                continue
            if end_id not in row:
                raise InvalidDistanceMatrix(
                    f"road distance matrix is missing directed arc {start_id!r} -> {end_id!r}"
                )
            distance = row[end_id]
            if isinstance(distance, bool) or not isinstance(distance, Real):
                raise InvalidDistanceMatrix(
                    f"road distance for {start_id!r} -> {end_id!r} must be numeric"
                )
            try:
                value = float(distance)
            except (OverflowError, ValueError) as exc:
                raise InvalidDistanceMatrix(
                    f"road distance for {start_id!r} -> {end_id!r} is outside the supported numeric range"
                ) from exc
            if math.isnan(value) or value == -math.inf or value < 0:
                raise InvalidDistanceMatrix(
                    f"road distance for {start_id!r} -> {end_id!r} must be non-negative"
                )
            # Positive infinity is an explicit unavailable directed arc.


def _arc_cost(matrix: Mapping, start_id: str, end_id: str) -> float | None:
    if start_id == end_id:
        return 0.0
    value = float(matrix[start_id][end_id])
    return value if math.isfinite(value) else None


def closed_tour_cost(
    route_ids: tuple[str, ...] | list[str], depot_id: str, matrix: Mapping
) -> float | None:
    """Return the directed depot-to-depot route cost, or None for a blocked arc."""
    if not route_ids:
        return 0.0
    locations = (depot_id, *route_ids, depot_id)
    distances = []
    for start_id, end_id in zip(locations, locations[1:]):
        distance = _arc_cost(matrix, start_id, end_id)
        if distance is None:
            return None
        distances.append(distance)
    try:
        total = math.fsum(distances)
    except OverflowError:
        return None
    return total if math.isfinite(total) else None


def _nearest_neighbor(
    stop_ids: tuple[str, ...],
    depot_id: str,
    matrix: Mapping,
    first_stop_id: str | None = None,
) -> tuple[str, ...] | None:
    remaining = set(stop_ids)
    route = []
    current_id = depot_id
    if first_stop_id is not None:
        first_leg = _arc_cost(matrix, depot_id, first_stop_id)
        if first_leg is None:
            return None
        route.append(first_stop_id)
        remaining.remove(first_stop_id)
        current_id = first_stop_id
    while remaining:
        choices = []
        for stop_id in remaining:
            distance = _arc_cost(matrix, current_id, stop_id)
            if distance is not None:
                choices.append((distance, stop_id))
        if not choices:
            return None
        _, next_id = min(choices)
        route.append(next_id)
        remaining.remove(next_id)
        current_id = next_id
    candidate = tuple(route)
    return candidate if closed_tour_cost(candidate, depot_id, matrix) is not None else None


def _improve_by_directed_two_opt(
    route: tuple[str, ...],
    depot_id: str,
    matrix: Mapping,
    max_passes: int,
    remaining_evaluations: int,
) -> tuple[tuple[str, ...], list[tuple[tuple[str, ...], float]], int]:
    current = route
    current_cost = closed_tour_cost(current, depot_id, matrix)
    if current_cost is None:
        return current, [], 0

    candidates = [(current, current_cost)]
    evaluations = 0
    for _ in range(max_passes):
        best_move: tuple[float, tuple[str, ...]] | None = None
        for start in range(len(current) - 1):
            for end in range(start + 1, len(current)):
                if evaluations >= remaining_evaluations:
                    break
                proposed = current[:start] + tuple(reversed(current[start : end + 1])) + current[end + 1 :]
                proposed_cost = closed_tour_cost(proposed, depot_id, matrix)
                evaluations += 1
                if proposed_cost is None or proposed_cost >= current_cost:
                    continue
                move = (proposed_cost, proposed)
                if best_move is None or move < best_move:
                    best_move = move
            if evaluations >= remaining_evaluations:
                break
        if best_move is None:
            break
        current_cost, current = best_move
        candidates.append((current, current_cost))

    return current, candidates, evaluations


def sequence_route(
    stops: list[dict],
    depot: dict,
    matrix: Mapping,
    *,
    max_passes: int = MAX_TWO_OPT_PASSES,
    max_candidate_evaluations: int = MAX_TWO_OPT_CANDIDATE_EVALUATIONS,
) -> list[dict]:
    """Sequence a fixed set of stops using deterministic directed matrix costs.

    The input order and deterministic nearest-neighbor orders are retained as
    candidates. Best-improvement 2-opt is limited to eight passes and 2,500
    candidate evaluations total by default. Only strict full-tour cost
    reductions are accepted. If no feasible candidate is found, NoFeasibleTour
    is raised; for matrices with unavailable arcs this bounded heuristic does
    not prove that no Hamiltonian cycle exists.
    """
    _validate_limits(max_passes, max_candidate_evaluations)
    if not isinstance(stops, list):
        raise RouteSequencingError("stops must be a list")
    depot_id = _stop_id(depot, "depot")
    stop_ids = tuple(_stop_id(stop, f"stop at index {index}") for index, stop in enumerate(stops))
    if depot_id in stop_ids:
        raise RouteSequencingError("stop IDs must not include the depot ID")
    if len(set(stop_ids)) != len(stop_ids):
        raise RouteSequencingError("stop IDs must be unique")
    if not stop_ids:
        return []

    _validate_matrix(matrix, (depot_id, *stop_ids))
    stops_by_id = dict(zip(stop_ids, stops))
    candidate_orders = []
    input_order = tuple(stop_ids)
    candidate_orders.append(input_order)
    nearest_order = _nearest_neighbor(stop_ids, depot_id, matrix)
    if nearest_order is not None and nearest_order not in candidate_orders:
        candidate_orders.append(nearest_order)
    first_stop_options = sorted(
        (
            (distance, stop_id)
            for stop_id in stop_ids
            if (distance := _arc_cost(matrix, depot_id, stop_id)) is not None
        )
    )
    for _, first_stop_id in first_stop_options:
        candidate = _nearest_neighbor(
            stop_ids,
            depot_id,
            matrix,
            first_stop_id=first_stop_id,
        )
        if candidate is not None and candidate not in candidate_orders:
            candidate_orders.append(candidate)

    candidates: dict[tuple[str, ...], float] = {}
    for order in candidate_orders:
        cost = closed_tour_cost(order, depot_id, matrix)
        if cost is not None:
            candidates[order] = cost

    if not candidates:
        raise NoFeasibleTour(
            "no feasible closed tour was found by the bounded deterministic nearest-neighbor candidates"
        )

    remaining_evaluations = max_candidate_evaluations
    for order in candidate_orders:
        if remaining_evaluations <= 0:
            break
        if order not in candidates:
            continue
        _, improved, evaluations = _improve_by_directed_two_opt(
            order,
            depot_id,
            matrix,
            max_passes,
            remaining_evaluations,
        )
        remaining_evaluations -= evaluations
        for candidate_order, cost in improved:
            existing = candidates.get(candidate_order)
            if existing is None or cost < existing:
                candidates[candidate_order] = cost

    best_order = min(candidates, key=lambda order: (candidates[order], order))
    return [stops_by_id[stop_id] for stop_id in best_order]

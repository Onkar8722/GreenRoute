"""Independent objective functions and small exact benchmark solvers."""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from typing import Iterable


def _finite_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def validate_bins(bins: list[dict], depot: dict) -> str | None:
    if not isinstance(bins, list):
        return "bins must be a list"
    depot_id = depot.get("id") if isinstance(depot, dict) else None
    if not isinstance(depot_id, str) or not depot_id:
        return "depot must have a non-empty string ID"

    seen = set()
    for index, item in enumerate(bins):
        if not isinstance(item, dict):
            return f"bin at index {index} must be an object"
        bin_id = item.get("id")
        if not isinstance(bin_id, str) or not bin_id or bin_id != bin_id.strip():
            return f"bin at index {index} must have a non-empty trimmed string ID"
        if bin_id == depot_id:
            return f"bin ID {bin_id!r} is reserved for the depot"
        if bin_id in seen:
            return f"duplicate bin ID {bin_id!r}"
        seen.add(bin_id)

        for key, low, high in (("lat", -90, 90), ("lng", -180, 180)):
            value = item.get(key)
            if not _finite_number(value) or not low <= value <= high:
                return f"bin {bin_id!r} has invalid {key}"
        fill = item.get("fill")
        if not _finite_number(fill) or not 0 <= fill <= 100:
            return f"bin {bin_id!r} has invalid fill"
    for key, low, high in (("lat", -90, 90), ("lng", -180, 180)):
        value = depot.get(key)
        if not _finite_number(value) or not low <= value <= high:
            return f"depot has invalid {key}"
    return None


def validate_fleet_instance(instance: dict) -> str | None:
    bins = instance.get("bins")
    depot = instance.get("depot")
    error = validate_bins(bins, depot)
    if error:
        return error
    for item in bins:
        load = item.get("load_units")
        if not _finite_number(load) or load < 0:
            return f"bin {item['id']!r} has invalid benchmark load_units"

    vehicles = instance.get("vehicles")
    drivers = instance.get("drivers")
    if not isinstance(vehicles, list) or not isinstance(drivers, list):
        return "vehicles and drivers must be lists"
    vehicle_ids = set()
    for vehicle in vehicles:
        if not isinstance(vehicle, dict):
            return "each vehicle must be an object"
        vehicle_id = vehicle.get("id")
        capacity = vehicle.get("capacity_units")
        if not isinstance(vehicle_id, str) or not vehicle_id or vehicle_id in vehicle_ids:
            return "vehicle IDs must be unique non-empty strings"
        if not _finite_number(capacity) or capacity < 0:
            return f"vehicle {vehicle_id!r} has invalid benchmark capacity_units"
        vehicle_ids.add(vehicle_id)
    if any(not isinstance(driver, str) or not driver for driver in drivers):
        return "driver IDs must be non-empty strings"
    if len(set(drivers)) != len(drivers):
        return "driver IDs must be unique"
    if not isinstance(instance.get("max_stops"), int) or instance["max_stops"] < 1:
        return "max_stops must be a positive integer"
    if not _finite_number(instance.get("threshold")) or not 0 <= instance["threshold"] <= 100:
        return "threshold must be between 0 and 100"
    return None


def canonical_sha256(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def matrix_distance(matrix: dict, start_id: str, end_id: str) -> float | None:
    if start_id == end_id:
        return 0.0
    try:
        value = matrix[start_id][end_id]
    except (KeyError, TypeError):
        return None
    if not _finite_number(value) or value < 0:
        return None
    return float(value)


def closed_tour_cost(route_ids: Iterable[str], depot_id: str, matrix: dict) -> float | None:
    route = list(route_ids)
    if not route:
        return 0.0
    locations = [depot_id, *route, depot_id]
    distances = []
    for start_id, end_id in zip(locations, locations[1:]):
        distance = matrix_distance(matrix, start_id, end_id)
        if distance is None:
            return None
        distances.append(distance)
    total = math.fsum(distances)
    return total if math.isfinite(total) else None


def validate_fixed_route(route_ids: list[str], expected_ids: Iterable[str]) -> str | None:
    expected = set(expected_ids)
    seen = set()
    for bin_id in route_ids:
        if bin_id not in expected:
            return f"route contains unknown stop {bin_id!r}"
        if bin_id in seen:
            return f"route contains duplicate stop {bin_id!r}"
        seen.add(bin_id)
    missing = expected - seen
    if missing:
        return f"route omits stops {sorted(missing)!r}"
    return None


def score_fixed_route(
    route_ids: list[str], expected_ids: Iterable[str], depot_id: str, matrix: dict
) -> dict:
    validation_error = validate_fixed_route(route_ids, expected_ids)
    if validation_error:
        return {"valid": False, "reason": validation_error, "cost_km": None}
    cost = closed_tour_cost(route_ids, depot_id, matrix)
    if cost is None:
        return {"valid": False, "reason": "route uses an unavailable or invalid directed arc", "cost_km": None}
    return {"valid": True, "reason": None, "cost_km": cost}


def matrix_nearest_neighbor(stop_ids: Iterable[str], depot_id: str, matrix: dict) -> list[str]:
    remaining = set(stop_ids)
    route = []
    current_id = depot_id
    while remaining:
        choices = []
        for stop_id in remaining:
            distance = matrix_distance(matrix, current_id, stop_id)
            if distance is not None:
                choices.append((distance, stop_id))
        if not choices:
            break
        _, next_id = min(choices)
        route.append(next_id)
        remaining.remove(next_id)
        current_id = next_id
    return route


def _cost_is_better(candidate: tuple, current: tuple | None) -> bool:
    if current is None:
        return True
    candidate_cost, candidate_route = candidate
    current_cost, current_route = current
    if candidate_cost < current_cost - 1e-12:
        return True
    return math.isclose(candidate_cost, current_cost, rel_tol=0.0, abs_tol=1e-12) and candidate_route < current_route


def best_tours_by_subset(
    stop_ids: Iterable[str], depot_id: str, matrix: dict
) -> dict[int, tuple[float, tuple[str, ...]] | None]:
    """Return the exact directed closed tour for every subset, keyed by its bitmask."""
    ids = tuple(sorted(stop_ids))
    count = len(ids)
    subset_count = 1 << count
    partial: list[dict[int, tuple[float, tuple[str, ...]]]] = [
        {} for _ in range(subset_count)
    ]

    for last, stop_id in enumerate(ids):
        first_leg = matrix_distance(matrix, depot_id, stop_id)
        if first_leg is not None:
            partial[1 << last][last] = (first_leg, (stop_id,))

    for mask in range(1, subset_count):
        for last, (cost, route) in tuple(partial[mask].items()):
            for next_index, next_id in enumerate(ids):
                next_bit = 1 << next_index
                if mask & next_bit:
                    continue
                leg = matrix_distance(matrix, ids[last], next_id)
                if leg is None:
                    continue
                new_mask = mask | next_bit
                candidate = (cost + leg, route + (next_id,))
                existing = partial[new_mask].get(next_index)
                if _cost_is_better(candidate, existing):
                    partial[new_mask][next_index] = candidate

    best_by_mask: dict[int, tuple[float, tuple[str, ...]] | None] = {0: (0.0, ())}
    for mask in range(1, subset_count):
        best = None
        for last, (cost, route) in partial[mask].items():
            return_leg = matrix_distance(matrix, ids[last], depot_id)
            if return_leg is None:
                continue
            candidate = (cost + return_leg, route)
            if _cost_is_better(candidate, best):
                best = candidate
        best_by_mask[mask] = best
    return best_by_mask


def held_karp_tour(
    stop_ids: Iterable[str], depot_id: str, matrix: dict
) -> tuple[list[str], float | None]:
    ids = tuple(stop_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("stop IDs must be unique")
    tours = best_tours_by_subset(ids, depot_id, matrix)
    best = tours[(1 << len(ids)) - 1]
    if best is None:
        return [], None
    cost, route = best
    return list(route), cost


def _fleet_tie_key(result: tuple[float, float, tuple]) -> tuple:
    fill_score, distance, assignments = result
    served_ids = tuple(sorted(bin_id for _, route in assignments for bin_id in route))
    return (-fill_score, round(distance, 9), served_ids, assignments)


def _prefer_fleet_result(candidate: tuple[float, float, tuple], current: tuple[float, float, tuple]) -> bool:
    return _fleet_tie_key(candidate) < _fleet_tie_key(current)


def exact_fleet_solution(instance: dict) -> dict:
    """Exact small-instance selection, capacity assignment, and directed tour solver."""
    validation_error = validate_fleet_instance(instance)
    if validation_error:
        raise ValueError(validation_error)

    eligible_bins = [
        item for item in instance["bins"] if item["fill"] >= instance["threshold"]
    ]
    eligible_bins.sort(key=lambda item: item["id"])
    ids = tuple(item["id"] for item in eligible_bins)
    index_by_id = {bin_id: index for index, bin_id in enumerate(ids)}
    bin_by_id = {item["id"]: item for item in eligible_bins}
    tours = best_tours_by_subset(ids, instance["depot"]["id"], instance["distance_matrix_km"])
    fill_by_mask = [0.0] * (1 << len(ids))
    load_by_mask = [0.0] * (1 << len(ids))
    for mask in range(1, 1 << len(ids)):
        bit = mask & -mask
        index = bit.bit_length() - 1
        previous = mask ^ bit
        item = bin_by_id[ids[index]]
        fill_by_mask[mask] = fill_by_mask[previous] + item["fill"]
        load_by_mask[mask] = load_by_mask[previous] + item["load_units"]

    vehicles = sorted(instance["vehicles"], key=lambda item: item["id"])
    driver_slots = min(len(vehicles), len(instance["drivers"]))
    max_stops = instance["max_stops"]

    @lru_cache(maxsize=None)
    def solve(vehicle_index: int, remaining: int, slots_left: int):
        if vehicle_index >= len(vehicles) or remaining == 0 or slots_left == 0:
            return (0.0, 0.0, ())

        best = solve(vehicle_index + 1, remaining, slots_left)
        vehicle = vehicles[vehicle_index]
        subset = remaining
        while subset:
            tour = tours[subset]
            if (
                subset.bit_count() <= max_stops
                and load_by_mask[subset] <= vehicle["capacity_units"]
                and tour is not None
            ):
                tail = solve(vehicle_index + 1, remaining ^ subset, slots_left - 1)
                candidate = (
                    fill_by_mask[subset] + tail[0],
                    tour[0] + tail[1],
                    ((vehicle["id"], tour[1]),) + tail[2],
                )
                if _prefer_fleet_result(candidate, best):
                    best = candidate
            subset = (subset - 1) & remaining
        return best

    fill_score, distance, assignments = solve(0, (1 << len(ids)) - 1, driver_slots)
    served = {bin_id for _, route in assignments for bin_id in route}
    return {
        "assignments": {vehicle_id: list(route) for vehicle_id, route in assignments},
        "unserved_ids": sorted(set(ids) - served),
        "served_fill_score": fill_score,
        "total_distance_km": distance,
        "eligible_count": len(ids),
        "available_route_slots": driver_slots,
    }


def _capacity_assignment(routes: list[list[str]], instance: dict) -> dict[str, list[str]] | None:
    active_routes = [list(route) for route in routes if route]
    vehicles = sorted(instance["vehicles"], key=lambda item: item["id"])
    if len(active_routes) > min(len(vehicles), len(instance["drivers"])):
        return None
    bins_by_id = {item["id"]: item for item in instance["bins"]}

    def assign(route_index: int, remaining_vehicle_ids: tuple[str, ...]):
        if route_index == len(active_routes):
            return {}
        route = active_routes[route_index]
        load = sum(bins_by_id[bin_id]["load_units"] for bin_id in route if bin_id in bins_by_id)
        for vehicle_id in remaining_vehicle_ids:
            vehicle = next(item for item in vehicles if item["id"] == vehicle_id)
            if load <= vehicle["capacity_units"]:
                rest = tuple(item for item in remaining_vehicle_ids if item != vehicle_id)
                tail = assign(route_index + 1, rest)
                if tail is not None:
                    return {vehicle_id: route, **tail}
        return None

    result = assign(0, tuple(item["id"] for item in vehicles))
    return result


def score_fleet_routes(routes: list[list[str]], instance: dict) -> dict:
    """Independently score a candidate under benchmark-only fleet constraints."""
    errors = []
    validation_error = validate_fleet_instance(instance)
    if validation_error:
        return {
            "feasible": False,
            "errors": [validation_error],
            "served_fill_score": 0,
            "total_distance_km": None,
            "vehicle_assignments": None,
        }

    bins_by_id = {item["id"]: item for item in instance["bins"]}
    eligible = {
        item["id"]
        for item in instance["bins"]
        if item["fill"] >= instance["threshold"]
    }
    active_routes = [list(route) for route in routes if route]
    flat_ids = [bin_id for route in active_routes for bin_id in route]
    if len(flat_ids) != len(set(flat_ids)):
        errors.append("a bin is assigned more than once")
    if any(bin_id not in eligible for bin_id in flat_ids):
        errors.append("a route contains an unknown or threshold-ineligible bin")
    if any(len(route) > instance["max_stops"] for route in active_routes):
        errors.append("a route exceeds max_stops")
    route_slots = min(len(instance["vehicles"]), len(instance["drivers"]))
    if len(active_routes) > route_slots:
        errors.append("routes exceed available vehicle/driver slots")

    for route in active_routes:
        if any(bin_id not in bins_by_id for bin_id in route):
            continue
        if closed_tour_cost(route, instance["depot"]["id"], instance["distance_matrix_km"]) is None:
            errors.append("a route uses an unavailable or invalid directed arc")
            break

    assignments = _capacity_assignment(active_routes, instance)
    if assignments is None:
        errors.append("routes cannot be assigned within benchmark vehicle capacities")

    served_ids = set(flat_ids) & eligible
    fill_score = sum(bins_by_id[bin_id]["fill"] for bin_id in served_ids)
    route_costs = [
        closed_tour_cost(route, instance["depot"]["id"], instance["distance_matrix_km"])
        for route in active_routes
    ]
    total_distance = None if any(cost is None for cost in route_costs) else math.fsum(route_costs)
    return {
        "feasible": not errors,
        "errors": errors,
        "served_fill_score": fill_score,
        "served_bin_count": len(served_ids),
        "total_distance_km": total_distance,
        "vehicle_assignments": assignments,
    }


def road_aware_greedy_solution(instance: dict) -> dict[str, list[str]]:
    """Fill-first baseline that inserts each eligible bin at minimum road-cost increase."""
    validation_error = validate_fleet_instance(instance)
    if validation_error:
        raise ValueError(validation_error)
    eligible_bins = [
        item for item in instance["bins"] if item["fill"] >= instance["threshold"]
    ]
    eligible_bins.sort(key=lambda item: (-item["fill"], item["id"]))
    vehicles = sorted(instance["vehicles"], key=lambda item: item["id"])
    driver_slots = min(len(vehicles), len(instance["drivers"]))
    bin_by_id = {item["id"]: item for item in instance["bins"]}
    routes = {vehicle["id"]: [] for vehicle in vehicles}

    for item in eligible_bins:
        choices = []
        active_count = sum(bool(route) for route in routes.values())
        for vehicle in vehicles:
            route = routes[vehicle["id"]]
            if not route and active_count >= driver_slots:
                continue
            if len(route) >= instance["max_stops"]:
                continue
            current_load = sum(bin_by_id[bin_id]["load_units"] for bin_id in route)
            if current_load + item["load_units"] > vehicle["capacity_units"]:
                continue
            old_cost = closed_tour_cost(route, instance["depot"]["id"], instance["distance_matrix_km"])
            for position in range(len(route) + 1):
                candidate_route = route[:position] + [item["id"]] + route[position:]
                new_cost = closed_tour_cost(
                    candidate_route, instance["depot"]["id"], instance["distance_matrix_km"]
                )
                if old_cost is not None and new_cost is not None:
                    increase = new_cost - old_cost
                    choices.append((round(increase, 9), vehicle["id"], tuple(candidate_route)))
        if choices:
            _, vehicle_id, chosen_route = min(choices)
            routes[vehicle_id] = list(chosen_route)

    return {vehicle_id: route for vehicle_id, route in routes.items() if route}

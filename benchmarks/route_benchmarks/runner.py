"""Run the deterministic offline route benchmarks and print JSON to stdout."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import math
import json
import os
import platform
import random
import statistics
import sys
import time
from pathlib import Path

from . import BENCHMARK_VERSION, FIXTURE_SCHEMA_VERSION
from .core import (
    canonical_sha256,
    closed_tour_cost,
    exact_fleet_solution,
    held_karp_tour,
    matrix_nearest_neighbor,
    road_aware_greedy_solution,
    score_fixed_route,
    score_fleet_routes,
)
from .fixtures import fleet_instances, fixed_instances
from src.route_sequencing import (
    MAX_TWO_OPT_CANDIDATE_EVALUATIONS,
    MAX_TWO_OPT_PASSES,
    sequence_route,
)


DEFAULT_SEEDS = tuple(range(30))
ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DISTANCE_SOURCE = "synthetic_directed_matrix"


def _load_production_app():
    # Importing app.py in TESTING mode selects its no-op cache and blocks service
    # startup. The runner calls route helpers only; it does not call API endpoints.
    os.environ["FLASK_ENV"] = "testing"
    os.environ["TESTING"] = "true"
    app_module = importlib.import_module("src.app")
    if not app_module._is_testing_mode():
        raise RuntimeError("production routing helpers did not load in TESTING mode")
    return app_module


def _fixture_hash(instance: dict) -> str:
    return canonical_sha256(instance)


def _run_seeded(seed: int | None, operation):
    state = random.getstate()
    if seed is not None:
        random.seed(seed)
    started = time.perf_counter()
    try:
        value = operation()
        elapsed = time.perf_counter() - started
        return value, elapsed, None
    except Exception as exc:  # Record algorithm failures as benchmark outcomes.
        elapsed = time.perf_counter() - started
        return None, elapsed, {"type": type(exc).__name__, "message": str(exc)}
    finally:
        random.setstate(state)


def _gap_percent(cost: float | None, optimum: float | None) -> float | None:
    if cost is None or optimum is None:
        return None
    if optimum == 0:
        return 0.0 if cost == 0 else None
    if math.isclose(cost, optimum, rel_tol=1e-12, abs_tol=1e-12):
        return 0.0
    return ((cost - optimum) / optimum) * 100.0


def _method_summary(runs: list[dict]) -> dict:
    successful = [run for run in runs if run["failure"] is None]
    adapter_runtimes = [run["runtime_seconds"] for run in successful]
    sequencing_runtimes = [
        run["sequencing_runtime_seconds"]
        for run in successful
        if run.get("sequencing_runtime_seconds") is not None
    ]
    valid = [run for run in successful if run["valid"]]
    gaps = [run["optimality_gap_percent"] for run in valid if run["optimality_gap_percent"] is not None]
    mean_adapter_runtime = statistics.fmean(adapter_runtimes) if adapter_runtimes else None
    return {
        "run_count": len(runs),
        "successful_run_count": len(successful),
        "valid_run_count": len(valid),
        # Keep the historical fields as adapter elapsed-time aliases.
        "mean_runtime_seconds": mean_adapter_runtime,
        "mean_adapter_elapsed_seconds": mean_adapter_runtime,
        "median_runtime_seconds": statistics.median(adapter_runtimes) if adapter_runtimes else None,
        "mean_sequencing_runtime_seconds": (
            statistics.fmean(sequencing_runtimes) if sequencing_runtimes else None
        ),
        "mean_optimality_gap_percent": statistics.fmean(gaps) if gaps else None,
    }


def _track_a_run(
    route_ids: list[str],
    expected_ids: list[str],
    instance: dict,
    elapsed: float,
    seed: int | None,
    reported_cost: float | None = None,
    failure: dict | None = None,
    extra: dict | None = None,
) -> dict:
    if failure is None:
        scored = score_fixed_route(
            route_ids,
            expected_ids,
            instance["depot"]["id"],
            instance["distance_matrix_km"],
        )
    else:
        scored = {"valid": False, "reason": failure["message"], "cost_km": None}
    exact_cost = instance.get("_exact_cost_km")
    result = {
        "seed": seed,
        "route_ids": route_ids,
        "valid": scored["valid"],
        "validity_reason": scored["reason"],
        "independent_cost_km": scored["cost_km"],
        "algorithm_reported_cost_km": reported_cost,
        "optimality_gap_percent": _gap_percent(scored["cost_km"], exact_cost),
        "runtime_seconds": elapsed,
        "failure": failure,
    }
    if extra:
        result.update(extra)
    if extra and "initial_order_cost_km" in extra:
        start_costs = [
            extra.get("initial_order_cost_km"),
            extra.get("matrix_nearest_neighbor_cost_km"),
        ]
        valid_start_costs = [cost for cost in start_costs if cost is not None]
        result["no_worse_than_starting_candidates"] = bool(valid_start_costs) and (
            scored["valid"]
            and all(scored["cost_km"] <= cost + 1e-12 for cost in valid_start_costs)
        )
    return result


def _run_production_fixed(app_module, instance: dict, algorithm: str):
    bins = copy.deepcopy(instance["bins"])
    routes = app_module.compute_routes_logic(
        bins,
        algorithm,
        num_trucks=1,
        max_stops=max(1, len(bins)),
        threshold=0,
        adaptive=False,
    )
    route = routes[0] if routes else []
    return [item["id"] for item in route], None, {"reported_cost_note": "production helper returns a route only"}


def _run_standalone_ga_fixed(instance: dict, population_size: int, generations: int):
    from src.genetic_algorithm import GeneticAlgorithm

    bins = copy.deepcopy(instance["bins"])
    ga = GeneticAlgorithm(
        copy.deepcopy(instance["depot"]),
        bins,
        population_size=population_size,
        generations=generations,
        distance_matrix=instance["distance_matrix_km"],
    )
    route, fitness = ga.optimize()
    summary = ga.get_summary()
    return list(route), summary["distance_km"], {
        "fitness_score": fitness,
        "distance_source": BENCHMARK_DISTANCE_SOURCE,
        "synthetic_matrix_supplied": True,
    }


def _run_directed_matrix_sequencer(instance: dict):
    bins = copy.deepcopy(instance["bins"])
    depot = copy.deepcopy(instance["depot"])
    matrix = instance["distance_matrix_km"]
    initial_order = [item["id"] for item in bins]
    nearest_order = matrix_nearest_neighbor(
        initial_order,
        depot["id"],
        matrix,
    )
    initial_cost = closed_tour_cost(initial_order, depot["id"], matrix)
    nearest_cost = closed_tour_cost(nearest_order, depot["id"], matrix)

    started = time.perf_counter()
    route = sequence_route(bins, depot, matrix)
    sequencing_runtime = time.perf_counter() - started
    return [item["id"] for item in route], None, {
        "distance_source": BENCHMARK_DISTANCE_SOURCE,
        "synthetic_matrix_supplied": True,
        "sequencing_runtime_seconds": sequencing_runtime,
        "initial_order_cost_km": initial_cost,
        "matrix_nearest_neighbor_cost_km": nearest_cost,
    }


def run_fixed_stop_benchmark(
    instance: dict, app_module=None, seeds: tuple[int, ...] = DEFAULT_SEEDS
) -> dict:
    """Evaluate fixed-stop orderings using the same independent directed score."""
    if app_module is None:
        app_module = _load_production_app()
    bins = instance["bins"]
    stop_ids = [item["id"] for item in bins]
    expected_ids = sorted(stop_ids)
    depot_id = instance["depot"]["id"]
    matrix = instance["distance_matrix_km"]

    exact_started = time.perf_counter()
    exact_route, exact_cost = held_karp_tour(stop_ids, depot_id, matrix)
    exact_runtime = time.perf_counter() - exact_started
    instance_for_score = dict(instance)
    instance_for_score["_exact_cost_km"] = exact_cost
    exact_scored = score_fixed_route(exact_route, expected_ids, depot_id, matrix)
    if not exact_scored["valid"]:
        raise ValueError(f"exact solver failed for {instance['fixture_id']}: {exact_scored['reason']}")

    methods = []

    def add_method(name, optimizer_objective, parameters, run_specs, execute):
        runs = []
        for seed in run_specs:
            value, elapsed, failure = _run_seeded(seed, lambda: execute(seed))
            if failure:
                route_ids, reported_cost, extra = [], None, {}
            else:
                route_ids, reported_cost, extra = value
            runs.append(
                _track_a_run(
                    route_ids,
                    expected_ids,
                    instance_for_score,
                    elapsed,
                    seed,
                    reported_cost,
                    failure,
                    extra,
                )
            )
        methods.append(
            {
                "name": name,
                "optimizer_objective": optimizer_objective,
                "parameters": parameters,
                "runs": runs,
                "summary": _method_summary(runs),
            }
        )

    deterministic_specs = (
        ("production_nearest", "coordinate Haversine nearest neighbor", "nearest"),
        ("production_priority", "fill and coordinate proximity score", "priority"),
        ("production_cluster", "coordinate KMeans followed by priority proximity", "cluster"),
    )
    production_params = {
        "num_trucks": 1,
        "max_stops": max(1, len(bins)),
        "threshold": 0,
    }
    for name, objective, algorithm in deterministic_specs:
        add_method(
            name,
            objective,
            production_params,
            (None,),
            lambda _seed, algorithm=algorithm: _run_production_fixed(app_module, instance, algorithm),
        )

    stochastic_specs = (
        (
            "production_ga",
            "Haversine distance minus 0.02 times total fill",
            "ga",
            {"population_size": 16, "generations": 50},
        ),
        (
            "production_multiobjective",
            "Haversine distance minus 0.035 times total fill",
            "multiobjective",
            {"population_size": 16, "generations": 50},
        ),
    )
    stochastic_seeds = seeds if len(bins) > 2 else (None,)
    for name, objective, algorithm, params in stochastic_specs:
        add_method(
            name,
            objective,
            {**production_params, **params, "random_seeds": list(seeds) if len(bins) > 2 else []},
            stochastic_seeds,
            lambda _seed, algorithm=algorithm: _run_production_fixed(app_module, instance, algorithm),
        )

    add_method(
        "matrix_nearest_neighbor",
        "greedy minimum outgoing directed arc; independent closed-tour scoring",
        {},
        (None,),
        lambda _seed: (
            matrix_nearest_neighbor(stop_ids, depot_id, matrix),
            None,
            {"reported_cost_note": "baseline returns a route only"},
        ),
    )

    add_method(
        "directed_matrix_nearest_neighbor_2opt",
        "minimum synthetic directed-matrix closed-tour cost using deterministic nearest neighbor and bounded directed 2-opt",
        {
            "max_passes": MAX_TWO_OPT_PASSES,
            "max_candidate_evaluations": MAX_TWO_OPT_CANDIDATE_EVALUATIONS,
            "matrix_preparation_included_in_runtime": False,
        },
        (None,),
        lambda _seed: _run_directed_matrix_sequencer(instance),
    )

    ga_parameters = {
        "population_size": 100,
        "generations": 50,
        "distance_weight": 0.5,
        "time_weight": 0.3,
        "emissions_weight": 0.2,
    }
    standalone_seeds = seeds if len(bins) > 1 else (None,)
    add_method(
        "standalone_genetic_algorithm",
        "weighted normalized distance, estimated time, and emissions",
        {**ga_parameters, "random_seeds": list(seeds) if len(bins) > 1 else []},
        standalone_seeds,
        lambda _seed: _run_standalone_ga_fixed(
            instance,
            ga_parameters["population_size"],
            ga_parameters["generations"],
        ),
    )

    exact_run = _track_a_run(
        exact_route,
        expected_ids,
        instance_for_score,
        exact_runtime,
        None,
        reported_cost=exact_cost,
        extra={"reported_cost_note": "Held-Karp objective value"},
    )
    methods.append(
        {
            "name": "exact_held_karp",
            "optimizer_objective": "minimum directed closed-tour distance",
            "parameters": {"complexity": "O(n^2 * 2^n)"},
            "runs": [exact_run],
            "summary": _method_summary([exact_run]),
        }
    )

    return {
        "fixture_id": instance["fixture_id"],
        "fixture_schema_version": instance["fixture_schema_version"],
        "distance_source": BENCHMARK_DISTANCE_SOURCE,
        "input_sha256": _fixture_hash(instance),
        "matrix_provenance": instance["matrix_provenance"],
        "stop_count": len(bins),
        "objective": "minimize sum of directed arcs depot -> each stop exactly once -> depot",
        "exact_optimum_km": exact_cost,
        "matrix_preparation_included_in_runtime": False,
        "methods": methods,
    }


def _run_production_fleet(app_module, instance: dict, algorithm: str):
    eligible = [
        copy.deepcopy(item)
        for item in instance["bins"]
        if item["fill"] >= instance["threshold"]
    ]
    truck_count = min(len(instance["vehicles"]), len(instance["drivers"]))
    routes = app_module.compute_routes_logic(
        eligible,
        algorithm,
        num_trucks=truck_count,
        max_stops=instance["max_stops"],
        threshold=instance["threshold"],
        adaptive=False,
    )
    return [[item["id"] for item in route] for route in routes]


def _fleet_quality(score: dict, exact: dict) -> dict:
    if not score["feasible"]:
        reason = "; ".join(score.get("errors") or []) or "route is infeasible"
        return {
            "comparison_status": "infeasible",
            "comparison_reason": reason,
            "fill_shortfall_vs_exact": None,
            "distance_regret_km_at_equal_fill": None,
            "matches_exact_fill_and_distance": None,
        }

    exact_fill = exact["served_fill_score"]
    score_fill = score["served_fill_score"]
    distance = score["total_distance_km"]
    exact_distance = exact["total_distance_km"]
    distance_regret = None
    if distance is not None and exact_fill == score_fill:
        distance_regret = distance - exact_distance
    return {
        "comparison_status": "feasible",
        "comparison_reason": None,
        "fill_shortfall_vs_exact": exact_fill - score_fill,
        "distance_regret_km_at_equal_fill": distance_regret,
        "matches_exact_fill_and_distance": (
            score_fill == exact_fill
            and distance is not None
            and math.isclose(distance, exact_distance, rel_tol=0.0, abs_tol=1e-9)
        ),
    }


def _fleet_method_summary(runs: list[dict], exact_fill: float) -> dict:
    successful = [run for run in runs if run["failure"] is None]
    feasible = [run for run in runs if run["feasible"]]
    fill_comparable = [
        run
        for run in feasible
        if run["objective_quality"]["fill_shortfall_vs_exact"] is not None
    ]
    distance_comparable = [
        run
        for run in feasible
        if run["served_fill_score"] == exact_fill
        and run["independent_total_distance_km"] is not None
        and run["objective_quality"]["distance_regret_km_at_equal_fill"] is not None
    ]
    fill_shortfalls = [
        run["objective_quality"]["fill_shortfall_vs_exact"]
        for run in fill_comparable
    ]
    distance_regrets = [
        run["objective_quality"]["distance_regret_km_at_equal_fill"]
        for run in distance_comparable
    ]
    runtimes = [run["runtime_seconds"] for run in successful]
    mean_adapter_runtime = statistics.fmean(runtimes) if runtimes else None
    return {
        "run_count": len(runs),
        "successful_run_count": len(successful),
        "failed_run_count": len(runs) - len(successful),
        "valid_run_count": len(feasible),
        "feasible_run_count": len(feasible),
        "infeasible_run_count": len(runs) - len(feasible),
        "comparison_sample_count": len(fill_comparable),
        "distance_comparison_sample_count": len(distance_comparable),
        "mean_runtime_seconds": mean_adapter_runtime,
        "mean_adapter_elapsed_seconds": mean_adapter_runtime,
        "median_runtime_seconds": statistics.median(runtimes) if runtimes else None,
        "mean_optimality_gap_percent": None,
        "mean_served_fill_score": (
            statistics.fmean(run["served_fill_score"] for run in fill_comparable)
            if fill_comparable
            else None
        ),
        "mean_fill_shortfall_vs_exact": (
            statistics.fmean(fill_shortfalls) if fill_shortfalls else None
        ),
        "mean_distance_km_at_equal_fill": (
            statistics.fmean(
                run["independent_total_distance_km"] for run in distance_comparable
            )
            if distance_comparable
            else None
        ),
        "mean_distance_regret_km_at_equal_fill": (
            statistics.fmean(distance_regrets) if distance_regrets else None
        ),
    }


def run_fleet_benchmark(
    instance: dict, app_module=None, seeds: tuple[int, ...] = DEFAULT_SEEDS
) -> dict:
    """Compare production selection with synthetic-matrix greedy and exact solvers."""
    if app_module is None:
        app_module = _load_production_app()

    exact_started = time.perf_counter()
    exact = exact_fleet_solution(instance)
    exact_runtime = time.perf_counter() - exact_started
    exact_routes = list(exact["assignments"].values())
    exact_score = score_fleet_routes(exact_routes, instance)
    exact_quality = _fleet_quality(exact_score, exact)

    methods = []

    def add_method(name, objective, parameters, run_specs, execute):
        runs = []
        for seed in run_specs:
            candidate, elapsed, failure = _run_seeded(seed, lambda: execute(seed))
            if failure:
                routes = []
                score = {
                    "feasible": False,
                    "errors": [failure["message"]],
                    "served_fill_score": 0,
                    "served_bin_count": 0,
                    "total_distance_km": None,
                    "vehicle_assignments": None,
                }
                quality = _fleet_quality(score, exact)
            else:
                # Fleet solvers may return a vehicle -> route mapping to retain
                # their assignment. The independent scorer accepts route lists
                # and rematches them against benchmark capacity constraints.
                routes = (
                    [candidate[key] for key in sorted(candidate)]
                    if isinstance(candidate, dict)
                    else candidate
                )
                score = score_fleet_routes(routes, instance)
                quality = _fleet_quality(score, exact)
            runs.append(
                {
                    "seed": seed,
                    "routes": routes,
                    "feasible": score["feasible"],
                    "feasibility_errors": score["errors"],
                    "served_fill_score": score["served_fill_score"],
                    "served_bin_count": score["served_bin_count"],
                    "independent_total_distance_km": score["total_distance_km"],
                    "vehicle_assignments": score["vehicle_assignments"],
                    "objective_quality": quality,
                    "runtime_seconds": elapsed,
                    "failure": failure,
                }
            )
        methods.append(
            {
                "name": name,
                "optimizer_objective": objective,
                "parameters": parameters,
                "runs": runs,
                "summary": _fleet_method_summary(runs, exact["served_fill_score"]),
            }
        )

    eligible_count = sum(
        item["fill"] >= instance["threshold"] for item in instance["bins"]
    )
    truck_count = min(len(instance["vehicles"]), len(instance["drivers"]))
    common_parameters = {
        "threshold": instance["threshold"],
        "eligible_bin_count": eligible_count,
        "max_stops_per_vehicle": instance["max_stops"],
        "available_vehicles": len(instance["vehicles"]),
        "available_drivers": len(instance["drivers"]),
        "maximum_active_routes": truck_count,
        "capacity_units_are_benchmark_assumptions": True,
    }

    add_method(
        "synthetic_matrix_greedy_baseline",
        "process bins by descending fill, then insert at minimum synthetic directed-matrix cost increase",
        common_parameters,
        (None,),
        lambda _seed: road_aware_greedy_solution(instance),
    )

    production_specs = (
        ("production_nearest", "fill partition then coordinate Haversine nearest neighbor", "nearest", False),
        ("production_priority", "coordinate clustering and fill/proximity priority", "priority", False),
        ("production_cluster", "coordinate KMeans then fill/proximity priority", "cluster", False),
        ("production_ga", "fill partition then Haversine distance/fill proxy", "ga", True),
        ("production_multiobjective", "fill partition then Haversine distance/fill proxy", "multiobjective", True),
    )
    for name, objective, algorithm, stochastic in production_specs:
        run_specs = seeds if stochastic else (None,)
        add_method(
            name,
            objective,
            {**common_parameters, "random_seeds": list(seeds) if stochastic else []},
            run_specs,
            lambda _seed, algorithm=algorithm: _run_production_fleet(app_module, instance, algorithm),
        )

    exact_runs = [
        {
            "seed": None,
            "routes": exact["assignments"],
            "feasible": exact_score["feasible"],
            "feasibility_errors": exact_score["errors"],
            "served_fill_score": exact_score["served_fill_score"],
            "served_bin_count": exact_score["served_bin_count"],
            "independent_total_distance_km": exact_score["total_distance_km"],
            "vehicle_assignments": exact["assignments"],
            "objective_quality": exact_quality,
            "runtime_seconds": exact_runtime,
            "failure": None,
        }
    ]
    methods.append(
        {
            "name": "exact_selection_assignment_and_tours",
            "optimizer_objective": "maximize served fill; minimize synthetic directed-matrix cost; bin-ID tie-break",
            "parameters": {**common_parameters, "enumeration": "subset DP plus Held-Karp tours"},
            "runs": exact_runs,
            "summary": {
                "run_count": 1,
                "successful_run_count": 1,
                "failed_run_count": 0,
                "valid_run_count": 1 if exact_score["feasible"] else 0,
                "feasible_run_count": 1 if exact_score["feasible"] else 0,
                "infeasible_run_count": 0 if exact_score["feasible"] else 1,
                "comparison_sample_count": 1 if exact_score["feasible"] else 0,
                "distance_comparison_sample_count": 1 if exact_score["feasible"] else 0,
                "mean_runtime_seconds": exact_runtime,
                "mean_adapter_elapsed_seconds": exact_runtime,
                "median_runtime_seconds": exact_runtime,
                "mean_optimality_gap_percent": 0.0 if exact_score["feasible"] else None,
                "mean_served_fill_score": exact_score["served_fill_score"] if exact_score["feasible"] else None,
                "mean_fill_shortfall_vs_exact": 0.0 if exact_score["feasible"] else None,
                "mean_distance_km_at_equal_fill": exact_score["total_distance_km"] if exact_score["feasible"] else None,
                "mean_distance_regret_km_at_equal_fill": 0.0 if exact_score["feasible"] else None,
            },
        }
    )

    return {
        "fixture_id": instance["fixture_id"],
        "fixture_schema_version": instance["fixture_schema_version"],
        "distance_source": BENCHMARK_DISTANCE_SOURCE,
        "input_sha256": _fixture_hash(instance),
        "matrix_provenance": instance["matrix_provenance"],
        "bin_count": len(instance["bins"]),
        "eligible_bin_count": eligible_count,
        "objective": "lexicographically maximize served fill, minimize synthetic directed-matrix cost, then bin IDs",
        "capacity_assumption": instance["load_assumption"],
        "exact_optimum": {
            "served_fill_score": exact["served_fill_score"],
            "total_distance_km": exact["total_distance_km"],
            "unserved_ids": exact["unserved_ids"],
        },
        "matrix_preparation_included_in_runtime": False,
        "methods": methods,
    }


def _source_sha256(relative_path: str) -> str:
    return hashlib.sha256(Path(ROOT, relative_path).read_bytes()).hexdigest()


def run_all_benchmarks(seeds: tuple[int, ...] = DEFAULT_SEEDS) -> dict:
    app_module = _load_production_app()
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
        "software": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "production_source_sha256": {
                "src/app.py": _source_sha256("src/app.py"),
                "src/genetic_algorithm.py": _source_sha256("src/genetic_algorithm.py"),
                "src/route_sequencing.py": _source_sha256("src/route_sequencing.py"),
            },
        },
        "protocol": {
            "random_seeds": list(seeds),
            "distance_source": BENCHMARK_DISTANCE_SOURCE,
            "matrix_preparation_included_in_runtime": False,
            "all_routes_scored_independently": True,
        },
        "fixed_stop_sequencing": [
            run_fixed_stop_benchmark(instance, app_module=app_module, seeds=seeds)
            for instance in fixed_instances()
        ],
        "selection_and_fleet_feasibility": [
            run_fleet_benchmark(instance, app_module=app_module, seeds=seeds)
            for instance in fleet_instances()
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--track",
        choices=("all", "fixed", "fleet"),
        default="all",
        help="run both tracks or one benchmark track",
    )
    args = parser.parse_args(argv)
    app_module = _load_production_app()
    result = {
        "benchmark_version": BENCHMARK_VERSION,
        "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
        "software": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "production_source_sha256": {
                "src/app.py": _source_sha256("src/app.py"),
                "src/genetic_algorithm.py": _source_sha256("src/genetic_algorithm.py"),
                "src/route_sequencing.py": _source_sha256("src/route_sequencing.py"),
            },
        },
        "protocol": {
            "random_seeds": list(DEFAULT_SEEDS),
            "distance_source": BENCHMARK_DISTANCE_SOURCE,
            "matrix_preparation_included_in_runtime": False,
            "all_routes_scored_independently": True,
        },
    }
    if args.track in ("all", "fixed"):
        result["fixed_stop_sequencing"] = [
            run_fixed_stop_benchmark(instance, app_module=app_module)
            for instance in fixed_instances()
        ]
    if args.track in ("all", "fleet"):
        result["selection_and_fleet_feasibility"] = [
            run_fleet_benchmark(instance, app_module=app_module)
            for instance in fleet_instances()
        ]
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

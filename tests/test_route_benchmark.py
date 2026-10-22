"""Offline tests for the route benchmark solvers and production adapters."""

import importlib
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmarks.route_benchmarks.core import (
    canonical_sha256,
    exact_fleet_solution,
    held_karp_tour,
    score_fixed_route,
    score_fleet_routes,
    validate_bins,
)
from benchmarks.route_benchmarks.fixtures import (
    DEPOT,
    fleet_instances,
    fixed_instances,
    make_fixed_instance,
)
from benchmarks.route_benchmarks.runner import (
    _gap_percent,
    _method_summary,
    run_fixed_stop_benchmark,
    run_fleet_benchmark,
)


class BenchmarkCoreTests(unittest.TestCase):
    def test_runtime_summary_separates_sequencing_and_adapter_elapsed_means(self):
        runs = [
            {
                "failure": None,
                "valid": True,
                "runtime_seconds": 10.0,
                "sequencing_runtime_seconds": 2.0,
                "optimality_gap_percent": 0.0,
            },
            {
                "failure": None,
                "valid": True,
                "runtime_seconds": 20.0,
                "sequencing_runtime_seconds": 4.0,
                "optimality_gap_percent": 1.0,
            },
            {
                "failure": {"message": "sequencer failed"},
                "valid": False,
                "runtime_seconds": 100.0,
                "sequencing_runtime_seconds": 50.0,
                "optimality_gap_percent": None,
            },
        ]

        summary = _method_summary(runs)

        self.assertEqual(summary["mean_runtime_seconds"], 15.0)
        self.assertEqual(summary["mean_adapter_elapsed_seconds"], 15.0)
        self.assertEqual(summary["mean_sequencing_runtime_seconds"], 3.0)

        non_sequencer_summary = _method_summary(
            [
                {
                    "failure": None,
                    "valid": True,
                    "runtime_seconds": 5.0,
                    "optimality_gap_percent": 0.0,
                }
            ]
        )
        self.assertIsNone(non_sequencer_summary["mean_sequencing_runtime_seconds"])

    def test_optimality_gap_normalizes_float_noise_at_exact_optimum(self):
        self.assertEqual(_gap_percent(34.300000000000004, 34.3), 0.0)
        self.assertIsNone(_gap_percent(None, 0))

    def test_versioned_fixtures_are_stable_complete_and_asymmetric(self):
        first = make_fixed_instance(5)
        second = make_fixed_instance(5)
        self.assertEqual(canonical_sha256(first), canonical_sha256(second))
        self.assertEqual(first["fixture_schema_version"], "synthetic-directed-v1")
        matrix = first["distance_matrix_km"]
        self.assertEqual(set(matrix), {"DEPOT", *(item["id"] for item in first["bins"])})
        self.assertNotEqual(matrix["DEPOT"][first["bins"][0]["id"]], matrix[first["bins"][0]["id"]]["DEPOT"])
        for start_id, row in matrix.items():
            self.assertEqual(set(row), set(matrix))
            self.assertEqual(row[start_id], 0)

    def test_held_karp_uses_directed_closed_tour_cost(self):
        matrix = {
            "DEPOT": {"DEPOT": 0, "A": 1, "B": 1},
            "A": {"DEPOT": 1, "A": 0, "B": 2},
            "B": {"DEPOT": 1, "A": 10, "B": 0},
        }
        route, cost = held_karp_tour(["A", "B"], "DEPOT", matrix)
        self.assertEqual(route, ["A", "B"])
        self.assertEqual(cost, 4)
        self.assertEqual(score_fixed_route(route, ["A", "B"], "DEPOT", matrix)["cost_km"], 4)

    def test_held_karp_breaks_tied_optima_by_lexical_route_and_scores_it(self):
        matrix = {
            "DEPOT": {"DEPOT": 0, "A": 1, "B": 2},
            "A": {"DEPOT": 6, "A": 0, "B": 5},
            "B": {"DEPOT": 4, "A": 2, "B": 0},
        }
        route, cost = held_karp_tour(["B", "A"], "DEPOT", matrix)

        self.assertEqual(route, ["A", "B"])
        independent_cost = (
            matrix["DEPOT"]["A"]
            + matrix["A"]["B"]
            + matrix["B"]["DEPOT"]
        )
        reverse_cost = (
            matrix["DEPOT"]["B"]
            + matrix["B"]["A"]
            + matrix["A"]["DEPOT"]
        )
        self.assertEqual(independent_cost, reverse_cost)
        self.assertEqual(cost, independent_cost)
        self.assertEqual(
            score_fixed_route(route, ["A", "B"], "DEPOT", matrix)["cost_km"],
            independent_cost,
        )

    def test_held_karp_handles_empty_single_and_unavailable_arcs(self):
        empty_route, empty_cost = held_karp_tour([], "DEPOT", {"DEPOT": {"DEPOT": 0}})
        self.assertEqual((empty_route, empty_cost), ([], 0))

        matrix = {"DEPOT": {"A": 2}, "A": {"DEPOT": 3}}
        route, cost = held_karp_tour(["A"], "DEPOT", matrix)
        self.assertEqual((route, cost), (["A"], 5))

        blocked = {"DEPOT": {"A": 2}, "A": {"DEPOT": float("inf")}}
        route, cost = held_karp_tour(["A"], "DEPOT", blocked)
        self.assertEqual(route, [])
        self.assertIsNone(cost)
        scored = score_fixed_route(["A"], ["A"], "DEPOT", blocked)
        self.assertFalse(scored["valid"])

    def test_invalid_duplicate_and_coordinate_inputs_are_separate_validation_errors(self):
        one_bin = [{"id": "A", "lat": 10.0, "lng": 20.0, "fill": 70}]
        self.assertIn("duplicate", validate_bins(one_bin + [dict(one_bin[0])], DEPOT))
        self.assertIn("invalid lat", validate_bins([dict(one_bin[0], lat=float("nan"))], DEPOT))
        self.assertIn("invalid lng", validate_bins([dict(one_bin[0], lng=181)], DEPOT))

    def test_exact_fleet_obeys_threshold_stops_drivers_and_capacity(self):
        instance = fleet_instances()[0]
        result = exact_fleet_solution(instance)
        routes = list(result["assignments"].values())
        score = score_fleet_routes(routes, instance)
        self.assertTrue(score["feasible"], score["errors"])
        self.assertEqual(result["eligible_count"], 8)
        self.assertLessEqual(len(result["assignments"]), 2)
        self.assertNotIn(instance["bins"][-1]["id"], [bin_id for route in routes for bin_id in route])
        self.assertEqual(result["served_fill_score"], score["served_fill_score"])
        for vehicle_id, route in result["assignments"].items():
            vehicle = next(item for item in instance["vehicles"] if item["id"] == vehicle_id)
            self.assertLessEqual(len(route), instance["max_stops"])
            load = sum(next(item for item in instance["bins"] if item["id"] == bin_id)["load_units"] for bin_id in route)
            self.assertLessEqual(load, vehicle["capacity_units"])

    def test_fleet_scorer_separates_stop_and_unavailable_arc_failures(self):
        instance = fleet_instances()[0]
        eligible = [item for item in instance["bins"] if item["fill"] >= instance["threshold"]]
        too_many_stops = [[item["id"] for item in eligible[: instance["max_stops"] + 1]]]
        stop_score = score_fleet_routes(too_many_stops, instance)
        self.assertFalse(stop_score["feasible"])
        self.assertTrue(any("max_stops" in error for error in stop_score["errors"]))

        blocked_instance = dict(instance)
        blocked_instance["distance_matrix_km"] = {key: dict(row) for key, row in instance["distance_matrix_km"].items()}
        first_id = eligible[0]["id"]
        blocked_instance["distance_matrix_km"]["DEPOT"][first_id] = None
        blocked_score = score_fleet_routes([[first_id]], blocked_instance)
        self.assertFalse(blocked_score["feasible"])
        self.assertTrue(any("unavailable" in error for error in blocked_score["errors"]))


class ProductionAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = patch.dict(os.environ, {"FLASK_ENV": "testing", "TESTING": "true"})
        cls._env.start()
        from benchmarks.route_benchmarks.runner import _load_production_app

        cls.app_module = _load_production_app()

    @classmethod
    def tearDownClass(cls):
        cls._env.stop()

    def test_fixed_track_scores_production_helpers_and_standalone_ga(self):
        result = run_fixed_stop_benchmark(make_fixed_instance(5), self.app_module, seeds=(0,))
        self.assertEqual(result["distance_source"], "synthetic_directed_matrix")
        methods = {item["name"]: item for item in result["methods"]}
        for name in (
            "production_nearest",
            "production_priority",
            "production_cluster",
            "production_ga",
            "production_multiobjective",
            "standalone_genetic_algorithm",
            "matrix_nearest_neighbor",
            "exact_held_karp",
        ):
            self.assertIn(name, methods)
            self.assertTrue(methods[name]["runs"])
        for name in ("production_nearest", "production_priority", "production_cluster"):
            self.assertTrue(methods[name]["runs"][0]["valid"])
        self.assertEqual(methods["standalone_genetic_algorithm"]["runs"][0]["seed"], 0)
        standalone_run = methods["standalone_genetic_algorithm"]["runs"][0]
        self.assertEqual(standalone_run["distance_source"], "synthetic_directed_matrix")
        self.assertTrue(standalone_run["synthetic_matrix_supplied"])

    def test_matrix_sequencer_is_benchmarked_on_every_fixed_fixture(self):
        for instance in fixed_instances():
            with self.subTest(fixture=instance["fixture_id"]):
                result = run_fixed_stop_benchmark(
                    instance,
                    self.app_module,
                    seeds=(0,),
                )
                methods = {item["name"]: item for item in result["methods"]}
                method = methods["directed_matrix_nearest_neighbor_2opt"]
                run = method["runs"][0]

                self.assertTrue(run["valid"], run["validity_reason"])
                self.assertEqual(run["distance_source"], "synthetic_directed_matrix")
                self.assertTrue(run["synthetic_matrix_supplied"])
                self.assertIsNone(run["algorithm_reported_cost_km"])
                self.assertIsNotNone(run["sequencing_runtime_seconds"])
                self.assertTrue(run["no_worse_than_starting_candidates"])
                self.assertLessEqual(
                    run["independent_cost_km"],
                    run["initial_order_cost_km"] + 1e-12,
                )
                self.assertLessEqual(
                    run["independent_cost_km"],
                    run["matrix_nearest_neighbor_cost_km"] + 1e-12,
                )
                self.assertIsNotNone(run["optimality_gap_percent"])
                self.assertFalse(
                    method["parameters"]["matrix_preparation_included_in_runtime"]
                )

    def test_runner_source_metadata_hashes_the_matrix_sequencer(self):
        from benchmarks.route_benchmarks import runner

        with (
            patch.object(runner, "_load_production_app", return_value=object()),
            patch.object(runner, "run_fixed_stop_benchmark", return_value={}),
            patch.object(runner, "run_fleet_benchmark", return_value={}),
        ):
            result = runner.run_all_benchmarks(seeds=())

        source_hashes = result["software"]["production_source_sha256"]
        self.assertEqual(len(source_hashes["src/route_sequencing.py"]), 64)

    def test_fleet_track_reports_capacity_feasibility_separately(self):
        result = run_fleet_benchmark(fleet_instances()[0], self.app_module, seeds=(0,))
        methods = {item["name"]: item for item in result["methods"]}
        self.assertEqual(result["distance_source"], "synthetic_directed_matrix")
        self.assertIn("synthetic directed-matrix cost", result["objective"])
        greedy = methods["synthetic_matrix_greedy_baseline"]["runs"][0]
        exact = methods["exact_selection_assignment_and_tours"]["runs"][0]
        self.assertTrue(greedy["feasible"], greedy["feasibility_errors"])
        self.assertTrue(exact["feasible"], exact["feasibility_errors"])
        for name in ("production_nearest", "production_priority", "production_cluster"):
            self.assertIsInstance(methods[name]["runs"][0]["feasible"], bool)
            self.assertIn("served_fill_score", methods[name]["runs"][0])


class FleetReportingTests(unittest.TestCase):
    def test_means_use_feasible_fill_runs_and_equal_fill_distance_runs(self):
        instance = fleet_instances()[0]
        exact_routes = list(exact_fleet_solution(instance)["assignments"].values())
        calls = {"ga": 0}

        def compute_routes_logic(bins, algorithm, **_kwargs):
            if algorithm == "ga":
                calls["ga"] += 1
                if calls["ga"] == 1:
                    by_id = {item["id"]: item for item in bins}
                    return [[by_id[bin_id] for bin_id in route] for route in exact_routes]
            return [bins]

        app_module = SimpleNamespace(compute_routes_logic=compute_routes_logic)
        result = run_fleet_benchmark(instance, app_module, seeds=(0, 1))
        methods = {item["name"]: item for item in result["methods"]}
        ga = methods["production_ga"]
        first_run, second_run = ga["runs"]

        self.assertTrue(first_run["feasible"], first_run["feasibility_errors"])
        self.assertFalse(second_run["feasible"])
        self.assertGreater(second_run["served_fill_score"], first_run["served_fill_score"])
        self.assertIsNotNone(second_run["independent_total_distance_km"])
        self.assertEqual(second_run["objective_quality"]["comparison_status"], "infeasible")
        self.assertIsNone(second_run["objective_quality"]["fill_shortfall_vs_exact"])
        self.assertIsNone(second_run["objective_quality"]["distance_regret_km_at_equal_fill"])
        self.assertIsNone(second_run["objective_quality"]["matches_exact_fill_and_distance"])
        self.assertTrue(second_run["feasibility_errors"])

        summary = ga["summary"]
        self.assertEqual(summary["run_count"], 2)
        self.assertEqual(summary["feasible_run_count"], 1)
        self.assertEqual(summary["infeasible_run_count"], 1)
        self.assertEqual(summary["comparison_sample_count"], 1)
        self.assertEqual(summary["distance_comparison_sample_count"], 1)
        self.assertEqual(summary["mean_served_fill_score"], first_run["served_fill_score"])
        self.assertEqual(summary["mean_fill_shortfall_vs_exact"], 0)
        self.assertEqual(
            summary["mean_distance_km_at_equal_fill"],
            first_run["independent_total_distance_km"],
        )

    def test_no_feasible_runs_have_null_comparative_means_and_visible_reasons(self):
        instance = fleet_instances()[0]
        app_module = SimpleNamespace(
            compute_routes_logic=lambda bins, _algorithm, **_kwargs: [bins]
        )
        result = run_fleet_benchmark(instance, app_module, seeds=(0,))
        method = next(item for item in result["methods"] if item["name"] == "production_nearest")
        run = method["runs"][0]
        summary = method["summary"]

        self.assertFalse(run["feasible"])
        self.assertTrue(run["feasibility_errors"])
        self.assertEqual(summary["run_count"], 1)
        self.assertEqual(summary["feasible_run_count"], 0)
        self.assertEqual(summary["infeasible_run_count"], 1)
        self.assertEqual(summary["comparison_sample_count"], 0)
        self.assertEqual(summary["distance_comparison_sample_count"], 0)
        self.assertIsNone(summary["mean_served_fill_score"])
        self.assertIsNone(summary["mean_fill_shortfall_vs_exact"])
        self.assertIsNone(summary["mean_distance_km_at_equal_fill"])
        self.assertIsNone(summary["mean_distance_regret_km_at_equal_fill"])

    def test_fixture_threshold_description_matches_fleet_inputs(self):
        below_threshold_counts = [
            sum(item["fill"] < instance["threshold"] for item in instance["bins"])
            for instance in fleet_instances()
        ]
        self.assertEqual(below_threshold_counts, [1, 1, 0])


if __name__ == "__main__":
    unittest.main()

import importlib
import os
import random
import unittest
from unittest.mock import patch

import psycopg2


DEPOT = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}
BIN_A = {"id": "A", "lat": 18.53, "lng": 73.86, "fill": 80}
BIN_B = {"id": "B", "lat": 18.54, "lng": 73.87, "fill": 90}
ROAD_MATRIX = {
    "DEPOT": {"A": 1.0, "B": 10.0},
    "A": {"DEPOT": 3.0, "B": 2.0},
    "B": {"DEPOT": 4.0, "A": 8.0},
}


class RouteCostConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._testing_environment = patch.dict(
            os.environ,
            {"FLASK_ENV": "testing", "TESTING": "true"},
        )
        cls._testing_environment.start()

        cache_module = importlib.import_module("src.cache")
        with (
            patch.object(cache_module, "get_cache"),
            patch.object(psycopg2, "connect"),
        ):
            cls.app_module = importlib.import_module("src.app")
            cls.app_module = importlib.reload(cls.app_module)

        cls.client = cls.app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._testing_environment.stop()

    def test_genetic_algorithm_uses_directed_road_cost_and_both_depot_legs(self):
        from src.genetic_algorithm import GeneticAlgorithm

        ga = GeneticAlgorithm(DEPOT, [BIN_A, BIN_B], distance_matrix=ROAD_MATRIX)
        self.assertEqual(ga.distance_source, "road_network")
        self.assertEqual(ga.calculate_route_distance(["A", "B"]), 7.0)
        self.assertEqual(ga.calculate_route_distance(["B", "A"]), 21.0)

    def test_incomplete_matrix_does_not_silently_mix_haversine_legs(self):
        from src.genetic_algorithm import GeneticAlgorithm

        incomplete = {"DEPOT": {"A": 1.0}, "A": {}}
        ga = GeneticAlgorithm(DEPOT, [BIN_A], distance_matrix=incomplete)
        with self.assertRaisesRegex(ValueError, "missing 'A' -> 'DEPOT'"):
            ga.calculate_route_distance(["A"])

        unreachable = {"DEPOT": {"A": 1.0}, "A": {"DEPOT": float("inf")}}
        ga = GeneticAlgorithm(DEPOT, [BIN_A], distance_matrix=unreachable)
        with self.assertRaisesRegex(ValueError, "no valid distance"):
            ga.calculate_route_distance(["A"])

    def test_haversine_is_explicit_when_no_road_matrix_is_supplied(self):
        from src.genetic_algorithm import GeneticAlgorithm

        ga = GeneticAlgorithm(DEPOT, [BIN_A])
        self.assertEqual(ga.distance_source, "haversine")
        self.assertGreater(ga.calculate_route_distance(["A"]), 0)

    def test_ga_handles_empty_single_and_two_stop_routes(self):
        from src.genetic_algorithm import GeneticAlgorithm

        empty = GeneticAlgorithm(DEPOT, [], population_size=1, generations=1)
        route, score = empty.optimize()
        self.assertEqual(route, [])
        self.assertEqual(score, 0)
        self.assertEqual(empty.get_summary()["distance_km"], 0)

        single = GeneticAlgorithm(
            DEPOT,
            [BIN_A],
            distance_matrix={"DEPOT": {"A": 1.0}, "A": {"DEPOT": 3.0}},
        )
        route, _ = single.optimize()
        self.assertEqual(route, ["A"])
        self.assertEqual(single.get_summary()["distance_km"], 4.0)

        random_state = random.getstate()
        try:
            random.seed(7)
            two_stops = GeneticAlgorithm(
                DEPOT,
                [BIN_A, BIN_B],
                population_size=4,
                generations=2,
                distance_matrix=ROAD_MATRIX,
            )
            route, _ = two_stops.optimize()
            self.assertCountEqual(route, ["A", "B"])
        finally:
            random.setstate(random_state)

    def test_ga_rejects_duplicate_bin_ids(self):
        from src.genetic_algorithm import GeneticAlgorithm

        with self.assertRaisesRegex(ValueError, "bin IDs must be unique"):
            GeneticAlgorithm(DEPOT, [BIN_A, dict(BIN_A)])
        with self.assertRaisesRegex(ValueError, "must not use the depot ID"):
            GeneticAlgorithm(DEPOT, [dict(BIN_A, id="DEPOT")])

    def test_route_endpoint_reports_road_cost_for_the_return_tour(self):
        module = self.app_module
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A, BIN_B]),
            patch.object(module, "build_distance_matrix", return_value=ROAD_MATRIX) as build_matrix,
            patch.object(module, "calculate_detailed_path", return_value=[[1.0, 2.0]]),
        ):
            response = self.client.post(
                "/api/route",
                json={"algorithm": "nearest", "num_trucks": 1, "max_stops": 2},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        route = result["routes"][0]
        expected = module._route_distance_from_matrix(route, ROAD_MATRIX)
        self.assertEqual(result["distances_km"], [round(expected, 2)])
        self.assertEqual(result["distance_source"], "road_network")
        self.assertEqual(result["distance_status"], "ok")
        self.assertNotIn("sequencing_method", result)
        build_matrix.assert_called_once_with([BIN_A, BIN_B], module.DEPOT, cap=100)

    def test_opt_in_sequencing_preserves_routes_and_scores_the_supplied_matrix_once(self):
        module = self.app_module
        bin_c = {"id": "C", "lat": 18.55, "lng": 73.88, "fill": 85}
        matrix = {
            "DEPOT": {"A": 2, "B": 1, "C": 2},
            "A": {"DEPOT": 2, "A": 0, "B": 10, "C": 50},
            "B": {"DEPOT": 3, "A": 1, "B": 0, "C": 50},
            "C": {"DEPOT": 3, "A": 50, "B": 50, "C": 0},
        }
        original_routes = [[BIN_A, BIN_B], [bin_c]]
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A, BIN_B, bin_c]),
            patch.object(module, "compute_routes_logic", return_value=original_routes),
            patch.object(module, "build_distance_matrix", return_value=matrix) as build_matrix,
            patch.object(module, "calculate_detailed_path", return_value=[[1.0, 2.0]]),
        ):
            response = self.client.post(
                "/api/route",
                json={
                    "algorithm": "nearest",
                    "num_trucks": 2,
                    "max_stops": 2,
                    "sequence_cost": "road_network",
                },
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        route_ids = [[item["id"] for item in route] for route in result["routes"]]
        self.assertEqual([set(route) for route in route_ids], [{"A", "B"}, {"C"}])
        self.assertEqual(route_ids[1], ["C"])
        self.assertEqual(route_ids[0], ["B", "A"])
        self.assertEqual(result["distances_km"], [4.0, 5.0])
        self.assertEqual(result["distance_source"], "road_network")
        self.assertEqual(result["distance_status"], "ok")
        self.assertEqual(
            result["sequencing_method"],
            "directed_matrix_nearest_neighbor_2opt",
        )
        self.assertNotIn("distance_fallback_reason", result)
        build_matrix.assert_called_once_with(
            [BIN_A, BIN_B, bin_c],
            module.DEPOT,
            cap=module.ROAD_SEQUENCE_MAX_STOPS,
        )

    def test_opt_in_sequencing_rejects_invalid_field_before_bin_loading(self):
        module = self.app_module
        with patch.object(module, "get_bins_data") as get_bins:
            response = self.client.post("/api/route", json={"sequence_cost": "haversine"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("sequence_cost", response.get_json()["error"])
        get_bins.assert_not_called()

    def test_opt_in_sequencing_applies_total_stop_cap_before_matrix_build(self):
        module = self.app_module
        bins = [
            {
                "id": f"BIN_{index:02d}",
                "lat": 18.5,
                "lng": 73.8,
                "fill": 90,
            }
            for index in range(module.ROAD_SEQUENCE_MAX_STOPS + 1)
        ]
        with (
            patch.object(module, "get_bins_data", return_value=bins),
            patch.object(module, "compute_routes_logic", return_value=[bins]),
            patch.object(module, "build_distance_matrix") as build_matrix,
        ):
            response = self.client.post(
                "/api/route",
                json={"sequence_cost": "road_network", "max_stops": 100},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("at most 50 selected stops", response.get_json()["error"])
        build_matrix.assert_not_called()

    def test_opt_in_sequencing_matrix_failures_do_not_fall_back_to_haversine(self):
        module = self.app_module
        for unavailable_matrix in (None, {}):
            with (
                patch.object(module, "get_bins_data", return_value=[BIN_A]),
                patch.object(module, "build_distance_matrix", return_value=unavailable_matrix),
                patch.object(module, "route_distance_km", side_effect=AssertionError("fallback used")),
                patch.object(module, "calculate_detailed_path") as calculate_path,
            ):
                response = self.client.post(
                    "/api/route",
                    json={"sequence_cost": "road_network", "max_stops": 1},
                )

            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.get_json()["code"],
                "road_matrix_unavailable",
            )
            calculate_path.assert_not_called()

    def test_opt_in_sequencing_reports_road_matrix_builder_failure(self):
        module = self.app_module
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A]),
            patch.object(
                module,
                "build_distance_matrix",
                side_effect=RuntimeError("road graph unavailable"),
            ),
            patch.object(module, "route_distance_km", side_effect=AssertionError("fallback used")),
            patch.object(module, "calculate_detailed_path") as calculate_path,
        ):
            response = self.client.post(
                "/api/route",
                json={"sequence_cost": "road_network", "max_stops": 1},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "road_matrix_unavailable")
        self.assertIn("road graph unavailable", response.get_json()["error"])
        calculate_path.assert_not_called()

    def test_opt_in_sequencing_reports_no_feasible_tour_without_fallback(self):
        module = self.app_module
        no_cycle_matrix = {
            "DEPOT": {"A": 1, "B": 2},
            "A": {"DEPOT": float("inf"), "B": float("inf")},
            "B": {"DEPOT": float("inf"), "A": float("inf")},
        }
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A, BIN_B]),
            patch.object(
                module,
                "compute_routes_logic",
                return_value=[[BIN_A, BIN_B]],
            ),
            patch.object(module, "build_distance_matrix", return_value=no_cycle_matrix),
            patch.object(module, "route_distance_km", side_effect=AssertionError("fallback used")),
        ):
            response = self.client.post(
                "/api/route",
                json={"sequence_cost": "road_network", "max_stops": 2},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["code"], "no_feasible_road_tour_found")
        self.assertIn("no feasible closed tour", response.get_json()["error"])

    def test_route_endpoint_marks_whole_response_degraded_on_unavailable_arc(self):
        module = self.app_module
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A]),
            patch.object(module, "build_distance_matrix", return_value={}),
            patch.object(module, "calculate_detailed_path", return_value=[]),
        ):
            response = self.client.post(
                "/api/route",
                json={"algorithm": "nearest", "num_trucks": 1, "max_stops": 1},
            )

        result = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["distance_source"], "haversine")
        self.assertEqual(result["distance_status"], "degraded")
        self.assertIn("Haversine", result["distance_fallback_reason"])
        self.assertEqual(
            result["distances_km"],
            [round(module.route_distance_km([BIN_A]), 2)],
        )

    def test_empty_route_response_has_no_distance_source(self):
        module = self.app_module
        with (
            patch.object(module, "get_bins_data", return_value=[]),
            patch.object(module, "build_distance_matrix") as build_matrix,
        ):
            response = self.client.post("/api/route", json={})

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["routes"], [])
        self.assertEqual(result["distances_km"], [])
        self.assertEqual(result["distance_source"], "none")
        self.assertEqual(result["distance_status"], "empty")
        build_matrix.assert_not_called()

    def test_route_endpoint_rejects_malformed_json_and_non_object_bodies(self):
        module = self.app_module
        with patch.object(module, "get_bins_data") as get_bins:
            malformed = self.client.post(
                "/api/route", data="{", content_type="application/json"
            )
            array_body = self.client.post("/api/route", json=[])

        self.assertEqual(malformed.status_code, 400)
        self.assertIn("Malformed JSON", malformed.get_json()["error"])
        self.assertEqual(array_body.status_code, 400)
        self.assertIn("must be an object", array_body.get_json()["error"])
        get_bins.assert_not_called()

    def test_route_endpoint_rejects_invalid_types_and_limits(self):
        module = self.app_module
        with patch.object(module, "get_bins_data") as get_bins:
            invalid_type = self.client.post("/api/route", json={"threshold": True})
            invalid_limit = self.client.post("/api/route", json={"max_stops": 0})
            invalid_trucks = self.client.post("/api/route", json={"num_trucks": 11})
            invalid_algorithm = self.client.post("/api/route", json={"algorithm": []})

        self.assertEqual(invalid_type.status_code, 400)
        self.assertEqual(invalid_limit.status_code, 400)
        self.assertEqual(invalid_trucks.status_code, 400)
        self.assertEqual(invalid_algorithm.status_code, 400)
        get_bins.assert_not_called()

    def test_ga_endpoint_rejects_out_of_range_iteration_parameters(self):
        module = self.app_module
        with patch.object(module, "get_bins_data") as get_bins:
            low_population = self.client.post(
                "/api/route/optimize-ga", json={"population_size": 9}
            )
            high_generations = self.client.post(
                "/api/route/optimize-ga", json={"generations": 101}
            )

        self.assertEqual(low_population.status_code, 400)
        self.assertEqual(high_generations.status_code, 400)
        get_bins.assert_not_called()

    def test_route_endpoint_rejects_duplicate_ids_and_invalid_coordinates(self):
        module = self.app_module
        duplicate_bins = [BIN_A, dict(BIN_A)]
        invalid_bins = [dict(BIN_A, lat=float("nan"))]
        with patch.object(module, "get_bins_data", side_effect=[duplicate_bins, invalid_bins]):
            duplicate_response = self.client.post("/api/route", json={})
            coordinate_response = self.client.post("/api/route", json={})

        self.assertEqual(duplicate_response.status_code, 422)
        self.assertIn("Duplicate bin ID", duplicate_response.get_json()["error"])
        self.assertEqual(coordinate_response.status_code, 422)
        self.assertIn("invalid lat", coordinate_response.get_json()["error"])

    def test_route_endpoint_rejects_out_of_range_and_infinite_coordinates(self):
        module = self.app_module
        invalid_bins = [
            [dict(BIN_A, lat=91)],
            [dict(BIN_A, lng=float("inf"))],
        ]
        with patch.object(module, "get_bins_data", side_effect=invalid_bins):
            out_of_range = self.client.post("/api/route", json={})
            infinite = self.client.post("/api/route", json={})

        self.assertEqual(out_of_range.status_code, 422)
        self.assertIn("invalid lat", out_of_range.get_json()["error"])
        self.assertEqual(infinite.status_code, 422)
        self.assertIn("invalid lng", infinite.get_json()["error"])

    def test_ga_endpoint_handles_single_stop_and_reports_road_source(self):
        module = self.app_module
        single_matrix = {"DEPOT": {"A": 1.0}, "A": {"DEPOT": 3.0}}
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A]),
            patch.object(module, "build_distance_matrix", return_value=single_matrix),
            patch.object(module, "calculate_detailed_path", return_value=[[1.0, 2.0]]),
        ):
            response = self.client.post(
                "/api/route/optimize-ga",
                json={"population_size": 10, "generations": 5},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["route"], ["A"])
        self.assertEqual(result["distance_km"], 4.0)
        self.assertEqual(result["distance_source"], "road_network")
        self.assertEqual(result["distance_status"], "ok")

    def test_ga_endpoint_explicitly_falls_back_for_unavailable_road_arcs(self):
        module = self.app_module
        with (
            patch.object(module, "get_bins_data", return_value=[BIN_A]),
            patch.object(module, "build_distance_matrix", return_value={}),
            patch.object(module, "calculate_detailed_path", return_value=[[1.0, 2.0]]),
        ):
            response = self.client.post(
                "/api/route/optimize-ga",
                json={"population_size": 10, "generations": 5},
            )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(result["distance_source"], "haversine")
        self.assertEqual(result["distance_status"], "degraded")
        self.assertIn("Haversine", result["distance_fallback_reason"])


if __name__ == "__main__":
    unittest.main()

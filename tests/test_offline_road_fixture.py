"""Offline tests for the frozen synthetic OSMnx-compatible graph fixture."""

from __future__ import annotations

import copy
import hashlib
import importlib
import itertools
import json
import math
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import networkx as nx
import osmnx as ox
import requests

from benchmarks.route_benchmarks.offline_graph_fixture import (
    DEPOT,
    FIXTURE_PATH,
    MANIFEST_PATH,
    NODE_BY_LOCATION_ID,
    STOPS,
)


class OfflineRoadFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._testing_environment = patch.dict(
            os.environ,
            {"FLASK_ENV": "testing", "TESTING": "true", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        cls._testing_environment.start()
        cls.routing = importlib.import_module("src.osmnx_routing")
        cls.sequencing = importlib.import_module("src.route_sequencing")
        cls.app_module = importlib.import_module("src.app")
        cls.app_module.app.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        cls._testing_environment.stop()

    def setUp(self):
        self.previous_graph = self.routing.G
        self.graph = ox.load_graphml(FIXTURE_PATH)
        self.routing.G = self.graph
        self.addCleanup(setattr, self.routing, "G", self.previous_graph)

        self.http_request = patch.object(
            requests.sessions.Session,
            "request",
            side_effect=AssertionError("HTTP is forbidden in the offline fixture tests"),
        )
        self.http_mock = self.http_request.start()
        self.addCleanup(self.http_request.stop)
        self.graph_download = patch.object(
            ox,
            "graph_from_point",
            side_effect=AssertionError("OSM graph downloads are forbidden in the offline fixture tests"),
        )
        self.graph_download_mock = self.graph_download.start()
        self.addCleanup(self.graph_download.stop)
        self.live_loader = patch.object(
            self.routing,
            "load_city_graph",
            side_effect=AssertionError("The live city graph loader must not be called by these tests"),
        )
        self.live_loader_mock = self.live_loader.start()
        self.addCleanup(self.live_loader.stop)

    def tearDown(self):
        self.http_request.stop()
        self.graph_download.stop()
        self.live_loader.stop()
        self.http_mock.assert_not_called()
        self.graph_download_mock.assert_not_called()
        self.live_loader_mock.assert_not_called()

    def _matrix(self, stops=None):
        return self.routing.build_distance_matrix(
            list(STOPS if stops is None else stops), DEPOT, cap=50
        )

    def _test_client(self):
        return self.app_module.app.test_client()

    def _post_fixed_route(self, *, geometry_patch=None):
        client = self._test_client()
        patches = [
            patch.object(self.app_module, "get_bins_data", return_value=copy.deepcopy(STOPS)),
            patch.object(
                self.app_module,
                "compute_routes_logic",
                return_value=[copy.deepcopy(STOPS)],
            ),
        ]
        if geometry_patch is not None:
            patches.append(patch.object(self.app_module, "calculate_detailed_path", return_value=geometry_patch))
        with patches[0], patches[1]:
            if len(patches) == 3:
                with patches[2]:
                    return client.post("/api/route", json={"sequence_cost": "road_network"})
            return client.post("/api/route", json={"sequence_cost": "road_network"})

    def test_fixture_manifest_hash_and_graph_metadata_match(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        actual_sha256 = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
        self.assertEqual(actual_sha256, manifest["fixture"]["sha256"])
        self.assertEqual(self.graph.number_of_nodes(), manifest["fixture"]["node_count"])
        self.assertEqual(self.graph.number_of_edges(), manifest["fixture"]["directed_edge_count"])
        self.assertIsInstance(self.graph, nx.MultiDiGraph)
        self.assertEqual(self.graph.graph["crs"].lower(), "epsg:4326")
        self.assertEqual(self.graph.graph["fixture_origin"], "synthetic_authored_fixture")
        self.assertTrue(self.graph.is_directed())

    def test_expected_coordinate_mapping_and_zero_snap_distance(self):
        locations = [DEPOT, *STOPS]
        expected_node_ids = [NODE_BY_LOCATION_ID[item["id"]] for item in locations]
        node_ids, snap_distances_m = ox.nearest_nodes(
            self.graph,
            [item["lng"] for item in locations],
            [item["lat"] for item in locations],
            return_dist=True,
        )
        self.assertEqual(list(node_ids), expected_node_ids)
        self.assertTrue(all(distance <= 1e-6 for distance in snap_distances_m))

    def test_matrix_matches_independent_directed_networkx_shortest_paths(self):
        matrix = self._matrix()
        locations = [DEPOT, *STOPS]
        for source in locations:
            source_node = NODE_BY_LOCATION_ID[source["id"]]
            independent = nx.single_source_dijkstra_path_length(
                self.graph, source_node, weight="length"
            )
            for target in locations:
                target_node = NODE_BY_LOCATION_ID[target["id"]]
                self.assertAlmostEqual(
                    matrix[source["id"]][target["id"]], independent[target_node] / 1000.0, places=12
                )

        self.assertAlmostEqual(matrix["DEPOT"]["SYN_BIN_A"], 0.1, places=12)
        self.assertAlmostEqual(matrix["SYN_BIN_A"]["SYN_BIN_B"], 0.12, places=12)
        self.assertAlmostEqual(matrix["SYN_BIN_B"]["SYN_BIN_A"], 0.4, places=12)
        self.assertFalse(self.graph.has_edge(3, 2))

    def test_sequencer_matches_all_six_directed_closed_tour_permutations(self):
        matrix = self._matrix()
        costs = [
            self.sequencing.closed_tour_cost(
                [stop["id"] for stop in order], DEPOT["id"], matrix
            )
            for order in itertools.permutations(STOPS)
        ]
        self.assertEqual(len(costs), 6)
        exact_cost = min(costs)
        sequenced = self.sequencing.sequence_route(STOPS, DEPOT, matrix)
        sequenced_cost = self.sequencing.closed_tour_cost(
            [stop["id"] for stop in sequenced], DEPOT["id"], matrix
        )
        self.assertAlmostEqual(exact_cost, 0.52, places=12)
        self.assertAlmostEqual(sequenced_cost, exact_cost, delta=1e-12)
        self.assertEqual([stop["id"] for stop in sequenced], ["SYN_BIN_A", "SYN_BIN_B", "SYN_BIN_C"])

    def test_path_geometry_follows_the_scored_cycle_and_is_checked_separately(self):
        matrix = self._matrix()
        route = self.sequencing.sequence_route(STOPS, DEPOT, matrix)
        ordered_locations = [DEPOT, *route, DEPOT]
        geometry = self.routing.calculate_detailed_path(ordered_locations)
        coordinate_to_node = {
            (data["y"], data["x"]): node_id
            for node_id, data in self.graph.nodes(data=True)
        }
        geometry_nodes = [coordinate_to_node[tuple(point)] for point in geometry]
        self.assertEqual(geometry_nodes, [1, 2, 3, 4, 1])

        geometry_length_m = sum(
            min(edge["length"] for edge in self.graph.get_edge_data(start, end).values())
            for start, end in zip(geometry_nodes, geometry_nodes[1:])
        )
        reported_matrix_km = self.sequencing.closed_tour_cost(
            [stop["id"] for stop in route], DEPOT["id"], matrix
        )
        self.assertAlmostEqual(geometry_length_m / 1000.0, reported_matrix_km, places=12)

    def test_missing_node_mapping_produces_matrix_rejected_by_sequencer(self):
        real_nearest_nodes = ox.nearest_nodes

        def fail_one_coordinate(graph, x, y, **kwargs):
            if math.isclose(x, STOPS[1]["lng"], abs_tol=1e-12):
                raise ValueError("synthetic missing mapping")
            return real_nearest_nodes(graph, x, y, **kwargs)

        with patch.object(ox, "nearest_nodes", side_effect=fail_one_coordinate):
            matrix = self._matrix()

        with self.assertRaises(self.sequencing.InvalidDistanceMatrix):
            self.sequencing.sequence_route(STOPS, DEPOT, matrix)

    def test_incomplete_matrix_is_rejected(self):
        incomplete = {
            "DEPOT": {"SYN_BIN_A": 0.1},
            "SYN_BIN_A": {},
        }
        with self.assertRaises(self.sequencing.InvalidDistanceMatrix):
            self.sequencing.sequence_route([STOPS[0]], DEPOT, incomplete)

    def test_unreachable_return_arc_fails_closed(self):
        one_way_graph = nx.MultiDiGraph(crs="epsg:4326")
        one_way_graph.add_node(1, x=DEPOT["lng"], y=DEPOT["lat"])
        one_way_graph.add_node(2, x=STOPS[0]["lng"], y=STOPS[0]["lat"])
        one_way_graph.add_edge(1, 2, length=100.0, oneway=True)
        self.routing.G = one_way_graph

        matrix = self._matrix([STOPS[0]])
        self.assertTrue(math.isinf(matrix["SYN_BIN_A"]["DEPOT"]))
        with self.assertRaises(self.sequencing.NoFeasibleTour):
            self.sequencing.sequence_route([STOPS[0]], DEPOT, matrix)

    def test_empty_and_single_stop_routes_are_safe(self):
        self.assertEqual(self.sequencing.sequence_route([], DEPOT, {}), [])
        matrix = self._matrix([STOPS[0]])
        route = self.sequencing.sequence_route([STOPS[0]], DEPOT, matrix)
        self.assertEqual([stop["id"] for stop in route], [STOPS[0]["id"]])
        self.assertAlmostEqual(
            self.sequencing.closed_tour_cost([STOPS[0]["id"]], DEPOT["id"], matrix),
            0.52,
            places=12,
        )

    def test_opt_in_api_scores_with_fixture_matrix_and_returns_separate_geometry(self):
        response = self._post_fixed_route()
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["distance_source"], "road_network")
        self.assertEqual(payload["distance_status"], "ok")
        self.assertAlmostEqual(payload["distances_km"][0], 0.52, places=2)
        self.assertEqual(
            [stop["id"] for stop in payload["routes"][0]],
            ["SYN_BIN_A", "SYN_BIN_B", "SYN_BIN_C"],
        )
        geometry = payload["path_geometries"][0]
        self.assertEqual(geometry[0], [DEPOT["lat"], DEPOT["lng"]])
        self.assertEqual(geometry[-1], [DEPOT["lat"], DEPOT["lng"]])

    def test_api_distance_is_independent_of_path_geometry_representation(self):
        sentinel_geometry = [[10.0, 20.0], [30.0, 40.0]]
        response = self._post_fixed_route(geometry_patch=sentinel_geometry)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["path_geometries"], [sentinel_geometry])
        self.assertAlmostEqual(payload["distances_km"][0], 0.52, places=2)
        self.assertEqual(payload["distance_source"], "road_network")


if __name__ == "__main__":
    unittest.main()

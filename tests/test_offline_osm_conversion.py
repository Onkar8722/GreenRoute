"""Small synthetic fixtures for the Phase 3C.2 JSON conversion path."""

import itertools
import json
import math
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import networkx as nx
import osmnx as ox
import requests

from benchmarks.route_benchmarks.osm_json_conversion import (
    ConversionError,
    canonical_graph_checksum,
    classify_way,
    convert_document,
    generate_synthetic_points,
    validate_against_source,
    validate_six_tours,
    verify_snap_point,
)


def grid_document():
    elements = []
    node_id = 1
    ids = {}
    for row in range(5):
        for column in range(5):
            ids[(row, column)] = node_id
            elements.append(
                {
                    "type": "node",
                    "id": node_id,
                    "lat": (row - 2) * 0.002,
                    "lon": (column - 2) * 0.002,
                }
            )
            node_id += 1
    way_id = 100
    for row in range(5):
        for column in range(4):
            elements.append(
                {
                    "type": "way",
                    "id": way_id,
                    "nodes": [ids[(row, column)], ids[(row, column + 1)]],
                    "tags": {"highway": "residential", "oneway": "no"},
                }
            )
            way_id += 1
    for row in range(4):
        for column in range(5):
            elements.append(
                {
                    "type": "way",
                    "id": way_id,
                    "nodes": [ids[(row, column)], ids[(row + 1, column)]],
                    "tags": {"highway": "residential", "oneway": "no"},
                }
            )
            way_id += 1

    # Parallel road, reverse oneway, implicit roundabout, ambiguous tags, and
    # malformed reference exercise independent source-to-graph validation.
    elements.extend(
        [
            {"type": "way", "id": 900, "nodes": [ids[(2, 1)], ids[(2, 2)]], "tags": {"highway": "primary", "oneway": "no"}},
            {"type": "way", "id": 901, "nodes": [ids[(2, 1)], ids[(2, 2)]], "tags": {"highway": "secondary", "oneway": "no"}},
            {"type": "way", "id": 902, "nodes": [ids[(1, 1)], ids[(1, 2)]], "tags": {"highway": "tertiary", "oneway": "-1"}},
            {"type": "way", "id": 903, "nodes": [ids[(1, 2)], ids[(1, 3)]], "tags": {"highway": "residential", "junction": "roundabout"}},
            {"type": "way", "id": 904, "nodes": [ids[(0, 0)], ids[(0, 1)]], "tags": {"highway": "primary", "access": "no", "motor_vehicle": "yes"}},
            {"type": "way", "id": 905, "nodes": [ids[(0, 1)], ids[(0, 2)]], "tags": {"highway": "primary", "oneway": "two way"}},
            {"type": "way", "id": 906, "nodes": [ids[(0, 2)], ids[(0, 3)]], "tags": {"highway": "residential", "service": "driveway"}},
            {"type": "way", "id": 907, "nodes": [ids[(0, 3)], 99999], "tags": {"highway": "primary"}},
        ]
    )
    return {"version": 0.6, "elements": elements}


def independent_haversine(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(min(1.0, math.sqrt(hav)))


def reference_from_document(document):
    nodes = {
        int(element["id"]): (float(element["lat"]), float(element["lon"]))
        for element in document["elements"]
        if element.get("type") == "node"
    }
    reference = nx.MultiDiGraph()
    for node_id, (lat, lon) in nodes.items():
        reference.add_node(node_id, x=lon, y=lat)
    for way in document["elements"]:
        if way.get("type") != "way" or classify_way(way)["status"] != "included":
            continue
        refs = way.get("nodes", [])
        if len(refs) < 2 or any(ref not in nodes for ref in refs):
            continue
        decision = classify_way(way)
        for index, (first, second) in enumerate(zip(refs, refs[1:])):
            arcs = [(first, second)]
            if decision["direction"] == "both":
                arcs.append((second, first))
            elif decision["direction"] == "reverse":
                arcs = [(second, first)]
            for u, v in arcs:
                length = independent_haversine(nodes[u], nodes[v])
                reference.add_edge(u, v, way_id=way["id"], segment_index=index, length=length)
    return reference


class TestOfflineOsmConversion(unittest.TestCase):
    def test_policy_is_conservative_and_explicit(self):
        self.assertEqual(classify_way({"tags": {"highway": "residential"}})["direction"], "both")
        self.assertEqual(classify_way({"tags": {"highway": "residential", "oneway": "-1"}})["direction"], "reverse")
        self.assertEqual(
            classify_way({"tags": {"highway": "residential", "junction": "roundabout"}})["reason"],
            "roundabout_default_forward",
        )
        self.assertEqual(classify_way({"tags": {"highway": "road"}})["status"], "excluded")
        self.assertEqual(classify_way({"tags": {"highway": "primary", "service": "driveway"}})["status"], "excluded")
        self.assertEqual(
            classify_way({"tags": {"highway": "primary", "access": "no", "motor_vehicle": "yes"}})["reason"],
            "conflicting_access_tags",
        )
        self.assertEqual(classify_way({"tags": {"highway": "primary", "oneway": "two way"}})["status"], "flagged")
        self.assertEqual(
            classify_way({"tags": {"highway": "primary", "maxweight": "7.5"}})["status"], "flagged"
        )
        self.assertEqual(
            classify_way({"tags": {"highway": "primary", "oneway:conditional": "yes @ (Mo-Fr)"}})["status"],
            "flagged",
        )

    def test_production_graph_is_built_from_source_with_direction_and_parallel_edges(self):
        result = convert_document(grid_document())
        graph = result["graph"]
        self.assertEqual(graph.graph["crs"], "EPSG:4326")
        self.assertGreater(result["diagnostics"]["parallel_directed_pair_count"], 0)
        reverse_edges = [data for _, _, _, data in graph.edges(keys=True, data=True) if data["way_id"] == 902]
        self.assertEqual(len(reverse_edges), 1)
        self.assertEqual(reverse_edges[0]["direction"], "reverse")
        roundabout_edges = [data for _, _, _, data in graph.edges(keys=True, data=True) if data["way_id"] == 903]
        self.assertEqual(len(roundabout_edges), 1)
        self.assertEqual(roundabout_edges[0]["policy_reason"], "roundabout_default_forward")
        self.assertEqual(result["diagnostics"]["way_counts"]["flagged"], 2)
        self.assertEqual(result["diagnostics"]["way_counts"]["malformed"], 1)
        self.assertEqual(result["diagnostics"]["reason_counts"]["service_tag_requires_review"], 1)
        self.assertEqual(result["diagnostics"]["node_count"], 25)

    def test_source_paths_and_lengths_match_independent_graph(self):
        result = convert_document(grid_document())
        metrics = validate_against_source(result)
        self.assertGreater(metrics["edge_records_matched"], 0)
        self.assertGreater(metrics["directed_pairs_checked"], 0)
        self.assertGreaterEqual(metrics["unreachable_directed_pairs_checked"], 0)
        self.assertLessEqual(metrics["maximum_length_error_m"], 1.5)

    def test_all_six_closed_tours_include_depot_departure_and_return(self):
        document = grid_document()
        result = convert_document(document)
        graph = result["graph"]
        points = generate_synthetic_points(graph, result["diagnostics"]["area"], count=4)
        self.assertTrue(all(point["inside_fixture_interior"] for point in points))
        independent = reference_from_document(document)
        report = validate_six_tours(graph, points, independent)
        self.assertEqual(report["permutation_count"], 6)
        self.assertTrue(all(tour["closed_tour_cost_m"] > 0 for tour in report["tours"]))
        self.assertTrue(all(tour["independent_haversine_cost_m"] > 0 for tour in report["tours"]))

        depot = points[0]["node_id"]
        stops = [point["node_id"] for point in points[1:]]
        independently_enumerated = []
        for order in itertools.permutations(stops):
            sequence = (depot, *order, depot)
            cost = sum(
                nx.shortest_path_length(independent, a, b, weight="length")
                for a, b in zip(sequence, sequence[1:])
            )
            independently_enumerated.append(cost)
        self.assertAlmostEqual(
            report["minimum_independent_cost_m"], min(independently_enumerated), delta=0.01
        )

    def test_disconnected_and_unavailable_directed_pairs_remain_unavailable(self):
        document = {
            "elements": [
                {"type": "node", "id": 1, "lat": -0.003, "lon": -0.003},
                {"type": "node", "id": 2, "lat": -0.003, "lon": -0.002},
                {"type": "node", "id": 3, "lat": 0.003, "lon": 0.002},
                {"type": "node", "id": 4, "lat": 0.003, "lon": 0.003},
                {"type": "node", "id": 5, "lat": -0.004, "lon": 0.004},
                {"type": "node", "id": 6, "lat": 0.004, "lon": -0.004},
                {"type": "way", "id": 11, "nodes": [1, 2], "tags": {"highway": "primary", "oneway": "yes"}},
                {"type": "way", "id": 12, "nodes": [2, 1], "tags": {"highway": "primary", "oneway": "yes"}},
                {"type": "way", "id": 13, "nodes": [3, 4], "tags": {"highway": "primary", "oneway": "yes"}},
                {"type": "way", "id": 14, "nodes": [4, 3], "tags": {"highway": "primary", "oneway": "yes"}},
            ]
        }
        graph = convert_document(document)["graph"]
        with self.assertRaises(nx.NetworkXNoPath):
            nx.shortest_path_length(graph, 1, 3, weight="length")

    def test_bad_ids_coordinates_and_references_are_flagged_or_rejected(self):
        invalid = grid_document()
        invalid["elements"].extend(
            [
                {"type": "node", "id": 99998, "lat": float("nan"), "lon": 0},
                {"type": "way", "id": 999, "nodes": [1, 99998], "tags": {"highway": "primary"}},
                {"type": "way", "nodes": [1, 2], "tags": {"highway": "primary"}},
            ]
        )
        result = convert_document(invalid)
        self.assertEqual(result["source_metadata"]["malformed_node_count"], 1)
        self.assertEqual(result["diagnostics"]["way_counts"]["malformed"], 3)

        conflicting = {
            "elements": [
                {"type": "node", "id": 1, "lat": 0, "lon": 0},
                {"type": "node", "id": 1, "lat": 1, "lon": 1},
            ]
        }
        with self.assertRaises(ConversionError):
            convert_document(conflicting)

    def test_snap_distance_and_fixture_boundary_are_enforced(self):
        area = {"bounds": [0.0, 0.0, 1.0, 1.0]}
        self.assertTrue(verify_snap_point({"lat": 0.5, "lon": 0.5, "snap_distance_m": 30.0}, area))
        self.assertFalse(verify_snap_point({"lat": 0.5, "lon": 0.5, "snap_distance_m": 30.001}, area))
        self.assertFalse(verify_snap_point({"lat": 0.0, "lon": 0.5, "snap_distance_m": 1.0}, area))

    def test_checksum_is_repeatable_and_network_boundaries_are_not_called(self):
        calls = []
        with (
            patch.object(requests.sessions.Session, "request", side_effect=lambda *a, **k: calls.append("http")),
            patch.object(socket.socket, "connect", side_effect=lambda *a, **k: calls.append("socket")),
            patch.object(ox, "graph_from_point", side_effect=lambda *a, **k: calls.append("osmnx")),
        ):
            first = convert_document(grid_document())
            second = convert_document(grid_document())
        self.assertEqual(calls, [])
        self.assertEqual(canonical_graph_checksum(first["graph"]), canonical_graph_checksum(second["graph"]))

    def test_graphml_roundtrip_preserves_canonical_graph(self):
        graph = convert_document(grid_document())["graph"]
        with tempfile.TemporaryDirectory(prefix="greenroute_osm_test_") as directory:
            path = Path(directory) / "synthetic.graphml"
            ox.save_graphml(graph, filepath=path)
            loaded = ox.load_graphml(path)
        self.assertEqual(canonical_graph_checksum(graph), canonical_graph_checksum(loaded))
        self.assertEqual(graph.number_of_nodes(), loaded.number_of_nodes())
        self.assertEqual(graph.number_of_edges(), loaded.number_of_edges())


if __name__ == "__main__":
    unittest.main()

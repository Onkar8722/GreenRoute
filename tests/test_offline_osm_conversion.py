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

import benchmarks.route_benchmarks.osm_json_conversion as conversion_module
from benchmarks.route_benchmarks.osm_json_conversion import (
    ConversionError,
    _compare_source_inventory,
    _reference_classify_way,
    _reference_source_selection,
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

    def test_converter_and_independent_policy_match_explicit_expected_fixtures(self):
        cases = [
            ("malformed_tags", {"tags": "bad"}, {"status": "flagged", "reason": "malformed_tags"}),
            ("missing_highway", {"tags": {}}, {"status": "flagged", "reason": "missing_or_ambiguous_highway"}),
            (
                "ambiguous_highway",
                {"tags": {"highway": "primary;secondary"}},
                {"status": "flagged", "reason": "missing_or_ambiguous_highway"},
            ),
            (
                "unsupported_highway",
                {"tags": {"highway": "road"}},
                {"status": "excluded", "reason": "highway_not_in_candidate_allowlist:road"},
            ),
            (
                "implicit_two_way",
                {"tags": {"highway": "residential"}},
                {"status": "included", "reason": "no_oneway_tag_bidirectional", "direction": "both"},
            ),
            (
                "explicit_forward",
                {"tags": {"highway": "primary", "oneway": "yes"}},
                {"status": "included", "reason": "explicit_oneway_forward", "direction": "forward"},
            ),
            (
                "explicit_reverse",
                {"tags": {"highway": "primary", "oneway": "-1"}},
                {"status": "included", "reason": "explicit_oneway_reverse", "direction": "reverse"},
            ),
            (
                "explicit_two_way",
                {"tags": {"highway": "primary", "oneway": "no"}},
                {"status": "included", "reason": "explicit_two_way", "direction": "both"},
            ),
            (
                "roundabout_default",
                {"tags": {"highway": "residential", "junction": "roundabout"}},
                {"status": "included", "reason": "roundabout_default_forward", "direction": "forward"},
            ),
            (
                "roundabout_conflict",
                {"tags": {"highway": "residential", "junction": "roundabout", "oneway": "no"}},
                {"status": "flagged", "reason": "roundabout_contradicted_by_two_way_tag"},
            ),
            (
                "ambiguous_oneway",
                {"tags": {"highway": "primary", "oneway": "two way"}},
                {"status": "flagged", "reason": "unsupported_oneway_value:two way"},
            ),
            (
                "malformed_oneway",
                {"tags": {"highway": "primary", "oneway": 1}},
                {"status": "flagged", "reason": "malformed_oneway_value"},
            ),
            (
                "explicit_denial",
                {"tags": {"highway": "primary", "access": "no"}},
                {"status": "excluded", "reason": "explicit_access_denial:access=no"},
            ),
            (
                "conflicting_access",
                {"tags": {"highway": "primary", "access": "no", "motor_vehicle": "yes"}},
                {"status": "flagged", "reason": "conflicting_access_tags"},
            ),
            (
                "contextual_access",
                {"tags": {"highway": "primary", "access": "destination"}},
                {"status": "flagged", "reason": "context_restricted_access:access=destination"},
            ),
            (
                "malformed_access",
                {"tags": {"highway": "primary", "access": 5}},
                {"status": "flagged", "reason": "malformed_access_value:access"},
            ),
            (
                "ambiguous_access",
                {"tags": {"highway": "primary", "access": "yes;permissive"}},
                {"status": "flagged", "reason": "unsupported_access_value:access=yes;permissive"},
            ),
            (
                "service_tag",
                {"tags": {"highway": "primary", "service": "driveway"}},
                {"status": "excluded", "reason": "service_tag_requires_review"},
            ),
            (
                "bus_facility",
                {"tags": {"highway": "primary", "busway": "lane"}},
                {"status": "flagged", "reason": "bus_facility_tag_requires_review"},
            ),
            (
                "vehicle_limit",
                {"tags": {"highway": "primary", "maxweight": "7.5"}},
                {"status": "flagged", "reason": "vehicle_profile_required:maxweight"},
            ),
            (
                "conditional_direction",
                {"tags": {"highway": "primary", "oneway:conditional": "yes @ (Mo-Fr)"}},
                {"status": "flagged", "reason": "conditional_access_or_direction_unsupported"},
            ),
        ]
        for name, way, expected in cases:
            with self.subTest(policy_fixture=name):
                self.assertEqual(classify_way(way), expected)
                self.assertEqual(_reference_classify_way(way), expected)

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
        inventory = metrics["selection_inventory"]
        self.assertTrue(inventory["valid"])
        self.assertEqual(inventory["expected_node_count"], inventory["actual_node_count"])
        self.assertEqual(inventory["expected_directed_edge_record_count"], inventory["actual_directed_edge_record_count"])
        self.assertEqual(inventory["missing_directed_edge_records"], [])
        self.assertEqual(inventory["unexpected_directed_edge_records"], [])
        self.assertIn("not direct coordinate-to-coordinate Haversine", metrics["distance_reference_definition"])
        self.assertEqual(metrics["path_comparison_sample"]["classification"], "all_nodes_exhaustive_for_this_graph")
        self.assertGreater(metrics["directed_pairs_checked"], 0)
        self.assertGreaterEqual(metrics["unreachable_directed_pairs_checked"], 0)
        self.assertLessEqual(metrics["maximum_length_error_m"], 1.5)

    def test_reference_selection_uses_source_area_and_not_converter_classifier_or_graph(self):
        result = convert_document(grid_document())
        source = result["source"]
        area = {"bounds": [-0.003, -0.003, 0.003, 0.003]}
        with patch.object(conversion_module, "classify_way", side_effect=AssertionError("shared classifier used")):
            selection = _reference_source_selection(source, area)
        self.assertEqual(set(selection["source_nodes"]), {7, 8, 9, 12, 13, 14, 17, 18, 19})
        self.assertEqual(len(selection["way_ids"]), 16)
        self.assertEqual(sum(selection["edge_inventory"].values()), 30)

        full_area = result["diagnostics"]["area"]
        full_selection = _reference_source_selection(source, full_area)
        inventory = _compare_source_inventory(full_selection, result["graph"])
        self.assertTrue(inventory["valid"])
        self.assertEqual(inventory["expected_node_count"], 25)
        self.assertEqual(inventory["expected_selected_way_count"], 44)
        self.assertEqual(inventory["expected_directed_edge_record_count"], 86)

    def test_inventory_reports_missing_unexpected_nodes_and_way_segments(self):
        result = convert_document(grid_document())
        selection = _reference_source_selection(result["source"], result["diagnostics"]["area"])
        altered = result["graph"].copy()
        u, v, key, data = next(iter(altered.edges(keys=True, data=True)))
        missing_record = (int(u), int(v), int(data["way_id"]), int(data["segment_index"]), str(data["direction"]))
        altered.remove_edge(u, v, key)
        missing_node = next(iter(selection["source_nodes"]))
        altered.remove_node(missing_node)
        altered.add_node(999999, x=0.0, y=0.0)
        remaining_nodes = [node_id for node_id in altered.nodes if node_id != 999999]
        altered.add_edge(
            remaining_nodes[0],
            remaining_nodes[1],
            way_id=999999,
            segment_index=123,
            direction="forward",
            length=1.0,
        )

        report = _compare_source_inventory(selection, altered)
        self.assertFalse(report["valid"])
        self.assertIn(missing_node, report["missing_node_ids"])
        self.assertIn(999999, report["unexpected_node_ids"])
        self.assertIn(
            {
                "from_node_id": missing_record[0],
                "to_node_id": missing_record[1],
                "source_way_id": missing_record[2],
                "segment_index": missing_record[3],
                "direction": missing_record[4],
                "count": 1,
            },
            report["missing_directed_edge_records"],
        )
        self.assertTrue(any(record["source_way_id"] == 999999 for record in report["unexpected_directed_edge_records"]))
        altered_result = {**result, "graph": altered}
        with self.assertRaises(ConversionError) as raised:
            validate_against_source(altered_result, reference_selection=selection)
        self.assertIn('"source_way_id":999999', str(raised.exception))
        self.assertIn('"segment_index":123', str(raised.exception))

    def test_all_six_closed_tours_include_depot_departure_and_return(self):
        document = grid_document()
        result = convert_document(document)
        graph = result["graph"]
        points = generate_synthetic_points(graph, result["diagnostics"]["area"], count=4)
        self.assertTrue(all(point["inside_fixture_interior"] for point in points))
        independent_selection = _reference_source_selection(result["source"], result["diagnostics"]["area"])
        independent = independent_selection["graph"]
        report = validate_six_tours(graph, points, independent)
        self.assertEqual(report["permutation_count"], 6)
        self.assertTrue(all(tour["closed_tour_cost_m"] > 0 for tour in report["tours"]))
        self.assertTrue(all(tour["independent_haversine_cost_m"] > 0 for tour in report["tours"]))
        self.assertIn("not direct coordinate-to-coordinate Haversine", report["independent_haversine_cost_definition"])

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

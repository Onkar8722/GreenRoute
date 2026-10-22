"""Offline tests for deterministic directed-matrix route sequencing."""

import unittest

from src.route_sequencing import (
    InvalidDistanceMatrix,
    NoFeasibleTour,
    closed_tour_cost,
    sequence_route,
)


DEPOT = {"id": "DEPOT"}


def complete_matrix(node_ids, values):
    return {
        start_id: {
            end_id: (
                0.0
                if start_id == end_id
                else values.get((start_id, end_id), 10.0)
            )
            for end_id in node_ids
        }
        for start_id in node_ids
    }


class DirectedMatrixSequencingTests(unittest.TestCase):
    def test_empty_single_and_two_stop_closed_tour_costs(self):
        self.assertEqual(sequence_route([], DEPOT, {}), [])

        one_stop = [{"id": "A", "fill": 80}]
        single_matrix = complete_matrix(
            ["DEPOT", "A"],
            {("DEPOT", "A"): 2.5, ("A", "DEPOT"): 4.25},
        )
        self.assertEqual(sequence_route(one_stop, DEPOT, single_matrix), one_stop)
        self.assertEqual(closed_tour_cost(("A",), "DEPOT", single_matrix), 6.75)

        two_stops = [{"id": "A"}, {"id": "B"}]
        two_matrix = complete_matrix(
            ["DEPOT", "A", "B"],
            {
                ("DEPOT", "A"): 2,
                ("A", "B"): 3,
                ("B", "DEPOT"): 7,
                ("DEPOT", "B"): 5,
                ("B", "A"): 11,
                ("A", "DEPOT"): 13,
            },
        )
        result = sequence_route(two_stops, DEPOT, two_matrix)
        self.assertEqual([item["id"] for item in result], ["A", "B"])
        self.assertEqual(closed_tour_cost(tuple(item["id"] for item in result), "DEPOT", two_matrix), 12)

    def test_directed_reversal_is_scored_with_all_reversed_arcs(self):
        matrix = complete_matrix(
            ["DEPOT", "A", "B"],
            {
                ("DEPOT", "A"): 1,
                ("A", "B"): 1,
                ("B", "DEPOT"): 8,
                ("DEPOT", "B"): 2,
                ("B", "A"): 100,
                ("A", "DEPOT"): 1,
            },
        )
        original_cost = closed_tour_cost(("A", "B"), "DEPOT", matrix)
        reverse_cost = closed_tour_cost(("B", "A"), "DEPOT", matrix)

        result = sequence_route([{"id": "A"}, {"id": "B"}], DEPOT, matrix)
        result_ids = tuple(item["id"] for item in result)

        self.assertEqual(result_ids, ("A", "B"))
        self.assertEqual(original_cost, 10)
        self.assertEqual(reverse_cost, 103)

    def test_strict_improvement_is_never_worse_than_input_or_nearest_neighbor(self):
        ids = ["DEPOT", "A", "B", "C"]
        matrix = complete_matrix(
            ids,
            {
                ("DEPOT", "A"): 10,
                ("DEPOT", "B"): 1,
                ("DEPOT", "C"): 20,
                ("A", "DEPOT"): 20,
                ("A", "B"): 20,
                ("A", "C"): 1,
                ("B", "DEPOT"): 1,
                ("B", "A"): 10,
                ("B", "C"): 20,
                ("C", "DEPOT"): 1,
                ("C", "A"): 20,
                ("C", "B"): 1,
            },
        )
        input_cost = closed_tour_cost(("A", "B", "C"), "DEPOT", matrix)
        nearest_cost = closed_tour_cost(("B", "A", "C"), "DEPOT", matrix)

        result = sequence_route(
            [{"id": "A"}, {"id": "B"}, {"id": "C"}],
            DEPOT,
            matrix,
        )
        result_cost = closed_tour_cost(
            tuple(item["id"] for item in result),
            "DEPOT",
            matrix,
        )

        self.assertLess(result_cost, input_cost)
        self.assertLessEqual(result_cost, nearest_cost)

    def test_ties_use_lexical_ids_and_are_repeatable(self):
        matrix = complete_matrix(
            ["DEPOT", "A", "B"],
            {
                ("DEPOT", "A"): 1,
                ("DEPOT", "B"): 1,
                ("A", "B"): 1,
                ("B", "A"): 1,
                ("A", "DEPOT"): 1,
                ("B", "DEPOT"): 1,
            },
        )
        stops = [{"id": "B"}, {"id": "A"}]
        first = sequence_route(stops, DEPOT, matrix)
        second = sequence_route(stops, DEPOT, matrix)

        self.assertEqual([item["id"] for item in first], ["A", "B"])
        self.assertEqual([item["id"] for item in second], ["A", "B"])
        self.assertEqual(closed_tour_cost(("A", "B"), "DEPOT", matrix), 3)

    def test_blocked_arcs_are_avoided_when_a_closed_candidate_exists(self):
        matrix = complete_matrix(
            ["DEPOT", "A", "B"],
            {
                ("DEPOT", "A"): 1,
                ("DEPOT", "B"): 2,
                ("A", "B"): float("inf"),
                ("A", "DEPOT"): 1,
                ("B", "A"): 1,
                ("B", "DEPOT"): 1,
            },
        )
        result = sequence_route([{"id": "A"}, {"id": "B"}], DEPOT, matrix)
        result_ids = tuple(item["id"] for item in result)
        self.assertEqual(result_ids, ("B", "A"))
        self.assertEqual(closed_tour_cost(result_ids, "DEPOT", matrix), 4)

    def test_incomplete_and_invalid_matrices_fail_explicitly(self):
        stops = [{"id": "A"}, {"id": "B"}]
        with self.assertRaisesRegex(InvalidDistanceMatrix, "missing directed arc"):
            sequence_route(stops, DEPOT, {"DEPOT": {"A": 1}, "A": {}, "B": {}})

        for invalid_distance in (-1, float("nan"), float("-inf"), True, "3"):
            matrix = complete_matrix(
                ["DEPOT", "A"],
                {("DEPOT", "A"): invalid_distance},
            )
            with self.subTest(invalid_distance=invalid_distance):
                with self.assertRaises(InvalidDistanceMatrix):
                    sequence_route([{"id": "A"}], DEPOT, matrix)

    def test_no_feasible_closed_tour_has_a_clear_error(self):
        matrix = complete_matrix(
            ["DEPOT", "A", "B"],
            {
                ("DEPOT", "A"): 1,
                ("DEPOT", "B"): 2,
                ("A", "B"): float("inf"),
                ("A", "DEPOT"): float("inf"),
                ("B", "A"): float("inf"),
                ("B", "DEPOT"): float("inf"),
            },
        )
        with self.assertRaisesRegex(NoFeasibleTour, "no feasible closed tour"):
            sequence_route([{"id": "A"}, {"id": "B"}], DEPOT, matrix)

    def test_duplicate_stop_ids_and_invalid_search_limits_are_rejected(self):
        matrix = complete_matrix(["DEPOT", "A"], {})
        with self.assertRaisesRegex(ValueError, "unique"):
            sequence_route([{"id": "A"}, {"id": "A"}], DEPOT, matrix)
        with self.assertRaisesRegex(ValueError, "max_passes"):
            sequence_route([{"id": "A"}], DEPOT, matrix, max_passes=True)


if __name__ == "__main__":
    unittest.main()

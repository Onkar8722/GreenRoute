"""Measure fixture-only OSMnx, matrix, sequencing, geometry, and API timings.

All timings use the authored synthetic GraphML fixture. HTTP and OSM graph
download boundaries are blocked for the entire measurement process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import osmnx as ox
import requests

from benchmarks.route_benchmarks.offline_graph_fixture import (
    DEPOT,
    FIXTURE_PATH,
    MANIFEST_PATH,
    STOPS,
)


def _measure(operation, repetitions: int) -> dict:
    for _ in range(3):
        operation()
    samples = []
    for _ in range(repetitions):
        started = time.perf_counter()
        operation()
        samples.append(time.perf_counter() - started)
    ordered = sorted(samples)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "sample_count": len(samples),
        "median_seconds": statistics.median(samples),
        "p95_nearest_rank_seconds": ordered[p95_index],
    }


def run(repetitions: int = 100) -> dict:
    os.environ["FLASK_ENV"] = "testing"
    os.environ["TESTING"] = "true"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    from src import osmnx_routing as routing
    from src.route_sequencing import closed_tour_cost, sequence_route
    from src.app import app, calculate_detailed_path
    import src.app as app_module

    logging.getLogger("src.osmnx_routing").setLevel(logging.WARNING)
    logging.getLogger("src.app").setLevel(logging.WARNING)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fixture_sha256 = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    if fixture_sha256 != manifest["fixture"]["sha256"]:
        raise RuntimeError("fixture SHA-256 does not match the committed manifest")

    original_graph = routing.G
    http_block = patch.object(
        requests.sessions.Session,
        "request",
        side_effect=AssertionError("HTTP is forbidden in the offline timing run"),
    )
    download_block = patch.object(
        ox,
        "graph_from_point",
        side_effect=AssertionError("OSM graph downloads are forbidden in the offline timing run"),
    )
    loader_block = patch.object(
        routing,
        "load_city_graph",
        side_effect=AssertionError("the live city graph loader is forbidden in the offline timing run"),
    )

    http_mock = http_block.start()
    download_mock = download_block.start()
    loader_mock = loader_block.start()
    try:
        graph_holder = {"graph": ox.load_graphml(FIXTURE_PATH)}
        routing.G = graph_holder["graph"]

        def load_graph():
            graph_holder["graph"] = ox.load_graphml(FIXTURE_PATH)

        graph_loading = _measure(load_graph, repetitions)
        routing.G = graph_holder["graph"]

        matrix_holder = {"matrix": None}

        def build_matrix():
            matrix_holder["matrix"] = routing.build_distance_matrix(STOPS, DEPOT, cap=50)

        mapping_and_matrix = _measure(build_matrix, repetitions)
        matrix = matrix_holder["matrix"]

        route_holder = {"route": None}

        def sequence():
            route_holder["route"] = sequence_route(STOPS, DEPOT, matrix)

        sequencing = _measure(sequence, repetitions)
        route = route_holder["route"]
        route_cost = closed_tour_cost([stop["id"] for stop in route], DEPOT["id"], matrix)

        def geometry():
            calculate_detailed_path([DEPOT, *route, DEPOT])

        path_geometry = _measure(geometry, repetitions)

        app.config["TESTING"] = True
        client = app.test_client()
        fixed_routes = [list(STOPS)]
        with (
            patch.object(app_module, "get_bins_data", return_value=[dict(stop) for stop in STOPS]),
            patch.object(app_module, "compute_routes_logic", return_value=fixed_routes),
        ):
            def post_route():
                response = client.post(
                    "/api/route", json={"sequence_cost": "road_network"}
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"offline test-client route failed: {response.status_code} "
                        f"{response.get_data(as_text=True)}"
                    )

            endpoint_total = _measure(post_route, repetitions)

        if not math.isfinite(route_cost):
            raise RuntimeError("sequencer produced a non-finite fixture route cost")
        if http_mock.call_count or download_mock.call_count or loader_mock.call_count:
            raise RuntimeError("an external request or live graph loader was attempted")

        return {
            "schema": "phase3b-offline-fixture-timing-v1",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "measurement_kind": "synthetic_fixture_only",
            "fixture_sha256": fixture_sha256,
            "fixture_node_count": graph_holder["graph"].number_of_nodes(),
            "fixture_directed_edge_count": graph_holder["graph"].number_of_edges(),
            "osmnx_version": ox.__version__,
            "python_version": sys.version,
            "platform": platform.platform(),
            "repetitions_per_stage": repetitions,
            "percentile_method": "nearest-rank p95 after three unreported warmups per stage",
            "sequenced_stop_ids": [stop["id"] for stop in route],
            "independent_matrix_route_cost_km": route_cost,
            "stages": {
                "graph_loading": {
                    **graph_loading,
                    "includes": "local GraphML file parsing and OSMnx GraphML attribute conversion",
                },
                "coordinate_mapping_and_matrix_build": {
                    **mapping_and_matrix,
                    "includes": "nearest-node mapping plus all directed Dijkstra rows and meter-to-kilometer conversion",
                },
                "sequencing": {
                    **sequencing,
                    "includes": "sequence_route only; matrix preparation excluded",
                },
                "path_geometry": {
                    **path_geometry,
                    "includes": "calculate_detailed_path on the already-loaded fixture graph",
                },
                "test_client_endpoint_total": {
                    **endpoint_total,
                    "includes": "Flask test-client dispatch through the production /api/route handler with local fixture graph and patched bin selection/data access",
                },
            },
            "network_calls": 0,
            "production_road_performance_claim": False,
        }
    finally:
        routing.G = original_graph
        loader_block.stop()
        download_block.stop()
        http_block.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=100)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional UTF-8 JSON output path; the default writes JSON to stdout",
    )
    args = parser.parse_args(argv)
    if args.repetitions < 10:
        parser.error("--repetitions must be at least 10 for a descriptive p95")
    rendered = json.dumps(run(args.repetitions), indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding="utf-8")
        sys.stderr.write(f"Wrote fixture timing JSON to {args.output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

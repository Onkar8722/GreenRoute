"""Run the bounded Phase 3C.2 offline OSM JSON conversion experiment."""

from __future__ import annotations

import ctypes
import argparse
import hashlib
import importlib.metadata
import json
import os
import shutil
import socket
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox
import requests

from benchmarks.route_benchmarks.osm_json_conversion import (
    MAX_DIRECTED_EDGES,
    MAX_NODES,
    MAX_SNAP_DISTANCE_M,
    POLICY_NAME,
    POLICY_VERSION,
    ConversionError,
    _reference_source_selection,
    canonical_graph_checksum,
    convert_parsed_source,
    generate_synthetic_points,
    parse_source_bytes,
    validate_against_source,
    validate_six_tours,
    verify_snap_point,
)


ROOT = Path(__file__).resolve().parents[2]
OCTOBER_SOURCE = ROOT / "src" / "cache" / "943a2cc7ad43949e49e19c3e9cfcc2576776572e.json"
OUTPUT_GRAPH = ROOT / "benchmarks" / "route_benchmarks" / "phase3c2_october_demo.graphml"
OUTPUT_MANIFEST = ROOT / "benchmarks" / "route_benchmarks" / "phase3c2_conversion_manifest.json"
OUTPUT_DIRECTORY = OUTPUT_GRAPH.parent
EXPECTED_SOURCE_SHA256 = "23199899b4952ffba3472c602CAFD65CF2E8F8ED1BE3D3FD7CE6FF45D29322DF".lower()
APRIL_SOURCE = ROOT / "cache" / "943a2cc7ad43949e49e19c3e9cfcc2576776572e.json"
EXPECTED_APRIL_SHA256 = "9e580eb806a53568b77aa74c1e38e2d0591358d09d11b78c79d1146acaed7d2d"
EXPECTED_METADATA = {
    "overpass_timestamp": "2026-10-20T09:10:21Z",
    "element_counts": {"node": 217484, "way": 44008, "relation": 0},
    "bounds": [18.3466226, 73.662884, 18.68269, 74.0418075],
}


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def peak_working_set_bytes() -> int | None:
    """Read this isolated process's Windows peak resident working set."""
    if os.name != "nt":
        return None
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    get_memory = psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
    get_memory.restype = wintypes.BOOL
    process = kernel32.GetCurrentProcess()
    ok = get_memory(
        process,
        ctypes.byref(counters),
        counters.cb,
    )
    return int(counters.PeakWorkingSetSize) if ok else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def block_network_calls() -> tuple[list[str], list[tuple[Any, str, Any]]]:
    """Install in-process guards; any attempted network boundary fails closed."""
    attempts: list[str] = []
    originals: list[tuple[Any, str, Any]] = []

    def deny(label: str):
        def blocked(*_args: Any, **_kwargs: Any) -> None:
            attempts.append(label)
            raise RuntimeError(f"Network access is forbidden in this offline experiment: {label}")

        return blocked

    patches = [
        (requests.sessions.Session, "request", "requests.Session.request"),
        (socket.socket, "connect", "socket.socket.connect"),
        (socket, "create_connection", "socket.create_connection"),
        (socket, "getaddrinfo", "socket.getaddrinfo"),
    ]
    for owner, name, label in patches:
        originals.append((owner, name, getattr(owner, name)))
        setattr(owner, name, deny(label))
    return attempts, originals


def restore_patches(originals: list[tuple[Any, str, Any]]) -> None:
    for owner, name, original in reversed(originals):
        setattr(owner, name, original)


def dependency_versions() -> dict[str, str]:
    packages = ("osmnx", "networkx", "geopandas", "pandas", "shapely", "pyproj")
    return {name: importlib.metadata.version(name) for name in packages}


def main(output_stem: str = "phase3c2_october_demo", *, reverify_existing: bool = False) -> int:
    global OUTPUT_GRAPH, OUTPUT_MANIFEST
    if not output_stem or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in output_stem):
        raise ConversionError("Output stem may contain only ASCII letters, digits, underscores, and hyphens")
    OUTPUT_GRAPH = OUTPUT_DIRECTORY / f"{output_stem}.graphml"
    manifest_name = (
        "phase3c2_conversion_manifest.json"
        if output_stem == "phase3c2_october_demo"
        else f"{output_stem}_manifest.json"
    )
    OUTPUT_MANIFEST = OUTPUT_DIRECTORY / manifest_name
    started = time.perf_counter()
    if not OCTOBER_SOURCE.is_file():
        raise ConversionError(f"Approved October source file not found: {OCTOBER_SOURCE}")
    existing_graph = OUTPUT_GRAPH.exists()
    existing_manifest = OUTPUT_MANIFEST.exists()
    if existing_graph != existing_manifest:
        raise ConversionError("Refusing to proceed with only one of the GraphML/manifest output pair present")
    existing_graph_sha256 = None
    if existing_graph:
        if not reverify_existing:
            raise ConversionError("Refusing to overwrite existing outputs; pass --reverify-existing to validate this pair")
        prior_manifest = json.loads(OUTPUT_MANIFEST.read_text(encoding="utf-8"))
        existing_graph_sha256 = sha256_file(OUTPUT_GRAPH)
        if (
            prior_manifest.get("schema") != "phase3c2-offline-osm-conversion-v1"
            or prior_manifest.get("source", {}).get("sha256_after") != EXPECTED_SOURCE_SHA256
            or prior_manifest.get("artifact", {}).get("sha256") != existing_graph_sha256
        ):
            raise ConversionError("Existing outputs do not match this runner's source identity; refusing refresh")
    cache_dirs = {(ROOT / "cache").resolve(), (ROOT / "src" / "cache").resolve()}
    if OUTPUT_GRAPH.resolve().parent in cache_dirs or OUTPUT_MANIFEST.resolve().parent in cache_dirs:
        raise ConversionError("Output path overlaps a protected source-cache directory")

    if not APRIL_SOURCE.is_file():
        raise ConversionError(f"April source file not found for hash verification: {APRIL_SOURCE}")
    april_hash_before = sha256_file(APRIL_SOURCE)
    if april_hash_before != EXPECTED_APRIL_SHA256:
        raise ConversionError(f"April source identity mismatch: {april_hash_before}")

    read_started = time.perf_counter()
    raw = OCTOBER_SOURCE.read_bytes()
    source_read_seconds = time.perf_counter() - read_started
    source_hash_before = hashlib.sha256(raw).hexdigest()
    if source_hash_before != EXPECTED_SOURCE_SHA256:
        raise ConversionError(f"October source identity mismatch: {source_hash_before}")

    parse_started = time.perf_counter()
    source, source_metadata = parse_source_bytes(raw)
    parse_seconds = time.perf_counter() - parse_started
    for key, expected in EXPECTED_METADATA.items():
        if source_metadata.get(key) != expected:
            raise ConversionError(
                f"October source metadata mismatch for {key}: {source_metadata.get(key)!r} != {expected!r}"
            )

    attempts, patches = block_network_calls()
    try:
        conversion = convert_parsed_source(
            source,
            source_metadata,
            expected_metadata=EXPECTED_METADATA,
            parse_seconds=parse_seconds,
        )
        graph = conversion["graph"]
        diagnostics = conversion["diagnostics"]
        if graph.number_of_nodes() > MAX_NODES or graph.number_of_edges() > MAX_DIRECTED_EDGES:
            raise ConversionError("Converted graph exceeded an approved graph-size cap")

        reference_selection = _reference_source_selection(source, diagnostics["area"])
        source_validation = validate_against_source(
            conversion,
            reference_selection=reference_selection,
        )
        points = generate_synthetic_points(graph, diagnostics["area"], count=4)
        if not all(verify_snap_point(point, diagnostics["area"], MAX_SNAP_DISTANCE_M) for point in points):
            raise ConversionError("One or more synthetic coordinates failed the interior/snap gate")
        tour_validation = validate_six_tours(graph, points, reference_selection["graph"])

        checksum_first = canonical_graph_checksum(graph)
        repeat_started = time.perf_counter()
        repeated = convert_parsed_source(source, source_metadata, expected_metadata=EXPECTED_METADATA)
        checksum_second = canonical_graph_checksum(repeated["graph"])
        repeat_seconds = time.perf_counter() - repeat_started
        if checksum_first != checksum_second:
            raise ConversionError("Repeated conversion did not reproduce the canonical graph checksum")

        source_hash_pre_artifact = sha256_file(OCTOBER_SOURCE)
        if source_hash_pre_artifact != source_hash_before:
            raise ConversionError("Source cache changed during the experiment; final artifact withheld")

        scratch_root = Path(tempfile.gettempdir()).resolve()
        if any(scratch_root == cache or cache in scratch_root.parents for cache in cache_dirs):
            raise ConversionError("Temporary scratch directory overlaps a protected source cache")
        with tempfile.TemporaryDirectory(prefix="greenroute_phase3c2_") as scratch_name:
            scratch = Path(scratch_name)
            scratch_graph = scratch / "phase3c2_october_demo.graphml"
            serialization_started = time.perf_counter()
            ox.save_graphml(graph, filepath=scratch_graph)
            serialization_seconds = time.perf_counter() - serialization_started
            roundtrip = ox.load_graphml(scratch_graph)
            if canonical_graph_checksum(roundtrip) != checksum_first:
                raise ConversionError("Serialized GraphML checksum differs after local reload")
            if roundtrip.number_of_nodes() != graph.number_of_nodes() or roundtrip.number_of_edges() != graph.number_of_edges():
                raise ConversionError("Serialized GraphML changed graph size")
            scratch_graph_sha256 = sha256_file(scratch_graph)
            if existing_graph and scratch_graph_sha256 != existing_graph_sha256:
                raise ConversionError("Reverified GraphML differs from the existing artifact; manifest was not refreshed")

            # All validation gates have passed. Publish new, previously absent
            # files only under the benchmark directory, never under either cache.
            publish_started = time.perf_counter()
            if not existing_graph:
                shutil.copyfile(scratch_graph, OUTPUT_GRAPH)
            artifact_publish_seconds = time.perf_counter() - publish_started

        source_hash_after = sha256_file(OCTOBER_SOURCE)
        if source_hash_after != source_hash_before:
            raise ConversionError("Source cache hash changed after artifact publication")
        april_hash_after = sha256_file(APRIL_SOURCE)
        if april_hash_after != april_hash_before:
            raise ConversionError("April source cache hash changed during the experiment")

        module_path = Path(__file__).with_name("osm_json_conversion.py")
        cache_april = ROOT / "cache" / "943a2cc7ad43949e49e19c3e9cfcc2576776572e.json"
        manifest = {
            "schema": "phase3c2-offline-osm-conversion-v1",
            "artifact": {
                "path": str(OUTPUT_GRAPH.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_file(OUTPUT_GRAPH),
                "canonical_graph_sha256": checksum_first,
                "status": "conversion-mechanics demonstration only; not a verified drive graph",
                "nodes": graph.number_of_nodes(),
                "directed_edges": graph.number_of_edges(),
                "parallel_directed_pair_count": diagnostics["parallel_directed_pair_count"],
                "strongly_connected_component_count": diagnostics["strongly_connected_component_count"],
                "largest_strong_component_nodes": diagnostics["largest_strong_component_nodes"],
                "largest_strong_component_fraction": diagnostics["largest_strong_component_fraction"],
            },
            "source": {
                "path": str(OCTOBER_SOURCE.relative_to(ROOT)).replace("\\", "/"),
                "sha256_before": source_hash_before,
                "sha256_before_artifact": source_hash_pre_artifact,
                "sha256_after": source_hash_after,
                "bytes": len(raw),
                "snapshot_timestamp": source_metadata["overpass_timestamp"],
                "element_counts": source_metadata["element_counts"],
                "geographic_bounds_lat_lon": source_metadata["bounds"],
                "generator": source_metadata["generator"],
                "query_filter_provenance": "unknown; not recorded in this response or supplied cache metadata",
                "copyright_notice": source.get("document", {}).get("osm3s", {}).get("copyright"),
                "april_snapshot": {
                    "path": str(cache_april.relative_to(ROOT)).replace("\\", "/"),
                    "sha256_before": april_hash_before,
                    "sha256_after": april_hash_after,
                    "used_for_conversion": False,
                },
            },
            "policy": {
                "name": POLICY_NAME,
                "version": POLICY_VERSION,
                "highway_allowlist": sorted(__import__("benchmarks.route_benchmarks.osm_json_conversion", fromlist=["ALLOWED_HIGHWAYS"]).ALLOWED_HIGHWAYS),
                "diagnostics": diagnostics,
                "access_policy_is_candidate_filter_only": True,
                "vehicle_profile": "unspecified; physical restrictions are excluded pending a real fleet profile",
            },
            "validation": {
                "source_graph_comparison": source_validation,
                "synthetic_points": points,
                "six_tour_enumeration": tour_validation,
                "repeat_conversion_seconds": repeat_seconds,
                "canonical_checksum_repeat_match": checksum_first == checksum_second,
                "serialized_graph_checksum_match": True,
                "serialized_graph_sha256": scratch_graph_sha256,
                "graphml_roundtrip_nodes": roundtrip.number_of_nodes(),
                "graphml_roundtrip_directed_edges": roundtrip.number_of_edges(),
            },
            "conversion": {
                "code_version": "phase3c2-offline-converter-v1",
                "converter_source_sha256": sha256_file(module_path),
                "runner_source_sha256": sha256_file(Path(__file__)),
                "dependency_versions": dependency_versions(),
                "crs": "EPSG:4326",
                "edge_length_method": "WGS84 ellipsoidal geodesic length in meters, rounded to 1 micrometer",
                "timings_seconds": {
                    "source_read_seconds": source_read_seconds,
                    **conversion["timings"],
                    "graphml_serialization_seconds": serialization_seconds,
                    "artifact_publish_seconds": artifact_publish_seconds,
                    "total_process_elapsed_seconds_before_manifest": time.perf_counter() - started,
                },
                "peak_process_working_set_bytes": peak_working_set_bytes(),
                "network_calls_attempted": len(attempts),
                "network_call_attempts": attempts,
            },
            "licensing": {
                "source_notice": "OpenStreetMap contributors; Open Database License (ODbL) per local response notice",
                "attribution_review": "pending",
                "share_alike_and_distribution_review": "pending; no legal conclusion made",
            },
            "limitations": [
                "Original Overpass query and requested network filter are unknown.",
                "The conservative candidate-access policy does not model a specific collection vehicle.",
                "The artifact is not integrated with production routing and is not a verified drive graph.",
                "ODbL attribution and distribution review is pending.",
            ],
        }
        OUTPUT_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if attempts:
            raise ConversionError(f"Unexpected blocked network call attempts: {attempts}")
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    finally:
        restore_patches(patches)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-stem",
        default="phase3c2_october_demo",
        help="new, absent artifact basename under benchmarks/route_benchmarks",
    )
    parser.add_argument(
        "--reverify-existing",
        action="store_true",
        help="rebuild and compare an existing artifact before refreshing only its manifest",
    )
    args = parser.parse_args()
    raise SystemExit(main(args.output_stem, reverify_existing=args.reverify_existing))

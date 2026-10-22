"""Bounded offline conversion of a local Overpass JSON response.

This module deliberately contains no graph-acquisition calls. The converter is
for a reproducible conversion-mechanics experiment, not a production truck
routing graph. See PHASE3C2_CONVERSION.md for the policy and source limits.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import osmnx as ox
from pyproj import Geod
from shapely.geometry import LineString, Point


POLICY_NAME = "greenroute_conservative_candidate_access"
POLICY_VERSION = "1.0.0"
MAX_NODES = 10_000
MAX_DIRECTED_EDGES = 25_000
MAX_SNAP_DISTANCE_M = 30.0
LENGTH_METHOD = "WGS84 ellipsoidal inverse geodesic (pyproj.Geod)"
ALLOWED_HIGHWAYS = frozenset(
    {
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
        "tertiary",
        "tertiary_link",
        "residential",
        "unclassified",
        "living_street",
        "trunk",
        "trunk_link",
    }
)
ACCESS_KEYS = ("access", "vehicle", "motor_vehicle", "hgv", "goods")
PERMITTED_ACCESS_VALUES = frozenset({"yes", "permissive"})
DENIED_ACCESS_VALUES = frozenset({"no", "private", "military", "emergency"})
CONTEXT_ACCESS_VALUES = frozenset(
    {"destination", "customers", "delivery", "agricultural", "forestry", "permit", "discouraged"}
)
VEHICLE_LIMIT_KEYS = ("maxweight", "maxheight", "maxwidth", "maxlength", "maxaxleload")
ROUTING_TAG_KEYS = frozenset(
    {
        "highway",
        "access",
        "vehicle",
        "motor_vehicle",
        "motorcar",
        "hgv",
        "goods",
        "maxweight",
        "maxheight",
        "maxwidth",
        "maxlength",
        "maxaxleload",
        "oneway",
        "junction",
        "service",
        "busway",
        "bus_stop",
    }
)
GEOD = Geod(ellps="WGS84")


class ConversionError(ValueError):
    """Raised when the local source cannot satisfy a conversion gate."""


def _is_finite_coordinate(lat: Any, lon: Any) -> bool:
    if (
        not isinstance(lat, (int, float))
        or isinstance(lat, bool)
        or not isinstance(lon, (int, float))
        or isinstance(lon, bool)
    ):
        return False
    try:
        lat_value, lon_value = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(lat_value)
        and math.isfinite(lon_value)
        and -90.0 <= lat_value <= 90.0
        and -180.0 <= lon_value <= 180.0
    )


def parse_source_bytes(raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode a local JSON document and validate its element identities."""
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Invalid Overpass JSON: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise ConversionError("Overpass JSON must contain an elements array")

    nodes: dict[int, tuple[float, float]] = {}
    invalid_node_ids: set[int] = set()
    ways: dict[int, dict[str, Any]] = {}
    duplicate_identical_nodes = 0
    duplicate_identical_ways = 0
    conflicting_ids: list[str] = []
    malformed_nodes = 0
    malformed_way_records = 0
    malformed_other_elements = 0

    for element in document["elements"]:
        if not isinstance(element, dict):
            malformed_other_elements += 1
            continue
        kind = element.get("type")
        if kind == "node":
            try:
                node_id = int(element["id"])
            except (KeyError, TypeError, ValueError):
                malformed_nodes += 1
                continue
            if not _is_finite_coordinate(element.get("lat"), element.get("lon")):
                malformed_nodes += 1
                invalid_node_ids.add(node_id)
                nodes.pop(node_id, None)
                continue
            coords = (float(element["lat"]), float(element["lon"]))
            if node_id in invalid_node_ids:
                continue
            old = nodes.get(node_id)
            if old is not None:
                if old == coords:
                    duplicate_identical_nodes += 1
                else:
                    conflicting_ids.append(f"node:{node_id}")
                    invalid_node_ids.add(node_id)
                    nodes.pop(node_id, None)
            else:
                nodes[node_id] = coords
        elif kind == "way":
            try:
                way_id = int(element["id"])
            except (KeyError, TypeError, ValueError):
                malformed_way_records += 1
                continue
            prior = ways.get(way_id)
            if prior is None:
                ways[way_id] = element
            elif prior == element:
                duplicate_identical_ways += 1
            else:
                conflicting_ids.append(f"way:{way_id}")

    if conflicting_ids:
        raise ConversionError(
            "Conflicting duplicate element IDs: " + ", ".join(sorted(set(conflicting_ids))[:20])
        )

    if not nodes:
        raise ConversionError("Source contains no valid coordinate nodes")
    lats = [coord[0] for coord in nodes.values()]
    lons = [coord[1] for coord in nodes.values()]
    metadata = {
        "generator": document.get("generator"),
        "overpass_timestamp": document.get("osm3s", {}).get("timestamp_osm_base"),
        "json_version": document.get("version"),
        "element_counts": {
            "node": sum(isinstance(e, dict) and e.get("type") == "node" for e in document["elements"]),
            "way": sum(isinstance(e, dict) and e.get("type") == "way" for e in document["elements"]),
            "relation": sum(isinstance(e, dict) and e.get("type") == "relation" for e in document["elements"]),
        },
        "valid_node_count": len(nodes),
        "unique_way_count": len(ways),
        "malformed_node_count": malformed_nodes,
        "malformed_way_record_count": malformed_way_records,
        "malformed_other_element_count": malformed_other_elements,
        "invalid_node_ids": sorted(invalid_node_ids),
        "duplicate_identical_node_count": duplicate_identical_nodes,
        "duplicate_identical_way_count": duplicate_identical_ways,
        "bounds": [min(lats), min(lons), max(lats), max(lons)],
    }
    return {
        "nodes": nodes,
        "ways": list(ways.values()),
        "document": document,
        "malformed_way_record_count": malformed_way_records,
    }, metadata


def classify_way(way: dict[str, Any]) -> dict[str, str]:
    """Apply policy v1.0.0; ambiguous/restricted tags are never admitted."""
    tags = way.get("tags", {})
    if not isinstance(tags, dict):
        return {"status": "flagged", "reason": "malformed_tags"}

    highway = tags.get("highway")
    if not isinstance(highway, str) or ";" in highway or not highway.strip():
        return {"status": "flagged", "reason": "missing_or_ambiguous_highway"}
    highway = highway.strip().lower()
    if highway not in ALLOWED_HIGHWAYS:
        return {"status": "excluded", "reason": f"highway_not_in_candidate_allowlist:{highway}"}

    if "service" in tags:
        return {"status": "excluded", "reason": "service_tag_requires_review"}
    if "busway" in tags or "bus_stop" in tags:
        return {"status": "flagged", "reason": "bus_facility_tag_requires_review"}
    if any(f"{key}:conditional" in tags for key in (*ACCESS_KEYS, "oneway", *VEHICLE_LIMIT_KEYS)) or any(
        key.startswith("oneway:") for key in tags
    ):
        return {"status": "flagged", "reason": "conditional_access_or_direction_unsupported"}

    for key in ACCESS_KEYS:
        if key not in tags:
            continue
        raw_value = tags[key]
        if not isinstance(raw_value, str):
            return {"status": "flagged", "reason": f"malformed_access_value:{key}"}
        value = raw_value.strip().lower()
        if value in PERMITTED_ACCESS_VALUES:
            continue
        if value in DENIED_ACCESS_VALUES:
            if any(
                isinstance(tags.get(other), str)
                and tags[other].strip().lower() in PERMITTED_ACCESS_VALUES
                for other in ACCESS_KEYS
                if other != key
            ):
                return {"status": "flagged", "reason": "conflicting_access_tags"}
            return {"status": "excluded", "reason": f"explicit_access_denial:{key}={value}"}
        if value in CONTEXT_ACCESS_VALUES:
            return {"status": "flagged", "reason": f"context_restricted_access:{key}={value}"}
        return {"status": "flagged", "reason": f"unsupported_access_value:{key}={value}"}

    # A value such as motorcar=yes does not establish access for a collection
    # vehicle. Any physical limit is excluded until a vehicle profile exists.
    for key in VEHICLE_LIMIT_KEYS:
        if key in tags:
            return {"status": "flagged", "reason": f"vehicle_profile_required:{key}"}

    oneway_raw = tags.get("oneway")
    junction = tags.get("junction")
    roundabout = isinstance(junction, str) and junction.strip().lower() == "roundabout"
    if oneway_raw is None or (isinstance(oneway_raw, str) and not oneway_raw.strip()):
        return {
            "status": "included",
            "reason": "roundabout_default_forward" if roundabout else "no_oneway_tag_bidirectional",
            "direction": "forward" if roundabout else "both",
        }
    if not isinstance(oneway_raw, str):
        return {"status": "flagged", "reason": "malformed_oneway_value"}
    oneway = oneway_raw.strip().lower()
    if oneway in {"yes", "true", "1"}:
        return {"status": "included", "reason": "explicit_oneway_forward", "direction": "forward"}
    if oneway in {"-1"}:
        return {"status": "included", "reason": "explicit_oneway_reverse", "direction": "reverse"}
    if oneway in {"no", "false", "0"}:
        if roundabout:
            return {"status": "flagged", "reason": "roundabout_contradicted_by_two_way_tag"}
        return {"status": "included", "reason": "explicit_two_way", "direction": "both"}
    return {"status": "flagged", "reason": f"unsupported_oneway_value:{oneway}"}


def _well_formed_sequence(way: dict[str, Any], source: dict[str, Any]) -> tuple[list[int] | None, str | None]:
    refs = way.get("nodes")
    if not isinstance(refs, list) or len(refs) < 2:
        return None, "invalid_way_sequence_length"
    try:
        ids = [int(ref) for ref in refs]
    except (TypeError, ValueError):
        return None, "invalid_way_node_reference"
    if any(a == b for a, b in zip(ids, ids[1:])):
        return None, "repeated_consecutive_node_reference"
    if any(node_id not in source["nodes"] for node_id in ids):
        return None, "missing_or_invalid_node_reference"
    return ids, None


def _crop_counts(source: dict[str, Any], radius: float, center: tuple[float, float]) -> tuple[int, int]:
    lat0, lon0 = center
    area_nodes = {
        node_id
        for node_id, (lat, lon) in source["nodes"].items()
        if abs(lat - lat0) <= radius and abs(lon - lon0) <= radius
    }
    referenced: set[int] = set()
    directed_edges = 0
    for way in source["ways"]:
        policy = classify_way(way)
        if policy["status"] != "included":
            continue
        refs, error = _well_formed_sequence(way, source)
        if error or not set(refs).issubset(area_nodes):
            continue
        referenced.update(refs)
        segment_count = len(refs) - 1
        directed_edges += segment_count * (2 if policy["direction"] == "both" else 1)
    return len(referenced), directed_edges


def select_cap_fitted_area(source: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Find the largest centered square satisfying the fixed graph caps."""
    min_lat, min_lon, max_lat, max_lon = metadata["bounds"]
    center = ((min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0)
    max_radius = min((max_lat - min_lat) / 2.0, (max_lon - min_lon) / 2.0)
    high = max_radius
    low = 0.0
    best: tuple[float, int, int] | None = None
    for _ in range(42):
        radius = (low + high) / 2.0
        node_count, edge_count = _crop_counts(source, radius, center)
        if node_count <= MAX_NODES and edge_count <= MAX_DIRECTED_EDGES:
            best = (radius, node_count, edge_count)
            low = radius
        else:
            high = radius
    if best is not None:
        # Binary search approaches a monotone inclusion boundary from below.
        # Expand by sub-micrometer angular tolerance only when the exact counts
        # still satisfy the hard caps, so boundary source nodes are not lost to
        # floating-point rounding.
        candidate_radius = min(max_radius, low + 1e-12)
        candidate_nodes, candidate_edges = _crop_counts(source, candidate_radius, center)
        if candidate_nodes <= MAX_NODES and candidate_edges <= MAX_DIRECTED_EDGES:
            best = (candidate_radius, candidate_nodes, candidate_edges)
    if best is None or best[1] < 4 or best[2] < 4:
        raise ConversionError("No centered source area satisfies the caps and minimum fixture coverage")
    radius, node_count, edge_count = best
    bounds = [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]
    return {
        "selection_method": "largest_centered_square_fitting_fixed_caps_v1",
        "center_lat_lon": list(center),
        "radius_degrees": radius,
        "bounds": bounds,
        "expected_node_count": node_count,
        "expected_directed_edge_count": edge_count,
        "max_nodes": MAX_NODES,
        "max_directed_edges": MAX_DIRECTED_EDGES,
    }


def _edge_records(source: dict[str, Any], area: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[int, tuple[float, float]], dict[str, Any]]:
    min_lat, min_lon, max_lat, max_lon = area["bounds"]
    area_node_ids = {
        node_id
        for node_id, (lat, lon) in source["nodes"].items()
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon
    }
    edge_records: list[dict[str, Any]] = []
    graph_node_ids: set[int] = set()
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()

    for way in source["ways"]:
        result = classify_way(way)
        refs, error = _well_formed_sequence(way, source)
        if error:
            counts["malformed"] += 1
            reasons[error] += 1
            continue
        if result["status"] == "flagged":
            counts["flagged"] += 1
            reasons[result["reason"]] += 1
            continue
        if result["status"] == "excluded":
            counts["excluded"] += 1
            reasons[result["reason"]] += 1
            continue
        if not set(refs).issubset(area_node_ids):
            counts["excluded"] += 1
            reasons["outside_experiment_bounds"] += 1
            continue

        tags = way.get("tags", {})
        if not isinstance(tags, dict):
            counts["flagged"] += 1
            reasons["malformed_tags"] += 1
            continue
        try:
            way_id = int(way["id"])
        except (KeyError, TypeError, ValueError):
            counts["malformed"] += 1
            reasons["invalid_way_id"] += 1
            continue
        graph_node_ids.update(refs)
        source_tags = json.dumps(tags, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for segment_index, (u, v) in enumerate(zip(refs, refs[1:])):
            directed = [(u, v, "forward")]
            if result["direction"] == "both":
                directed.append((v, u, "reverse"))
            elif result["direction"] == "reverse":
                directed = [(v, u, "reverse")]
            for start, end, direction in directed:
                lat1, lon1 = source["nodes"][start]
                lat2, lon2 = source["nodes"][end]
                edge_records.append(
                    {
                        "u": start,
                        "v": end,
                        "way_id": way_id,
                        "segment_index": segment_index,
                        "direction": direction,
                        "policy_reason": result["reason"],
                        "highway": tags["highway"],
                        "oneway": tags.get("oneway", "implicit_roundabout" if result["reason"] == "roundabout_default_forward" else "no"),
                        "source_tags": source_tags,
                        "geometry": LineString([(lon1, lat1), (lon2, lat2)]),
                    }
                )
        counts["included"] += 1
        reasons[result["reason"]] += 1

    if source.get("malformed_way_record_count", 0):
        reasons["invalid_way_record_id"] += source["malformed_way_record_count"]
    selected_nodes = {node_id: source["nodes"][node_id] for node_id in graph_node_ids}
    diagnostics = {
        "way_counts": {
            "included": counts.get("included", 0),
            "excluded": counts.get("excluded", 0),
            "flagged": counts.get("flagged", 0),
            "malformed": counts.get("malformed", 0) + source.get("malformed_way_record_count", 0),
        },
        "reason_counts": dict(sorted(reasons.items())),
        "candidate_node_count": len(selected_nodes),
        "directed_edge_count": len(edge_records),
    }
    return edge_records, selected_nodes, diagnostics


def _independent_haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Spherical reference calculation, independent of converter's Geod call."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    return 6_371_008.8 * (2.0 * math.asin(min(1.0, math.sqrt(hav))))


def _reference_classify_way(way: dict[str, Any]) -> dict[str, str]:
    """Independent executable statement of policy v1.0.0 for source validation.

    Keep this policy oracle separate from ``classify_way`` and its constants.
    The duplication is intentional: the source-selection check must be able to
    detect drift in the converter's classifier.
    """
    allowed_highways = {
        "living_street", "primary", "primary_link", "residential", "secondary",
        "secondary_link", "tertiary", "tertiary_link", "trunk", "trunk_link", "unclassified",
    }
    access_keys = ("access", "vehicle", "motor_vehicle", "hgv", "goods")
    permitted = {"yes", "permissive"}
    denied = {"no", "private", "military", "emergency"}
    contextual = {"destination", "customers", "delivery", "agricultural", "forestry", "permit", "discouraged"}
    vehicle_limits = ("maxweight", "maxheight", "maxwidth", "maxlength", "maxaxleload")

    tags = way.get("tags", {})
    if not isinstance(tags, dict):
        return {"status": "flagged", "reason": "malformed_tags"}
    highway = tags.get("highway")
    if not isinstance(highway, str) or ";" in highway or not highway.strip():
        return {"status": "flagged", "reason": "missing_or_ambiguous_highway"}
    highway = highway.strip().lower()
    if highway not in allowed_highways:
        return {"status": "excluded", "reason": f"highway_not_in_candidate_allowlist:{highway}"}
    if "service" in tags:
        return {"status": "excluded", "reason": "service_tag_requires_review"}
    if "busway" in tags or "bus_stop" in tags:
        return {"status": "flagged", "reason": "bus_facility_tag_requires_review"}
    if any(f"{key}:conditional" in tags for key in (*access_keys, "oneway", *vehicle_limits)) or any(
        key.startswith("oneway:") for key in tags
    ):
        return {"status": "flagged", "reason": "conditional_access_or_direction_unsupported"}

    for key in access_keys:
        if key not in tags:
            continue
        raw_value = tags[key]
        if not isinstance(raw_value, str):
            return {"status": "flagged", "reason": f"malformed_access_value:{key}"}
        value = raw_value.strip().lower()
        if value in permitted:
            continue
        if value in denied:
            conflicts_with_permission = any(
                other != key
                and isinstance(tags.get(other), str)
                and tags[other].strip().lower() in permitted
                for other in access_keys
            )
            if conflicts_with_permission:
                return {"status": "flagged", "reason": "conflicting_access_tags"}
            return {"status": "excluded", "reason": f"explicit_access_denial:{key}={value}"}
        if value in contextual:
            return {"status": "flagged", "reason": f"context_restricted_access:{key}={value}"}
        return {"status": "flagged", "reason": f"unsupported_access_value:{key}={value}"}

    for key in vehicle_limits:
        if key in tags:
            return {"status": "flagged", "reason": f"vehicle_profile_required:{key}"}

    raw_oneway = tags.get("oneway")
    roundabout = isinstance(tags.get("junction"), str) and tags["junction"].strip().lower() == "roundabout"
    if raw_oneway is None or (isinstance(raw_oneway, str) and not raw_oneway.strip()):
        return {
            "status": "included",
            "reason": "roundabout_default_forward" if roundabout else "no_oneway_tag_bidirectional",
            "direction": "forward" if roundabout else "both",
        }
    if not isinstance(raw_oneway, str):
        return {"status": "flagged", "reason": "malformed_oneway_value"}
    oneway = raw_oneway.strip().lower()
    if oneway in {"yes", "true", "1"}:
        return {"status": "included", "reason": "explicit_oneway_forward", "direction": "forward"}
    if oneway == "-1":
        return {"status": "included", "reason": "explicit_oneway_reverse", "direction": "reverse"}
    if oneway in {"no", "false", "0"}:
        if roundabout:
            return {"status": "flagged", "reason": "roundabout_contradicted_by_two_way_tag"}
        return {"status": "included", "reason": "explicit_two_way", "direction": "both"}
    return {"status": "flagged", "reason": f"unsupported_oneway_value:{oneway}"}


def _reference_source_records(source: dict[str, Any]) -> tuple[dict[int, tuple[float, float]], list[dict[str, Any]]]:
    """Read validated node/way records from the original decoded JSON document."""
    document = source.get("document")
    if not isinstance(document, dict) or not isinstance(document.get("elements"), list):
        raise ConversionError("Independent source selection requires the original elements array")

    nodes: dict[int, tuple[float, float]] = {}
    invalid_nodes: set[int] = set()
    ways: dict[int, dict[str, Any]] = {}
    for element in document["elements"]:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "node":
            try:
                node_id = int(element["id"])
                lat, lon = element["lat"], element["lon"]
            except (KeyError, TypeError, ValueError):
                continue
            valid = (
                isinstance(lat, (int, float)) and not isinstance(lat, bool)
                and isinstance(lon, (int, float)) and not isinstance(lon, bool)
                and math.isfinite(float(lat)) and math.isfinite(float(lon))
                and -90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0
            )
            if not valid:
                invalid_nodes.add(node_id)
                nodes.pop(node_id, None)
                continue
            if node_id in invalid_nodes:
                continue
            coordinates = (float(lat), float(lon))
            previous = nodes.get(node_id)
            if previous is not None and previous != coordinates:
                raise ConversionError(f"Independent source selection found conflicting node ID {node_id}")
            nodes[node_id] = coordinates
        elif element.get("type") == "way":
            try:
                way_id = int(element["id"])
            except (KeyError, TypeError, ValueError):
                continue
            previous = ways.get(way_id)
            if previous is not None and previous != element:
                raise ConversionError(f"Independent source selection found conflicting way ID {way_id}")
            ways[way_id] = element
    return nodes, list(ways.values())


def _reference_way_sequence(way: dict[str, Any], nodes: dict[int, tuple[float, float]]) -> list[int] | None:
    """Validate a source way sequence without using converter sequence helpers."""
    raw_refs = way.get("nodes")
    if not isinstance(raw_refs, list) or len(raw_refs) < 2:
        return None
    try:
        refs = [int(ref) for ref in raw_refs]
    except (TypeError, ValueError):
        return None
    if any(first == second for first, second in zip(refs, refs[1:])):
        return None
    if any(node_id not in nodes for node_id in refs):
        return None
    return refs


def _reference_source_selection(source: dict[str, Any], area: dict[str, Any]) -> dict[str, Any]:
    """Select source ways/nodes and build a reference graph without graph output."""
    source_nodes, ways = _reference_source_records(source)
    min_lat, min_lon, max_lat, max_lon = area["bounds"]
    selected_nodes: dict[int, tuple[float, float]] = {}
    selected_way_ids: set[int] = set()
    expected_edges: Counter[tuple[int, int, int, int, str]] = Counter()

    for way in ways:
        decision = _reference_classify_way(way)
        if decision["status"] != "included":
            continue
        refs = _reference_way_sequence(way, source_nodes)
        if refs is None:
            continue
        if any(
            not (min_lat <= source_nodes[node_id][0] <= max_lat and min_lon <= source_nodes[node_id][1] <= max_lon)
            for node_id in refs
        ):
            continue
        try:
            way_id = int(way["id"])
        except (KeyError, TypeError, ValueError):
            continue
        selected_way_ids.add(way_id)
        for node_id in refs:
            selected_nodes[node_id] = source_nodes[node_id]
        for segment_index, (first, second) in enumerate(zip(refs, refs[1:])):
            directed = [(first, second, "forward")]
            if decision["direction"] == "both":
                directed.append((second, first, "reverse"))
            elif decision["direction"] == "reverse":
                directed = [(second, first, "reverse")]
            for u, v, direction in directed:
                expected_edges[(u, v, way_id, segment_index, direction)] += 1

    reference = nx.MultiDiGraph(crs="EPSG:4326")
    for node_id, (lat, lon) in selected_nodes.items():
        reference.add_node(node_id, x=lon, y=lat)
    for (u, v, way_id, segment_index, direction), count in expected_edges.items():
        length = _independent_haversine_m(selected_nodes[u], selected_nodes[v])
        for _ in range(count):
            reference.add_edge(
                u, v, way_id=way_id, segment_index=segment_index, direction=direction, length=length
            )

    return {
        "source_nodes": selected_nodes,
        "way_ids": selected_way_ids,
        "edge_inventory": expected_edges,
        "graph": reference,
    }


def _edge_inventory_record(token: tuple[int, int, int, int, str], count: int) -> dict[str, Any]:
    u, v, way_id, segment_index, direction = token
    return {
        "from_node_id": u,
        "to_node_id": v,
        "source_way_id": way_id,
        "segment_index": segment_index,
        "direction": direction,
        "count": count,
    }


def _compare_source_inventory(selection: dict[str, Any], graph: nx.MultiDiGraph) -> dict[str, Any]:
    """Compare complete source-derived node and directed-edge inventories."""
    expected_nodes = set(selection["source_nodes"])
    actual_nodes = {int(node_id) for node_id in graph.nodes}
    expected_edges: Counter[tuple[int, int, int, int, str]] = selection["edge_inventory"]
    actual_edges: Counter[tuple[int, int, int, int, str]] = Counter()
    malformed_actual_edges = 0
    for u, v, _, data in graph.edges(keys=True, data=True):
        try:
            token = (int(u), int(v), int(data["way_id"]), int(data["segment_index"]), str(data["direction"]))
        except (KeyError, TypeError, ValueError):
            malformed_actual_edges += 1
            continue
        actual_edges[token] += 1

    missing_nodes = sorted(expected_nodes - actual_nodes)
    unexpected_nodes = sorted(actual_nodes - expected_nodes)
    missing_edges = expected_edges - actual_edges
    unexpected_edges = actual_edges - expected_edges
    ordered_missing = sorted(missing_edges.items())
    ordered_unexpected = sorted(unexpected_edges.items())
    matched_edges = sum(expected_edges.values()) - sum(missing_edges.values())
    return {
        "selection_method": "independent_raw_source_records_with_manifest_area_bounds",
        "policy_method": "separate_v1.0.0_policy_oracle_with_explicit_fixture_expectations",
        "valid": not (missing_nodes or unexpected_nodes or missing_edges or unexpected_edges or malformed_actual_edges),
        "expected_node_count": len(expected_nodes),
        "actual_node_count": len(actual_nodes),
        "matched_node_count": len(expected_nodes & actual_nodes),
        "missing_node_ids": missing_nodes[:50],
        "missing_node_count": len(missing_nodes),
        "unexpected_node_ids": unexpected_nodes[:50],
        "unexpected_node_count": len(unexpected_nodes),
        "expected_selected_way_count": len(selection["way_ids"]),
        "actual_selected_way_count": len({token[2] for token in actual_edges}),
        "expected_directed_edge_record_count": sum(expected_edges.values()),
        "actual_directed_edge_record_count": sum(actual_edges.values()) + malformed_actual_edges,
        "matched_directed_edge_record_count": matched_edges,
        "missing_directed_edge_records": [
            _edge_inventory_record(token, count) for token, count in ordered_missing[:50]
        ],
        "missing_directed_edge_record_count": sum(missing_edges.values()),
        "unexpected_directed_edge_records": [
            _edge_inventory_record(token, count) for token, count in ordered_unexpected[:50]
        ],
        "unexpected_directed_edge_record_count": sum(unexpected_edges.values()) + malformed_actual_edges,
        "malformed_actual_edge_record_count": malformed_actual_edges,
        "diagnostic_record_limit": 50,
    }


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9)
    if hasattr(value, "item"):
        value = value.item()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _canonical_int(value: Any) -> int:
    return int(value)


def _canonical_tags(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def canonical_graph_checksum(graph: nx.MultiDiGraph) -> str:
    nodes = [
        [int(node), _canonical_scalar(data.get("x")), _canonical_scalar(data.get("y"))]
        for node, data in sorted(graph.nodes(data=True), key=lambda item: int(item[0]))
    ]
    edges = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        edges.append(
            [
                int(u),
                int(v),
                int(key),
                _canonical_int(data.get("way_id")),
                _canonical_int(data.get("segment_index")),
                _canonical_scalar(data.get("direction")),
                _canonical_scalar(data.get("length")),
                _canonical_tags(data.get("source_tags")),
                _canonical_scalar(data.get("policy_reason")),
            ]
        )
    edges.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[4]))
    encoded = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def convert_parsed_source(
    source: dict[str, Any],
    metadata: dict[str, Any],
    *,
    expected_metadata: dict[str, Any] | None = None,
    parse_seconds: float = 0.0,
) -> dict[str, Any]:
    """Filter, build, and index parsed elements; performs no I/O or acquisition."""
    if expected_metadata:
        for key in ("overpass_timestamp", "element_counts", "bounds"):
            if metadata.get(key) != expected_metadata.get(key):
                raise ConversionError(f"Source metadata mismatch for {key}")

    filter_started = time.perf_counter()
    area = select_cap_fitted_area(source, metadata)
    edges, selected_nodes, diagnostics = _edge_records(source, area)
    filtering_seconds = time.perf_counter() - filter_started
    if len(selected_nodes) > MAX_NODES or len(edges) > MAX_DIRECTED_EDGES:
        raise ConversionError("Selected graph exceeds an approved fixed cap")
    if len(edges) != area["expected_directed_edge_count"] or len(selected_nodes) != area["expected_node_count"]:
        raise ConversionError("Cap-fitted area counts changed during conversion")

    length_started = time.perf_counter()
    for edge in edges:
        u_lat, u_lon = source["nodes"][edge["u"]]
        v_lat, v_lon = source["nodes"][edge["v"]]
        length = GEOD.inv(u_lon, u_lat, v_lon, v_lat)[2]
        if not math.isfinite(length) or length < 0.0:
            raise ConversionError(f"Invalid edge length for way {edge['way_id']} segment {edge['segment_index']}")
        edge["length"] = round(float(length), 6)
    length_seconds = time.perf_counter() - length_started

    # Deterministic per-(u,v) keys preserve parallel source ways and segments.
    edges.sort(key=lambda e: (e["u"], e["v"], e["way_id"], e["segment_index"], e["direction"]))
    key_counters: Counter[tuple[int, int]] = Counter()
    edge_rows = []
    edge_index = []
    for edge in edges:
        pair = (edge["u"], edge["v"])
        key = key_counters[pair]
        key_counters[pair] += 1
        edge_index.append((edge["u"], edge["v"], key))
        edge_rows.append(
            {
                "length": edge["length"],
                "way_id": edge["way_id"],
                "segment_index": edge["segment_index"],
                "direction": edge["direction"],
                "policy_reason": edge["policy_reason"],
                "highway": str(edge["highway"]),
                # OSMnx's GraphML loader treats an attribute literally named
                # `oneway` as boolean; retain the source tag without that
                # type collision.
                "oneway_tag": str(edge["oneway"]),
                "source_tags": edge["source_tags"],
                "geometry": edge["geometry"],
            }
        )
    edge_multi_index = __import__("pandas").MultiIndex.from_tuples(edge_index, names=["u", "v", "key"])

    graph_started = time.perf_counter()
    node_items = sorted(selected_nodes.items())
    nodes_gdf = gpd.GeoDataFrame(
        {
            "x": [selected_nodes[node_id][1] for node_id, _ in node_items],
            "y": [selected_nodes[node_id][0] for node_id, _ in node_items],
            "geometry": [Point(selected_nodes[node_id][1], selected_nodes[node_id][0]) for node_id, _ in node_items],
        },
        index=__import__("pandas").Index([node_id for node_id, _ in node_items], name="osmid"),
        crs="EPSG:4326",
    )
    edges_gdf = gpd.GeoDataFrame(edge_rows, index=edge_multi_index, geometry="geometry", crs="EPSG:4326")
    graph = ox.graph_from_gdfs(
        nodes_gdf,
        edges_gdf,
        graph_attrs={
            "crs": "EPSG:4326",
            "source": "OpenStreetMap Overpass JSON local snapshot",
            "conversion_policy": f"{POLICY_NAME}@{POLICY_VERSION}",
            "artifact_status": "conversion-mechanics demonstration; not a verified drive graph",
        },
    )
    graph_seconds = time.perf_counter() - graph_started

    spatial_started = time.perf_counter()
    spatial_index = nodes_gdf.sindex
    spatial_index_size = len(spatial_index)
    spatial_seconds = time.perf_counter() - spatial_started

    graph.graph["source_snapshot"] = str(metadata.get("overpass_timestamp"))
    graph.graph["length_method"] = LENGTH_METHOD
    graph.graph["bounds"] = json.dumps(area["bounds"], separators=(",", ":"))
    graph.graph["source_query_filter_provenance"] = "unknown; not present in source response"
    diagnostics.update(
        {
            "node_count": graph.number_of_nodes(),
            "directed_edge_count": graph.number_of_edges(),
            "parallel_directed_pair_count": sum(
                1
                for count in Counter((u, v) for u, v, _ in graph.edges(keys=True)).values()
                if count > 1
            ),
            "spatial_index_size": spatial_index_size,
            "area": area,
        }
    )
    diagnostics["strongly_connected_component_count"] = nx.number_strongly_connected_components(graph)
    largest_scc = max((len(component) for component in nx.strongly_connected_components(graph)), default=0)
    diagnostics["largest_strong_component_nodes"] = largest_scc
    diagnostics["largest_strong_component_fraction"] = largest_scc / graph.number_of_nodes() if graph.number_of_nodes() else 0.0
    return {
        "graph": graph,
        "nodes_gdf": nodes_gdf,
        "edges_gdf": edges_gdf,
        "source": source,
        "source_metadata": metadata,
        "diagnostics": diagnostics,
        "timings": {
            "parse_seconds": parse_seconds,
            "filtering_seconds": filtering_seconds,
            "length_calculation_seconds": length_seconds,
            "graph_construction_seconds": graph_seconds,
            "spatial_index_seconds": spatial_seconds,
        },
    }


def convert_document(document: dict[str, Any], expected_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Filter, build, and index an in-memory source document; performs no I/O."""
    parse_started = time.perf_counter()
    source, metadata = parse_source_bytes(json.dumps(document, separators=(",", ":")).encode("utf-8"))
    parse_seconds = time.perf_counter() - parse_started
    return convert_parsed_source(
        source,
        metadata,
        expected_metadata=expected_metadata,
        parse_seconds=parse_seconds,
    )


def validate_against_source(
    result: dict[str, Any],
    *,
    max_reference_nodes: int = 32,
    reference_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate source selection fully, then compare distances on a small pair sample."""
    if max_reference_nodes < 2:
        raise ValueError("max_reference_nodes must be at least 2")
    graph = result["graph"]
    source = result["source"]
    area = result["diagnostics"]["area"]
    selection = reference_selection or _reference_source_selection(source, area)
    inventory = _compare_source_inventory(selection, graph)
    if not inventory["valid"]:
        raise ConversionError(
            "Independent source selection inventory mismatch: "
            + json.dumps(inventory, sort_keys=True, separators=(",", ":"))
        )

    maximum_length_error_m = 0.0
    length_tolerance_m = 0.05
    for u, v, _, data in graph.edges(keys=True, data=True):
        source_u, source_v = int(u), int(v)
        a = selection["source_nodes"][source_u]
        b = selection["source_nodes"][source_v]
        independent_length = _independent_haversine_m(a, b)
        error = abs(float(data["length"]) - independent_length)
        maximum_length_error_m = max(maximum_length_error_m, error)
        if error > max(length_tolerance_m, independent_length * 0.006):
            way_id = int(data["way_id"])
            segment_index = int(data["segment_index"])
            raise ConversionError(
                f"Independent length validation failed for way {way_id} segment {segment_index} "
                f"arc {source_u}->{source_v}: {error:.6f} m"
            )

    reference = selection["graph"]
    all_node_ids = sorted(int(node_id) for node_id in graph.nodes)
    if len(all_node_ids) > max_reference_nodes:
        # Deterministic smoke sample: evenly spaced indices from sorted node IDs.
        indices = sorted(
            {
                round(i * (len(all_node_ids) - 1) / (max_reference_nodes - 1))
                for i in range(max_reference_nodes)
            }
        )
        sampled_node_ids = [all_node_ids[index] for index in indices]
        sample_type = "deterministic_smoke_sample"
    else:
        sampled_node_ids = all_node_ids
        sample_type = "all_nodes_exhaustive_for_this_graph"

    pairs_checked = 0
    unreachable_pairs_checked = 0
    maximum_path_error_m = 0.0
    path_tolerance_m = 1.0
    for source_id in sampled_node_ids:
        for target_id in sampled_node_ids:
            pairs_checked += 1
            try:
                expected_distance = nx.shortest_path_length(reference, source_id, target_id, weight="length")
            except nx.NetworkXNoPath:
                expected_distance = None
            try:
                observed_distance = nx.shortest_path_length(graph, source_id, target_id, weight="length")
            except nx.NetworkXNoPath:
                observed_distance = None
            if expected_distance is None or observed_distance is None:
                if expected_distance != observed_distance:
                    raise ConversionError(f"Directed reachability mismatch for {source_id}->{target_id}")
                unreachable_pairs_checked += 1
                continue
            error = abs(float(observed_distance) - float(expected_distance))
            maximum_path_error_m = max(maximum_path_error_m, error)
            if error > max(path_tolerance_m, float(expected_distance) * 0.006):
                raise ConversionError(f"Directed shortest-path mismatch for {source_id}->{target_id}: {error:.6f} m")

    possible_ordered_pairs = len(all_node_ids) ** 2
    return {
        "selection_inventory": inventory,
        "distance_reference_definition": (
            "Shortest-path distance in meters on an independently selected source-topology graph; "
            "reference edge weights are spherical Haversine distances between source node coordinates. "
            "Endpoints are graph node IDs; synthetic-coordinate snap offsets are excluded. "
            "This is not direct coordinate-to-coordinate Haversine distance."
        ),
        "length_calculation": "WGS84 ellipsoidal versus independent spherical Haversine",
        "length_tolerance": "max(0.05 m, 0.6% of independent source-segment distance)",
        "maximum_length_error_m": maximum_length_error_m,
        "edge_records_matched": inventory["matched_directed_edge_record_count"],
        "directed_pairs_checked": pairs_checked,
        "unreachable_directed_pairs_checked": unreachable_pairs_checked,
        "path_tolerance": "max(1 m, 0.6% of independent source-graph path distance)",
        "maximum_path_error_m": maximum_path_error_m,
        "path_comparison_sample": {
            "classification": sample_type,
            "construction": (
                "sort all converted graph node IDs; choose evenly spaced rounded index positions; "
                "evaluate the Cartesian product of sampled IDs, including self-pairs"
            ),
            "sampled_node_count": len(sampled_node_ids),
            "sampled_node_ids": sampled_node_ids,
            "sampled_ordered_pair_count": pairs_checked,
            "possible_ordered_pair_count": possible_ordered_pairs,
            "ordered_pair_fraction": pairs_checked / possible_ordered_pairs if possible_ordered_pairs else None,
            "limitations": (
                "The 32-node deterministic smoke sample is not random or spatially stratified and does not "
                "establish graph-wide path accuracy or coverage."
            ) if sample_type == "deterministic_smoke_sample" else None,
        },
    }


def generate_synthetic_points(graph: nx.MultiDiGraph, area: dict[str, Any], count: int = 4) -> list[dict[str, Any]]:
    """Create deterministic grid points, snapping only within the crop interior."""
    min_lat, min_lon, max_lat, max_lon = area["bounds"]
    center_lat = (min_lat + max_lat) / 2.0
    margin_lat = MAX_SNAP_DISTANCE_M / 110_574.0
    margin_lon = MAX_SNAP_DISTANCE_M / (111_320.0 * max(0.01, math.cos(math.radians(center_lat))))
    inner = [min_lat + margin_lat, min_lon + margin_lon, max_lat - margin_lat, max_lon - margin_lon]
    if inner[0] >= inner[2] or inner[1] >= inner[3]:
        raise ConversionError("Experiment crop has no interior after the 30 m boundary margin")

    largest_scc = max(nx.strongly_connected_components(graph), key=len)
    scc_nodes = set(largest_scc)
    coordinates: list[tuple[float, float]] = []
    grid_size = 61
    for row in range(grid_size):
        lat = inner[0] + (inner[2] - inner[0]) * row / (grid_size - 1)
        for column in range(grid_size):
            lon = inner[1] + (inner[3] - inner[1]) * column / (grid_size - 1)
            coordinates.append((lat, lon))
    snapped_nodes, _ = ox.nearest_nodes(
        graph,
        X=[lon for _, lon in coordinates],
        Y=[lat for lat, _ in coordinates],
        return_dist=True,
    )
    points = []
    used_nodes: set[int] = set()
    for (lat, lon), node_value in zip(coordinates, snapped_nodes, strict=True):
        node_id = int(node_value)
        if node_id not in scc_nodes or node_id in used_nodes:
            continue
        node_lat, node_lon = graph.nodes[node_id]["y"], graph.nodes[node_id]["x"]
        snap_distance = abs(GEOD.inv(lon, lat, node_lon, node_lat)[2])
        if snap_distance > MAX_SNAP_DISTANCE_M:
            continue
        points.append(
            {
                "id": f"SYN_POINT_{len(points)}",
                "lat": lat,
                "lon": lon,
                "node_id": node_id,
                "snap_distance_m": snap_distance,
                "inside_fixture_interior": True,
            }
        )
        used_nodes.add(node_id)
        if len(points) == count:
            break
    if len(points) < count:
        raise ConversionError(
            f"Only {len(points)} distinct synthetic points passed the interior, SCC, and 30 m snap checks"
        )
    return points


def validate_six_tours(
    graph: nx.MultiDiGraph,
    points: list[dict[str, Any]],
    reference_graph: nx.MultiDiGraph | None = None,
) -> dict[str, Any]:
    """Compare all six closed tours using converted and independent matrices."""
    if len(points) != 4:
        raise ConversionError("Tour validation requires one depot and exactly three synthetic stops")
    depot = points[0]["node_id"]
    stops = [point["node_id"] for point in points[1:]]
    matrix: dict[tuple[int, int], float | None] = {}
    reference_matrix: dict[tuple[int, int], float | None] = {}
    for u in [depot, *stops]:
        for v in [depot, *stops]:
            try:
                matrix[(u, v)] = float(nx.shortest_path_length(graph, u, v, weight="length"))
            except nx.NetworkXNoPath:
                matrix[(u, v)] = None
            if reference_graph is not None:
                try:
                    reference_matrix[(u, v)] = float(
                        nx.shortest_path_length(reference_graph, u, v, weight="length")
                    )
                except nx.NetworkXNoPath:
                    reference_matrix[(u, v)] = None
    tours = []
    import itertools

    for order in itertools.permutations(stops):
        nodes = (depot, *order, depot)
        legs = [matrix.get((u, v)) for u, v in zip(nodes, nodes[1:])]
        cost = None if any(leg is None for leg in legs) else sum(float(leg) for leg in legs)
        tour = {"order": list(order), "closed_tour_cost_m": cost}
        if reference_graph is not None:
            reference_legs = [reference_matrix.get((u, v)) for u, v in zip(nodes, nodes[1:])]
            reference_cost = (
                None
                if any(leg is None for leg in reference_legs)
                else sum(float(leg) for leg in reference_legs)
            )
            if (cost is None) != (reference_cost is None):
                raise ConversionError("Tour reachability differs from the independent source graph")
            if cost is not None and reference_cost is not None:
                error = abs(cost - reference_cost)
                if error > max(1.0, reference_cost * 0.006):
                    raise ConversionError(f"Independent tour-cost validation failed: {error:.6f} m")
            tour["independent_haversine_cost_m"] = reference_cost
        tours.append(tour)
    if len(tours) != 6 or any(
        tour["closed_tour_cost_m"] is None
        or (reference_graph is not None and tour.get("independent_haversine_cost_m") is None)
        for tour in tours
    ):
        raise ConversionError("Not all six directed closed tours are available for the synthetic sample")
    return {
        "depot_node": depot,
        "stop_nodes": stops,
        "permutation_count": len(tours),
        "tours": tours,
        "minimum_cost_m": min(tour["closed_tour_cost_m"] for tour in tours),
        "minimum_independent_cost_m": (
            min(tour["independent_haversine_cost_m"] for tour in tours)
            if reference_graph is not None
            else None
        ),
        "cost_definition": "directed depot->stop->...->depot, including outbound and return legs",
        "independent_haversine_cost_definition": (
            "Shortest-path cost on the independently selected source-topology graph, using spherical "
            "Haversine segment weights between source graph nodes; endpoints are snapped graph node IDs; "
            "synthetic-coordinate snap offsets are excluded. This is not direct coordinate-to-coordinate "
            "Haversine distance."
        ),
    }


def verify_snap_point(point: dict[str, Any], area: dict[str, Any], maximum_m: float = MAX_SNAP_DISTANCE_M) -> bool:
    min_lat, min_lon, max_lat, max_lon = area["bounds"]
    center_lat = (min_lat + max_lat) / 2.0
    margin_lat = maximum_m / 110_574.0
    margin_lon = maximum_m / (111_320.0 * max(0.01, math.cos(math.radians(center_lat))))
    return (
        min_lat + margin_lat <= point["lat"] <= max_lat - margin_lat
        and min_lon + margin_lon <= point["lon"] <= max_lon - margin_lon
        and point["snap_distance_m"] <= maximum_m
    )


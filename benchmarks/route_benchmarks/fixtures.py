"""Versioned synthetic fixtures. No live road data or external services are used."""

from copy import deepcopy

from . import FIXTURE_SCHEMA_VERSION


DEPOT = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}


def _synthetic_coordinates(index: int, salt: int) -> tuple[float, float]:
    """Return stable coordinates near Pune for production coordinate-based routes."""
    lat = 18.48 + ((index * 17 + salt * 3) % 31) / 1000
    lng = 73.80 + ((index * 23 + salt * 5) % 41) / 1000
    return round(lat, 6), round(lng, 6)


def directed_matrix(node_ids: list[str], salt: int) -> dict[str, dict[str, float]]:
    """Build a complete synthetic directed matrix from the versioned integer formula."""
    matrix: dict[str, dict[str, float]] = {}
    for start_index, start_id in enumerate(node_ids):
        row = {}
        for end_index, end_id in enumerate(node_ids):
            if start_index == end_index:
                row[end_id] = 0.0
            else:
                tenths = (
                    start_index * 37
                    + end_index * 61
                    + start_index * end_index * 13
                    + salt * 23
                ) % 190
                row[end_id] = round(2.0 + tenths / 10.0, 1)
        matrix[start_id] = row
    return matrix


def make_fixed_instance(stop_count: int) -> dict:
    """Create one fixed-stop sequencing instance; supports edge cases and 5/8/10."""
    if stop_count not in (0, 1, 5, 8, 10):
        raise ValueError("fixed-stop fixture size must be 0, 1, 5, 8, or 10")

    fixture_id = f"fixed_{stop_count:02d}_{FIXTURE_SCHEMA_VERSION}"
    bins = []
    for index in range(stop_count):
        lat, lng = _synthetic_coordinates(index + 1, stop_count + 1)
        fill = 55 + ((index * 17 + stop_count * 11) % 46)
        bins.append({"id": f"F{stop_count:02d}_{index + 1:02d}", "lat": lat, "lng": lng, "fill": fill})

    node_ids = [DEPOT["id"]] + [bin_item["id"] for bin_item in bins]
    return {
        "fixture_id": fixture_id,
        "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
        "track": "fixed_stop_sequencing",
        "matrix_provenance": "synthetic integer formula in fixtures.py; no external artifact",
        "matrix_salt": stop_count + 1,
        "depot": deepcopy(DEPOT),
        "bins": bins,
        "distance_matrix_km": directed_matrix(node_ids, stop_count + 1),
        "threshold": 0,
        "max_stops": max(1, stop_count),
    }


def _fleet_instance(
    fixture_id: str,
    fills: list[int],
    loads: list[int],
    threshold: int,
    max_stops: int,
    vehicle_capacities: list[int],
    driver_count: int,
    salt: int,
) -> dict:
    bins = []
    for index, (fill, load_units) in enumerate(zip(fills, loads), start=1):
        lat, lng = _synthetic_coordinates(index + 20, salt)
        bins.append(
            {
                "id": f"{fixture_id.upper()}_{index:02d}",
                "lat": lat,
                "lng": lng,
                "fill": fill,
                "load_units": load_units,
            }
        )

    vehicle_ids = [f"VEHICLE_{index:02d}" for index in range(1, len(vehicle_capacities) + 1)]
    driver_ids = [f"DRIVER_{index:02d}" for index in range(1, driver_count + 1)]
    node_ids = [DEPOT["id"]] + [bin_item["id"] for bin_item in bins]
    return {
        "fixture_id": fixture_id,
        "fixture_schema_version": FIXTURE_SCHEMA_VERSION,
        "track": "selection_and_fleet_feasibility",
        "matrix_provenance": "synthetic integer formula in fixtures.py; no external artifact",
        "matrix_salt": salt,
        "depot": deepcopy(DEPOT),
        "bins": bins,
        "distance_matrix_km": directed_matrix(node_ids, salt),
        "threshold": threshold,
        "max_stops": max_stops,
        "vehicles": [
            {"id": vehicle_id, "capacity_units": capacity}
            for vehicle_id, capacity in zip(vehicle_ids, vehicle_capacities)
        ],
        "drivers": driver_ids,
        "load_assumption": (
            "load_units and vehicle capacity_units are explicit synthetic fixture values; "
            "load is not inferred from fill percentage and is not production behavior"
        ),
    }


def fixed_instances() -> list[dict]:
    return [make_fixed_instance(size) for size in (0, 1, 5, 8, 10)]


def fleet_instances() -> list[dict]:
    return [
        _fleet_instance(
            "fleet_08_2v2d_v1",
            fills=[95, 90, 85, 80, 75, 70, 65, 65, 64],
            loads=[5, 6, 4, 5, 3, 5, 4, 4, 2],
            threshold=65,
            max_stops=4,
            vehicle_capacities=[12, 12],
            driver_count=2,
            salt=108,
        ),
        _fleet_instance(
            "fleet_10_3v2d_v1",
            fills=[98, 95, 90, 85, 80, 75, 70, 65, 60, 60, 59],
            loads=[5, 4, 6, 5, 3, 5, 4, 4, 3, 2, 2],
            threshold=60,
            max_stops=5,
            vehicle_capacities=[10, 12, 16],
            driver_count=2,
            salt=210,
        ),
        _fleet_instance(
            "fleet_12_3v3d_v1",
            fills=[100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 55, 55],
            loads=[4, 4, 5, 3, 5, 4, 3, 4, 3, 2, 2, 1],
            threshold=55,
            max_stops=4,
            vehicle_capacities=[10, 11, 12],
            driver_count=3,
            salt=312,
        ),
    ]

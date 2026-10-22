"""Shared paths and coordinates for the Phase 3B synthetic graph harness."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "phase3b_synthetic_drive_graph.graphml"
MANIFEST_PATH = ROOT / "tests" / "fixtures" / "phase3b_road_fixture_manifest.json"

DEPOT = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}
STOPS = [
    {"id": "SYN_BIN_A", "lat": 18.5204, "lng": 73.8577, "fill": 80},
    {"id": "SYN_BIN_B", "lat": 18.5204, "lng": 73.8587, "fill": 85},
    {"id": "SYN_BIN_C", "lat": 18.5204, "lng": 73.8597, "fill": 90},
]

NODE_BY_LOCATION_ID = {
    "DEPOT": 1,
    "SYN_BIN_A": 2,
    "SYN_BIN_B": 3,
    "SYN_BIN_C": 4,
}

#!/usr/bin/env python3
"""
IoT simulator for GreenRoute.
Generates fake truck telemetry and bin sensor data and sends it to the Flask API.
"""

import json
import random
import sys
import time
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:5000"

TRUCK_IDS = ["truck_1", "truck_2", "truck_3"]


def fetch_bins() -> list[dict]:
    req = Request(f"{BASE_URL}/api/bins")
    with urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body).get("bins", [])


def post_json(path: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    url = f"{BASE_URL}{path}"
    req = Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404 and path == "/api/telemetry":
            alt_url = f"{BASE_URL}/api/telemetry/"
            alt_req = Request(alt_url, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST")
            with urlopen(alt_req, timeout=10) as alt_resp:
                return json.loads(alt_resp.read().decode("utf-8"))
        raise


def simulate_truck_telemetry(truck_id: str, start_lat: float, start_lng: float):
    gps_latitude = round(start_lat + random.uniform(-0.01, 0.01), 6)
    gps_longitude = round(start_lng + random.uniform(-0.01, 0.01), 6)
    payload = {
        "truck_id": truck_id,
        "gps_latitude": gps_latitude,
        "gps_longitude": gps_longitude,
        "gps_accuracy": round(random.uniform(2.0, 10.0), 1),
        "speed_kmh": round(random.uniform(0, 40), 1),
        "heading_degrees": round(random.uniform(0, 360), 1),
        "harsh_braking_events": random.randint(0, 2),
        "harsh_acceleration_events": random.randint(0, 2),
        "idle_duration_min": random.randint(0, 5),
        "fuel_consumption_liters_per_km": round(random.uniform(0.15, 0.5), 3),
        "engine_temp_celsius": round(random.uniform(70.0, 95.0), 1),
        "engine_diagnostics": {"codes": []},
        "driver_id": f"driver_{random.randint(1, 5)}",
        "recorded_at": datetime.now().isoformat(),
    }
    return payload


def simulate_bin_sensor(bin_info: dict):
    fill = max(0, min(100, bin_info["fill"] + random.randint(-5, 8)))
    status = 0
    if fill >= 95:
        status = 1
    if random.random() < 0.03:
        status = 2
    payload = {
        "truck_id": random.choice(TRUCK_IDS),
        "bin_id": bin_info["id"],
        "gps_latitude": bin_info["lat"],
        "gps_longitude": bin_info["lng"],
        "fill_level": fill,
        "status": status,
        "recorded_at": datetime.now().isoformat(),
    }
    return payload


def run_simulation(iterations: int = 20, delay_seconds: float = 1.0):
    print("🚚 Starting IoT simulator")
    bins = fetch_bins()
    if not bins:
        print("❌ No bins found. Make sure the Flask server is running.")
        return
    print(f"Loaded {len(bins)} bins from API")

    for i in range(iterations):
        print(f"\nIteration {i + 1}/{iterations}")

        truck_index = i % len(TRUCK_IDS)
        truck = TRUCK_IDS[truck_index]
        reference_bin = random.choice(bins)
        telemetry = simulate_truck_telemetry(truck, reference_bin["lat"], reference_bin["lng"])

        try:
            response = post_json("/api/telemetry", telemetry)
            print(f"Sent truck telemetry for {truck}: {response}")
        except (HTTPError, URLError) as exc:
            print(f"Failed to send truck telemetry: {exc}")
            break

        if random.random() < 0.75:
            bin_payload = simulate_bin_sensor(reference_bin)
            try:
                response = post_json("/api/telemetry", bin_payload)
                print(f"Sent bin sensor update for {bin_payload['bin_id']}: {response}")
            except (HTTPError, URLError) as exc:
                print(f"Failed to send bin telemetry: {exc}")
                break

        time.sleep(delay_seconds)

    print("\n✅ IoT simulation complete")


if __name__ == "__main__":
    count = 20
    delay = 1.0
    if len(sys.argv) > 1:
        count = int(sys.argv[1])
    if len(sys.argv) > 2:
        delay = float(sys.argv[2])

    run_simulation(count, delay)

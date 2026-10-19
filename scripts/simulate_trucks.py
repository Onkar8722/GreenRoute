#!/usr/bin/env python3
"""
Truck Route Simulation for GreenRoute.

This script simulates trucks driving along optimized routes in real-time.
It creates route assignments, then moves virtual trucks stop-by-stop,
inserting tracking records into the database so the Fleet and Tracking
dashboards show live vehicle movement.

Usage:
    python scripts/simulate_trucks.py           # Default: 3 trucks
    python scripts/simulate_trucks.py 5         # 5 trucks
"""

import json
import math
import random
import sys
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:5000"


def api_get(path):
    """GET request to Flask API."""
    req = Request(f"{BASE_URL}{path}")
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_post(path, payload):
    """POST request to Flask API."""
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def interpolate(lat1, lng1, lat2, lng2, steps=5):
    """Generate intermediate GPS points between two locations."""
    points = []
    for i in range(1, steps + 1):
        t = i / steps
        lat = lat1 + (lat2 - lat1) * t + random.uniform(-0.0003, 0.0003)
        lng = lng1 + (lng2 - lng1) * t + random.uniform(-0.0003, 0.0003)
        points.append((round(lat, 6), round(lng, 6)))
    return points


def run_simulation(num_trucks=3):
    print("=" * 60)
    print("🚛 GreenRoute Truck Route Simulator")
    print("=" * 60)

    # ── Step 1: Get all bins and find critical ones ──
    print("\n📡 Fetching bin data...")
    bins_data = api_get("/api/bins")
    all_bins = bins_data.get("bins", [])
    print(f"   Found {len(all_bins)} bins total")

    # ── Step 2: Run GA route optimization ──
    print("\n🧬 Running Genetic Algorithm route optimization...")
    print("   (This may take 10-30 seconds on first run)")
    try:
        ga_result = api_post("/api/route/optimize-ga", {
            "threshold": 70,
            "max_stops": 15,
            "population_size": 80,
            "generations": 30,
        })
        if "error" in ga_result:
            print(f"   ⚠️  GA returned error: {ga_result['error']}")
            print("   Falling back to simple route selection...")
            # Fallback: just pick top-fill bins
            critical = sorted(all_bins, key=lambda b: -b["fill"])[:15]
            route_bin_ids = [b["id"] for b in critical]
        else:
            route_bin_ids = ga_result.get("route", [])
            print(f"   ✅ Optimized route: {len(route_bin_ids)} stops")
            print(f"   📏 Distance: {ga_result.get('distance_km', 0):.1f} km")
    except Exception as e:
        print(f"   ❌ GA failed: {e}")
        critical = sorted(all_bins, key=lambda b: -b["fill"])[:15]
        route_bin_ids = [b["id"] for b in critical]

    # Build a lookup map for bin coordinates
    bin_map = {b["id"]: b for b in all_bins}

    # ── Step 3: Split route into truck sub-routes ──
    stops_per_truck = max(1, len(route_bin_ids) // num_trucks)
    truck_routes = []
    for t in range(num_trucks):
        start = t * stops_per_truck
        end = start + stops_per_truck if t < num_trucks - 1 else len(route_bin_ids)
        sub_route = route_bin_ids[start:end]
        if sub_route:
            truck_routes.append(sub_route)

    print(f"\n🚚 Dispatching {len(truck_routes)} trucks:")
    for i, route in enumerate(truck_routes):
        print(f"   Truck {i+1}: {len(route)} stops")

    # ── Step 4: Create DB records for drivers, vehicles, assignments ──
    print("\n📋 Creating route assignments in database...")
    import psycopg2
    from dotenv import load_dotenv
    import os

    load_dotenv("config/.env")
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", 5432),
        dbname=os.getenv("DB_NAME", "greenroute_db"),
        user=os.getenv("DB_USER", "greenroute_user"),
        password=os.getenv("DB_PASSWORD", "greenroute_pass"),
    )
    cursor = conn.cursor()

    driver_names = ["Rajesh Kumar", "Amit Patil", "Suresh Sharma", "Vikram Singh", "Pradeep Joshi"]
    vehicle_plates = ["MH-12-AB-1001", "MH-12-CD-2002", "MH-12-EF-3003", "MH-12-GH-4004", "MH-12-IJ-5005"]
    vehicle_types = ["Truck", "Truck", "Van", "Truck", "Van"]

    assignment_ids = []

    for t_idx, route in enumerate(truck_routes):
        driver_id = f"DRV_{t_idx+1:03d}"
        driver_name = driver_names[t_idx % len(driver_names)]
        vehicle_id = f"VEH_{t_idx+1:03d}"
        plate = vehicle_plates[t_idx % len(vehicle_plates)]
        vtype = vehicle_types[t_idx % len(vehicle_types)]

        # Upsert driver
        cursor.execute("""
            INSERT INTO drivers (driver_id, name) VALUES (%s, %s)
            ON CONFLICT (driver_id) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """, (driver_id, driver_name))
        db_driver_id = cursor.fetchone()[0]

        # Upsert vehicle
        cursor.execute("""
            INSERT INTO vehicles (vehicle_id, license_plate, vehicle_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (vehicle_id) DO UPDATE SET license_plate = EXCLUDED.license_plate, vehicle_type = EXCLUDED.vehicle_type
            RETURNING id
        """, (vehicle_id, plate, vtype))
        db_vehicle_id = cursor.fetchone()[0]

        # Link vehicle to driver
        cursor.execute("UPDATE drivers SET vehicle_id = %s WHERE id = %s", (db_vehicle_id, db_driver_id))

        # Calculate route distance
        total_dist = 0
        for i in range(len(route) - 1):
            if route[i] in bin_map and route[i+1] in bin_map:
                b1, b2 = bin_map[route[i]], bin_map[route[i+1]]
                total_dist += math.sqrt((b1["lat"] - b2["lat"])**2 + (b1["lng"] - b2["lng"])**2) * 111
        duration = int((total_dist / 30) * 60 + len(route) * 2)

        # Create assignment
        import uuid
        asn_id = f"ASN_{uuid.uuid4().hex[:8]}"
        cursor.execute("""
            INSERT INTO route_assignments
            (assignment_id, driver_id, vehicle_id, route_data,
             total_distance_km, estimated_duration_minutes, status, scheduled_time, start_time)
            VALUES (%s, %s, %s, %s, %s, %s, 'in_progress', NOW(), NOW())
        """, (asn_id, db_driver_id, db_vehicle_id, json.dumps(route),
              round(total_dist, 2), duration))

        assignment_ids.append((asn_id, db_vehicle_id, route))
        print(f"   ✅ Assignment {asn_id}: {driver_name} → {plate} ({len(route)} stops, {total_dist:.1f} km)")

    conn.commit()

    # ── Step 5: Simulate truck movement along routes ──
    print("\n" + "=" * 60)
    print("🔴 LIVE SIMULATION STARTED")
    print("   Open http://localhost:5000/tracking to watch trucks move!")
    print("   Press Ctrl+C to stop")
    print("=" * 60)

    # Depot location
    depot_lat, depot_lng = 18.5204, 73.8567
    step_delay = 1.5  # seconds between position updates

    try:
        # Each truck moves independently through its route
        truck_positions = {}  # asn_id -> current index
        for asn_id, _, _ in assignment_ids:
            truck_positions[asn_id] = 0

        max_steps = max(len(r) for _, _, r in assignment_ids) * 6  # ~6 GPS points per stop

        for step in range(max_steps + 10):
            any_active = False

            for asn_id, db_veh_id, route in assignment_ids:
                pos_idx = truck_positions[asn_id]
                stop_idx = pos_idx // 6  # 6 intermediate points per stop
                sub_step = pos_idx % 6

                if stop_idx >= len(route):
                    continue  # This truck finished

                any_active = True
                current_bin_id = route[stop_idx]

                # Determine start and end coordinates for interpolation
                if stop_idx == 0 and sub_step == 0:
                    start_lat, start_lng = depot_lat, depot_lng
                elif sub_step == 0:
                    prev_bin = bin_map.get(route[stop_idx - 1], {"lat": depot_lat, "lng": depot_lng})
                    start_lat, start_lng = prev_bin["lat"], prev_bin["lng"]
                else:
                    # Continue from last known position
                    start_lat = depot_lat
                    start_lng = depot_lng

                target_bin = bin_map.get(current_bin_id)
                if not target_bin:
                    truck_positions[asn_id] += 1
                    continue

                # Interpolate position
                t = (sub_step + 1) / 6
                if sub_step == 0:
                    prev_bin = bin_map.get(route[stop_idx - 1] if stop_idx > 0 else route[0],
                                           {"lat": depot_lat, "lng": depot_lng})
                    start_lat, start_lng = prev_bin["lat"], prev_bin["lng"]

                lat = start_lat + (target_bin["lat"] - start_lat) * t + random.uniform(-0.0002, 0.0002)
                lng = start_lng + (target_bin["lng"] - start_lng) * t + random.uniform(-0.0002, 0.0002)

                # Insert tracking record
                cursor.execute("""
                    INSERT INTO vehicle_tracking
                    (assignment_id, vehicle_id, latitude, longitude,
                     current_bin_id, current_stop_number, bins_collected, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """, (asn_id, db_veh_id, round(lat, 6), round(lng, 6),
                      current_bin_id if sub_step >= 4 else None,
                      stop_idx + 1 if sub_step >= 5 else stop_idx,
                      stop_idx if sub_step >= 5 else max(0, stop_idx - 1)))
                conn.commit()

                if sub_step == 5:
                    print(f"   🗑️  Truck {asn_id[-8:]}: Collected bin {current_bin_id} "
                          f"(stop {stop_idx+1}/{len(route)})")

                truck_positions[asn_id] += 1

            if not any_active:
                print("\n✅ All trucks have completed their routes!")
                break

            time.sleep(step_delay)

    except KeyboardInterrupt:
        print("\n\n⏹️  Simulation stopped by user")

    finally:
        # Mark assignments as completed
        for asn_id, _, _ in assignment_ids:
            cursor.execute("""
                UPDATE route_assignments SET status = 'completed', completed_time = NOW()
                WHERE assignment_id = %s
            """, (asn_id,))
        conn.commit()
        cursor.close()
        conn.close()
        print("📊 Route assignments marked as completed")
        print("=" * 60)


if __name__ == "__main__":
    trucks = 3
    if len(sys.argv) > 1:
        trucks = int(sys.argv[1])
    run_simulation(trucks)

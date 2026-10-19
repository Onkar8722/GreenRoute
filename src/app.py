"""
GreenRoute — Flask API for smart waste monitoring and route optimization.
Enhanced with Pydantic validation, Redis caching, MQTT integration, and GA optimization.
"""
from __future__ import annotations

import csv
import math
import os
import sys
import random
import threading
import time
import logging
from copy import deepcopy
from io import StringIO
from datetime import datetime, timedelta

# Add parent directory to Python path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, redirect, render_template, request, url_for
import psycopg2
from psycopg2.extras import Json, RealDictCursor

# Import new modules
from .models import (
    BinResponse, AlertResponse, RouteResponse, 
    VehicleTelemetryCreate, StatsResponse, HealthCheckResponse
)
from .cache import get_cache, CACHE_TTL
from .db import DatabaseManager, init_database
from .mqtt_listener import MQTTListener
from config.config import Config, get_config
from .genetic_algorithm import GeneticAlgorithm
from .forecasting import WasteForecaster
from .fleet_management_api import FleetManagementAPI
from .collection_window_predictor import CollectionWindowPredictor
from .osmnx_routing import calculate_detailed_path, load_city_graph, build_distance_matrix
from sklearn.cluster import KMeans
import numpy as np

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Setup paths - app.py is in src/, project root is parent, data is in frontend/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "pune_smart_bin_dataset.csv")

DEPOT = {"lat": 18.5204, "lng": 73.8567, "id": "DEPOT"}

# Load configuration
try:
    config = get_config(os.getenv('FLASK_ENV', 'development'))
    DB_CONFIG = config.DB_CONFIG
except Exception as e:
    logger.warning(f"Config load error (using defaults): {e}")
    # Use default config
    DB_CONFIG = {
        'host': 'localhost',
        'port': 5432,
        'database': 'greenroute_db',
        'user': 'greenroute_user',
        'password': 'greenroute_pass'
    }
    config = None

# Setup template and static folders - now in the project root
template_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
static_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# Initialize Flask
app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
if config:
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['DEBUG'] = config.DEBUG
else:
    app.config['SECRET_KEY'] = 'dev-key-change-in-production'
    app.config['DEBUG'] = False

# Initialize services
cache = get_cache()
db_manager = DatabaseManager(DB_CONFIG)
mqtt_listener = None  # Will be initialized on startup
forecaster = None  # Will be initialized on startup

SIMULATION_LOCK = threading.Lock()
SIMULATION_STOP_EVENT = threading.Event()
SIMULATION_THREAD = None
SIMULATION_STATE = {
    "active": False,
    "iterations": 0,
    "total_telemetry_sent": 0,
    "total_bin_updates_sent": 0,
    "start_time": None,
    "last_update_at": None,
    "last_event": None,
    "error_count": 0,
    "status_message": "idle",
    "events": [],
}

def get_db_connection():
    """Create database connection."""
    return psycopg2.connect(**DB_CONFIG)

def load_bins_from_db() -> list[dict]:
    """Load bins data from PostgreSQL database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT id, latitude, longitude, fill_level as fill, status,
                   battery_level, signal_strength, bin_type, capacity_liters,
                   last_updated
            FROM bins
            WHERE status >= 0
            ORDER BY id
        """)

        bins = []
        for row in cursor.fetchall():
            bins.append({
                "id": row['id'],
                "lat": float(row['latitude']),
                "lng": float(row['longitude']),
                "fill": int(row['fill']),
                "status": int(row['status']),
                "battery_level": row['battery_level'],
                "signal_strength": row['signal_strength'],
                "bin_type": row['bin_type'],
                "capacity_liters": row['capacity_liters'],
                "timestamp": row['last_updated'].isoformat() if row['last_updated'] else None
            })

        cursor.close()
        conn.close()
        return bins

    except Exception as e:
        print(f"Database error: {e}")
        # Fallback to CSV if database fails
        return load_bins_from_csv()


def record_fill_history(bin_id: str, fill_level: int, recorded_at: datetime):
    """Append fill history record for a bin."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO fill_history (bin_id, fill_level, recorded_at) VALUES (%s, %s, %s)",
            (bin_id, fill_level, recorded_at),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"Failed to insert fill history: {exc}")
    finally:
        cursor.close()
        conn.close()


def create_alert(bin_id: str, alert_type: str, severity: str, message: str):
    """Create an alert entry for a bin."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO alerts (bin_id, alert_type, severity, message) VALUES (%s, %s, %s, %s)",
            (bin_id, alert_type, severity, message),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"Failed to create alert: {exc}")
    finally:
        cursor.close()
        conn.close()


def save_vehicle_telemetry(payload: dict):
    """Save incoming vehicle telemetry to the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO vehicle_telemetry (
                truck_id, gps_latitude, gps_longitude, gps_accuracy,
                speed_kmh, heading_degrees, harsh_braking_events,
                harsh_acceleration_events, idle_duration_min,
                fuel_consumption_liters_per_km, engine_temp_celsius,
                engine_diagnostics, driver_id, recorded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload.get('truck_id'),
                payload.get('gps_latitude'),
                payload.get('gps_longitude'),
                payload.get('gps_accuracy'),
                payload.get('speed_kmh'),
                payload.get('heading_degrees'),
                payload.get('harsh_braking_events', 0),
                payload.get('harsh_acceleration_events', 0),
                payload.get('idle_duration_min', 0),
                payload.get('fuel_consumption_liters_per_km'),
                payload.get('engine_temp_celsius'),
                psycopg2.extras.Json(payload.get('engine_diagnostics', {})),
                payload.get('driver_id'),
                payload.get('recorded_at', datetime.now()),
            ),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"Failed to save telemetry: {exc}")
        raise
    finally:
        cursor.close()
        conn.close()


def update_bin_from_sensor(bin_id: str, fill_level: int, status: int | None = None):
    """Update bin current status from sensor telemetry."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if status is None:
            cursor.execute(
                "UPDATE bins SET fill_level = %s, last_updated = %s WHERE id = %s",
                (fill_level, datetime.now(), bin_id),
            )
        else:
            cursor.execute(
                "UPDATE bins SET fill_level = %s, status = %s, last_updated = %s WHERE id = %s",
                (fill_level, status, datetime.now(), bin_id),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"Failed to update bin: {exc}")
    finally:
        cursor.close()
        conn.close()


def update_bin_from_sensor_and_alert(bin_id: str, fill_level: int, status: int | None = None):
    """Update bin status and create an alert if needed."""
    update_bin_from_sensor(bin_id, fill_level, status)
    record_fill_history(bin_id, fill_level, datetime.now())
    if fill_level >= 90:
        create_alert(
            bin_id,
            "overflow",
            "high",
            f"Bin fill level is critically high at {fill_level}%",
        )
    if status == 2:
        create_alert(
            bin_id,
            "malfunction",
            "critical",
            "Bin sensor reports malfunction",
        )

def load_bins_from_csv() -> list[dict]:
    """Fallback: Load bins from CSV file."""
    out: list[dict] = []
    if os.path.isfile(DATA_PATH):
        with open(DATA_PATH, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                if not row.get("bin_id"):
                    continue
                out.append({
                    "id": row["bin_id"].strip(),
                    "lat": float(row["latitude"]),
                    "lng": float(row["longitude"]),
                    "fill": int(float(row["fill_level"])),
                    "status": 1 if int(float(row["fill_level"])) >= 80 else 0,
                    "timestamp": row.get("timestamp", "").strip(),
                })
    return out


def ensure_data_file():
    """Ensure the data directory exists and warn if the CSV dataset is missing."""
    data_dir = os.path.dirname(DATA_PATH)
    os.makedirs(data_dir, exist_ok=True)
    if not os.path.isfile(DATA_PATH):
        print(f"⚠️  Dataset missing at {DATA_PATH}. CSV fallback may not be available.")


def get_bins_data() -> list[dict]:
    """Get bins data, preferring database over CSV."""
    return load_bins_from_db()


def _record_simulation_event(message: str):
    with SIMULATION_LOCK:
        SIMULATION_STATE["events"].append(f"{datetime.now().isoformat()} - {message}")
        SIMULATION_STATE["events"] = SIMULATION_STATE["events"][-50:]


def _set_simulation_status(**kwargs):
    with SIMULATION_LOCK:
        for key, value in kwargs.items():
            if key in SIMULATION_STATE:
                SIMULATION_STATE[key] = value


def get_simulation_status() -> dict:
    with SIMULATION_LOCK:
        status = {k: v for k, v in SIMULATION_STATE.items() if k != "events"}
        status["events"] = list(SIMULATION_STATE["events"][-20:])
        return status


def process_simulation_payload(payload: dict, event_type: str):
    try:
        save_vehicle_telemetry(payload)
        if payload.get("bin_id") and payload.get("fill_level") is not None:
            update_bin_from_sensor_and_alert(
                payload["bin_id"],
                int(payload["fill_level"]),
                int(payload.get("status", 0)) if payload.get("status") is not None else None,
            )
        with SIMULATION_LOCK:
            if event_type == "truck":
                SIMULATION_STATE["total_telemetry_sent"] += 1
            if event_type == "bin":
                SIMULATION_STATE["total_bin_updates_sent"] += 1
            SIMULATION_STATE["iterations"] += 1
            SIMULATION_STATE["last_update_at"] = datetime.now().isoformat()
            SIMULATION_STATE["last_event"] = event_type
            SIMULATION_STATE["status_message"] = f"Processed {event_type} payload"
        _record_simulation_event(f"{event_type.title()} payload processed for {payload.get('truck_id', 'unknown')} {payload.get('bin_id', '')}".strip())
    except Exception as exc:
        with SIMULATION_LOCK:
            SIMULATION_STATE["error_count"] += 1
            SIMULATION_STATE["status_message"] = f"Simulation error: {exc}"
        _record_simulation_event(f"ERROR: {exc}")


def _build_truck_payload(truck_id: str, reference_bin: dict) -> dict:
    return {
        "truck_id": truck_id,
        "gps_latitude": round(reference_bin["lat"] + random.uniform(-0.01, 0.01), 6),
        "gps_longitude": round(reference_bin["lng"] + random.uniform(-0.01, 0.01), 6),
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
        "bin_id": reference_bin["id"],
        "recorded_at": datetime.now().isoformat(),
    }


def _build_bin_payload(reference_bin: dict) -> dict:
    fill = max(0, min(100, reference_bin["fill"] + random.randint(-5, 8)))
    status = 0
    if fill >= 95:
        status = 1
    if random.random() < 0.03:
        status = 2
    return {
        "truck_id": f"truck_{random.randint(1, 3)}",
        "bin_id": reference_bin["id"],
        "gps_latitude": reference_bin["lat"],
        "gps_longitude": reference_bin["lng"],
        "fill_level": fill,
        "status": status,
        "recorded_at": datetime.now().isoformat(),
    }


def simulation_worker(interval_seconds: float, max_iterations: int):
    _set_simulation_status(active=True, status_message="running", start_time=datetime.now().isoformat())
    _record_simulation_event("Simulation started")
    iteration = 0
    try:
        while not SIMULATION_STOP_EVENT.is_set() and (max_iterations <= 0 or iteration < max_iterations):
            bins = get_bins_data()
            if not bins:
                _record_simulation_event("No bins available for simulation")
                break
            reference_bin = random.choice(bins)
            truck_id = f"truck_{(iteration % 3) + 1}"
            truck_payload = _build_truck_payload(truck_id, reference_bin)
            process_simulation_payload(truck_payload, "truck")
            if random.random() < 0.75:
                bin_payload = _build_bin_payload(reference_bin)
                process_simulation_payload(bin_payload, "bin")
            iteration += 1
            time.sleep(interval_seconds)
    finally:
        _set_simulation_status(active=False, status_message="stopped")
        _record_simulation_event("Simulation stopped")


def start_simulation(interval_seconds: float = 1.0, max_iterations: int = 0) -> bool:
    global SIMULATION_THREAD
    with SIMULATION_LOCK:
        if SIMULATION_STATE["active"]:
            return False
        SIMULATION_STOP_EVENT.clear()
        SIMULATION_THREAD = threading.Thread(
            target=simulation_worker,
            args=(interval_seconds, max_iterations),
            daemon=True,
        )
        SIMULATION_THREAD.start()
        return True


def stop_simulation() -> bool:
    if not SIMULATION_STATE["active"]:
        return False
    SIMULATION_STOP_EVENT.set()
    return True


def load_fill_history(bin_id: str, limit: int = 30) -> list[dict]:
    """Load recent historical fill levels for a bin."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT recorded_at, fill_level
            FROM fill_history
            WHERE bin_id = %s
            ORDER BY recorded_at ASC
            LIMIT %s
            """,
            (bin_id, limit),
        )
        history = cursor.fetchall()
        cursor.close()
        conn.close()
        return [
            {"recorded_at": row["recorded_at"], "fill_level": int(row["fill_level"])}
            for row in history
        ]
    except Exception:
        return []


def predict_bin_fill(bin_id: str, lookahead_hours: int = 24) -> int:
    """Predict next fill level using a simple linear trend over fill history."""
    history = load_fill_history(bin_id, limit=24)
    if not history:
        return 0
    if len(history) < 2:
        return history[-1]["fill_level"]

    start = history[0]["recorded_at"]
    x = [(row["recorded_at"] - start).total_seconds() / 3600.0 for row in history]
    y = [row["fill_level"] for row in history]
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    numerator = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    denominator = sum((xi - x_mean) ** 2 for xi in x)
    slope = numerator / denominator if denominator else 0.0
    prediction = history[-1]["fill_level"] + slope * lookahead_hours
    return max(0, min(100, round(prediction)))


def adaptive_demand_filter(bins: list[dict], threshold: int, lookahead_hours: int = 24) -> list[dict]:
    """Filter bins using predicted future fill to avoid unnecessary collections."""
    filtered = []
    for b in bins:
        predicted = predict_bin_fill(b["id"], lookahead_hours)
        if b["fill"] >= threshold or predicted >= threshold:
            filtered.append(b)
    return filtered


def route_fitness(route: list[dict], mode: str = "multiobjective") -> float:
    if not route:
        return 0.0
    distance = route_distance_km(route)
    fill_sum = sum(b["fill"] for b in route)
    if mode == "distance":
        return distance
    if mode == "multiobjective":
        return distance - (fill_sum * 0.035)
    return distance - (fill_sum * 0.02)


def mutate_route(route: list[dict]) -> list[dict]:
    if len(route) < 2:
        return route
    mutated = route.copy()
    i, j = random.sample(range(len(route)), 2)
    mutated[i], mutated[j] = mutated[j], mutated[i]
    return mutated


def crossover_route(parent1: list[dict], parent2: list[dict]) -> list[dict]:
    if len(parent1) < 2 or len(parent2) < 2:
        return parent1.copy()
    size = len(parent1)
    start = random.randrange(size)
    end = random.randrange(start + 1, size + 1)
    child = [None] * size
    segment = parent1[start:end]
    child[start:end] = segment
    taken_ids = {b["id"] for b in segment}
    fill = [b for b in parent2 if b["id"] not in taken_ids]
    for idx in range(size):
        if child[idx] is None:
            child[idx] = fill.pop(0)
    return child


def genetic_optimize_route(
    bins: list[dict],
    max_stops: int,
    population_size: int = 16,
    generations: int = 50,
    mode: str = "multiobjective",
) -> list[dict]:
    if not bins:
        return []
    candidates = sorted(bins, key=lambda b: -b["fill"])[:max_stops]
    if len(candidates) <= 2:
        return candidates
    population = []
    base_route = nearest_neighbor(candidates, max_stops)
    population.append(base_route)
    for _ in range(population_size - 1):
        population.append(mutate_route(base_route))

    best = base_route
    best_score = route_fitness(best, mode)
    for _ in range(generations):
        scored = [(route_fitness(route, mode), route) for route in population]
        scored.sort(key=lambda x: x[0])
        population = [route for _, route in scored[: max(2, population_size // 4)]]
        if scored[0][0] < best_score:
            best_score, best = scored[0][0], scored[0][1]
        while len(population) < population_size:
            parents = random.sample(population, 2)
            child = crossover_route(parents[0], parents[1])
            if random.random() < 0.45:
                child = mutate_route(child)
            population.append(child)
    return best


def partition_bins_for_trucks(bins: list[dict], num_trucks: int, max_stops: int) -> list[list[dict]]:
    capacity = num_trucks * max_stops
    selected = sorted(bins, key=lambda b: (-b["fill"], b["lat"], b["lng"]))[:capacity]
    groups = [[] for _ in range(num_trucks)]
    for index, bin_item in enumerate(selected):
        groups[index % num_trucks].append(bin_item)
    return groups


def compute_routes_ga(
    eligible: list[dict],
    num_trucks: int,
    max_stops: int,
    mode: str = "multiobjective",
) -> list[list[dict]]:
    groups = partition_bins_for_trucks(eligible, num_trucks, max_stops)
    return [genetic_optimize_route(group, max_stops, mode=mode) for group in groups]


def compute_routes_logic(
    eligible: list[dict], algo: str, num_trucks: int, max_stops: int, threshold: int, adaptive: bool = False
) -> list[list[dict]]:
    if adaptive:
        eligible = adaptive_demand_filter(eligible, threshold)
    if not eligible:
        return []
    if algo == "nearest":
        if num_trucks == 1:
            return [nearest_neighbor(eligible, max_stops)]
        remaining = list(sorted(eligible, key=lambda x: (-x["fill"], x["lat"], x["lng"])))
        routes = []
        for _ in range(num_trucks):
            if not remaining:
                routes.append([])
            else:
                route = nearest_neighbor(remaining[:max_stops], max_stops)
                routes.append(route)
                remaining = remaining[max_stops:]
        return routes
    if algo == "priority":
        if num_trucks == 1:
            return [priority_proximity(eligible, max_stops)]
        return cluster_based(eligible, num_trucks, max_stops)
    if algo == "cluster":
        return cluster_based(eligible, num_trucks, max_stops)
    if algo == "ga":
        return compute_routes_ga(eligible, num_trucks, max_stops, mode="fill")
    if algo == "multiobjective":
        return compute_routes_ga(eligible, num_trucks, max_stops, mode="multiobjective")
    if algo == "adaptive":
        return compute_routes_ga(eligible, num_trucks, max_stops, mode="multiobjective")
    return cluster_based(eligible, num_trucks, max_stops)


def skipped_bins(eligible: list[dict], routes: list[list[dict]]) -> list[dict]:
    assigned = {b["id"] for r in routes for b in r}
    return [b for b in eligible if b["id"] not in assigned]

@app.get("/api/forecast")
def api_forecast():
    """Forecast bin fill levels - FAST VERSION using simple math."""
    hours = request.args.get("hours", default=24, type=int)
    hours = max(1, min(168, hours))  # 1 to 168 hours
    
    # Try cache first
    cache_key = f"hours_{hours}"
    cached = cache.get('forecast', cache_key)
    if cached:
        logger.debug(f"Forecast retrieved from cache (hours={hours})")
        return jsonify(cached)
    
    # Use simple linear forecast (fast, doesn't need ML)
    bins = get_bins_data()
    forecasts = []
    
    # Only forecast top 20 critical bins for speed
    critical_bins = sorted([b for b in bins if b["fill"] > 30], key=lambda x: -x["fill"])[:20]
    critical_bins.extend(sorted([b for b in bins if b["fill"] <= 30], key=lambda x: -x["fill"])[:10])
    
    for b in critical_bins:
        # Simple linear progression forecast
        hourly_data = []
        current_fill = b["fill"]
        
        for h in range(0, min(hours, 24)):  # Limit forecast generation to 24h
            # Simulate fill level increase (0.5-1% per hour)
            if current_fill < 90:
                increment = 0.5 + (h % 5) * 0.1
                current_fill = min(100, current_fill + increment)
            
            hourly_data.append({
                "hour_ahead": h,
                "predicted_fill": round(current_fill, 2),
                "timestamp": (datetime.now() + timedelta(hours=h)).isoformat()
            })
        
        forecasts.append({
            "id": b["id"],
            "fill": b["fill"],
            "predicted_fill": round(current_fill, 2),
            "data": hourly_data
        })
    
    predicted_critical = sum(1 for item in forecasts if item["predicted_fill"] >= 80)
    
    result = {
        "forecast_hours": min(hours, 24),
        "predicted_critical_bins": predicted_critical,
        "forecasts": forecasts,
    }
    
    # Cache for 10 minutes
    cache.set('forecast', cache_key, result, 600)
    return jsonify(result)

def haversine_km(a: dict, b: dict) -> float:
    r_earth = 6371.0
    d_lat = math.radians(b["lat"] - a["lat"])
    d_lng = math.radians(b["lng"] - a["lng"])
    la1 = math.radians(a["lat"])
    la2 = math.radians(b["lat"])
    s = math.sin(d_lat / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(d_lng / 2) ** 2
    return r_earth * 2 * math.atan2(math.sqrt(s), math.sqrt(max(0.0, 1 - s)))


def route_distance_km(route: list[dict]) -> float:
    if not route:
        return 0.0
    total = haversine_km(DEPOT, route[0])
    for i in range(len(route) - 1):
        total += haversine_km(route[i], route[i + 1])
    total += haversine_km(route[-1], DEPOT)
    return total


def nearest_neighbor(bins: list[dict], max_stops: int) -> list[dict]:
    remaining = list(bins)
    route: list[dict] = []
    current = DEPOT
    while remaining and len(route) < max_stops:
        best_idx = -1
        best_dist = float("inf")
        for i, b in enumerate(remaining):
            d = haversine_km(current, b)
            if d < best_dist:
                best_dist = d
                best_idx = i
        if best_idx < 0:
            break
        nxt = remaining.pop(best_idx)
        route.append(nxt)
        current = nxt
    return route


def priority_proximity(bins: list[dict], max_stops: int) -> list[dict]:
    remaining = sorted(bins, key=lambda x: -x["fill"])
    route: list[dict] = []
    current = DEPOT
    max_d = 15.0
    while remaining and len(route) < max_stops:
        best_idx = -1
        best_score = float("-inf")
        for i, b in enumerate(remaining):
            d = haversine_km(current, b)
            prox = max(0.0, 1.0 - d / max_d)
            fill_score = b["fill"] / 100.0
            score = fill_score * 0.65 + prox * 0.35
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx < 0:
            break
        nxt = remaining.pop(best_idx)
        route.append(nxt)
        current = nxt
    return route


def cluster_based(bins: list[dict], num_trucks: int, max_stops: int) -> list[list[dict]]:
    if not bins:
        return [[] for _ in range(num_trucks)]
    
    if len(bins) <= num_trucks:
        # Trivial case: assign one bin per truck
        return [[b] for b in bins] + [[] for _ in range(num_trucks - len(bins))]

    # Extract coordinates for clustering
    coords = np.array([[b["lat"], b["lng"]] for b in bins])
    
    # Use Scikit-Learn KMeans strictly defining clusters by the fleet size available
    kmeans = KMeans(n_clusters=num_trucks, random_state=42, n_init=10)
    labels = kmeans.fit_predict(coords)

    # Distribute bins into their assigned clusters
    sectors = [[] for _ in range(num_trucks)]
    for idx, b in enumerate(bins):
        sectors[labels[idx]].append(b)

    # Ensure empty lists are kept if a cluster fails (rare)
    return [priority_proximity(sorted(s, key=lambda x: -x["fill"]), max_stops) for s in sectors]


@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    """Serve interactive analytics dashboard."""
    return render_template("dashboard.html")

@app.route("/dashboard2")
def dashboard_v2():
    """Serve new interactive analytics dashboard."""
    return render_template("dashboard_v2.html")

@app.route("/test")
def test_api():
    """Serve API test page."""
    return render_template("test.html")

@app.route("/debug")
def debug():
    """Serve debug dashboard."""
    return render_template("debug.html")

@app.route("/routes")
def routes():
    """Serve route optimization planner."""
    return render_template("routes.html")

@app.route("/fleet")
def fleet_management():
    """Serve fleet management dashboard."""
    return render_template("fleet.html")

@app.route("/tracking")
def tracking():
    """Serve real-time tracking dashboard."""
    return render_template("tracking.html")

@app.route("/driver")
def driver_app():
    """Serve driver mobile app."""
    return render_template("driver.html")

@app.route("/scenarios")
def scenarios():
    """Serve what-if analysis interface."""
    return render_template("scenarios.html")

@app.route("/greenroute.html")
def legacy():
    return redirect(url_for("dashboard"))


@app.get("/api/health")
def api_health():
    """Health check endpoint for monitoring."""
    try:
        # Check database
        conn = get_db_connection()
        conn.close()
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "unhealthy"
    
    # Check cache
    cache_status = "healthy" if cache.health_check() else "unhealthy"
    
    # Check MQTT
    mqtt_status = "unknown"
    if mqtt_listener:
        mqtt_status = "healthy" if mqtt_listener.connected else "disconnected"
    
    return jsonify({
        "status": "healthy" if db_status == "healthy" else "degraded",
        "database": db_status,
        "cache": cache_status,
        "mqtt": mqtt_status,
        "timestamp": datetime.now().isoformat()
    })


@app.get("/api/bins")
def api_bins():
    """Get all bins with optional filtering - CACHED for 2 minutes."""
    min_fill = request.args.get("min_fill", type=int)
    status_q = request.args.get("status", "all")

    # Generate cache key based on filters
    cache_key = f"min_fill_{min_fill}_status_{status_q}"
    cached = cache.get('bins', cache_key)
    if cached:
        logger.debug(f"Bins retrieved from cache (filters: {cache_key})")
        return jsonify(cached)
    
    bins = get_bins_data()
    rows = deepcopy(bins)
    if min_fill is not None:
        rows = [b for b in rows if b["fill"] >= min_fill]
    if status_q not in (None, "", "all"):
        try:
            st = int(status_q)
            rows = [b for b in rows if b["status"] == st]
        except ValueError:
            pass
    
    result = {"bins": rows, "count": len(rows)}
    
    # Cache for 2 minutes
    cache.set('bins', cache_key, result, CACHE_TTL['bins'])
    return jsonify(result)


@app.get("/api/alerts")
def api_alerts():
    """Bins at or above fill threshold - CACHED for 1 minute."""
    threshold = request.args.get("threshold", default=80, type=int)
    threshold = max(0, min(100, threshold))
    
    # Try cache first
    cache_key = f"threshold_{threshold}"
    cached = cache.get('alerts', cache_key)
    if cached:
        logger.debug(f"Alerts retrieved from cache (threshold={threshold})")
        return jsonify(cached)
    
    bins = get_bins_data()
    alerted = [b for b in bins if b["fill"] >= threshold]
    alerted.sort(key=lambda x: -x["fill"])
    
    # Limit to first 50 bins to avoid size issues
    limited_alerted = alerted[:50]
    result = {
        "threshold": threshold,
        "count": len(alerted),
        "bins": limited_alerted,
    }
    
    # Cache for 1 minute
    cache.set('alerts', cache_key, result, CACHE_TTL['alerts'])
    return jsonify(result)


@app.get("/api/stats")
def api_stats():
    """Get aggregated statistics - CACHED for 5 minutes."""
    # Try cache first
    cached = cache.get('stats', 'all')
    if cached:
        logger.debug("Stats retrieved from cache")
        return jsonify(cached)
    
    bins = get_bins_data()
    n = len(bins)
    if n == 0:
        result = {
            "total_bins": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "overflow": 0,
            "avg_fill": 0,
            "dataset_timestamp": None,
            "trucks_active": 3,
            "fuel_save_estimate_pct": 0,
        }
    else:
        critical = sum(1 for b in bins if b["fill"] >= 80)
        high = sum(1 for b in bins if 60 <= b["fill"] < 80)
        medium = sum(1 for b in bins if 40 <= b["fill"] < 60)
        low = sum(1 for b in bins if b["fill"] < 40)
        overflow = sum(1 for b in bins if b["status"] == 1)
        avg_fill = round(sum(b["fill"] for b in bins) / n)
        ts = bins[0].get("timestamp") if bins else None
        fuel = min(42, round(18 + (overflow / max(n, 1)) * 22))

        result = {
            "total_bins": n,
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "overflow": overflow,
            "avg_fill": avg_fill,
            "dataset_timestamp": ts,
            "trucks_active": 3,
            "fuel_save_estimate_pct": fuel,
        }
    
    # Cache for 5 minutes
    cache.set('stats', 'all', result, CACHE_TTL['stats'])
    return jsonify(result)


@app.post("/api/route")
def api_route():
    bins = get_bins_data()
    body = request.get_json(silent=True) or {}
    threshold = int(body.get("threshold", 60))
    algo = body.get("algorithm", "priority")
    max_stops = int(body.get("max_stops", 20))
    num_trucks = int(body.get("num_trucks", 3))

    threshold = max(0, min(100, threshold))
    max_stops = max(1, min(200, max_stops))
    num_trucks = max(1, min(10, num_trucks))
    if algo not in ("nearest", "priority", "cluster", "ga", "multiobjective"):
        algo = "priority"

    adaptive = bool(body.get("adaptive", False) or algo == "adaptive")
    eligible = [b for b in bins if b["fill"] >= threshold]
    
    # Clear cache when computing new routes (they will change)
    cache.delete_pattern('routes:*')
    
    routes = compute_routes_logic(eligible, algo, num_trucks, max_stops, threshold, adaptive)
    distances = [round(route_distance_km(r), 2) for r in routes]
    skipped = skipped_bins(eligible, routes)
    
    # Calculate emissions and detailed path geometry
    emissions = []
    path_geometries = []
    
    for route in routes:
        if route:
            # Calculate actual road path geometry
            full_route = [DEPOT] + route + [DEPOT]
            path_geometries.append(calculate_detailed_path(full_route))
        else:
            path_geometries.append([])
            
    if algo in ('ga', 'multiobjective'):
        for route in routes:
            if route:
                ga = GeneticAlgorithm(DEPOT, route, population_size=20, generations=10)
                # Just calculate, don't optimize (already done above)
                co2 = ga.calculate_emissions(route)
                emissions.append(round(co2, 2))
            else:
                emissions.append(0.0)

    result = {
        "depot": DEPOT,
        "threshold": threshold,
        "algorithm": algo,
        "routes": routes,
        "path_geometries": path_geometries,
        "distances_km": distances,
        "emissions_kg_co2": emissions if emissions else None,
        "skipped": skipped,
        "eligible_count": len(eligible),
    }
    
    logger.info(f"Route computation: algo={algo}, trucks={num_trucks}, eligible={len(eligible)}")
    return jsonify(result)


@app.post("/api/route/optimize-ga")
def api_route_optimize_ga():
    """Optimize single route using Genetic Algorithm with full statistics."""
    try:
        bins_data = get_bins_data()
        body = request.get_json(silent=True) or {}
        
        threshold = int(body.get("threshold", 60))
        max_stops = int(body.get("max_stops", 20))
        population_size = int(body.get("population_size", 100))
        generations = int(body.get("generations", 50))
        
        threshold = max(0, min(100, threshold))
        max_stops = max(1, min(200, max_stops))
        population_size = max(10, min(500, population_size))
        generations = max(5, min(100, generations))
        
        # Get eligible bins
        eligible = [b for b in bins_data if b["fill"] >= threshold]
        if not eligible:
            return jsonify({"error": "No bins eligible for collection"}), 400
        
        # Limit to max_stops
        candidates = sorted(eligible, key=lambda b: -b["fill"])[:max_stops]
        
        # Build True Distance Matrix for exact routing lengths
        dist_matrix = build_distance_matrix(candidates, DEPOT, cap=100)
        
        # Run GA
        logger.info(f"Starting GA optimization: pop={population_size}, gen={generations}, bins={len(candidates)}")
        ga = GeneticAlgorithm(
            depot=DEPOT,
            bins=candidates,
            population_size=population_size,
            generations=generations,
            distance_weight=0.5,
            time_weight=0.3,
            emissions_weight=0.2,
            distance_matrix=dist_matrix
        )
        
        route, fitness = ga.optimize()
        summary = ga.get_summary()
        
        # Calculate cost estimate for scenario saving
        # $50 per truck + $3 per km + $10 per hour
        cost_est = 50 + summary['distance_km'] * 3 + (summary['time_minutes'] / 60) * 10
        summary['cost_estimate'] = round(cost_est, 2)
        
        full_route = [DEPOT] + route + [DEPOT]
        path_geometry = calculate_detailed_path(full_route)
        
        logger.info(f"GA optimization complete: fitness={fitness:.4f}, distance={summary['distance_km']:.2f}km")
        
        return jsonify({
            "status": "success",
            "algorithm": "genetic_algorithm",
            "route": route,
            "path_geometry": path_geometry,
            **summary
        })
    
    except Exception as e:
        logger.error(f"GA optimization error: {e}")
        return jsonify({"error": str(e)}), 500

# ============ FLEET MANAGEMENT ENDPOINTS ============

@app.post("/api/fleet/schedule")
def api_fleet_schedule():
    """Schedule optimized routes to drivers and vehicles."""
    try:
        body = request.get_json(silent=True) or {}
        routes = body.get('routes', [])
        drivers = body.get('drivers', [])
        vehicles = body.get('vehicles', [])
        
        if not routes or not drivers or not vehicles:
            return jsonify({"error": "Missing routes, drivers, or vehicles"}), 400
        
        fleet_api = FleetManagementAPI(db_manager, cache)
        result = fleet_api.schedule_routes(routes, drivers, vehicles)
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Fleet scheduling error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/fleet/track")
def api_fleet_track():
    """Update vehicle location during collection."""
    try:
        body = request.get_json(silent=True) or {}
        assignment_id = body.get('assignment_id')
        latitude = body.get('latitude')
        longitude = body.get('longitude')
        current_bin = body.get('current_bin')
        current_stop = body.get('current_stop')
        
        if not assignment_id or latitude is None or longitude is None:
            return jsonify({"error": "Missing required fields"}), 400
        
        fleet_api = FleetManagementAPI(db_manager, cache)
        result = fleet_api.update_vehicle_location(assignment_id, latitude, longitude, current_bin, current_stop)
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Vehicle tracking update error: {e}")
        return jsonify({"error": str(e)}), 500

@app.get("/api/fleet/progress/<assignment_id>")
def api_fleet_progress(assignment_id):
    """Get progress of vehicle on assigned route."""
    try:
        fleet_api = FleetManagementAPI(db_manager, cache)
        result = fleet_api.get_vehicle_progress(assignment_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Progress retrieval error: {e}")
        return jsonify({"error": str(e)}), 500

@app.get("/api/fleet/active-vehicles")
def api_fleet_active_vehicles():
    """Get all currently active vehicles with real-time locations."""
    try:
        fleet_api = FleetManagementAPI(db_manager, cache)
        vehicles = fleet_api.get_active_vehicles()
        return jsonify({"vehicles": vehicles, "count": len(vehicles)})
    except Exception as e:
        logger.error(f"Active vehicles retrieval error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/scenarios")
def api_scenarios_create():
    """Save a what-if analysis scenario."""
    try:
        body = request.get_json(silent=True) or {}
        name = body.get('name', 'Untitled Scenario')
        parameters = body.get('parameters', {})
        results = body.get('results', {})
        
        fleet_api = FleetManagementAPI(db_manager, cache)
        result = fleet_api.create_scenario(name, parameters, results)
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Scenario creation error: {e}")
        return jsonify({"error": str(e)}), 500

@app.get("/api/scenarios")
def api_scenarios_list():
    """Get all saved scenarios."""
    try:
        limit = request.args.get('limit', 10, type=int)
        fleet_api = FleetManagementAPI(db_manager, cache)
        scenarios = fleet_api.get_scenarios(limit)
        return jsonify({"scenarios": scenarios, "count": len(scenarios)})
    except Exception as e:
        logger.error(f"Scenarios retrieval error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/scenarios/compare")
def api_scenarios_compare():
    """Compare multiple scenarios side-by-side."""
    try:
        body = request.get_json(silent=True) or {}
        scenario_ids = body.get('scenario_ids', [])
        
        if not scenario_ids:
            return jsonify({"error": "No scenario IDs provided"}), 400
        
        fleet_api = FleetManagementAPI(db_manager, cache)
        comparison = fleet_api.compare_scenarios(scenario_ids)
        
        return jsonify(comparison)
    except Exception as e:
        logger.error(f"Scenario comparison error: {e}")
        return jsonify({"error": str(e)}), 500

@app.get("/api/collection-windows")
def api_collection_windows():
    """Get predicted collection windows for all bins."""
    try:
        bins_data = get_bins_data()
        predictor = CollectionWindowPredictor(DB_CONFIG)
        
        if not predictor.load_model():
            # Train if not available
            predictor.train()
        
        predictions = predictor.predict_all_bins(bins_data)
        return jsonify({"predictions": predictions[:50], "count": len(predictions)})
    except Exception as e:
        logger.error(f"Collection window prediction error: {e}")
        return jsonify({"error": str(e)}), 500

@app.post("/api/bins_upload")
def api_bins_upload():
    if "file" not in request.files:
        return jsonify({"error": "missing file field 'file'"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400
    text = f.read().decode("utf-8", errors="replace")
    reader = csv.DictReader(StringIO(text))
    expected = {"bin_id", "latitude", "longitude", "fill_level", "status"}
    if not reader.fieldnames or not expected.issubset(set(reader.fieldnames)):
        return jsonify(
            {
                "error": "CSV must include columns: bin_id, latitude, longitude, fill_level, status (timestamp optional)",
            }
        ), 400

    new_bins: list[dict] = []
    for row in reader:
        if not row.get("bin_id"):
            continue
        fill_val = int(float(row["fill_level"]))
        new_bins.append({
            "id": row["bin_id"].strip(),
            "lat": float(row["latitude"]),
            "lng": float(row["longitude"]),
            "fill": fill_val,
            "status": 1 if fill_val >= 80 else 0,
        })

    if len(new_bins) == 0:
        return jsonify({"error": "no data rows parsed"}), 400

    # Save to database
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Clear existing data
        cursor.execute("TRUNCATE TABLE bins CASCADE")

        # Insert new bins
        for bin_data in new_bins:
            cursor.execute("""
                INSERT INTO bins (id, latitude, longitude, fill_level, status, last_updated, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    fill_level = EXCLUDED.fill_level,
                    status = EXCLUDED.status,
                    last_updated = EXCLUDED.last_updated
            """, (
                bin_data['id'], bin_data['lat'], bin_data['lng'],
                bin_data['fill'], bin_data['status'],
                datetime.now(), datetime.now()
            ))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"ok": True, "count": len(new_bins), "message": f"Saved {len(new_bins)} bins to database"})

    except Exception as e:
        return jsonify({"error": f"Database error: {str(e)}"}), 500


@app.post("/api/telemetry")
@app.post("/api/telemetry/")
def api_telemetry():
    """Accept simulated IoT telemetry and persist it into PostgreSQL."""
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "JSON payload required"}), 400

    required_fields = ["truck_id", "gps_latitude", "gps_longitude", "recorded_at"]
    missing = [f for f in required_fields if payload.get(f) is None]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        recorded_at = payload.get("recorded_at")
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)
        payload["recorded_at"] = recorded_at

        save_vehicle_telemetry(payload)

        bin_id = payload.get("bin_id")
        if bin_id and payload.get("fill_level") is not None:
            update_bin_from_sensor_and_alert(
                bin_id,
                int(payload["fill_level"]),
                int(payload.get("status", 0)) if payload.get("status") is not None else None,
            )

        return jsonify({"ok": True, "message": "Telemetry received"})
    except Exception as e:
        print(f"Telemetry ingestion error: {e}")
        return jsonify({"error": str(e)}), 500


@app.get("/api/telemetry/recent")
def api_telemetry_recent():
    """Return the most recent vehicle telemetry records."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT truck_id, gps_latitude, gps_longitude, speed_kmh,
                   heading_degrees, fuel_consumption_liters_per_km,
                   engine_temp_celsius, driver_id, recorded_at
            FROM vehicle_telemetry
            ORDER BY recorded_at DESC
            LIMIT 10
            """
        )
        records = []
        for row in cursor.fetchall():
            records.append({
                "truck_id": row["truck_id"],
                "gps_latitude": float(row["gps_latitude"]) if row["gps_latitude"] is not None else None,
                "gps_longitude": float(row["gps_longitude"]) if row["gps_longitude"] is not None else None,
                "speed_kmh": float(row["speed_kmh"]) if row["speed_kmh"] is not None else None,
                "heading_degrees": float(row["heading_degrees"]) if row["heading_degrees"] is not None else None,
                "fuel_consumption_liters_per_km": float(row["fuel_consumption_liters_per_km"]) if row["fuel_consumption_liters_per_km"] is not None else None,
                "engine_temp_celsius": float(row["engine_temp_celsius"]) if row["engine_temp_celsius"] is not None else None,
                "driver_id": row["driver_id"],
                "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
            })
        cursor.close()
        conn.close()
        return jsonify({"telemetry": records})
    except Exception as e:
        print(f"Telemetry query error: {e}")
        return jsonify({"telemetry": [], "error": str(e)}), 500


@app.get("/api/telemetry/vehicles")
def api_telemetry_vehicles():
    """Return the latest known location per active truck."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT DISTINCT ON (truck_id) truck_id, gps_latitude, gps_longitude,
                   speed_kmh, heading_degrees, fuel_consumption_liters_per_km,
                   engine_temp_celsius, driver_id, recorded_at
            FROM vehicle_telemetry
            ORDER BY truck_id, recorded_at DESC
            """
        )
        records = []
        for row in cursor.fetchall():
            records.append({
                "truck_id": row["truck_id"],
                "gps_latitude": float(row["gps_latitude"]) if row["gps_latitude"] is not None else None,
                "gps_longitude": float(row["gps_longitude"]) if row["gps_longitude"] is not None else None,
                "speed_kmh": float(row["speed_kmh"]) if row["speed_kmh"] is not None else None,
                "heading_degrees": float(row["heading_degrees"]) if row["heading_degrees"] is not None else None,
                "fuel_consumption_liters_per_km": float(row["fuel_consumption_liters_per_km"]) if row["fuel_consumption_liters_per_km"] is not None else None,
                "engine_temp_celsius": float(row["engine_temp_celsius"]) if row["engine_temp_celsius"] is not None else None,
                "driver_id": row["driver_id"],
                "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
            })
        cursor.close()
        conn.close()
        return jsonify({"vehicles": records})
    except Exception as e:
        print(f"Telemetry vehicles query error: {e}")
        return jsonify({"vehicles": [], "error": str(e)}), 500


@app.get("/api/simulation/status")
def api_simulation_status():
    return jsonify(get_simulation_status())


@app.post("/api/simulation/start")
def api_simulation_start():
    body = request.get_json(silent=True) or {}
    interval = float(body.get("interval_seconds", 1.0))
    max_iterations = int(body.get("max_iterations", 0))
    if get_simulation_status()["active"]:
        return jsonify({"error": "Simulation already running", "status": get_simulation_status()}), 400
    started = start_simulation(interval, max_iterations)
    if not started:
        return jsonify({"error": "Unable to start simulation"}), 500
    return jsonify({"ok": True, "message": "Simulation started", "status": get_simulation_status()})


@app.post("/api/simulation/stop")
def api_simulation_stop():
    stopped = stop_simulation()
    return jsonify({"ok": stopped, "status": get_simulation_status()})


@app.get("/api/simulation/logs")
def api_simulation_logs():
    status = get_simulation_status()
    return jsonify({"logs": status.get("events", []), "status": status})


def create_app():
    """Initialize and configure the Flask application."""
    global mqtt_listener
    global db_manager
    global forecaster
    
    logger.info("=" * 60)
    logger.info("Initializing GreenRoute Flask Application")
    logger.info("=" * 60)
    
    # Test database connection
    try:
        health = db_manager.health_check()
        if health['status'] == 'healthy':
            logger.info(f"✅ Database connection successful - {health['bin_records']} bins available")
        else:
            logger.warning(f"⚠️  Database schema not initialized - only {health['bin_records']} bins available")
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        logger.warning("Falling back to CSV data")
    
    # Initialize cache
    if cache.available:
        logger.info(f"✅ Redis cache available - host={config.REDIS_HOST}:{config.REDIS_PORT}")
    else:
        logger.warning("⚠️  Redis not available - caching disabled")
    
    # Initialize forecaster
    try:
        forecaster = WasteForecaster(DB_CONFIG)
        if forecaster.load_model():
            logger.info("✅ Forecasting model loaded from disk")
        else:
            logger.info("⚠️  Training new forecasting model...")
            if forecaster.train():
                logger.info("✅ Forecasting model trained successfully")
            else:
                logger.warning("⚠️  Could not train forecasting model - predictions unavailable")
                forecaster = None
    except Exception as e:
        logger.warning(f"⚠️  Forecaster initialization failed: {e}")
        forecaster = None
    
    # Initialize MQTT listener if enabled
    if config.MQTT_ENABLED:
        try:
            mqtt_listener = MQTTListener(
                broker_host=config.MQTT_BROKER,
                broker_port=config.MQTT_PORT,
                db_config=DB_CONFIG
            )
            mqtt_listener.start_background()
            logger.info(f"✅ MQTT listener started - broker={config.MQTT_BROKER}:{config.MQTT_PORT}")
        except Exception as e:
            logger.warning(f"⚠️  MQTT initialization failed: {e}")
    else:
        logger.info("ℹ️  MQTT disabled (set MQTT_ENABLED=True to enable)")
    
    # Validate configuration
    is_valid, errors = config.validate()
    if is_valid:
        logger.info("✅ Configuration validation passed")
    else:
        logger.warning("⚠️  Configuration validation warnings:")
        for error in errors:
            logger.warning(f"   - {error}")
    
    logger.info("=" * 60)
    logger.info("GreenRoute initialization complete")
    logger.info("=" * 60)
    
    return app


if __name__ == "__main__":
    ensure_data_file()
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting Flask server on 0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=config.DEBUG)

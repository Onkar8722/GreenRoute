-- Canonical GreenRoute PostgreSQL schema for fresh installations.
-- Existing databases are checked for compatibility by DatabaseManager; this
-- idempotent script intentionally does not rename, drop, or reinterpret data.

CREATE TABLE IF NOT EXISTS bins (
    id TEXT PRIMARY KEY,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    fill_level INTEGER NOT NULL CHECK (fill_level BETWEEN 0 AND 100),
    status INTEGER NOT NULL DEFAULT 0 CHECK (status BETWEEN 0 AND 2),
    battery_level INTEGER CHECK (battery_level BETWEEN 0 AND 100),
    signal_strength INTEGER CHECK (signal_strength BETWEEN -120 AND 0),
    bin_type TEXT NOT NULL DEFAULT 'residential',
    capacity_liters INTEGER NOT NULL DEFAULT 120 CHECK (capacity_liters > 0),
    last_emptied TIMESTAMP,
    last_updated TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fill_history (
    id BIGSERIAL PRIMARY KEY,
    bin_id TEXT NOT NULL REFERENCES bins(id) ON DELETE RESTRICT,
    fill_level INTEGER NOT NULL CHECK (fill_level BETWEEN 0 AND 100),
    recorded_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS routes (
    id BIGSERIAL PRIMARY KEY,
    truck_id TEXT NOT NULL,
    algorithm TEXT NOT NULL DEFAULT 'nearest',
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'in_progress', 'completed', 'failed')),
    bin_sequence TEXT[] NOT NULL DEFAULT '{}',
    planned_bins_count INTEGER CHECK (planned_bins_count >= 0),
    actual_bins_count INTEGER CHECK (actual_bins_count >= 0),
    planned_distance_km DOUBLE PRECISION CHECK (planned_distance_km >= 0),
    actual_distance_km DOUBLE PRECISION CHECK (actual_distance_km >= 0),
    estimated_duration_min INTEGER CHECK (estimated_duration_min >= 0),
    actual_duration_min INTEGER CHECK (actual_duration_min >= 0),
    co2_kg DOUBLE PRECISION CHECK (co2_kg >= 0),
    fuel_liters DOUBLE PRECISION CHECK (fuel_liters >= 0),
    cost_estimate DOUBLE PRECISION CHECK (cost_estimate >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vehicle_telemetry (
    id BIGSERIAL PRIMARY KEY,
    truck_id TEXT NOT NULL,
    gps_latitude DOUBLE PRECISION CHECK (gps_latitude BETWEEN -90 AND 90),
    gps_longitude DOUBLE PRECISION CHECK (gps_longitude BETWEEN -180 AND 180),
    gps_accuracy DOUBLE PRECISION CHECK (gps_accuracy >= 0),
    speed_kmh DOUBLE PRECISION CHECK (speed_kmh >= 0),
    heading_degrees DOUBLE PRECISION CHECK (heading_degrees BETWEEN 0 AND 360),
    harsh_braking_events INTEGER NOT NULL DEFAULT 0 CHECK (harsh_braking_events >= 0),
    harsh_acceleration_events INTEGER NOT NULL DEFAULT 0 CHECK (harsh_acceleration_events >= 0),
    idle_duration_min INTEGER NOT NULL DEFAULT 0 CHECK (idle_duration_min >= 0),
    fuel_consumption_liters_per_km DOUBLE PRECISION CHECK (fuel_consumption_liters_per_km >= 0),
    engine_temp_celsius DOUBLE PRECISION,
    engine_diagnostics JSONB NOT NULL DEFAULT '{}'::jsonb,
    driver_id TEXT,
    recorded_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id BIGSERIAL PRIMARY KEY,
    bin_id TEXT NOT NULL REFERENCES bins(id) ON DELETE RESTRICT,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium'
        CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'assigned', 'resolved', 'false_alarm')),
    assigned_to TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS forecasting_models (
    id BIGSERIAL PRIMARY KEY,
    model_type TEXT NOT NULL,
    training_date DATE NOT NULL DEFAULT CURRENT_DATE,
    accuracy_score DOUBLE PRECISION CHECK (accuracy_score BETWEEN 0 AND 1),
    mae DOUBLE PRECISION CHECK (mae >= 0),
    rmse DOUBLE PRECISION CHECK (rmse >= 0),
    features TEXT[] NOT NULL DEFAULT '{}',
    model_path TEXT,
    performance_notes TEXT
);

CREATE TABLE IF NOT EXISTS vehicles (
    id BIGSERIAL PRIMARY KEY,
    vehicle_id TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL,
    license_plate TEXT NOT NULL UNIQUE,
    capacity_liters DOUBLE PRECISION NOT NULL DEFAULT 5000 CHECK (capacity_liters > 0),
    fuel_type TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS drivers (
    id BIGSERIAL PRIMARY KEY,
    driver_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    license_number TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'on_route', 'off_duty')),
    vehicle_id BIGINT REFERENCES vehicles(id) ON DELETE RESTRICT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS route_assignments (
    assignment_id TEXT PRIMARY KEY,
    driver_id BIGINT NOT NULL REFERENCES drivers(id) ON DELETE RESTRICT,
    vehicle_id BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    route_data JSONB NOT NULL CHECK (jsonb_typeof(route_data) = 'array'),
    total_distance_km DOUBLE PRECISION NOT NULL CHECK (total_distance_km >= 0),
    estimated_duration_minutes INTEGER NOT NULL CHECK (estimated_duration_minutes >= 0),
    estimated_emissions_kg DOUBLE PRECISION CHECK (estimated_emissions_kg >= 0),
    status TEXT NOT NULL DEFAULT 'assigned'
        CHECK (status IN ('assigned', 'in_progress', 'completed', 'cancelled')),
    scheduled_time TIMESTAMP,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS vehicle_tracking (
    id BIGSERIAL PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES route_assignments(assignment_id) ON DELETE RESTRICT,
    vehicle_id BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    current_bin_id TEXT REFERENCES bins(id) ON DELETE RESTRICT,
    current_stop_number INTEGER CHECK (current_stop_number >= 0),
    total_stops INTEGER CHECK (total_stops >= 0),
    bins_collected INTEGER NOT NULL DEFAULT 0 CHECK (bins_collected >= 0),
    status TEXT,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    results JSONB NOT NULL DEFAULT '{}'::jsonb,
    total_distance_km DOUBLE PRECISION CHECK (total_distance_km >= 0),
    total_time_minutes INTEGER CHECK (total_time_minutes >= 0),
    total_emissions_kg DOUBLE PRECISION CHECK (total_emissions_kg >= 0),
    cost_estimate DOUBLE PRECISION CHECK (cost_estimate >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS schema_versions (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW(),
    description TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_bins_fill_level ON bins(fill_level DESC);
CREATE INDEX IF NOT EXISTS idx_bins_status ON bins(status);
CREATE INDEX IF NOT EXISTS idx_bins_lat_lng ON bins(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_fill_history_bin_recorded ON fill_history(bin_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_routes_truck_status ON routes(truck_id, status);
CREATE INDEX IF NOT EXISTS idx_telemetry_truck_recorded ON vehicle_telemetry(truck_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_bin_created ON alerts(bin_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_drivers_status ON drivers(status);
CREATE INDEX IF NOT EXISTS idx_drivers_vehicle ON drivers(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_assignments_status ON route_assignments(status);
CREATE INDEX IF NOT EXISTS idx_assignments_driver ON route_assignments(driver_id);
CREATE INDEX IF NOT EXISTS idx_assignments_vehicle ON route_assignments(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_tracking_assignment_timestamp ON vehicle_tracking(assignment_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_tracking_vehicle_timestamp ON vehicle_tracking(vehicle_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_scenarios_created ON scenarios(created_at DESC);

CREATE OR REPLACE VIEW active_bins AS
SELECT * FROM bins WHERE fill_level > 0;

CREATE OR REPLACE VIEW critical_bins AS
SELECT * FROM bins WHERE fill_level >= 80;

CREATE OR REPLACE VIEW recent_alerts AS
SELECT * FROM alerts
WHERE created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;

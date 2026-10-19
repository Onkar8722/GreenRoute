-- GreenRoute PostgreSQL Database Schema
-- Run this script to create the database structure

-- Create database (run this manually if needed)
-- CREATE DATABASE greenroute_db;

-- Connect to the database
-- \c greenroute_db;

-- BINS TABLE: Track smart waste containers
CREATE TABLE IF NOT EXISTS bins (
    id TEXT PRIMARY KEY,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    fill_level INTEGER CHECK (fill_level >= 0 AND fill_level <= 100),
    status INTEGER DEFAULT 0, -- 0=normal, 1=overflow, 2=malfunction
    battery_level INTEGER CHECK (battery_level >= 0 AND battery_level <= 100),
    signal_strength INTEGER CHECK (signal_strength >= -120 AND signal_strength <= 0),
    bin_type TEXT DEFAULT 'residential', -- 'residential', 'commercial', 'industrial'
    capacity_liters INTEGER DEFAULT 120,
    last_emptied TIMESTAMP,
    last_updated TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW()
);

-- FILL HISTORY: Time-series data for analytics
CREATE TABLE IF NOT EXISTS fill_history (
    id SERIAL PRIMARY KEY,
    bin_id TEXT NOT NULL REFERENCES bins(id) ON DELETE CASCADE,
    fill_level INTEGER NOT NULL,
    recorded_at TIMESTAMP NOT NULL
);

-- ROUTES TABLE: Track planned/executed collection routes
CREATE TABLE IF NOT EXISTS routes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    truck_id TEXT NOT NULL,
    algorithm TEXT DEFAULT 'nearest', -- 'nearest', 'priority', 'genetic', 'ant_colony'
    status TEXT DEFAULT 'planned', -- 'planned', 'in_progress', 'completed', 'failed'
    bin_sequence TEXT[], -- JSON array of bin IDs in order
    planned_bins_count INTEGER,
    actual_bins_count INTEGER,
    planned_distance_km FLOAT,
    actual_distance_km FLOAT,
    estimated_duration_min INTEGER,
    actual_duration_min INTEGER,
    co2_kg FLOAT,
    fuel_liters FLOAT,
    cost_estimate FLOAT,
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- VEHICLE TELEMETRY: Real-time vehicle tracking
CREATE TABLE IF NOT EXISTS vehicle_telemetry (
    id SERIAL PRIMARY KEY,
    truck_id TEXT NOT NULL,
    gps_latitude FLOAT,
    gps_longitude FLOAT,
    gps_accuracy FLOAT,
    speed_kmh FLOAT,
    heading_degrees FLOAT,
    harsh_braking_events INTEGER DEFAULT 0,
    harsh_acceleration_events INTEGER DEFAULT 0,
    idle_duration_min INTEGER DEFAULT 0,
    fuel_consumption_liters_per_km FLOAT,
    engine_temp_celsius FLOAT,
    engine_diagnostics JSONB, -- Store all OBD-II codes
    driver_id TEXT,
    recorded_at TIMESTAMP NOT NULL
);

-- ALERTS TABLE: Track operational events
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    bin_id TEXT NOT NULL REFERENCES bins(id) ON DELETE CASCADE,
    alert_type TEXT, -- 'overflow', 'malfunction', 'sensor_error', 'contamination'
    severity TEXT DEFAULT 'medium', -- 'critical', 'high', 'medium', 'low'
    message TEXT,
    status TEXT DEFAULT 'open', -- 'open', 'assigned', 'resolved', 'false_alarm'
    assigned_to TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    resolved_at TIMESTAMP
);

-- FORECASTING MODEL: Store ML model metadata
CREATE TABLE IF NOT EXISTS forecasting_models (
    id SERIAL PRIMARY KEY,
    model_type TEXT, -- 'svm', 'prophet', 'neural_network'
    training_date DATE DEFAULT CURRENT_DATE,
    accuracy_score FLOAT,
    mae FLOAT, -- Mean Absolute Error
    rmse FLOAT, -- Root Mean Squared Error
    features TEXT[], -- Feature names used
    model_path TEXT, -- Filesystem path to pickled model
    performance_notes TEXT
);

-- SCENARIOS TABLE: Store what-if analysis scenarios
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    parameters JSONB,
    results JSONB,
    total_distance_km FLOAT,
    total_time_minutes INTEGER,
    total_emissions_kg FLOAT,
    cost_estimate FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_bins_fill_level ON bins(fill_level DESC);
CREATE INDEX IF NOT EXISTS idx_bins_status ON bins(status);
-- Note: PostGIS extension needed for geospatial index
-- CREATE INDEX IF NOT EXISTS idx_bins_location ON bins USING GIST(ll_to_earth(latitude, longitude));
CREATE INDEX IF NOT EXISTS idx_bins_lat_lng ON bins(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_fill_history_bin_date ON fill_history(bin_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_routes_truck_status ON routes(truck_id, status);
CREATE INDEX IF NOT EXISTS idx_telemetry_truck_recorded ON vehicle_telemetry(truck_id, recorded_at DESC);

-- Create a view for active bins (fill_level > 0)
CREATE OR REPLACE VIEW active_bins AS
SELECT * FROM bins WHERE fill_level > 0;

-- Create a view for critical bins (fill_level >= 80)
CREATE OR REPLACE VIEW critical_bins AS
SELECT * FROM bins WHERE fill_level >= 80;

-- Create a view for recent alerts (last 24 hours)
CREATE OR REPLACE VIEW recent_alerts AS
SELECT * FROM alerts
WHERE created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at DESC;

-- Insert sample data (optional - for testing)
-- This will be populated by the migration script

-- Grant permissions (adjust as needed)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO greenroute_user;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO greenroute_user;
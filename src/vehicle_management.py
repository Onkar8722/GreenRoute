"""
Vehicle & Driver Management - Database schema and models.
Manages vehicle fleet, drivers, route assignments, and real-time tracking.
"""

# SQL to add to your database initialization:
DB_EXTENSIONS = """
-- Create vehicles table
CREATE TABLE IF NOT EXISTS vehicles (
    id SERIAL PRIMARY KEY,
    vehicle_id VARCHAR(50) UNIQUE NOT NULL,
    vehicle_type VARCHAR(20) NOT NULL, -- 'truck', 'van', 'auto'
    license_plate VARCHAR(20) UNIQUE NOT NULL,
    capacity_liters FLOAT DEFAULT 5000,
    fuel_type VARCHAR(20) NOT NULL, -- 'diesel', 'electric', 'hybrid'
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create drivers table
CREATE TABLE IF NOT EXISTS drivers (
    id SERIAL PRIMARY KEY,
    driver_id VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    phone VARCHAR(20),
    email VARCHAR(100),
    license_number VARCHAR(50) UNIQUE NOT NULL,
    status VARCHAR(20) DEFAULT 'available', -- 'available', 'on_route', 'off_duty'
    vehicle_id INT REFERENCES vehicles(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create route assignments
CREATE TABLE IF NOT EXISTS route_assignments (
    id SERIAL PRIMARY KEY,
    assignment_id VARCHAR(50) UNIQUE NOT NULL,
    driver_id INT REFERENCES drivers(id),
    vehicle_id INT REFERENCES vehicles(id),
    route_data JSONB NOT NULL, -- Stores the bin IDs and optimization results
    total_distance_km FLOAT,
    estimated_duration_minutes INT,
    estimated_emissions_kg FLOAT,
    status VARCHAR(20) DEFAULT 'assigned', -- 'assigned', 'in_progress', 'completed', 'cancelled'
    scheduled_time TIMESTAMP,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create vehicle/driver location tracking
CREATE TABLE IF NOT EXISTS vehicle_tracking (
    id SERIAL PRIMARY KEY,
    assignment_id VARCHAR(50) REFERENCES route_assignments(assignment_id),
    vehicle_id INT REFERENCES vehicles(id),
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    current_bin_id VARCHAR(50),
    current_stop_number INT,
    total_stops INT,
    bins_collected INT DEFAULT 0,
    status VARCHAR(20),
    timestamp TIMESTAMP DEFAULT NOW()
);

-- Create collection windows predictions
CREATE TABLE IF NOT EXISTS collection_windows (
    id SERIAL PRIMARY KEY,
    bin_id VARCHAR(50),
    predicted_full_time TIMESTAMP,
    confidence_score FLOAT,
    optimal_collection_window_start TIMESTAMP,
    optimal_collection_window_end TIMESTAMP,
    recommended_frequency VARCHAR(20), -- 'daily', 'every_2_days', 'weekly'
    model_version VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create what-if analysis scenarios
CREATE TABLE IF NOT EXISTS scenarios (
    id SERIAL PRIMARY KEY,
    scenario_id VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    parameters JSONB NOT NULL, -- Stores all optimization parameters
    results JSONB NOT NULL, -- Stores optimization results
    total_distance_km FLOAT,
    total_time_minutes INT,
    total_emissions_kg FLOAT,
    cost_estimate FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_drivers_status ON drivers(status);
CREATE INDEX IF NOT EXISTS idx_drivers_vehicle ON drivers(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_assignments_status ON route_assignments(status);
CREATE INDEX IF NOT EXISTS idx_assignments_driver ON route_assignments(driver_id);
CREATE INDEX IF NOT EXISTS idx_tracking_assignment ON vehicle_tracking(assignment_id);
CREATE INDEX IF NOT EXISTS idx_tracking_timestamp ON vehicle_tracking(timestamp);
CREATE INDEX IF NOT EXISTS idx_windows_bin ON collection_windows(bin_id);
CREATE INDEX IF NOT EXISTS idx_scenarios_created ON scenarios(created_at);
"""

# Python models for type safety
from dataclasses import dataclass
from typing import Optional, Dict, List
from datetime import datetime
import json

@dataclass
class Vehicle:
    vehicle_id: str
    vehicle_type: str  # 'truck', 'van', 'auto'
    license_plate: str
    capacity_liters: float = 5000
    fuel_type: str = 'diesel'
    
    def to_dict(self):
        return {
            'vehicle_id': self.vehicle_id,
            'vehicle_type': self.vehicle_type,
            'license_plate': self.license_plate,
            'capacity_liters': self.capacity_liters,
            'fuel_type': self.fuel_type
        }

@dataclass
class Driver:
    driver_id: str
    name: str
    license_number: str
    phone: Optional[str] = None
    email: Optional[str] = None
    status: str = 'available'
    vehicle_id: Optional[str] = None
    
    def to_dict(self):
        return {
            'driver_id': self.driver_id,
            'name': self.name,
            'license_number': self.license_number,
            'phone': self.phone,
            'email': self.email,
            'status': self.status,
            'vehicle_id': self.vehicle_id
        }

@dataclass
class RouteAssignment:
    assignment_id: str
    driver_id: str
    vehicle_id: str
    route_data: List[str]  # bin IDs
    distance_km: float
    duration_minutes: int
    emissions_kg: float
    status: str = 'assigned'
    scheduled_time: Optional[datetime] = None
    
    def to_dict(self):
        return {
            'assignment_id': self.assignment_id,
            'driver_id': self.driver_id,
            'vehicle_id': self.vehicle_id,
            'route_data': self.route_data,
            'distance_km': self.distance_km,
            'duration_minutes': self.duration_minutes,
            'emissions_kg': self.emissions_kg,
            'status': self.status,
            'scheduled_time': self.scheduled_time.isoformat() if self.scheduled_time else None
        }

@dataclass
class VehicleTracking:
    assignment_id: str
    vehicle_id: str
    latitude: float
    longitude: float
    current_bin_id: Optional[str] = None
    current_stop_number: Optional[int] = None
    total_stops: Optional[int] = None
    bins_collected: int = 0
    status: str = 'in_progress'
    
    def to_dict(self):
        return {
            'assignment_id': self.assignment_id,
            'vehicle_id': self.vehicle_id,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'current_bin_id': self.current_bin_id,
            'current_stop_number': self.current_stop_number,
            'total_stops': self.total_stops,
            'bins_collected': self.bins_collected,
            'status': self.status
        }

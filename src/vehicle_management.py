"""Vehicle and driver data models for fleet management.

The canonical fleet schema is defined in ``docs/database_schema.sql`` and is
initialized by ``DatabaseManager``; this module intentionally contains no
alternate schema definition.
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

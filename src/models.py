"""
Pydantic models for data validation in GreenRoute.
Ensures data integrity across API endpoints and database operations.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, validator


class BinBase(BaseModel):
    """Base bin information."""
    id: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    bin_type: str = Field(default="residential")
    capacity_liters: int = Field(ge=10, le=1000)
    
    class Config:
        from_attributes = True


class BinCreate(BinBase):
    """Schema for creating a new bin."""
    pass


class BinUpdate(BaseModel):
    """Schema for updating bin data."""
    fill_level: Optional[int] = Field(None, ge=0, le=100)
    status: Optional[int] = None
    battery_level: Optional[int] = Field(None, ge=0, le=100)
    signal_strength: Optional[int] = Field(None, ge=-120, le=0)
    last_emptied: Optional[datetime] = None


class BinResponse(BinBase):
    """Full bin response with current status."""
    fill: int = Field(ge=0, le=100)
    status: int
    battery_level: int
    signal_strength: int
    timestamp: Optional[str]
    
    class Config:
        from_attributes = True


class FillHistoryCreate(BaseModel):
    """Create a fill history record."""
    bin_id: str
    fill_level: int = Field(ge=0, le=100)
    recorded_at: datetime


class FillHistoryResponse(FillHistoryCreate):
    """Fill history response."""
    id: int
    
    class Config:
        from_attributes = True


class AlertBase(BaseModel):
    """Base alert information."""
    bin_id: str
    alert_type: str
    severity: str = Field(default="medium")
    message: str


class AlertCreate(AlertBase):
    """Create alert schema."""
    pass


class AlertResponse(AlertBase):
    """Alert response schema."""
    id: int
    status: str = "open"
    assigned_to: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class RouteBase(BaseModel):
    """Base route information."""
    truck_id: str
    algorithm: str = "nearest"
    bin_sequence: List[str]
    planned_bins_count: int
    planned_distance_km: float = Field(ge=0)
    estimated_duration_min: int = Field(ge=0)


class RouteCreate(RouteBase):
    """Create route schema."""
    pass


class RouteResponse(RouteBase):
    """Route response with execution details."""
    id: str
    status: str = "planned"
    actual_bins_count: Optional[int] = None
    actual_distance_km: Optional[float] = None
    actual_duration_min: Optional[int] = None
    co2_kg: Optional[float] = None
    fuel_liters: Optional[float] = None
    cost_estimate: Optional[float] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class VehicleTelemetryCreate(BaseModel):
    """Create vehicle telemetry record."""
    truck_id: str
    gps_latitude: float = Field(ge=-90, le=90)
    gps_longitude: float = Field(ge=-180, le=180)
    gps_accuracy: float = Field(ge=0)
    speed_kmh: float = Field(ge=0)
    heading_degrees: float = Field(ge=0, le=360)
    harsh_braking_events: int = Field(default=0, ge=0)
    harsh_acceleration_events: int = Field(default=0, ge=0)
    idle_duration_min: int = Field(default=0, ge=0)
    fuel_consumption_liters_per_km: float = Field(ge=0)
    engine_temp_celsius: float = Field(ge=-40, le=150)
    driver_id: Optional[str] = None
    recorded_at: datetime


class VehicleTelemetryResponse(VehicleTelemetryCreate):
    """Vehicle telemetry response."""
    id: int
    
    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    """Statistics response for KPI endpoints."""
    total_bins: int
    bins_above_80_percent: int
    average_fill_level: float
    critical_alerts: int
    high_alerts: int
    total_fleet_distance_km: float
    total_co2_kg: float
    total_fuel_liters: float
    average_response_time_min: float


class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str
    database: str
    redis: str
    mqtt: str
    timestamp: datetime

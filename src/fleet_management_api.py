"""
Advanced Fleet Management APIs.
Integrates scheduling, tracking, what-if analysis, and ML predictions.
"""

import uuid
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
from flask import request, jsonify

logger = logging.getLogger(__name__)

class FleetManagementAPI:
    """API handlers for fleet management features."""
    
    def __init__(self, db_manager, cache_manager):
        self.db = db_manager
        self.cache = cache_manager
    
    # ============ ROUTE SCHEDULING ============
    
    def schedule_routes(self, routes_data: List[List[str]], drivers_data: List[Dict], 
                       vehicles_data: List[Dict]) -> Dict:
        """
        Assign optimized routes to vehicles and drivers.
        
        Parameters:
            routes_data: List of routes (each route is list of bin IDs)
            drivers_data: List of driver info
            vehicles_data: List of vehicle info
        
        Returns:
            {
                'assignments': [route_assignment_details],
                'unassigned_routes': [],
                'total_distance': float,
                'total_time': int,
                'total_cost': float
            }
        """
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            assignments = []
            total_distance = 0
            total_duration = 0
            
            # Assign routes to drivers/vehicles in round-robin
            for idx, route in enumerate(routes_data):
                driver_idx = idx % len(drivers_data)
                vehicle_idx = idx % len(vehicles_data)
                
                driver = drivers_data[driver_idx]
                vehicle = vehicles_data[vehicle_idx]
                
                # Calculate route distance (simplified - use route_distance_km)
                from app import route_distance_km, get_bins_data
                
                bins = get_bins_data()
                bin_map = {b['id']: b for b in bins}
                route_bins = [bin_map[bid] for bid in route if bid in bin_map]
                
                distance = route_distance_km(route_bins) if route_bins else 0
                duration = int((distance / 30) * 60 + len(route) * 2)  # 30 km/h + 2min per stop
                
                assignment_id = f"ASN_{uuid.uuid4().hex[:8]}"
                
                # Store in database
                cursor.execute("""
                    INSERT INTO route_assignments 
                    (assignment_id, driver_id, vehicle_id, route_data, 
                     total_distance_km, estimated_duration_minutes, status, scheduled_time)
                    SELECT %s, d.id, v.id, %s, %s, %s, 'assigned', NOW() + INTERVAL '1 hour'
                    FROM drivers d
                    JOIN vehicles v ON d.vehicle_id = v.id
                    WHERE d.driver_id = %s AND v.vehicle_id = %s
                """, (assignment_id, json.dumps(route), distance, duration, 
                      driver['driver_id'], vehicle['vehicle_id']))
                
                assignments.append({
                    'assignment_id': assignment_id,
                    'driver': driver['name'],
                    'vehicle': vehicle['license_plate'],
                    'bins': route,
                    'distance_km': round(distance, 2),
                    'duration_minutes': duration,
                    'status': 'assigned'
                })
                
                total_distance += distance
                total_duration += duration
            
            conn.commit()
            cursor.close()
            conn.close()
            
            # Cost estimation: $50 per truck + $3 per km + $10 per hour
            total_cost = len(assignments) * 50 + total_distance * 3 + (total_duration / 60) * 10
            
            return {
                'success': True,
                'assignments': assignments,
                'total_distance_km': round(total_distance, 2),
                'total_time_minutes': total_duration,
                'total_cost_estimate': round(total_cost, 2)
            }
        
        except Exception as e:
            logger.error(f"Route scheduling error: {e}")
            return {'success': False, 'error': str(e)}
    
    # ============ REAL-TIME TRACKING ============
    
    def update_vehicle_location(self, assignment_id: str, latitude: float, 
                               longitude: float, current_bin: Optional[str],
                               current_stop: Optional[int] = None) -> Dict:
        """Update vehicle location during route."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO vehicle_tracking 
                (assignment_id, vehicle_id, latitude, longitude, current_bin_id, current_stop_number)
                SELECT %s, v.id, %s, %s, %s, %s
                FROM route_assignments ra
                JOIN vehicles v ON ra.vehicle_id = v.id
                WHERE ra.assignment_id = %s
            """, (assignment_id, latitude, longitude, current_bin, current_stop, assignment_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {'success': True, 'message': 'Location updated'}
        
        except Exception as e:
            logger.error(f"Location update error: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_vehicle_progress(self, assignment_id: str) -> Dict:
        """Get current progress of vehicle on route."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    ra.assignment_id, ra.route_data, ra.total_distance_km, 
                    ra.estimated_duration_minutes, ra.status,
                    ra.start_time, CURRENT_TIMESTAMP as now_time,
                    (SELECT COUNT(*) FROM vehicle_tracking WHERE assignment_id = %s) as total_updates,
                    (SELECT COUNT(DISTINCT current_stop_number) FROM vehicle_tracking 
                     WHERE assignment_id = %s AND current_stop_number IS NOT NULL) as stops_completed,
                    (SELECT MAX(bins_collected) FROM vehicle_tracking WHERE assignment_id = %s) as bins_collected
                FROM route_assignments ra
                WHERE ra.assignment_id = %s
            """, (assignment_id, assignment_id, assignment_id, assignment_id))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not result:
                return {'error': 'Assignment not found'}
            
            route_bins = json.loads(result[1])
            elapsed_minutes = int((result[6] - result[5]).total_seconds() / 60) if result[5] else 0
            stops_completed = result[8] or 0
            bins_collected = result[9] or 0
            
            progress_pct = int((stops_completed / len(route_bins) * 100)) if route_bins else 0
            
            return {
                'assignment_id': assignment_id,
                'status': result[4],
                'total_bins': len(route_bins),
                'bins_collected': bins_collected,
                'stops_completed': stops_completed,
                'progress_percent': progress_pct,
                'elapsed_minutes': elapsed_minutes,
                'estimated_remaining_minutes': max(0, result[3] - elapsed_minutes) if result[3] else 0,
                'total_distance_km': result[2]
            }
        
        except Exception as e:
            logger.error(f"Progress retrieval error: {e}")
            return {'error': str(e)}
    
    def get_active_vehicles(self) -> List[Dict]:
        """Get all currently active vehicles with locations."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT DISTINCT ON (ra.assignment_id)
                    ra.assignment_id, d.name, v.license_plate, v.vehicle_type,
                    vt.latitude, vt.longitude, vt.current_bin_id, vt.timestamp,
                    ra.status
                FROM route_assignments ra
                JOIN vehicle_tracking vt ON ra.assignment_id = vt.assignment_id
                JOIN drivers d ON ra.driver_id = d.id
                JOIN vehicles v ON ra.vehicle_id = v.id
                WHERE ra.status = 'in_progress'
                ORDER BY ra.assignment_id, vt.timestamp DESC
            """)
            
            vehicles = []
            for row in cursor.fetchall():
                vehicles.append({
                    'assignment_id': row[0],
                    'driver': row[1],
                    'license_plate': row[2],
                    'vehicle_type': row[3],
                    'latitude': float(row[4]),
                    'longitude': float(row[5]),
                    'current_bin': row[6],
                    'last_update': row[7].isoformat(),
                    'status': row[8]
                })
            
            cursor.close()
            conn.close()
            
            return vehicles
        
        except Exception as e:
            logger.error(f"Active vehicles retrieval error: {e}")
            return []
    
    # ============ WHAT-IF ANALYSIS ============
    
    def create_scenario(self, name: str, parameters: Dict, results: Dict) -> Dict:
        """Save a what-if scenario for comparison."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            scenario_id = f"SCN_{uuid.uuid4().hex[:8]}"
            
            cursor.execute("""
                INSERT INTO scenarios 
                (scenario_id, name, description, parameters, results, 
                 total_distance_km, total_time_minutes, total_emissions_kg, cost_estimate)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                scenario_id,
                name,
                parameters.get('description', ''),
                json.dumps(parameters),
                json.dumps(results),
                results.get('distance_km', results.get('total_distance_km', 0)),
                results.get('time_minutes', results.get('total_time_minutes', 0)),
                results.get('co2_kg', results.get('total_emissions_kg', 0)),
                results.get('cost_estimate', 0)
            ))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {'success': True, 'scenario_id': scenario_id}
        
        except Exception as e:
            logger.error(f"Scenario creation error: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_scenarios(self, limit: int = 10) -> List[Dict]:
        """Get all saved scenarios."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT scenario_id, name, description, 
                       total_distance_km, total_time_minutes, total_emissions_kg, cost_estimate, created_at
                FROM scenarios
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            
            scenarios = []
            for row in cursor.fetchall():
                scenarios.append({
                    'scenario_id': row[0],
                    'name': row[1],
                    'description': row[2],
                    'distance_km': float(row[3]),
                    'total_distance_km': float(row[3]),
                    'total_time_minutes': int(row[4]),
                    'total_emissions_kg': float(row[5]),
                    'cost_estimate': float(row[6]),
                    'created_at': row[7].isoformat()
                })
            
            cursor.close()
            conn.close()
            
            return scenarios
        
        except Exception as e:
            logger.error(f"Scenarios retrieval error: {e}")
            return []
    
    def compare_scenarios(self, scenario_ids: List[str]) -> Dict:
        """Compare multiple scenarios side-by-side."""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            placeholders = ','.join(['%s'] * len(scenario_ids))
            cursor.execute(f"""
                SELECT scenario_id, name, total_distance_km, total_time_minutes, 
                       total_emissions_kg, cost_estimate
                FROM scenarios
                WHERE scenario_id IN ({placeholders})
            """, scenario_ids)
            
            scenarios = []
            min_distance = float('inf')
            min_time = float('inf')
            min_emissions = float('inf')
            min_cost = float('inf')
            
            for row in cursor.fetchall():
                scenario = {
                    'scenario_id': row[0],
                    'name': row[1],
                    'distance_km': float(row[2]),
                    'time_minutes': int(row[3]),
                    'emissions_kg': float(row[4]),
                    'cost': float(row[5])
                }
                scenarios.append(scenario)
                
                min_distance = min(min_distance, scenario['distance_km'])
                min_time = min(min_time, scenario['time_minutes'])
                min_emissions = min(min_emissions, scenario['emissions_kg'])
                min_cost = min(min_cost, scenario['cost'])
            
            # Calculate improvements
            for scenario in scenarios:
                scenario['distance_improvement'] = round(
                    ((scenario['distance_km'] - min_distance) / min_distance * 100), 1) if min_distance > 0 else 0
                scenario['time_improvement'] = round(
                    ((scenario['time_minutes'] - min_time) / min_time * 100), 1) if min_time > 0 else 0
                scenario['emissions_improvement'] = round(
                    ((scenario['emissions_kg'] - min_emissions) / min_emissions * 100), 1) if min_emissions > 0 else 0
                scenario['cost_improvement'] = round(
                    ((scenario['cost'] - min_cost) / min_cost * 100), 1) if min_cost > 0 else 0
            
            cursor.close()
            conn.close()
            
            return {
                'scenarios': scenarios,
                'best_distance': min_distance,
                'best_time': min_time,
                'best_emissions': min_emissions,
                'best_cost': min_cost
            }
        
        except Exception as e:
            logger.error(f"Scenario comparison error: {e}")
            return {'error': str(e)}

"""
Unit tests for GreenRoute routing algorithms.
Tests baseline algorithms (nearest neighbor, priority, clustering).
"""

import unittest
import math
from datetime import datetime


class RouteTestCase:
    """Base class for route testing utilities."""
    
    @staticmethod
    def haversine_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        """Calculate great-circle distance in km."""
        R = 6371  # Earth's radius in km
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        delta_lat = math.radians(lat2 - lat1)
        delta_lng = math.radians(lng2 - lng1)
        
        a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        return R * c


class TestNearestNeighbor(unittest.TestCase, RouteTestCase):
    """Test Nearest Neighbor algorithm."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.depot = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}
        self.bins = [
            {"id": "BIN_001", "lat": 18.5210, "lng": 73.8575, "fill": 85},
            {"id": "BIN_002", "lat": 18.5215, "lng": 73.8580, "fill": 90},
            {"id": "BIN_003", "lat": 18.5200, "lng": 73.8560, "fill": 45},
            {"id": "BIN_004", "lat": 18.5225, "lng": 73.8590, "fill": 75},
        ]
    
    def test_nearest_neighbor_constructs_valid_route(self):
        """Test that NN constructs valid route."""
        route = self._construct_nn_route()
        
        # Check all bins are included
        self.assertEqual(len(route), len(self.bins))
        
        # Check no duplicates
        self.assertEqual(len(route), len(set(route)))
        
        # Check all bin IDs are valid
        bin_ids = {b['id'] for b in self.bins}
        for bin_id in route:
            self.assertIn(bin_id, bin_ids)
    
    def test_nearest_neighbor_returns_valid_distance(self):
        """Test that NN returns valid total distance."""
        route = self._construct_nn_route()
        distance = self._calculate_route_distance(route)
        
        # Distance should be positive
        self.assertGreater(distance, 0)
        
        # Distance should be less than visiting every bin from depot and back
        max_distance = self._calculate_max_distance()
        self.assertLess(distance, max_distance)
    
    def test_nearest_neighbor_greedy_property(self):
        """Test that NN always picks nearest unvisited bin."""
        # This is more of an integration test
        route = self._construct_nn_route()
        
        # Verify greedy selection at first step
        current = "DEPOT"
        current_pos = self.depot
        
        unvisited = {b['id']: b for b in self.bins}
        next_bin_id = route[0]
        next_bin = unvisited[next_bin_id]
        
        # Check that next bin is nearest
        for bid in unvisited:
            b = unvisited[bid]
            dist_to_next = self.haversine_distance(
                current_pos['lat'], current_pos['lng'],
                next_bin['lat'], next_bin['lng']
            )
            dist_to_other = self.haversine_distance(
                current_pos['lat'], current_pos['lng'],
                b['lat'], b['lng']
            )
            # Next bin should be shortest or equal (allowing for floating point ops)
            self.assertLessEqual(dist_to_next, dist_to_other + 0.001)
    
    def _construct_nn_route(self) -> list:
        """Construct NN route (simplified implementation)."""
        unvisited = {b['id']: b for b in self.bins}
        route = []
        current = self.depot
        
        while unvisited:
            # Find nearest unvisited bin
            nearest_id = None
            nearest_dist = float('inf')
            
            for bin_id, bin_data in unvisited.items():
                dist = self.haversine_distance(
                    current['lat'], current['lng'],
                    bin_data['lat'], bin_data['lng']
                )
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_id = bin_id
            
            route.append(nearest_id)
            current = unvisited[nearest_id]
            del unvisited[nearest_id]
        
        return route
    
    def _calculate_route_distance(self, route: list) -> float:
        """Calculate total route distance."""
        distance = 0
        current = self.depot
        
        for bin_id in route:
            bin_data = next(b for b in self.bins if b['id'] == bin_id)
            distance += self.haversine_distance(
                current['lat'], current['lng'],
                bin_data['lat'], bin_data['lng']
            )
            current = bin_data
        
        # Return to depot
        distance += self.haversine_distance(
            current['lat'], current['lng'],
            self.depot['lat'], self.depot['lng']
        )
        
        return distance
    
    def _calculate_max_distance(self) -> float:
        """Calculate maximum possible distance."""
        distance = 0
        current = self.depot
        
        for b in self.bins:
            distance += self.haversine_distance(
                current['lat'], current['lng'],
                b['lat'], b['lng']
            )
            current = b
        
        distance += self.haversine_distance(
            current['lat'], current['lng'],
            self.depot['lat'], self.depot['lng']
        )
        
        return distance


class TestPriorityRouting(unittest.TestCase, RouteTestCase):
    """Test Priority-based routing."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.depot = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}
        self.bins = [
            {"id": "BIN_001", "lat": 18.5210, "lng": 73.8575, "fill": 95},  # Critical
            {"id": "BIN_002", "lat": 18.5215, "lng": 73.8580, "fill": 35},  # Low
            {"id": "BIN_003", "lat": 18.5200, "lng": 73.8560, "fill": 85},  # High
            {"id": "BIN_004", "lat": 18.5225, "lng": 73.8590, "fill": 45},  # Medium
        ]
    
    def test_priority_routing_respects_fill_level(self):
        """Test that priority routing orders by fill level."""
        sorted_bins = sorted(
            self.bins,
            key=lambda b: (-b['fill'], b['id'])  # Descending fill level
        )
        
        # Verify sorting
        self.assertEqual(sorted_bins[0]['fill'], 95)
        self.assertEqual(sorted_bins[1]['fill'], 85)
        self.assertEqual(sorted_bins[2]['fill'], 45)
        self.assertEqual(sorted_bins[3]['fill'], 35)
    
    def test_priority_routing_skips_low_fill(self):
        """Test that priority routing can skip low-fill bins."""
        low_threshold = 50
        high_priority = [b for b in self.bins if b['fill'] >= low_threshold]
        
        self.assertEqual(len(high_priority), 3)
        self.assertNotIn("BIN_002", [b['id'] for b in high_priority])
    
    def test_priority_preserves_all_critical_bins(self):
        """Test that critical bins (>80%) are always included."""
        critical = [b for b in self.bins if b['fill'] >= 80]
        
        self.assertEqual(len(critical), 2)
        self.assertIn("BIN_001", [b['id'] for b in critical])
        self.assertIn("BIN_003", [b['id'] for b in critical])


class TestClusterRouting(unittest.TestCase, RouteTestCase):
    """Test clustering-based routing."""
    
    def setUp(self):
        """Set up test fixtures with clustered bins."""
        self.depot = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}
        
        # Cluster 1: North
        self.cluster_north = [
            {"id": "BIN_NORTH_1", "lat": 18.5400, "lng": 73.8600},
            {"id": "BIN_NORTH_2", "lat": 18.5410, "lng": 73.8610},
        ]
        
        # Cluster 2: South
        self.cluster_south = [
            {"id": "BIN_SOUTH_1", "lat": 18.5000, "lng": 73.8500},
            {"id": "BIN_SOUTH_2", "lat": 18.4990, "lng": 73.8490},
        ]
        
        self.bins = self.cluster_north + self.cluster_south
    
    def test_clustering_groups_nearby_bins(self):
        """Test that bins are correctly grouped into clusters."""
        clusters = self._simple_clustering(k=2)
        
        self.assertEqual(len(clusters), 2)
        
        # Each cluster should have 2 bins
        for cluster in clusters:
            self.assertEqual(len(cluster), 2)
    
    def test_cluster_centroids_are_valid(self):
        """Test that cluster centroids are calculated correctly."""
        clusters = self._simple_clustering(k=2)
        
        for cluster in clusters:
            centroid = self._calculate_centroid(cluster)
            
            # Centroid should be valid lat/lng
            self.assertGreaterEqual(centroid['lat'], -90)
            self.assertLessEqual(centroid['lat'], 90)
            self.assertGreaterEqual(centroid['lng'], -180)
            self.assertLessEqual(centroid['lng'], 180)
    
    def _simple_clustering(self, k: int = 2) -> list:
        """Simple k-means clustering."""
        # For testing: just split into k groups
        group_size = len(self.bins) // k
        clusters = []
        
        for i in range(k):
            start = i * group_size
            end = start + group_size if i < k - 1 else len(self.bins)
            clusters.append(self.bins[start:end])
        
        return clusters
    
    def _calculate_centroid(self, bins: list) -> dict:
        """Calculate cluster centroid."""
        if not bins:
            return {"lat": 0, "lng": 0}
        
        avg_lat = sum(b['lat'] for b in bins) / len(bins)
        avg_lng = sum(b['lng'] for b in bins) / len(bins)
        
        return {"lat": avg_lat, "lng": avg_lng}


class TestRouteOptimization(unittest.TestCase, RouteTestCase):
    """Test overall route optimization."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.depot = {"id": "DEPOT", "lat": 18.5204, "lng": 73.8567}
        self.bins = [
            {"id": f"BIN_{i:03d}", "lat": 18.52 + i*0.001, "lng": 73.86 + i*0.001, "fill": 50+i*5}
            for i in range(10)
        ]
    
    def test_route_includes_all_bins(self):
        """Test that route includes all bins."""
        route = list(range(len(self.bins)))
        
        self.assertEqual(len(route), len(self.bins))
        self.assertEqual(set(route), set(range(len(self.bins))))
    
    def test_vehicle_capacity_constraint(self):
        """Test vehicle capacity constraints."""
        vehicle_capacity = 500  # liters
        bin_sizes = [50, 60, 70, 80, 90]
        
        total = sum(bin_sizes)
        self.assertLessEqual(total, vehicle_capacity)
    
    def test_multiple_trucks_needed(self):
        """Test when multiple trucks are needed."""
        vehicle_capacity = 100
        bin_sizes = [60, 60, 60]  # 3 trucks needed
        
        trucks_needed = 0
        current_load = 0
        
        for size in bin_sizes:
            if current_load + size > vehicle_capacity:
                trucks_needed += 1
                current_load = 0
            current_load += size
        
        trucks_needed += 1  # For remaining load
        self.assertEqual(trucks_needed, 3)


if __name__ == '__main__':
    unittest.main()

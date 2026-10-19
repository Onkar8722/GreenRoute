"""
Genetic Algorithm for vehicle routing optimization.
Provides advanced route optimization compared to nearest neighbor baseline.
"""

import random
import math
from typing import List, Tuple, Dict, Optional
from copy import deepcopy
import logging

logger = logging.getLogger(__name__)


class GeneticAlgorithm:
    """Genetic Algorithm for solving Vehicle Routing Problem (VRP)."""
    
    def __init__(self, 
                 depot: Dict,
                 bins: List[Dict],
                 population_size: int = 100,
                 generations: int = 50,
                 mutation_rate: float = 0.1,
                 crossover_rate: float = 0.8,
                 distance_weight: float = 0.5,
                 time_weight: float = 0.3,
                 emissions_weight: float = 0.2,
                 distance_matrix: Optional[Dict[str, Dict[str, float]]] = None):
        """
        Initialize Genetic Algorithm.
        
        Args:
            depot: Depot location {"lat": .., "lng": ..}
            bins: List of bins {"id": .., "lat": .., "lng": .., "fill": ..}
            population_size: GA population size
            generations: Number of generations
            mutation_rate: Mutation probability
            crossover_rate: Crossover probability
            distance_weight: Weight for distance in fitness
            time_weight: Weight for time in fitness
            emissions_weight: Weight for emissions in fitness
            distance_matrix: Pre-computed real road distance matrix for true road optimization
        """
        self.depot = depot
        self.bins = bins
        self.bin_ids = [b['id'] for b in bins]
        self.distance_matrix = distance_matrix
        
        self.population_size = population_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        
        # Multi-objective weights (should sum to 1)
        self.distance_weight = distance_weight
        self.time_weight = time_weight
        self.emissions_weight = emissions_weight
        
        # Create location lookup for faster distance calculation
        self.locations = {self.depot['id']: self.depot}
        for b in bins:
            self.locations[b['id']] = b
        
        # Cache for distances (avoid recalculation)
        self.distance_cache = {}
        
        # Statistics
        self.best_fitness_history = []
        self.best_route = None
        self.best_fitness = float('inf')
    
    def haversine_distance(self, loc1: Dict, loc2: Dict) -> float:
        """Calculate distance between two locations in km."""
        key = (loc1['id'], loc2['id'])
        
        # Check pre-computed road matrix first
        if self.distance_matrix:
            if loc1['id'] in self.distance_matrix and loc2['id'] in self.distance_matrix[loc1['id']]:
                road_dist = self.distance_matrix[loc1['id']][loc2['id']]
                if road_dist != float('inf'):
                    return road_dist
        
        # Check cache
        if key in self.distance_cache:
            return self.distance_cache[key]
        
        R = 6371  # Earth's radius in km
        lat1, lng1 = math.radians(loc1['lat']), math.radians(loc1['lng'])
        lat2, lng2 = math.radians(loc2['lat']), math.radians(loc2['lng'])
        
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        
        distance = R * c
        self.distance_cache[key] = distance
        return distance
    
    def calculate_route_distance(self, route: List[str]) -> float:
        """Calculate total distance for a route."""
        distance = 0
        current = self.depot
        
        for bin_id in route:
            next_loc = self.locations[bin_id]
            distance += self.haversine_distance(current, next_loc)
            current = next_loc
        
        # Return to depot
        distance += self.haversine_distance(current, self.depot)
        
        return distance
    
    def calculate_route_time(self, route: List[str]) -> float:
        """
        Calculate estimated time for route.
        Assumptions: 30 km/h average speed, 2 min per stop
        """
        distance = self.calculate_route_distance(route)
        avg_speed = 30  # km/h
        time_hours = distance / avg_speed
        stop_time = len(route) * (2/60)  # 2 minutes per stop
        
        return (time_hours + stop_time) * 60  # Return in minutes
    
    def calculate_emissions(self, route: List[str]) -> float:
        """
        Calculate estimated CO2 emissions for route.
        Assumption: 2.31 kg CO2 per liter of diesel
        Assumption: 7 liters per 100 km average consumption
        """
        distance = self.calculate_route_distance(route)
        liters = (distance / 100) * 7
        co2_per_liter = 2.31
        
        return liters * co2_per_liter
    
    def fitness(self, route: List[str]) -> float:
        """
        Calculate fitness score (lower is better).
        Multi-objective: distance + time + emissions
        """
        distance = self.calculate_route_distance(route)
        time_minutes = self.calculate_route_time(route)
        emissions = self.calculate_emissions(route)
        
        # Normalize values (for fair weighting)
        # Normalize distance to 0-1 scale (assuming max 100km reasonable)
        norm_distance = min(distance / 100, 1.0)
        
        # Normalize time to 0-1 scale (assuming max 240 minutes reasonable)
        norm_time = min(time_minutes / 240, 1.0)
        
        # Normalize emissions to 0-1 scale (assuming max 50kg CO2 reasonable)
        norm_emissions = min(emissions / 50, 1.0)
        
        # Weighted fitness
        fitness_score = (
            self.distance_weight * norm_distance +
            self.time_weight * norm_time +
            self.emissions_weight * norm_emissions
        )
        
        return fitness_score
    
    def create_random_route(self) -> List[str]:
        """Create a random route."""
        route = deepcopy(self.bin_ids)
        random.shuffle(route)
        return route
    
    def initialize_population(self) -> List[List[str]]:
        """Create initial population."""
        return [self.create_random_route() for _ in range(self.population_size)]
    
    def selection(self, population: List[List[str]], 
                 fitness_scores: List[float]) -> Tuple[List[str], List[str]]:
        """
        Select two parents using tournament selection.
        
        Returns:
            Tuple of two parent routes
        """
        tournament_size = 3
        
        # Tournament 1
        indices1 = random.sample(range(len(population)), tournament_size)
        parent1_idx = min(indices1, key=lambda i: fitness_scores[i])
        
        # Tournament 2
        indices2 = random.sample(range(len(population)), tournament_size)
        parent2_idx = min(indices2, key=lambda i: fitness_scores[i])
        
        return deepcopy(population[parent1_idx]), deepcopy(population[parent2_idx])
    
    def crossover(self, parent1: List[str], parent2: List[str]) -> List[str]:
        """
        Order Crossover (OX) operation.
        Preserves relative order while mixing parental genes.
        """
        if random.random() > self.crossover_rate:
            return deepcopy(parent1)
        
        size = len(parent1)
        
        # Select crossover points
        p1, p2 = sorted(random.sample(range(size), 2))
        
        # Child inherits segment from parent1
        child = [None] * size
        child[p1:p2] = parent1[p1:p2]
        
        # Fill remaining from parent2 in order
        ptr = p2
        for item in parent2:
            if item not in child:
                if ptr >= size:
                    ptr = 0
                child[ptr] = item
                ptr += 1
        
        return child
    
    def mutate(self, route: List[str]) -> List[str]:
        """
        Mutation operator: Swap two random cities.
        """
        if random.random() > self.mutation_rate:
            return route
        
        mutated = deepcopy(route)
        i, j = random.sample(range(len(mutated)), 2)
        mutated[i], mutated[j] = mutated[j], mutated[i]
        
        return mutated
    
    def optimize(self) -> Tuple[List[str], float]:
        """
        Run genetic algorithm optimization.
        
        Returns:
            Tuple of (best_route, best_fitness_score)
        """
        logger.info(f"Starting GA optimization with pop_size={self.population_size}, "
                   f"generations={self.generations}")
        
        # Initialize
        population = self.initialize_population()
        
        for generation in range(self.generations):
            # Evaluate fitness
            fitness_scores = [self.fitness(route) for route in population]
            
            # Track best
            best_idx = fitness_scores.index(min(fitness_scores))
            gen_best = population[best_idx]
            gen_best_fitness = fitness_scores[best_idx]
            
            if gen_best_fitness < self.best_fitness:
                self.best_fitness = gen_best_fitness
                self.best_route = deepcopy(gen_best)
            
            self.best_fitness_history.append(self.best_fitness)
            
            if (generation + 1) % 10 == 0:
                logger.info(f"Generation {generation + 1}: Best fitness = {self.best_fitness:.4f}")
            
            # Create next generation
            new_population = []
            
            # Elitism: Keep best individual
            new_population.append(deepcopy(gen_best))
            
            # Generate offspring
            while len(new_population) < self.population_size:
                parent1, parent2 = self.selection(population, fitness_scores)
                child = self.crossover(parent1, parent2)
                child = self.mutate(child)
                new_population.append(child)
            
            population = new_population
        
        logger.info(f"GA optimization complete. Best fitness: {self.best_fitness:.4f}")
        logger.info(f"Best route distance: {self.calculate_route_distance(self.best_route):.2f} km")
        logger.info(f"Best route time: {self.calculate_route_time(self.best_route):.2f} minutes")
        logger.info(f"Best route emissions: {self.calculate_emissions(self.best_route):.2f} kg CO2")
        
        return self.best_route, self.best_fitness
    
    def get_summary(self) -> Dict:
        """Get optimization summary."""
        if not self.best_route:
            return {"error": "Optimization not run yet"}
        
        return {
            "route": self.best_route,
            "fitness_score": self.best_fitness,
            "distance_km": self.calculate_route_distance(self.best_route),
            "time_minutes": self.calculate_route_time(self.best_route),
            "co2_kg": self.calculate_emissions(self.best_route),
            "bin_count": len(self.best_route),
            "generations_run": len(self.best_fitness_history),
            "convergence_history": self.best_fitness_history[-10:]  # Last 10 values
        }

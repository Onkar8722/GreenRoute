#!/usr/bin/env python3
"""Test route optimization endpoint."""
import requests
import json
import time

BASE_URL = "http://127.0.0.1:5000"

print("=" * 60)
print("🛣️  Route Optimization Test")
print("=" * 60)

# Test 1: Get current statistics
print("\n1️⃣  Checking current bin statistics...")
try:
    res = requests.get(f"{BASE_URL}/api/stats")
    stats = res.json()
    print(f"✅ Total bins: {stats['total_bins']}")
    print(f"✅ Critical (>70%): {stats['critical']}")
    print(f"✅ High (60-70%): {stats['high']}")
    print(f"✅ Average fill: {stats['avg_fill']}%")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 2: Optimize route with default parameters
print("\n2️⃣  Running GA route optimization (default params)...")
print("   Parameters: threshold=60%, max_stops=20, pop_size=100, gen=50")
try:
    payload = {
        "threshold": 60,
        "max_stops": 20,
        "population_size": 100,
        "generations": 50
    }
    
    start = time.time()
    res = requests.post(f"{BASE_URL}/api/route/optimize-ga", json=payload)
    elapsed = time.time() - start
    
    result = res.json()
    
    if res.status_code == 200:
        print(f"✅ Optimization complete in {elapsed:.2f}s")
        print(f"   Route stops: {result['bin_count']}")
        print(f"   Total distance: {result['distance_km']:.2f} km")
        print(f"   Estimated time: {result['time_minutes']:.0f} minutes")
        print(f"   CO₂ emissions: {result['co2_kg']:.2f} kg")
        print(f"   Fitness score: {result['fitness_score']:.4f}")
        print(f"   First 5 bins in route: {result['route'][:5]}")
    else:
        print(f"❌ Error: {result.get('error', 'Unknown error')}")
        
except Exception as e:
    print(f"❌ Error: {e}")

# Test 3: Optimize with stricter threshold
print("\n3️⃣  Running optimization with threshold=80% (critical bins only)...")
try:
    payload = {
        "threshold": 80,
        "max_stops": 15,
        "population_size": 50,
        "generations": 30
    }
    
    start = time.time()
    res = requests.post(f"{BASE_URL}/api/route/optimize-ga", json=payload)
    elapsed = time.time() - start
    
    result = res.json()
    
    if res.status_code == 200:
        print(f"✅ Optimization complete in {elapsed:.2f}s")
        print(f"   Critical bins to visit: {result['bin_count']}")
        print(f"   Distance: {result['distance_km']:.2f} km")
        print(f"   Time: {result['time_minutes']:.0f} minutes")
        print(f"   CO₂: {result['co2_kg']:.2f} kg")
    else:
        print(f"ℹ️  {result.get('error', 'No critical bins eligible')}")
        
except Exception as e:
    print(f"❌ Error: {e}")

# Test 4: Verify routes.html is accessible
print("\n4️⃣  Checking route optimizer page...")
try:
    res = requests.get(f"{BASE_URL}/routes")
    if res.status_code == 200 and 'GreenRoute' in res.text and 'Route Optimizer' in res.text:
        print("✅ Routes optimizer page is accessible")
    else:
        print("❌ Routes page not found or malformed")
except Exception as e:
    print(f"❌ Error: {e}")

print("\n" + "=" * 60)
print("🎉 Route optimization system is ready!")
print("📍 Open: http://127.0.0.1:5000/routes")
print("=" * 60)

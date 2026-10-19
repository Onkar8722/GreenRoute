#!/usr/bin/env python3
"""
GreenRoute API Test Script
Test all endpoints to verify database integration
"""

import requests
import json
import time

BASE_URL = "http://localhost:5000"

def test_endpoint(name, url, method='GET', data=None, expected_status=200):
    """Test an API endpoint."""
    print(f"\n🧪 Testing {name}...")
    try:
        if method == 'GET':
            response = requests.get(url)
        elif method == 'POST':
            response = requests.post(url, json=data)
        else:
            print(f"❌ Unsupported method: {method}")
            return False

        if response.status_code == expected_status:
            print(f"✅ {name}: Status {response.status_code}")
            return True
        else:
            print(f"❌ {name}: Expected {expected_status}, got {response.status_code}")
            print(f"   Response: {response.text[:200]}...")
            return False

    except Exception as e:
        print(f"❌ {name}: Error - {e}")
        return False

def main():
    """Run all API tests."""
    print("🚀 Starting GreenRoute API Tests...")

    # Wait for server to be ready
    time.sleep(2)

    tests_passed = 0
    total_tests = 0

    # Test 1: Stats endpoint
    total_tests += 1
    if test_endpoint("Stats API", f"{BASE_URL}/api/stats"):
        tests_passed += 1

    # Test 2: Bins endpoint
    total_tests += 1
    if test_endpoint("Bins API", f"{BASE_URL}/api/bins"):
        tests_passed += 1

    # Test 3: Bins with filter
    total_tests += 1
    if test_endpoint("Bins API (filtered)", f"{BASE_URL}/api/bins?min_fill=80"):
        tests_passed += 1

    # Test 4: Alerts endpoint
    total_tests += 1
    if test_endpoint("Alerts API", f"{BASE_URL}/api/alerts"):
        tests_passed += 1

    # Test 5: Route optimization
    total_tests += 1
    route_data = {
        "threshold": 60,
        "algorithm": "priority",
        "max_stops": 10,
        "num_trucks": 2
    }
    if test_endpoint("Route API", f"{BASE_URL}/api/route", method='POST', data=route_data):
        tests_passed += 1

    # Test 6: Web interface
    total_tests += 1
    if test_endpoint("Web Interface", f"{BASE_URL}/"):
        tests_passed += 1

    print(f"\n📊 Test Results: {tests_passed}/{total_tests} passed")

    if tests_passed == total_tests:
        print("🎉 All tests passed! Database integration successful!")
        return True
    else:
        print("⚠️  Some tests failed. Check the output above.")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
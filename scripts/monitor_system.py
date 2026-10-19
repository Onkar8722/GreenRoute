#!/usr/bin/env python3
"""
GreenRoute System Monitoring Utility

Monitor health of Flask app and all service dependencies in real-time.
Usage: python monitor_system.py
"""

import os
import sys
import json
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# Configuration
API_URL = "http://127.0.0.1:5000"
HEALTH_ENDPOINT = f"{API_URL}/api/health"
REFRESH_INTERVAL = 5  # seconds

# Color codes
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'
CLEAR = '\033[2J'
MOVE_HOME = '\033[H'

def clear_screen():
    """Clear terminal screen"""
    print(CLEAR + MOVE_HOME, end='')

def format_time(timestamp):
    """Format timestamp for display"""
    if isinstance(timestamp, str):
        return timestamp.split('T')[1][:8]  # HH:MM:SS
    return str(timestamp)[:8]

def get_status_color(status):
    """Get color code for status"""
    if status == 'healthy':
        return GREEN
    elif status == 'degraded':
        return YELLOW
    else:
        return RED

def get_service_color(available):
    """Get color code for service availability"""
    return GREEN if available else RED

def check_health():
    """Fetch health status from Flask app"""
    try:
        response = requests.get(HEALTH_ENDPOINT, timeout=2)
        if response.status_code == 200:
            return response.json()
        else:
            return {
                'status': 'error',
                'error': f'HTTP {response.status_code}',
                'timestamp': datetime.now().isoformat()
            }
    except requests.exceptions.ConnectionError:
        return {
            'status': 'offline',
            'error': 'Cannot connect to Flask app',
            'timestamp': datetime.now().isoformat()
        }
    except requests.exceptions.Timeout:
        return {
            'status': 'timeout',
            'error': 'Request timed out',
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }

def display_header():
    """Display header"""
    print(f"\n{BOLD}{BLUE}GreenRoute System Monitor{RESET}")
    print("=" * 70)
    print(f"Monitoring {API_URL}")
    print("=" * 70)

def display_health(health: Dict[str, Any]):
    """Display health status"""
    status = health.get('status', 'unknown')
    status_color = get_status_color(status)
    
    print(f"\n{BOLD}System Status:{RESET}")
    print(f"  {status_color}● {status.upper()}{RESET}")
    print(f"  Last updated: {format_time(health.get('timestamp', 'N/A'))}")
    
    if 'error' in health:
        print(f"  {RED}Error: {health['error']}{RESET}")
    
    return status in ['healthy', 'degraded']

def display_services(health: Dict[str, Any]):
    """Display service status"""
    print(f"\n{BOLD}Service Status:{RESET}")
    
    services = {
        'database': 'PostgreSQL',
        'cache': 'Redis',
        'mqtt': 'MQTT Broker',
    }
    
    for key, name in services.items():
        available = health.get(key, False)
        color = get_service_color(available)
        status_text = "Connected" if available else "Offline"
        print(f"  {color}● {name:20} {status_text}{RESET}")

def display_endpoints(health: Dict[str, Any]):
    """Display endpoint status"""
    print(f"\n{BOLD}API Endpoints:{RESET}")
    
    endpoints = {
        '/api/stats': 'System statistics',
        '/api/bins': 'All bins',
        '/api/alerts': 'Alerts',
        '/api/forecast': 'Forecast',
        '/api/route/optimize': 'Route optimization',
    }
    
    status = health.get('status', 'offline')
    
    for endpoint, desc in endpoints.items():
        if status in ['healthy', 'degraded']:
            print(f"  {GREEN}✓ {endpoint:25} {desc}{RESET}")
            print(f"      {BLUE}curl http://127.0.0.1:5000{endpoint}{RESET}")
        else:
            print(f"  {RED}✗ {endpoint:25} (app offline){RESET}")

def display_quick_test():
    """Display quick test commands"""
    print(f"\n{BOLD}Quick Test Commands:{RESET}")
    
    tests = [
        ("Health check", "curl http://127.0.0.1:5000/api/health"),
        ("System stats", "curl http://127.0.0.1:5000/api/stats"),
        ("List bins", "curl http://127.0.0.1:5000/api/bins"),
        ("Test GA routing", "curl -X POST http://127.0.0.1:5000/api/route/optimize-ga -H 'Content-Type: application/json' -d '{\"num_bins\": 10}'"),
    ]
    
    for idx, (name, cmd) in enumerate(tests, 1):
        print(f"\n  {idx}. {name}:")
        print(f"     {BLUE}{cmd}{RESET}")

def display_troubleshooting(health: Dict[str, Any]):
    """Display troubleshooting tips"""
    print(f"\n{BOLD}Troubleshooting:{RESET}")
    
    status = health.get('status', 'offline')
    
    if status == 'offline':
        print(f"  {RED}Flask app is not running{RESET}")
        print(f"    1. Make sure Docker containers are running:")
        print(f"       {BLUE}docker ps{RESET}")
        print(f"    2. Start the Flask app:")
        print(f"       {BLUE}python app.py{RESET}")
    
    elif status == 'degraded':
        database = health.get('database', False)
        cache = health.get('cache', False)
        mqtt = health.get('mqtt', False)
        
        if not database:
            print(f"  {YELLOW}PostgreSQL not connected{RESET}")
            print(f"    - Check: {BLUE}docker ps | grep postgres{RESET}")
            print(f"    - Restart: {BLUE}docker restart greenroute-postgres{RESET}")
        
        if not cache:
            print(f"  {YELLOW}Redis not connected{RESET}")
            print(f"    - Check: {BLUE}docker ps | grep redis{RESET}")
            print(f"    - Restart: {BLUE}docker restart greenroute-redis{RESET}")
        
        if not mqtt:
            print(f"  {YELLOW}MQTT not connected{RESET}")
            print(f"    - Check: {BLUE}docker ps | grep mqtt{RESET}")
            print(f"    - Restart: {BLUE}docker restart greenroute-mqtt{RESET}")
    
    elif status == 'healthy':
        print(f"  {GREEN}All systems operational!{RESET}")

def display_stats():
    """Display system statistics"""
    try:
        response = requests.get(f"{API_URL}/api/stats", timeout=2)
        if response.status_code == 200:
            stats = response.json()
            print(f"\n{BOLD}System Statistics:{RESET}")
            
            if isinstance(stats, dict):
                for key, value in stats.items():
                    if key == 'total_distance_km':
                        print(f"  {key:20} {value:>10.2f} km")
                    elif key == 'avg_fill_level':
                        print(f"  {key:20} {value:>10.1f}%")
                    elif key == 'timestamp' or 'time' in key.lower():
                        print(f"  {key:20} {format_time(str(value))}")
                    else:
                        print(f"  {key:20} {value:>10}")
    except:
        pass

def display_footer():
    """Display footer with refresh info"""
    print(f"\n{'=' * 70}")
    print(f"{YELLOW}Refreshing every {REFRESH_INTERVAL} seconds... (Press Ctrl+C to exit){RESET}")
    print()

def main():
    """Main monitoring loop"""
    iteration = 0
    
    try:
        while True:
            # Fetch data
            health = check_health()
            
            # Display
            clear_screen()
            display_header()
            display_health(health)
            
            if health.get('status') != 'offline':
                display_services(health)
                display_stats()
                display_endpoints(health)
            else:
                display_endpoints(health)
            
            display_troubleshooting(health)
            display_quick_test()
            display_footer()
            
            # Wait before next refresh
            iteration += 1
            time.sleep(REFRESH_INTERVAL)
    
    except KeyboardInterrupt:
        print(f"\n{BLUE}Monitor stopped.{RESET}\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n{RED}Error: {e}{RESET}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()

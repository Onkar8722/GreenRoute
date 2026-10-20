#!/usr/bin/env python3
"""
GreenRoute Environment Verification Script

Checks for all required dependencies and services before running the system.
Usage: python verify_setup.py
"""

import os
import sys
import subprocess
import importlib.util
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

def log_success(message):
    print(f"{GREEN}✅ {message}{RESET}")

def log_error(message):
    print(f"{RED}❌ {message}{RESET}")

def log_warning(message):
    print(f"{YELLOW}⚠️  {message}{RESET}")

def log_info(message):
    print(f"{BLUE}ℹ️  {message}{RESET}")

def section(title):
    print(f"\n{BOLD}{BLUE}{title}{RESET}")
    print("=" * 60)

def check_python_version():
    """Verify Python 3.10+"""
    section("Python Version")
    version = sys.version_info
    version_str = f"{version.major}.{version.minor}.{version.micro}"
    
    if version.major >= 3 and version.minor >= 10:
        log_success(f"Python {version_str}")
        return True
    else:
        log_error(f"Python {version_str} (requires 3.10+)")
        return False

def check_python_packages():
    """Verify all required Python packages are installed"""
    section("Python Packages")
    
    required_packages = {
        'flask': 'Flask (web framework)',
        'psycopg2': 'psycopg2 (PostgreSQL driver)',
        'redis': 'redis (cache client)',
        'pydantic': 'Pydantic (validation)',
        'paho.mqtt': 'paho-mqtt (IoT client)',
        'sklearn': 'scikit-learn (ML)',
        'numpy': 'numpy (numerics)',
        'dotenv': 'python-dotenv (env support)',
        'osmnx': 'OSMnx (road routing)',
        'networkx': 'NetworkX (road routing)',
        'requests': 'requests (monitoring and live smoke scripts)',
    }
    
    all_present = True
    for module_name, display_name in required_packages.items():
        if importlib.util.find_spec(module_name):
            log_success(f"{display_name}")
        else:
            log_error(f"{display_name} - MISSING")
            log_info(f"  Install with: pip install {module_name}")
            all_present = False
    
    return all_present

def check_docker():
    """Verify Docker is installed"""
    section("Docker Setup")
    
    try:
        result = subprocess.run(['docker', '--version'], 
                              capture_output=True, 
                              text=True,
                              timeout=5)
        if result.returncode == 0:
            version = result.stdout.strip()
            log_success(f"{version}")
            
            # Check if daemon is running
            try:
                subprocess.run(['docker', 'ps'], 
                             capture_output=True, 
                             text=True,
                             timeout=5)
                log_success("Docker daemon is running")
                return True
            except:
                log_warning("Docker is installed but daemon is not running")
                log_info("  Start Docker Desktop to enable container services")
                return False
        else:
            log_error("Docker command failed")
            return False
    except FileNotFoundError:
        log_error("Docker is not installed")
        log_info("  Download from: https://www.docker.com/products/docker-desktop")
        return False
    except subprocess.TimeoutExpired:
        log_error("Docker command timed out")
        log_info("  Docker daemon may not be running")
        return False

def check_required_files():
    """Verify all required project files exist"""
    section("Project Files")
    
    base_dir = Path(__file__).parent
    required_files = {
        'app.py': 'Flask application',
        'config.py': 'Configuration module',
        'models.py': 'Pydantic models',
        'cache.py': 'Redis cache layer',
        'db.py': 'Database manager',
        'mqtt_listener.py': 'MQTT listener',
        'genetic_algorithm.py': 'GA optimizer',
        'test_routing.py': 'Unit tests',
        'requirements.txt': 'Dependencies list',
        'database_schema.sql': 'SQL schema',
        'data/pune_smart_bin_dataset.csv': 'Sample data',
    }
    
    all_present = True
    for filename, description in required_files.items():
        filepath = base_dir / filename
        if filepath.exists():
            log_success(f"{description}")
        else:
            log_error(f"{description} - MISSING: {filename}")
            all_present = False
    
    return all_present

def check_ports():
    """Check if required ports are available"""
    section("Network Ports")
    
    import socket
    
    ports = {
        5432: 'PostgreSQL',
        6379: 'Redis',
        1883: 'MQTT',
        5000: 'Flask application',
    }
    
    available_ports = []
    unavailable_ports = []
    
    for port, service in ports.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            result = sock.connect_ex(('localhost', port))
            if result == 0:
                log_warning(f"Port {port} ({service}) - IN USE")
                unavailable_ports.append(port)
            else:
                log_success(f"Port {port} ({service}) - available")
                available_ports.append(port)
        finally:
            sock.close()
    
    if unavailable_ports:
        log_warning(f"Some services may already be running. If starting fresh, stop existing containers:")
        for port in unavailable_ports:
            log_info(f"  docker ps (to find container)")
            log_info(f"  docker stop <container-id>")
        return False
    return True

def check_environment_file():
    """Check for .env configuration file"""
    section(".env Configuration")
    
    env_file = Path(__file__).parent / '.env'
    env_example = Path(__file__).parent / '.env.example'
    
    if env_file.exists():
        log_success(".env file found (will use custom settings)")
        return True
    elif env_example.exists():
        log_info(".env.example found but not used (.env not present)")
        log_info("  Using default configuration from config.py")
        return True
    else:
        log_info("No .env file (using defaults from config.py)")
        return True

def check_database_connectivity():
    """Test PostgreSQL connectivity (if available)"""
    section("Database Connectivity")
    
    try:
        import psycopg2
        from config.config import Config
        
        try:
            conn = psycopg2.connect(
                host=Config.DB_CONFIG['host'],
                port=Config.DB_CONFIG['port'],
                user=Config.DB_CONFIG['user'],
                password=Config.DB_CONFIG['password'],
                database=Config.DB_CONFIG['database'],
                connect_timeout=2
            )
            conn.close()
            log_success("PostgreSQL connection successful")
            return True
        except psycopg2.OperationalError:
            log_warning("PostgreSQL not responding (container may not be started)")
            log_info("  Run: .\\start_services.ps1 (Windows) or bash start_services.sh (Mac/Linux)")
            return False
    except ImportError:
        log_warning("psycopg2 not imported yet")
        return False

def check_redis_connectivity():
    """Test Redis connectivity (if available)"""
    section("Redis Connectivity")
    
    try:
        import redis
        from config.config import Config
        
        try:
            r = redis.Redis(
                host=Config.REDIS_HOST,
                port=Config.REDIS_PORT,
                socket_connect_timeout=2,
                decode_responses=True
            )
            r.ping()
            log_success("Redis connection successful")
            return True
        except:
            log_warning("Redis not responding (optional service)")
            log_info("  App will work without caching (slower)")
            return False
    except ImportError:
        log_warning("redis not imported yet")
        return False

def show_next_steps(issues):
    """Show next steps based on environment check results"""
    section("Next Steps")
    
    if not issues:
        log_success("✅ All checks passed! Ready to start services.\n")
        print(f"{BLUE}Run the following commands:{RESET}")
        print(f"\n1. {BOLD}Windows (PowerShell):{RESET}")
        print(f"   .\\start_services.ps1\n")
        print(f"2. {BOLD}Mac/Linux (Bash):{RESET}")
        print(f"   bash start_services.sh\n")
        print(f"3. {BOLD}Wait for services to start (10-15 seconds){RESET}\n")
        print(f"4. {BOLD}Initialize database:{RESET}")
        print(f"   python init_db.py\n")
        print(f"5. {BOLD}Start Flask app (in new terminal):{RESET}")
        print(f"   python app.py\n")
        print(f"6. {BOLD}Test endpoints:{RESET}")
        print(f"   curl http://127.0.0.1:5000/api/health\n")
    else:
        log_warning(f"⚠️  {len(issues)} issue(s) found. Please fix before starting:\n")
        for i, issue in enumerate(issues, 1):
            print(f"{YELLOW}{i}. {issue}{RESET}")
        print()

def main():
    """Run all checks"""
    print(f"\n{BOLD}{BLUE}GreenRoute Environment Verification{RESET}")
    print("=" * 60)
    print("This script checks if your environment is ready for GreenRoute.\n")
    
    issues = []
    
    # Run checks
    checks = [
        ("Python Version", check_python_version),
        ("Python Packages", check_python_packages),
        ("Required Files", check_required_files),
        ("Docker Installation", check_docker),
        ("Network Ports", check_ports),
        ("Environment Configuration", check_environment_file),
    ]
    
    # Optional checks (don't fail if they fail)
    optional_checks = [
        ("PostgreSQL Connectivity", check_database_connectivity),
        ("Redis Connectivity", check_redis_connectivity),
    ]
    
    for check_name, check_func in checks:
        try:
            if not check_func():
                issues.append(f"❌ {check_name} check failed")
        except Exception as e:
            log_error(f"Error running {check_name}: {e}")
            issues.append(f"⚠️  {check_name} check encountered an error")
    
    # Run optional checks
    for check_name, check_func in optional_checks:
        try:
            check_func()
        except Exception as e:
            log_info(f"Skipping {check_name}: {e}")
    
    # Summary
    show_next_steps(issues)
    
    return 0 if not issues else 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)

#!/usr/bin/env python
"""Test if imports work correctly after reorganization"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("Testing imports...")
print("-" * 50)

try:
    print("✓ Importing config...")
    from config.config import Config, get_config
    print("  ✅ Config imported successfully")
except Exception as e:
    print(f"  ❌ Config import failed: {e}")
    sys.exit(1)

try:
    print("✓ Importing Flask app modules...")
    from src.cache import get_cache
    from src.db import DatabaseManager
    from src.models import BinResponse
    print("  ✅ Flask modules imported successfully")
except Exception as e:
    print(f"  ❌ Flask modules import failed: {e}")
    sys.exit(1)

try:
    print("✓ Importing ML modules...")
    from src.genetic_algorithm import GeneticAlgorithm
    from src.forecasting import WasteForecaster
    from src.collection_window_predictor import CollectionWindowPredictor
    print("  ✅ ML modules imported successfully")
except Exception as e:
    print(f"  ❌ ML modules import failed: {e}")
    sys.exit(1)

try:
    print("✓ Importing Flask app...")
    from src.app import app
    print("  ✅ Flask app imported successfully")
except Exception as e:
    print(f"  ❌ Flask app import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("-" * 50)
print("✅ All imports successful!")
print("\nYou can now run: python src/app.py")

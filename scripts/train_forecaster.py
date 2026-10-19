#!/usr/bin/env python3
"""
Train the forecasting model with historical data.
"""

import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.forecasting import WasteForecaster
from config.config import Config

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'greenroute_db',
    'user': 'greenroute_user',
    'password': 'greenroute_pass'
}

if __name__ == "__main__":
    print("Training SVM forecasting model...")
    forecaster = WasteForecaster(DB_CONFIG)
    
    if forecaster.train(days=30):
        print("✅ Model trained and saved successfully!")
        
        # Test prediction
        print("\nTesting predictions...")
        forecast = forecaster.forecast_all_bins(hours_ahead=24)
        
        if forecast:
            print(f"✅ Generated forecasts for {len(forecast)} bins")
            
            # Show sample
            sample_bin = list(forecast.keys())[0]
            print(f"\nSample forecast for bin {sample_bin}:")
            for pred in forecast[sample_bin][:6]:
                print(f"  {pred['hour_ahead']}h: {pred['predicted_fill']:.1f}% fill")
    else:
        print("❌ Failed to train model")

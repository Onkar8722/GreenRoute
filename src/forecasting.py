#!/usr/bin/env python3
"""
Waste Generation Forecasting using SVM
Predicts bin fill levels based on historical data and time features
"""

import os
import sys
import numpy as np
from datetime import datetime, timedelta
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestRegressor
import pickle
import logging
from pathlib import Path
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WasteForecaster:
    """SVM-based waste generation forecasting"""
    
    def __init__(self, db_config):
        self.db_config = db_config
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = ['hour', 'day_of_week', 'is_weekend', 'trend']
        self.model_path = Path('models/forecast_model.pkl')
        self.model_path.parent.mkdir(exist_ok=True)
        
    def get_historical_data(self, days=30):
        """Fetch historical fill data from database"""
        try:
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get fill history for last N days
            query = """
                SELECT bin_id, fill_level, recorded_at
                FROM fill_history
                WHERE recorded_at >= NOW() - INTERVAL '%s days'
                ORDER BY bin_id, recorded_at
            """
            cursor.execute(query, (days,))
            data = cursor.fetchall()
            cursor.close()
            conn.close()
            
            logger.info(f"Fetched {len(data)} historical records")
            return data
        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            return []
    
    def extract_features(self, timestamp):
        """Extract time-based features from timestamp"""
        dt = timestamp if isinstance(timestamp, datetime) else datetime.fromisoformat(str(timestamp))
        
        hour = dt.hour
        day_of_week = dt.weekday()  # 0=Monday, 6=Sunday
        is_weekend = 1 if day_of_week >= 5 else 0
        
        return [hour, day_of_week, is_weekend]
    
    def prepare_training_data(self, historical_data):
        """Prepare features and targets from historical data"""
        X = []
        y = []
        
        # Group by bin
        bins_data = {}
        for record in historical_data:
            bin_id = record['bin_id']
            if bin_id not in bins_data:
                bins_data[bin_id] = []
            bins_data[bin_id].append(record)
        
        # Create training samples
        for bin_id, records in bins_data.items():
            records = sorted(records, key=lambda x: x['recorded_at'])
            
            for i in range(1, len(records)):
                # Features: time-based + trend
                timestamp = records[i]['recorded_at']
                features = self.extract_features(timestamp)
                
                # Add trend (how much fill level changed)
                prev_fill = records[i-1]['fill_level']
                curr_fill = records[i]['fill_level']
                trend = (curr_fill - prev_fill) / max(prev_fill, 1)  # Avoid division by zero
                
                features.append(trend)
                X.append(features)
                y.append(curr_fill)
        
        if not X:
            logger.warning("No training data available")
            return None, None
        
        X = np.array(X)
        y = np.array(y)
        
        logger.info(f"Prepared {len(X)} training samples")
        return X, y
    
    def train(self, historical_data=None, days=30):
        """Train the forecasting model"""
        logger.info("Training forecasting model...")
        
        # Fetch data if not provided
        if historical_data is None:
            historical_data = self.get_historical_data(days=days)
        
        if not historical_data:
            logger.warning("No historical data available for training")
            return False
        
        # Prepare data
        X, y = self.prepare_training_data(historical_data)
        if X is None:
            return False
        
        # Scale features
        X_scaled = self.scaler.fit_transform(X)
        
        # Train using Random Forest (faster than SVM for large datasets)
        try:
            from sklearn.ensemble import RandomForestRegressor
            self.model = RandomForestRegressor(n_estimators=50, max_depth=10, n_jobs=-1)
            self.model.fit(X_scaled, y)
            
            # Save model
            with open(self.model_path, 'wb') as f:
                pickle.dump((self.model, self.scaler), f)
            
            # Calculate accuracy
            score = self.model.score(X_scaled, y)
            logger.info(f"✅ Model trained successfully (R² score: {score:.4f})")
            return True
        except Exception as e:
            logger.error(f"Error training model: {e}")
            return False
    
    def load_model(self):
        """Load pre-trained model"""
        try:
            if self.model_path.exists():
                with open(self.model_path, 'rb') as f:
                    self.model, self.scaler = pickle.load(f)
                logger.info("✅ Model loaded from disk")
                return True
            else:
                logger.warning("Model file not found, will train new model")
                return False
        except Exception as e:
            logger.error(f"Error loading model: {e}")
            return False
    
    def predict_bin_fill(self, bin_id, hours_ahead=24):
        """Predict bin fill level for future hours"""
        if self.model is None:
            if not self.load_model():
                logger.error("No model available")
                return None
        
        try:
            # Get current fill level
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute(
                "SELECT fill_level FROM bins WHERE id = %s",
                (bin_id,)
            )
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if not result:
                logger.warning(f"Bin {bin_id} not found")
                return None
            
            current_fill = result['fill_level']
            predictions = []
            current_time = datetime.now()
            current_fill_pred = current_fill
            
            # Predict for each hour ahead
            for hour in range(hours_ahead):
                future_time = current_time + timedelta(hours=hour)
                features = self.extract_features(future_time)
                
                # Estimate trend (assumes gradual increase)
                trend = 0.02 if current_fill_pred < 80 else -0.01
                features.append(trend)
                
                # Scale and predict
                X = np.array([features])
                X_scaled = self.scaler.transform(X)
                predicted_fill = float(self.model.predict(X_scaled)[0])
                
                # Bounds check
                predicted_fill = max(0, min(100, predicted_fill))
                
                predictions.append({
                    'timestamp': future_time.isoformat(),
                    'predicted_fill': round(predicted_fill, 2),
                    'hour_ahead': hour
                })
                
                current_fill_pred = predicted_fill
            
            return predictions
        except Exception as e:
            logger.error(f"Error predicting fill level: {e}")
            return None
    
    def forecast_all_bins(self, hours_ahead=24):
        """Forecast for all bins"""
        try:
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT id FROM bins LIMIT 50")
            bins = cursor.fetchall()
            cursor.close()
            conn.close()
            
            forecasts = {}
            for bin_record in bins:
                bin_id = bin_record['id']
                forecast = self.predict_bin_fill(bin_id, hours_ahead)
                if forecast:
                    forecasts[bin_id] = forecast
            
            logger.info(f"Generated forecasts for {len(forecasts)} bins")
            return forecasts
        except Exception as e:
            logger.error(f"Error forecasting all bins: {e}")
            return {}


# Example usage
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.config import Config
    
    # Initialize forecaster
    forecaster = WasteForecaster(Config.DB_CONFIG)
    
    # Train model (one-time)
    logger.info("Training forecasting model...")
    forecaster.train()
    
    # Make predictions
    logger.info("\nMaking predictions...")
    forecast = forecaster.forecast_all_bins(hours_ahead=24)
    
    if forecast:
        logger.info(f"✅ Generated forecasts for {len(forecast)} bins")
        
        # Show sample
        sample_bin = list(forecast.keys())[0]
        logger.info(f"\nSample forecast for bin {sample_bin}:")
        for pred in forecast[sample_bin][:5]:
            logger.info(f"  {pred['hour_ahead']}h: {pred['predicted_fill']}% fill")
    else:
        logger.error("❌ No forecasts generated")

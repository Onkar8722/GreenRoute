"""
Machine Learning module for predicting optimal collection windows.
Uses historical fill data to predict when bins will reach capacity.
"""

import pickle
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import logging
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

class CollectionWindowPredictor:
    """Predicts optimal collection times for each bin based on historical patterns."""
    
    def __init__(self, db_config):
        self.db_config = db_config
        self.model = None
        self.scaler = StandardScaler()
        self.feature_names = [
            'hour_of_day', 'day_of_week', 'is_weekend', 
            'current_fill', 'historical_avg_fill_rate', 'days_since_last_collection'
        ]
        self.model_path = 'models/collection_window_model.pkl'
        self.scaler_path = 'models/collection_window_scaler.pkl'
    
    def _get_db_connection(self):
        """Get database connection."""
        return psycopg2.connect(**self.db_config)
    
    def extract_features(self, bin_id: str, current_data: Dict) -> np.ndarray:
        """Extract features for prediction."""
        now = datetime.now()
        
        # Get historical data for this bin
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get fill rate over last 30 days
            cursor.execute("""
                SELECT AVG(fill_level) as avg_fill, 
                       (MAX(fill_level) - MIN(fill_level)) / 30 as daily_rate
                FROM fill_history
                WHERE bin_id = %s AND recorded_at > NOW() - INTERVAL '30 days'
            """, (bin_id,))
            
            hist = cursor.fetchone()
            cursor.close()
            conn.close()
            
            avg_fill_rate = hist['daily_rate'] if hist and hist['daily_rate'] else 1.5
            
        except Exception as e:
            logger.warning(f"Error getting historical data for {bin_id}: {e}")
            avg_fill_rate = 1.5  # Default 1.5% per day
        
        # Extract temporal features
        hour_of_day = now.hour
        day_of_week = now.weekday()
        is_weekend = 1 if day_of_week >= 5 else 0
        
        current_fill = current_data.get('fill', 50)
        days_since_collection = current_data.get('days_since_collection', 3)
        
        features = np.array([
            hour_of_day,
            day_of_week,
            is_weekend,
            current_fill,
            avg_fill_rate,
            days_since_collection
        ]).reshape(1, -1)
        
        return features
    
    def train(self, days_of_history: int = 30):
        """Train model on historical fill data."""
        logger.info(f"Training collection window predictor on {days_of_history} days of history...")
        
        try:
            conn = self._get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Load historical data
            cursor.execute("""
                SELECT 
                    fh.bin_id,
                    EXTRACT(HOUR FROM fh.recorded_at) as hour_of_day,
                    EXTRACT(DOW FROM fh.recorded_at) as day_of_week,
                    CASE WHEN EXTRACT(DOW FROM fh.recorded_at) >= 5 THEN 1 ELSE 0 END as is_weekend,
                    fh.fill_level as current_fill,
                    LAG(fh.fill_level) OVER (PARTITION BY fh.bin_id ORDER BY fh.recorded_at) as prev_fill,
                    EXTRACT(EPOCH FROM (fh.recorded_at - LAG(fh.recorded_at) OVER (PARTITION BY fh.bin_id ORDER BY fh.recorded_at))) / 86400 as days_since_last
                FROM fill_history fh
                WHERE fh.recorded_at > NOW() - (%s * INTERVAL '1 day')
                ORDER BY fh.bin_id, fh.recorded_at
                LIMIT 50000
            """, (days_of_history,))
            
            data = cursor.fetchall()
            cursor.close()
            conn.close()
            
            if not data:
                logger.warning("No historical data found for training")
                return False
            
            X = []
            y = []
            
            for row in data:
                if row['prev_fill'] is None:
                    continue
                
                # Feature: how much will it take to reach 90% from current fill?
                days_to_full = max(1, (90 - row['current_fill']) / (row['current_fill'] - row['prev_fill'] + 0.1))
                
                features = [
                    row['hour_of_day'],
                    row['day_of_week'],
                    row['is_weekend'],
                    row['current_fill'],
                    (row['current_fill'] - row['prev_fill']) / (row['days_since_last'] + 0.1),
                    row['days_since_last']
                ]
                
                X.append(features)
                y.append(days_to_full)
            
            X = np.array(X)
            y = np.array(y)
            
            # Normalize features
            X_scaled = self.scaler.fit_transform(X)
            
            # Train model
            self.model = RandomForestRegressor(
                n_estimators=50,
                max_depth=10,
                random_state=42,
                n_jobs=-1
            )
            self.model.fit(X_scaled, y)
            
            # Save model
            with open(self.model_path, 'wb') as f:
                pickle.dump(self.model, f)
            with open(self.scaler_path, 'wb') as f:
                pickle.dump(self.scaler, f)
            
            logger.info(f"✅ Training complete. Model saved. Accuracy: {self.model.score(X_scaled, y):.3f}")
            return True
            
        except Exception as e:
            logger.error(f"Training error: {e}")
            return False
    
    def load_model(self):
        """Load pre-trained model."""
        try:
            with open(self.model_path, 'rb') as f:
                self.model = pickle.load(f)
            with open(self.scaler_path, 'rb') as f:
                self.scaler = pickle.load(f)
            logger.info("✅ Collection window model loaded")
            return True
        except FileNotFoundError:
            logger.warning("Model not found, will need training")
            return False
    
    def predict_fill_time(self, bin_id: str, current_data: Dict) -> Dict:
        """
        Predict when a bin will reach capacity (90%).
        
        Returns:
            {
                'bin_id': str,
                'current_fill': float,
                'predicted_days_to_full': float,
                'predicted_full_datetime': datetime,
                'optimal_collection_window': {'start': datetime, 'end': datetime},
                'confidence': float,
                'recommended_frequency': str
            }
        """
        if self.model is None:
            return None
        
        try:
            features = self.extract_features(bin_id, current_data)
            features_scaled = self.scaler.transform(features)
            
            days_to_full = max(0.5, self.model.predict(features_scaled)[0])
            predicted_full = datetime.now() + timedelta(days=days_to_full)
            
            # Optimal collection window: 2 days before it gets full (80%+)
            current_fill = current_data.get('fill', 50)
            fill_rate = (90 - current_fill) / max(days_to_full, 0.1)
            days_to_80_percent = (80 - current_fill) / max(fill_rate, 0.1)
            
            window_start = datetime.now() + timedelta(days=max(0, days_to_80_percent - 1))
            window_end = predicted_full
            
            # Determine recommended frequency
            if days_to_full < 2:
                frequency = 'daily'
            elif days_to_full < 4:
                frequency = 'every_2_days'
            elif days_to_full < 8:
                frequency = 'every_3_days'
            else:
                frequency = 'weekly'
            
            # Get feature importance for confidence
            feature_importance = self.model.feature_importances_
            confidence = float(np.mean([feature_importance[i] for i in range(len(feature_importance))]))
            confidence = min(0.95, max(0.6, confidence))  # Clamp between 0.6 and 0.95
            
            return {
                'bin_id': bin_id,
                'current_fill': current_fill,
                'predicted_days_to_full': round(days_to_full, 1),
                'predicted_full_datetime': predicted_full.isoformat(),
                'optimal_collection_window': {
                    'start': window_start.isoformat(),
                    'end': window_end.isoformat()
                },
                'confidence': round(confidence, 2),
                'recommended_frequency': frequency
            }
        
        except Exception as e:
            logger.error(f"Prediction error for {bin_id}: {e}")
            return None
    
    def predict_all_bins(self, bins_data: List[Dict]) -> List[Dict]:
        """Predict collection windows for all bins."""
        predictions = []
        for bin_data in bins_data:
            pred = self.predict_fill_time(bin_data['id'], bin_data)
            if pred:
                predictions.append(pred)
        return predictions

#!/usr/bin/env python3
"""
Generate historical fill data for training the forecasting model.
"""

import psycopg2
import random
from datetime import datetime, timedelta

DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'greenroute_db',
    'user': 'greenroute_user',
    'password': 'greenroute_pass'
}

def generate_fill_history():
    """Generate historical fill data for training."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        print("Generating historical fill data...")
        
        # Get all bins
        cursor.execute("SELECT id FROM bins LIMIT 50")
        bins = cursor.fetchall()
        
        total_records = 0
        
        # For each bin, generate 30 days of hourly data
        for bin_record in bins:
            bin_id = bin_record[0]
            
            # Start from 30 days ago
            current_time = datetime.now() - timedelta(days=30)
            end_time = datetime.now()
            
            current_fill = random.randint(20, 50)  # Start with random fill level
            
            while current_time < end_time:
                # Simulate fill level changes (gradually increase, occasional drops)
                if random.random() < 0.7:  # 70% chance of slight increase
                    current_fill += random.uniform(0.5, 2.0)
                else:  # 30% chance of decrease (collection)
                    current_fill = random.randint(10, 40)
                
                # Keep within bounds
                current_fill = max(0, min(100, current_fill))
                
                # Insert record
                cursor.execute(
                    "INSERT INTO fill_history (bin_id, fill_level, recorded_at) VALUES (%s, %s, %s)",
                    (bin_id, round(current_fill, 1), current_time)
                )
                
                total_records += 1
                current_time += timedelta(hours=1)
        
        conn.commit()
        print(f"✅ Generated {total_records} historical records")
        
        cursor.close()
        conn.close()
        return True
    
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


if __name__ == "__main__":
    generate_fill_history()

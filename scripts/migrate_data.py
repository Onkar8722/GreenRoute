#!/usr/bin/env python3
"""
GreenRoute Data Migration Script
Migrate CSV data to PostgreSQL database
"""

import csv
import os
import psycopg2
from datetime import datetime, timedelta
import random

# Database connection parameters
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'greenroute_db',
    'user': 'greenroute_user',
    'password': 'greenroute_pass'
}

def get_db_connection():
    """Create database connection."""
    return psycopg2.connect(**DB_CONFIG)

def migrate_bins_data(csv_path: str):
    """Migrate bins data from CSV to PostgreSQL."""
    print(f"📂 Migrating bins data from {csv_path}...")

    bins_data = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get('bin_id'):
                continue

            # Parse and validate data
            try:
                bin_data = {
                    'id': row['bin_id'].strip(),
                    'latitude': float(row['latitude']),
                    'longitude': float(row['longitude']),
                    'fill_level': int(float(row['fill_level'])),
                    'status': int(row.get('status', 0)),
                    'battery_level': random.randint(80, 100),  # Simulate battery
                    'signal_strength': random.randint(-85, -45),  # Simulate signal
                    'bin_type': random.choice(['residential', 'commercial', 'industrial']),
                    'capacity_liters': random.choice([120, 240, 360]),
                    'last_updated': datetime.now(),
                    'created_at': datetime.now()
                }
                bins_data.append(bin_data)
            except (ValueError, KeyError) as e:
                print(f"⚠️  Skipping invalid row: {e}")
                continue

    # Insert into database
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Clear existing data
        cursor.execute("TRUNCATE TABLE bins CASCADE")

        # Insert bins
        for bin_data in bins_data:
            cursor.execute("""
                INSERT INTO bins (id, latitude, longitude, fill_level, status,
                                battery_level, signal_strength, bin_type,
                                capacity_liters, last_updated, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    fill_level = EXCLUDED.fill_level,
                    status = EXCLUDED.status,
                    battery_level = EXCLUDED.battery_level,
                    signal_strength = EXCLUDED.signal_strength,
                    last_updated = EXCLUDED.last_updated
            """, (
                bin_data['id'], bin_data['latitude'], bin_data['longitude'],
                bin_data['fill_level'], bin_data['status'], bin_data['battery_level'],
                bin_data['signal_strength'], bin_data['bin_type'],
                bin_data['capacity_liters'], bin_data['last_updated'],
                bin_data['created_at']
            ))

        conn.commit()
        print(f"✅ Migrated {len(bins_data)} bins successfully!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def generate_historical_data():
    """Generate sample historical fill data for analytics."""
    print("📊 Generating historical fill data...")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get all bin IDs
        cursor.execute("SELECT id FROM bins")
        bin_ids = [row[0] for row in cursor.fetchall()]

        # Generate 30 days of historical data
        base_date = datetime.now() - timedelta(days=30)

        historical_records = []
        for bin_id in bin_ids:
            for day in range(31):  # 31 days
                for hour in [6, 12, 18, 22]:  # 4 readings per day
                    record_date = base_date + timedelta(days=day, hours=hour)

                    # Simulate realistic fill patterns
                    base_fill = random.randint(10, 90)
                    # Add some noise and trends
                    fill_variation = random.randint(-10, 10)
                    time_factor = 1.2 if hour in [18, 22] else 0.8  # Higher in evening
                    final_fill = max(0, min(100, int(base_fill * time_factor + fill_variation)))

                    historical_records.append((bin_id, final_fill, record_date))

        # Insert historical data
        cursor.executemany("""
            INSERT INTO fill_history (bin_id, fill_level, recorded_at)
            VALUES (%s, %s, %s)
        """, historical_records)

        conn.commit()
        print(f"✅ Generated {len(historical_records)} historical records!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Historical data generation failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def generate_sample_routes():
    """Generate sample route data for testing."""
    print("🚛 Generating sample route data...")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get some bin IDs
        cursor.execute("SELECT id FROM bins LIMIT 20")
        bin_ids = [row[0] for row in cursor.fetchall()]

        # Generate sample routes
        algorithms = ['nearest', 'priority', 'genetic']
        trucks = ['truck_1', 'truck_2', 'truck_3']

        for i in range(10):
            route_bins = random.sample(bin_ids, random.randint(5, 15))
            algorithm = random.choice(algorithms)
            truck = random.choice(trucks)

            # Calculate mock metrics
            distance = len(route_bins) * random.uniform(2.0, 5.0)
            duration = distance * random.uniform(0.8, 1.2)
            co2 = distance * 0.8  # kg CO2
            fuel = distance * 0.2  # liters

            cursor.execute("""
                INSERT INTO routes (truck_id, algorithm, status, bin_sequence,
                                  planned_bins_count, planned_distance_km,
                                  estimated_duration_min, co2_kg, fuel_liters)
                VALUES (%s, %s, 'completed', %s, %s, %s, %s, %s, %s)
            """, (
                truck, algorithm, route_bins, len(route_bins),
                round(distance, 2), round(duration, 1),
                round(co2, 2), round(fuel, 2)
            ))

        conn.commit()
        print("✅ Generated sample route data!")

    except Exception as e:
        conn.rollback()
        print(f"❌ Route generation failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def verify_migration():
    """Verify the migration was successful."""
    print("🔍 Verifying migration...")

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Check table counts
        tables = ['bins', 'fill_history', 'routes', 'alerts', 'vehicle_telemetry', 'forecasting_models']
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"   {table}: {count} records")

        # Check views
        views = ['active_bins', 'critical_bins', 'recent_alerts']
        for view in views:
            cursor.execute(f"SELECT COUNT(*) FROM {view}")
            count = cursor.fetchone()[0]
            print(f"   {view}: {count} records")

        print("✅ Migration verification complete!")

    except Exception as e:
        print(f"❌ Verification failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

def main():
    """Main migration function."""
    print("🚀 Starting GreenRoute database migration...")

    # Path to CSV file
    csv_path = os.path.join(os.path.dirname(__file__), 'data', 'pune_smart_bin_dataset.csv')

    if not os.path.exists(csv_path):
        print(f"❌ CSV file not found: {csv_path}")
        return

    try:
        # Step 1: Migrate bins data
        migrate_bins_data(csv_path)

        # Step 2: Generate historical data
        generate_historical_data()

        # Step 3: Generate sample routes
        generate_sample_routes()

        # Step 4: Verify
        verify_migration()

        print("\n🎉 Migration completed successfully!")
        print("📊 Your database now contains:")
        print("   • Real bin data from Pune")
        print("   • 30 days of historical fill data")
        print("   • Sample route optimization data")
        print("   • Ready for advanced analytics!")

    except Exception as e:
        print(f"\n💥 Migration failed: {e}")
        print("Please check your PostgreSQL connection and try again.")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
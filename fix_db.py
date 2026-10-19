import psycopg2
import os
from dotenv import load_dotenv

load_dotenv('config/.env')

DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', 5432)
DB_NAME = os.getenv('DB_NAME', 'greenroute_db')
DB_USER = os.getenv('DB_USER', 'greenroute_user')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'greenroute_pass')

def main():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cursor = conn.cursor()
        
        # Scenarios Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS scenarios (
            scenario_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            parameters JSONB,
            results JSONB,
            total_distance_km FLOAT,
            total_time_minutes INTEGER,
            total_emissions_kg FLOAT,
            cost_estimate FLOAT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)
        
        # Route Assignments Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS drivers (
            id SERIAL PRIMARY KEY,
            driver_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            vehicle_id INT
        );
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id SERIAL PRIMARY KEY,
            vehicle_id TEXT UNIQUE NOT NULL,
            license_plate TEXT NOT NULL,
            vehicle_type TEXT
        );
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS route_assignments (
            assignment_id TEXT PRIMARY KEY,
            driver_id INT REFERENCES drivers(id),
            vehicle_id INT REFERENCES vehicles(id),
            route_data JSONB,
            total_distance_km FLOAT,
            estimated_duration_minutes INTEGER,
            status TEXT,
            scheduled_time TIMESTAMP,
            start_time TIMESTAMP,
            completed_time TIMESTAMP
        );
        """)
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_tracking (
            id SERIAL PRIMARY KEY,
            assignment_id TEXT REFERENCES route_assignments(assignment_id),
            vehicle_id INT REFERENCES vehicles(id),
            latitude FLOAT,
            longitude FLOAT,
            current_bin_id TEXT,
            current_stop_number INT,
            bins_collected INT DEFAULT 0,
            timestamp TIMESTAMP DEFAULT NOW()
        );
        """)

        conn.commit()
        print("SUCCESS! Missing tables added to running database.")
        
    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    main()

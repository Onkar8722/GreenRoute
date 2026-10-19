#!/usr/bin/env python3
"""
Simple Database Setup Script for GreenRoute
"""

import psycopg2
from psycopg2 import sql
import csv
import sys

# Connection parameters
DB_HOST = 'localhost'
DB_PORT = 5432
DB_USER = 'greenroute_user'
DB_PASSWORD = 'greenroute_pass'
DB_NAME = 'greenroute_db'

print("=" * 60)
print("GreenRoute Database Setup")
print("=" * 60)

try:
    # Connect to the database
    print("\n1. Connecting to PostgreSQL...")
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    cursor = conn.cursor()
    print("✅ Connected successfully")
    
    # Read and execute schema
    print("\n2. Creating database schema...")
    with open('database_schema.sql', 'r') as f:
        schema_sql = f.read()
    
    # Execute schema creation
    cursor.execute(schema_sql)
    conn.commit()
    print("✅ Schema created successfully")
    
    # Load sample data
    print("\n3. Loading 104 Pune bins from CSV...")
    with open('data/pune_smart_bin_dataset.csv', 'r') as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            try:
                cursor.execute("""
                    INSERT INTO bins (id, latitude, longitude, fill_level, battery_level, signal_strength)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (
                    row.get('bin_id', str(count)),
                    float(row.get('latitude', 0)),
                    float(row.get('longitude', 0)),
                    int(row.get('fill_level', 50)),
                    int(row.get('battery_level', 90)),
                    int(row.get('signal_strength', -50))
                ))
                count += 1
            except Exception as e:
                print(f"Warning: Could not load row {count}: {e}")
        
        conn.commit()
        print(f"✅ Loaded {count} bins successfully")
    
    # Verify
    print("\n4. Verifying database...")
    cursor.execute("SELECT COUNT(*) FROM bins")
    bin_count = cursor.fetchone()[0]
    print(f"✅ Total bins in database: {bin_count}")
    
    # Summary
    print("\n" + "=" * 60)
    print("✅ DATABASE SETUP COMPLETE!")
    print("=" * 60)
    print(f"Database: {DB_NAME}")
    print(f"Host: {DB_HOST}:{DB_PORT}")
    print(f"User: {DB_USER}")
    print(f"Bins loaded: {bin_count}")
    print("\nNext steps:")
    print("  1. Run: python app.py")
    print("  2. Visit: http://127.0.0.1:5000/api/health")
    print("=" * 60)
    
    cursor.close()
    conn.close()

except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nTroubleshooting:")
    print("  - Verify PostgreSQL is running")
    print("  - Check database credentials in this script")
    print("  - Ensure database 'greenroute_db' exists")
    sys.exit(1)

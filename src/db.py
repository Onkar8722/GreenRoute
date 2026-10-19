"""
Database initialization and migration utilities for GreenRoute.
Handles schema creation, data loading, and version management.
"""

import logging
import os
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database initialization and operations."""
    
    def __init__(self, db_config: dict):
        """
        Initialize database manager.
        
        Args:
            db_config: Dictionary with host, port, database, user, password
        """
        self.db_config = db_config
    
    def create_connection(self):
        """Create database connection."""
        try:
            conn = psycopg2.connect(**self.db_config)
            logger.debug("Database connection established")
            return conn
        except psycopg2.Error as e:
            logger.error(f"Database connection failed: {e}")
            raise
    
    def get_connection(self):
        """Alias for create_connection (used by FleetManagementAPI)."""
        return self.create_connection()
    
    def initialize_schema(self, schema_file: str = "database_schema.sql") -> bool:
        """
        Initialize database schema from SQL file.
        
        Args:
            schema_file: Path to SQL file containing schema
            
        Returns:
            True if successful
        """
        try:
            if not os.path.exists(schema_file):
                logger.error(f"Schema file not found: {schema_file}")
                return False
            
            with open(schema_file, 'r') as f:
                schema_sql = f.read()
            
            conn = self.create_connection()
            cursor = conn.cursor()
            
            # Execute schema script
            cursor.execute(schema_sql)
            conn.commit()
            
            cursor.close()
            conn.close()
            
            logger.info("Database schema initialized successfully")
            return True
        
        except Exception as e:
            logger.error(f"Schema initialization error: {e}")
            return False
    
    def load_sample_data(self, csv_file: str) -> int:
        """
        Load sample data from CSV file.
        
        Args:
            csv_file: Path to CSV file
            
        Returns:
            Number of records inserted
        """
        import csv
        
        try:
            if not os.path.exists(csv_file):
                logger.error(f"CSV file not found: {csv_file}")
                return 0
            
            conn = self.create_connection()
            cursor = conn.cursor()
            
            inserted = 0
            
            with open(csv_file, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        cursor.execute("""
                            INSERT INTO bins 
                            (id, latitude, longitude, fill_level, status, 
                             battery_level, signal_strength, bin_type, capacity_liters)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE
                            SET fill_level = EXCLUDED.fill_level,
                                battery_level = EXCLUDED.battery_level,
                                signal_strength = EXCLUDED.signal_strength,
                                last_updated = NOW()
                        """, (
                            row.get('id'),
                            float(row.get('latitude', 0)),
                            float(row.get('longitude', 0)),
                            int(row.get('fill_level', 0)),
                            int(row.get('status', 0)),
                            int(row.get('battery_level', 100)),
                            int(row.get('signal_strength', -50)),
                            row.get('bin_type', 'residential'),
                            int(row.get('capacity_liters', 120))
                        ))
                        inserted += 1
                    
                    except Exception as e:
                        logger.warning(f"Error inserting row: {e}")
                        continue
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Loaded {inserted} sample bin records")
            return inserted
        
        except Exception as e:
            logger.error(f"Data loading error: {e}")
            return 0
    
    def get_schema_version(self) -> str:
        """Get current database schema version."""
        try:
            conn = self.create_connection()
            cursor = conn.cursor()
            
            # Create version table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version VARCHAR(50) PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT NOW(),
                    description TEXT
                )
            """)
            conn.commit()
            
            # Get latest version
            cursor.execute("SELECT version FROM schema_versions ORDER BY applied_at DESC LIMIT 1")
            result = cursor.fetchone()
            
            cursor.close()
            conn.close()
            
            return result[0] if result else "0.0.0"
        
        except Exception as e:
            logger.error(f"Error getting schema version: {e}")
            return "unknown"
    
    def record_migration(self, version: str, description: str = "") -> bool:
        """Record a migration in the database."""
        try:
            conn = self.create_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO schema_versions (version, description)
                VALUES (%s, %s)
            """, (version, description))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Recorded migration {version}")
            return True
        
        except Exception as e:
            logger.error(f"Error recording migration: {e}")
            return False
    
    def backup_bins(self, backup_file: str = None) -> bool:
        """
        Backup bins table to CSV.
        
        Args:
            backup_file: Path to backup file (default: bins_backup_TIMESTAMP.csv)
            
        Returns:
            True if successful
        """
        import csv
        
        try:
            if backup_file is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = f"backups/bins_backup_{timestamp}.csv"
            
            os.makedirs(os.path.dirname(backup_file), exist_ok=True)
            
            conn = self.create_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT * FROM bins")
            rows = cursor.fetchall()
            
            if not rows:
                logger.warning("No bins to backup")
                return True
            
            with open(backup_file, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            
            cursor.close()
            conn.close()
            
            logger.info(f"Backed up {len(rows)} bin records to {backup_file}")
            return True
        
        except Exception as e:
            logger.error(f"Backup error: {e}")
            return False
    
    def health_check(self) -> dict:
        """Check database health."""
        try:
            conn = self.create_connection()
            cursor = conn.cursor()
            
            # Check if main tables exist
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'bins'
                )
            """)
            bins_exist = cursor.fetchone()[0]
            
            # Count records
            cursor.execute("SELECT COUNT(*) FROM bins")
            bin_count = cursor.fetchone()[0] if bins_exist else 0
            
            cursor.close()
            conn.close()
            
            return {
                "status": "healthy",
                "connected": True,
                "schema_initialized": bins_exist,
                "bin_records": bin_count
            }
        
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {
                "status": "unhealthy",
                "connected": False,
                "schema_initialized": False,
                "error": str(e)
            }


def init_database(base_dir: str, db_config: dict) -> bool:
    """
    Initialize complete database setup.
    
    Args:
        base_dir: Base project directory
        db_config: Database configuration
        
    Returns:
        True if successful
    """
    db_manager = DatabaseManager(db_config)
    
    logger.info("Starting database initialization...")
    
    # Initialize schema
    schema_file = os.path.join(base_dir, "database_schema.sql")
    if not db_manager.initialize_schema(schema_file):
        logger.error("Failed to initialize schema")
        return False
    
    # Load sample data
    csv_file = os.path.join(base_dir, "data", "pune_smart_bin_dataset.csv")
    if os.path.exists(csv_file):
        db_manager.load_sample_data(csv_file)
    else:
        logger.warning(f"Sample data file not found: {csv_file}")
    
    # Record version
    db_manager.record_migration("1.0.0", "Initial schema creation")
    
    # Health check
    health = db_manager.health_check()
    logger.info(f"Database health: {health}")
    
    return health['status'] == 'healthy'

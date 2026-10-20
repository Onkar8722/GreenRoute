"""
Database initialization and migration utilities for GreenRoute.
Handles schema creation, data loading, and version management.
"""

import logging
import os
import csv
from pathlib import Path
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor

from .bin_upload import parse_bins_csv
from .db_transaction import transaction

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SCHEMA_PATH = PROJECT_ROOT / "docs" / "database_schema.sql"
SAMPLE_DATA_PATH = PROJECT_ROOT / "data" / "pune_smart_bin_dataset.csv"

REQUIRED_SCHEMA_COLUMNS = {
    "bins": {"id", "latitude", "longitude", "fill_level", "status", "last_updated"},
    "fill_history": {"id", "bin_id", "fill_level", "recorded_at"},
    "routes": {"id", "truck_id", "bin_sequence", "status"},
    "vehicle_telemetry": {"id", "truck_id", "recorded_at"},
    "alerts": {"id", "bin_id", "alert_type", "severity", "status", "created_at"},
    "vehicles": {"id", "vehicle_id", "vehicle_type", "license_plate"},
    "drivers": {"id", "driver_id", "name", "vehicle_id"},
    "route_assignments": {
        "assignment_id", "driver_id", "vehicle_id", "route_data",
        "total_distance_km", "estimated_duration_minutes", "status", "end_time",
    },
    "vehicle_tracking": {
        "id", "assignment_id", "vehicle_id", "latitude", "longitude",
        "current_bin_id", "current_stop_number", "timestamp",
    },
    "scenarios": {"scenario_id", "name", "parameters", "results", "created_at"},
    "forecasting_models": {"id", "model_type", "training_date"},
    "schema_versions": {"version", "applied_at", "description"},
}
REQUIRED_FOREIGN_KEYS = {
    ("fill_history", "bin_id", "bins", "id"),
    ("alerts", "bin_id", "bins", "id"),
    ("drivers", "vehicle_id", "vehicles", "id"),
    ("route_assignments", "driver_id", "drivers", "id"),
    ("route_assignments", "vehicle_id", "vehicles", "id"),
    ("vehicle_tracking", "assignment_id", "route_assignments", "assignment_id"),
    ("vehicle_tracking", "vehicle_id", "vehicles", "id"),
    ("vehicle_tracking", "current_bin_id", "bins", "id"),
}
REQUIRED_UNIQUE_KEYS = {
    ("bins", "id"),
    ("fill_history", "id"),
    ("routes", "id"),
    ("vehicle_telemetry", "id"),
    ("alerts", "id"),
    ("forecasting_models", "id"),
    ("vehicles", "id"),
    ("vehicles", "vehicle_id"),
    ("vehicles", "license_plate"),
    ("drivers", "id"),
    ("drivers", "driver_id"),
    ("route_assignments", "assignment_id"),
    ("vehicle_tracking", "id"),
    ("scenarios", "scenario_id"),
    ("schema_versions", "version"),
}


class DatabaseManager:
    """Manages database initialization and operations."""
    
    def __init__(self, db_config: dict):
        """
        Initialize database manager.
        
        Args:
            db_config: Dictionary with host, port, database, user, password
        """
        self.db_config = db_config
        self.last_error = None
    
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
    
    def initialize_schema(self, schema_file: str | os.PathLike | None = None) -> bool:
        """
        Initialize database schema from SQL file.
        
        Args:
            schema_file: Path to SQL file containing schema
            
        Returns:
            True if successful
        """
        try:
            schema_path = Path(schema_file) if schema_file else CANONICAL_SCHEMA_PATH
            if not schema_path.is_absolute():
                schema_path = PROJECT_ROOT / schema_path
            if not schema_path.is_file():
                raise FileNotFoundError(f"Canonical database schema not found: {schema_path}")

            schema_sql = schema_path.read_text(encoding="utf-8")
            with transaction(self.create_connection()) as cursor:
                cursor.execute(schema_sql)
                cursor.execute(
                    """
                    SELECT table_name, column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                    """
                )
                actual = {}
                for table_name, column_name in cursor.fetchall():
                    actual.setdefault(table_name, set()).add(column_name)
                missing = {
                    table: sorted(columns - actual.get(table, set()))
                    for table, columns in REQUIRED_SCHEMA_COLUMNS.items()
                    if columns - actual.get(table, set())
                }
                if missing:
                    details = "; ".join(
                        f"{table}: {', '.join(columns)}"
                        for table, columns in sorted(missing.items())
                    )
                    raise RuntimeError(
                        "Existing database schema is incompatible with the canonical "
                        f"schema (missing columns: {details}). No automatic data migration was attempted."
                    )
                cursor.execute(
                    """
                    SELECT tc.table_name, kcu.column_name,
                           ccu.table_name, ccu.column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_catalog = kcu.constraint_catalog
                     AND tc.constraint_schema = kcu.constraint_schema
                     AND tc.constraint_name = kcu.constraint_name
                     AND tc.table_name = kcu.table_name
                    JOIN information_schema.constraint_column_usage AS ccu
                      ON tc.constraint_catalog = ccu.constraint_catalog
                     AND tc.constraint_schema = ccu.constraint_schema
                     AND tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.constraint_schema = current_schema()
                    """
                )
                actual_foreign_keys = set(cursor.fetchall())
                missing_foreign_keys = REQUIRED_FOREIGN_KEYS - actual_foreign_keys
                if missing_foreign_keys:
                    details = "; ".join(
                        f"{table}.{column} -> {referenced_table}.{referenced_column}"
                        for table, column, referenced_table, referenced_column
                        in sorted(missing_foreign_keys)
                    )
                    raise RuntimeError(
                        "Existing database schema is incompatible with the canonical "
                        f"schema (missing foreign keys: {details}). "
                        "No automatic data migration was attempted."
                    )
                cursor.execute(
                    """
                    SELECT tc.table_name, kcu.column_name
                    FROM information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                      ON tc.constraint_catalog = kcu.constraint_catalog
                     AND tc.constraint_schema = kcu.constraint_schema
                     AND tc.constraint_name = kcu.constraint_name
                     AND tc.table_name = kcu.table_name
                    WHERE tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
                      AND tc.constraint_schema = current_schema()
                    """
                )
                actual_unique_keys = set(cursor.fetchall())
                missing_unique_keys = REQUIRED_UNIQUE_KEYS - actual_unique_keys
                if missing_unique_keys:
                    details = "; ".join(
                        f"{table}.{column}"
                        for table, column in sorted(missing_unique_keys)
                    )
                    raise RuntimeError(
                        "Existing database schema is incompatible with the canonical "
                        f"schema (missing primary/unique keys: {details}). "
                        "No automatic data migration was attempted."
                    )

            logger.info("Database schema initialized successfully")
            self.last_error = None
            return True
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Schema initialization error: {self.last_error}")
            return False
    
    def load_sample_data(self, csv_file: str | os.PathLike = SAMPLE_DATA_PATH) -> int:
        """
        Load sample data from CSV file.
        
        Args:
            csv_file: Path to CSV file
            
        Returns:
            Number of validated CSV records processed
        """
        try:
            csv_path = Path(csv_file)
            if not csv_path.is_absolute():
                csv_path = PROJECT_ROOT / csv_path
            if not csv_path.is_file():
                raise FileNotFoundError(f"Sample bin CSV not found: {csv_path}")

            content = csv_path.read_bytes()
            bins = parse_bins_csv(content)
            with transaction(self.create_connection()) as cursor:
                for bin_data in bins:
                    cursor.execute(
                        """
                        INSERT INTO bins
                        (id, latitude, longitude, fill_level, status)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            bin_data["id"], bin_data["lat"], bin_data["lng"],
                            bin_data["fill"], bin_data["status"],
                        ),
                    )

            inserted = len(bins)
            logger.info(f"Processed {inserted} validated sample bin records")
            self.last_error = None
            return inserted
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Data loading error: {self.last_error}")
            return 0
    
    def get_schema_version(self) -> str:
        """Get current database schema version."""
        try:
            with transaction(self.create_connection()) as cursor:
                cursor.execute("SELECT version FROM schema_versions ORDER BY applied_at DESC LIMIT 1")
                result = cursor.fetchone()
            
            return result[0] if result else "0.0.0"
        
        except Exception as e:
            logger.error(f"Error getting schema version: {e}")
            return "unknown"
    
    def record_migration(self, version: str, description: str = "") -> bool:
        """Record a migration in the database."""
        try:
            with transaction(self.create_connection()) as cursor:
                cursor.execute("""
                INSERT INTO schema_versions (version, description)
                VALUES (%s, %s)
                ON CONFLICT (version) DO UPDATE
                SET description = EXCLUDED.description, applied_at = NOW()
                """, (version, description))
            
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
    project_root = Path(base_dir).resolve()
    schema_file = project_root / "docs" / "database_schema.sql"
    if not db_manager.initialize_schema(schema_file):
        logger.error("Failed to initialize schema")
        return False
    
    # Load sample data
    csv_file = project_root / "data" / "pune_smart_bin_dataset.csv"
    loaded_count = db_manager.load_sample_data(csv_file)
    if loaded_count <= 0:
        logger.error(f"Sample bin records were not loaded: {db_manager.last_error or 'CSV contained no records'}")
        return False
    
    # Record version
    if not db_manager.record_migration("2.0.0", "Canonical database schema"):
        logger.error("Failed to record database schema version")
        return False
    
    # Health check
    health = db_manager.health_check()
    logger.info(f"Database health: {health}")
    
    return health['status'] == 'healthy'

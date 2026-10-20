#!/usr/bin/env python3
"""
Initialize PostgreSQL database for GreenRoute.
Waits for database to be ready, creates schema, and loads sample data.

Usage:
    python init_db.py
"""

import sys
import time
import logging
from pathlib import Path

# Add project root to path (scripts is in root/scripts)
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import Config
from src.db import DatabaseManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def wait_for_database(db_config, max_retries=30, retry_delay=2):
    """
    Wait for PostgreSQL to be ready.
    
    Args:
        db_config: Database configuration
        max_retries: Maximum number of connection attempts
        retry_delay: Seconds to wait between attempts
        
    Returns:
        True if database is ready, False if timeout
    """
    logger.info(f"Waiting for PostgreSQL at {db_config['host']}:{db_config['port']}...")
    
    for attempt in range(max_retries):
        try:
            db_mgr = DatabaseManager(db_config)
            conn = db_mgr.create_connection()
            conn.close()
            logger.info(f"✅ Database is ready (after {attempt} retries)")
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                logger.debug(f"Connection attempt {attempt + 1}/{max_retries} failed, retrying...")
                time.sleep(retry_delay)
            else:
                logger.error(f"❌ Database connection failed after {max_retries} attempts")
                logger.error(f"   {str(e)}")
                return False
    
    return False


def main():
    """Initialize the database."""
    logger.info("=" * 60)
    logger.info("GreenRoute Database Initialization")
    logger.info("=" * 60)
    
    # Get base directory
    project_root = Path(__file__).resolve().parent.parent
    
    # Use config
    config = Config
    db_config = config.DB_CONFIG
    
    logger.info(f"Database: {db_config['database']}")
    logger.info(f"Host: {db_config['host']}:{db_config['port']}")
    
    # Wait for database
    logger.info("")
    if not wait_for_database(db_config):
        logger.error("Database is not available. Please ensure PostgreSQL is running.")
        logger.error("")
        logger.error("To start PostgreSQL, run:")
        logger.error("  docker run -d --name greenroute-postgres \\")
        logger.error("    -e POSTGRES_PASSWORD=greenroute_pass \\")
        logger.error("    -e POSTGRES_USER=greenroute_user \\")
        logger.error("    -e POSTGRES_DB=greenroute_db \\")
        logger.error("    -p 5432:5432 postgres:15")
        return False
    
    # Initialize database manager
    db_mgr = DatabaseManager(db_config)
    
    # Initialize schema
    logger.info("")
    logger.info("Initializing database schema...")
    if db_mgr.initialize_schema():
        logger.info("✅ Schema initialized successfully")
    else:
        logger.error("❌ Failed to initialize schema")
        return False
    
    # Load sample data
    logger.info("")
    logger.info("Loading sample data...")
    csv_file = project_root / "data" / "pune_smart_bin_dataset.csv"
    count = db_mgr.load_sample_data(csv_file)
    if count <= 0:
        logger.error(
            "❌ Sample data could not be loaded: "
            f"{db_mgr.last_error or 'CSV contains no records'}"
        )
        return False
    logger.info(f"✅ Loaded {count} bin records from CSV")
    
    # Record migration
    logger.info("")
    logger.info("Recording migration...")
    if not db_mgr.record_migration("2.0.0", "Canonical database schema and sample data"):
        logger.error("❌ Failed to record schema version")
        return False
    logger.info("✅ Migration recorded")
    
    # Health check
    logger.info("")
    logger.info("Running health check...")
    health = db_mgr.health_check()
    
    if health['status'] == 'healthy':
        logger.info("=" * 60)
        logger.info("✅ DATABASE INITIALIZATION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"   Schema initialized: {health['schema_initialized']}")
        logger.info(f"   Bin records: {health['bin_records']}")
        logger.info("")
        logger.info("Next steps:")
        logger.info("  1. If in a new terminal, start the Flask app:")
        logger.info("       python app.py")
        logger.info("  2. Visit http://127.0.0.1:5000")
        logger.info("  3. Test endpoints at /api/stats, /api/bins, etc.")
        logger.info("")
        return True
    else:
        logger.error("=" * 60)
        logger.error("❌ HEALTH CHECK FAILED")
        logger.error("=" * 60)
        logger.error(f"   Status: {health.get('status')}")
        if 'error' in health:
            logger.error(f"   Error: {health['error']}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)

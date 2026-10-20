"""Compatibility command for initializing the canonical GreenRoute schema."""

import logging
import sys

from config.config import Config
from src.db import DatabaseManager


def main():
    logging.basicConfig(level=logging.INFO)
    manager = DatabaseManager(Config.DB_CONFIG)
    if not manager.initialize_schema():
        logging.error("Canonical schema initialization failed: %s", manager.last_error)
        return False
    logging.info("Canonical database schema initialized successfully")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

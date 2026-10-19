"""
Configuration management for GreenRoute.
Handles environment variables and application settings.
"""

import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from multiple possible locations
config_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(config_dir, '.env')
if not os.path.exists(env_path):
    env_path = os.path.join(os.path.dirname(config_dir), '.env')
load_dotenv(dotenv_path=env_path, override=True)


class Config:
    """Base configuration."""
    
    # Flask
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    TESTING = os.getenv('TESTING', 'False').lower() == 'true'
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    
    # Database
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 5432))
    DB_NAME = os.getenv('DB_NAME', 'greenroute_db')
    DB_USER = os.getenv('DB_USER', 'greenroute_user')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'greenroute_pass')
    
    DB_CONFIG = {
        'host': DB_HOST,
        'port': DB_PORT,
        'database': DB_NAME,
        'user': DB_USER,
        'password': DB_PASSWORD
    }
    
    # Redis
    REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
    REDIS_DB = int(os.getenv('REDIS_DB', 0))
    REDIS_ENABLED = os.getenv('REDIS_ENABLED', 'True').lower() == 'true'
    
    # MQTT
    MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
    MQTT_PORT = int(os.getenv('MQTT_PORT', 1883))
    MQTT_ENABLED = os.getenv('MQTT_ENABLED', 'True').lower() == 'true'
    
    # Routing
    DEPOT_LAT = float(os.getenv('DEPOT_LAT', 18.5204))
    DEPOT_LNG = float(os.getenv('DEPOT_LNG', 73.8567))
    DEPOT_ID = os.getenv('DEPOT_ID', 'DEPOT')
    
    # Algorithm weights
    DISTANCE_WEIGHT = float(os.getenv('DISTANCE_WEIGHT', 0.5))
    TIME_WEIGHT = float(os.getenv('TIME_WEIGHT', 0.3))
    EMISSIONS_WEIGHT = float(os.getenv('EMISSIONS_WEIGHT', 0.2))
    
    # Thresholds
    FILL_CRITICAL = int(os.getenv('FILL_CRITICAL', 80))
    FILL_WARNING = int(os.getenv('FILL_WARNING', 60))
    FILL_SKIP = int(os.getenv('FILL_SKIP', 30))
    
    # Forecasting
    FORECAST_WINDOW_DAYS = int(os.getenv('FORECAST_WINDOW_DAYS', 30))
    FORECAST_MIN_ACCURACY = float(os.getenv('FORECAST_MIN_ACCURACY', 0.85))
    
    # Genetic Algorithm
    GA_POPULATION_SIZE = int(os.getenv('GA_POPULATION_SIZE', 100))
    GA_GENERATIONS = int(os.getenv('GA_GENERATIONS', 50))
    GA_MUTATION_RATE = float(os.getenv('GA_MUTATION_RATE', 0.1))
    GA_CROSSOVER_RATE = float(os.getenv('GA_CROSSOVER_RATE', 0.8))
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'logs/greenroute.log')
    
    # Data paths
    DATA_DIR = os.getenv('DATA_DIR', 'data')
    BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')
    MODELS_DIR = os.getenv('MODELS_DIR', 'models')
    
    @classmethod
    def to_dict(cls) -> dict:
        """Convert configuration to dictionary."""
        return {
            k: v for k, v in cls.__dict__.items()
            if not k.startswith('_') and k.isupper()
        }
    
    @classmethod
    def validate(cls) -> tuple[bool, list]:
        """
        Validate configuration.
        
        Returns:
            (is_valid, list_of_errors)
        """
        errors = []
        
        # Check database config
        if not cls.DB_HOST:
            errors.append("DB_HOST not configured")
        
        if cls.DB_PORT <= 0 or cls.DB_PORT > 65535:
            errors.append(f"DB_PORT invalid: {cls.DB_PORT}")
        
        # Check coordinates
        if cls.DEPOT_LAT < -90 or cls.DEPOT_LAT > 90:
            errors.append(f"DEPOT_LAT invalid: {cls.DEPOT_LAT}")
        
        if cls.DEPOT_LNG < -180 or cls.DEPOT_LNG > 180:
            errors.append(f"DEPOT_LNG invalid: {cls.DEPOT_LNG}")
        
        # Check weights sum to 1
        weights_sum = cls.DISTANCE_WEIGHT + cls.TIME_WEIGHT + cls.EMISSIONS_WEIGHT
        if abs(weights_sum - 1.0) > 0.01:
            errors.append(f"Algorithm weights don't sum to 1: {weights_sum}")
        
        # Check thresholds
        if cls.FILL_SKIP >= cls.FILL_WARNING >= cls.FILL_CRITICAL:
            errors.append("Fill level thresholds not in correct order")
        
        return len(errors) == 0, errors


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    TESTING = False


class TestingConfig(Config):
    """Testing configuration."""
    DEBUG = True
    TESTING = True
    DB_NAME = 'greenroute_test_db'


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    TESTING = False


def get_config(env: Optional[str] = None) -> Config:
    """
    Get configuration based on environment.
    
    Args:
        env: Environment name ('development', 'testing', 'production')
             If None, uses FLASK_ENV environment variable
    
    Returns:
        Configuration object
    """
    if env is None:
        env = os.getenv('FLASK_ENV', 'development')
    
    config_map = {
        'development': DevelopmentConfig,
        'testing': TestingConfig,
        'production': ProductionConfig,
    }
    
    return config_map.get(env, DevelopmentConfig)

"""
Redis caching layer for GreenRoute.
Handles cache operations with TTL policies for different data types.
"""

import json
import logging
from typing import Any, Optional
import redis
from datetime import timedelta

logger = logging.getLogger(__name__)

# TTL policies (in seconds)
CACHE_TTL = {
    "stats": 300,  # 5 minutes
    "alerts": 60,  # 1 minute
    "bins": 120,  # 2 minutes
    "routes": 600,  # 10 minutes
    "telemetry": 30,  # 30 seconds
    "forecast": 3600,  # 1 hour
}


class RedisCache:
    """Redis cache client with TTL management and error handling."""
    
    def __init__(self, host: str = 'localhost', port: int = 6379, 
                 db: int = 0, decode_responses: bool = True):
        """
        Initialize Redis client.
        
        Args:
            host: Redis server host
            port: Redis server port
            db: Database number
            decode_responses: Decode bytes to strings
        """
        try:
            self.client = redis.Redis(
                host=host,
                port=port,
                db=db,
                decode_responses=decode_responses,
                socket_connect_timeout=5
            )
            # Test connection
            self.client.ping()
            self.available = True
            logger.info("Redis connection established")
        except Exception as e:
            logger.warning(f"Redis unavailable: {e}. Cache will be disabled.")
            self.client = None
            self.available = False
    
    def _get_key(self, category: str, identifier: str) -> str:
        """Generate cache key."""
        return f"greenroute:{category}:{identifier}"
    
    def get(self, category: str, identifier: str) -> Optional[Any]:
        """
        Retrieve cached value.
        
        Args:
            category: Cache category (e.g., 'stats', 'bins')
            identifier: Unique identifier for the data
            
        Returns:
            Deserialized object or None if not found
        """
        if not self.available:
            return None
        
        try:
            key = self._get_key(category, identifier)
            value = self.client.get(key)
            
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    def set(self, category: str, identifier: str, value: Any, 
            ttl: Optional[int] = None) -> bool:
        """
        Store value in cache with TTL.
        
        Args:
            category: Cache category
            identifier: Unique identifier
            value: Data to cache (will be JSON-serialized)
            ttl: Time-to-live in seconds (uses default if None)
            
        Returns:
            True if successful
        """
        if not self.available:
            return False
        
        try:
            key = self._get_key(category, identifier)
            ttl_seconds = ttl or CACHE_TTL.get(category, 300)
            
            self.client.setex(
                key,
                ttl_seconds,
                json.dumps(value, default=str)
            )
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
    
    def delete(self, category: str, identifier: str) -> bool:
        """Delete cached value."""
        if not self.available:
            return False
        
        try:
            key = self._get_key(category, identifier)
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False
    
    def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern."""
        if not self.available:
            return 0
        
        try:
            keys = self.client.keys(f"greenroute:{pattern}*")
            if keys:
                return self.client.delete(*keys)
            return 0
        except Exception as e:
            logger.error(f"Cache pattern delete error: {e}")
            return 0
    
    def clear_all(self) -> bool:
        """Clear all GreenRoute cache."""
        if not self.available:
            return False
        
        try:
            keys = self.client.keys("greenroute:*")
            if keys:
                self.client.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"Cache clear error: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check if Redis is healthy."""
        if not self.client:
            return False
        
        try:
            self.client.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            self.available = False
            return False


# Global cache instance
_cache_instance: Optional[RedisCache] = None


def get_cache() -> RedisCache:
    """Get or create global Redis cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RedisCache()
    return _cache_instance


def cache_endpoint(category: str, ttl: Optional[int] = None):
    """
    Decorator for caching endpoint responses.
    
    Usage:
        @app.route('/api/stats')
        @cache_endpoint('stats', ttl=300)
        def get_stats():
            ...
    """
    def decorator(func):
        def wrapper(identifier='all', *args, **kwargs):
            cache = get_cache()
            
            # Try to get from cache
            cached = cache.get(category, identifier)
            if cached is not None:
                return cached
            
            # Call function if not cached
            result = func(*args, **kwargs)
            
            # Store in cache
            cache.set(category, identifier, result, ttl)
            
            return result
        return wrapper
    return decorator

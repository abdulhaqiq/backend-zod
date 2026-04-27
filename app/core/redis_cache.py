"""
Redis cache layer for frequently accessed data.
Install: pip install redis
"""
import json
import logging
from typing import Any
from datetime import timedelta

try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from app.core.config import settings

_log = logging.getLogger(__name__)

# Redis client (singleton)
_redis_client: redis.Redis | None = None


async def get_redis() -> redis.Redis | None:
    """Get Redis client. Returns None if Redis is not available/configured."""
    global _redis_client
    
    if not REDIS_AVAILABLE:
        return None
    
    if not settings.REDIS_URL:
        return None
    
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Test connection
            await _redis_client.ping()
            _log.info("Redis connected successfully")
        except Exception as e:
            _log.warning(f"Redis connection failed: {e}. Falling back to database only.")
            _redis_client = None
    
    return _redis_client


async def cache_get(key: str) -> Any | None:
    """Get value from cache. Returns None if not found or Redis unavailable."""
    client = await get_redis()
    if not client:
        return None
    
    try:
        value = await client.get(key)
        if value:
            return json.loads(value)
    except Exception as e:
        _log.warning(f"Redis GET error for {key}: {e}")
    
    return None


async def cache_set(key: str, value: Any, ttl: int | timedelta = 300) -> bool:
    """
    Set value in cache with TTL (time to live).
    Args:
        key: Cache key
        value: Value to cache (will be JSON serialized)
        ttl: Time to live in seconds (default 5 minutes) or timedelta
    Returns:
        True if cached successfully, False otherwise
    """
    client = await get_redis()
    if not client:
        return False
    
    try:
        if isinstance(ttl, timedelta):
            ttl = int(ttl.total_seconds())
        
        await client.setex(key, ttl, json.dumps(value, default=str))
        return True
    except Exception as e:
        _log.warning(f"Redis SET error for {key}: {e}")
        return False


async def cache_delete(key: str) -> bool:
    """Delete key from cache. Returns True if deleted, False otherwise."""
    client = await get_redis()
    if not client:
        return False
    
    try:
        await client.delete(key)
        return True
    except Exception as e:
        _log.warning(f"Redis DELETE error for {key}: {e}")
        return False


async def cache_delete_pattern(pattern: str) -> int:
    """
    Delete all keys matching pattern.
    Example: cache_delete_pattern("user:123:*")
    Returns count of deleted keys.
    """
    client = await get_redis()
    if not client:
        return 0
    
    try:
        keys = []
        async for key in client.scan_iter(match=pattern):
            keys.append(key)
        
        if keys:
            return await client.delete(*keys)
        return 0
    except Exception as e:
        _log.warning(f"Redis DELETE pattern error for {pattern}: {e}")
        return 0


# Cache key builders
def profile_cache_key(user_id: str) -> str:
    return f"profile:{user_id}"


def subscription_cache_key(user_id: str) -> str:
    return f"subscription:{user_id}"


def discover_counts_cache_key(user_id: str) -> str:
    return f"counts:{user_id}"


def swipes_cache_key(user_id: str, mode: str = "date") -> str:
    return f"swipes:{user_id}:{mode}"


async def invalidate_user_cache(user_id: str):
    """Invalidate all cache for a user (call after profile updates)."""
    await cache_delete_pattern(f"*:{user_id}*")

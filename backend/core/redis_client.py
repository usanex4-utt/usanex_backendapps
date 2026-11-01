# backend/core/redis_client.py
import logging
from typing import Optional
import redis.asyncio as aioredis
from backend.config import settings

logger = logging.getLogger(__name__)
_redis: Optional[aioredis.Redis] = None

def init_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        logger.info("Connecting to Layerbase Cloud Redis...")
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=100,
            socket_connect_timeout=10, # Cloud wake up ke liye 10 sec
            socket_timeout=10,         # Read/Write ke liye 10 sec
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return _redis

def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis

async def ping_redis() -> bool:
    try:
        return await get_redis().ping()
    except Exception as e:
        logger.error("Redis ping failed: %s", e)
        return False

async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("Redis client closed")
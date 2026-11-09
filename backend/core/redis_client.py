
# =========================================================
# USANEX - UPSTASH REDIS CLIENT
# backend/core/redis_client.py
# =========================================================

import logging
from typing import Optional

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from backend.config import settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None


# =========================================================
# INITIALIZE REDIS
# =========================================================

def init_redis() -> aioredis.Redis:
    global _redis

    if _redis is not None:
        return _redis

    redis_url = settings.redis_url

    if not redis_url:
        raise RuntimeError(
            "REDIS_URL is missing. Configure Upstash Redis URL."
        )

    if not redis_url.startswith("rediss://"):
        logger.warning(
            "Redis URL is not using TLS. "
            "For Upstash, use rediss://"
        )

    logger.info("Connecting to Upstash Redis...")

    _redis = aioredis.from_url(
        redis_url,
        decode_responses=True,
        max_connections=20,
        socket_connect_timeout=10,
        socket_timeout=10,
        health_check_interval=30,
    )

    logger.info("Upstash Redis client initialized")

    return _redis


# =========================================================
# GET REDIS CLIENT
# =========================================================

def get_redis() -> aioredis.Redis:

    if _redis is None:
        raise RuntimeError(
            "Redis not initialized. Call init_redis() first."
        )

    return _redis


# =========================================================
# REDIS HEALTH CHECK
# =========================================================

async def ping_redis() -> bool:

    try:
        result = await get_redis().ping()

        if result:
            logger.info("Upstash Redis connection successful")
            return True

        return False

    except (RedisError, RuntimeError) as e:

        logger.error(
            "Upstash Redis ping failed: %s",
            e
        )

        return False


# =========================================================
# CLOSE REDIS CONNECTION
# =========================================================

async def close_redis() -> None:
    global _redis

    if _redis is not None:

        try:
            await _redis.aclose()

            logger.info(
                "Upstash Redis connection closed"
            )

        except Exception as e:

            logger.error(
                "Redis shutdown error: %s",
                e
            )

        finally:
            _redis = None

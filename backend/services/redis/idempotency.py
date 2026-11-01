# backend/services/redis/idempotency.py
import json
import logging
from typing import Optional

from backend.core.redis_client import get_redis
from . import keys

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TTL = 300   # 5 minutes
RESULT_TTL = 600         # 10 minutes


async def acquire(key: str, ttl: int = DEFAULT_LOCK_TTL) -> bool:
    """
    Try to acquire the idempotency lock.
    True  -> caller is the first to process this key; proceed.
    False -> already processing or processed; check get_result.
    """
    try:
        result = await get_redis().set(
            keys.idempotency(f"lock:{key}"),
            "1",
            nx=True,
            ex=ttl,
        )
        return bool(result)
    except Exception as e:
        logger.warning("idempotency acquire failed: %s", e)
        return True  # fail-open


async def get_result(key: str) -> Optional[dict]:
    try:
        raw = await get_redis().get(keys.idempotency(f"result:{key}"))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception as e:
        logger.warning("idempotency get_result failed: %s", e)
        return None


async def store_result(key: str, result: dict, ttl: int = RESULT_TTL) -> None:
    try:
        await get_redis().set(
            keys.idempotency(f"result:{key}"),
            json.dumps(result, default=str),
            ex=ttl,
        )
    except Exception as e:
        logger.warning("idempotency store_result failed: %s", e)


async def release(key: str) -> None:
    """Release the lock on failure so client can retry."""
    try:
        await get_redis().delete(keys.idempotency(f"lock:{key}"))
    except Exception:
        pass


# Backwards-compatible alias (couple_chat.py referenced this name)
async def check_and_lock(key: str) -> bool:
    return await acquire(key)
# backend/services/redis/rate_limit.py
import logging
from backend.core.redis_client import get_redis
from . import keys

logger = logging.getLogger(__name__)

async def hit(
    action: str,
    identifier: str,
    limit: int,
    window_seconds: int
) -> tuple[bool, int]:

    try:
        r = get_redis()
        key = keys.rate_limit(action, identifier)

        count = await r.incr(key)

        ttl = await r.ttl(key)

        # Set expiry for new or old permanent keys
        if ttl == -1:
            await r.expire(key, window_seconds)

        allowed = count <= limit
        remaining = max(0, limit - count)

        return allowed, remaining

    except Exception as e:
        logger.warning(
            "rate_limit hit failed (%s): %s",
            action,
            e
        )
        return True, limit



async def retry_after(action: str, identifier: str) -> int:
    """Seconds until window resets. Returns 0 on error."""
    try:
        ttl = await get_redis().ttl(keys.rate_limit(action, identifier))
        return max(0, int(ttl or 0))
    except Exception:
        return 0


async def reset(action: str, identifier: str) -> None:
    try:
        await get_redis().delete(keys.rate_limit(action, identifier))
    except Exception:
        pass

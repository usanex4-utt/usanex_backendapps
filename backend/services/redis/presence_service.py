# backend/services/redis/presence_service.py
import logging

from backend.core.redis_client import get_redis
from . import keys

logger = logging.getLogger(__name__)

# Short TTL — heartbeat from WS keeps refreshing this.
PRESENCE_TTL = 60  # seconds


async def set_online(user_id: int) -> None:
    """Mark user online (also refreshes TTL on heartbeat)."""
    try:
        await get_redis().set(
            keys.presence(user_id), "1", ex=PRESENCE_TTL
        )
    except Exception as e:
        logger.warning("presence set_online failed: %s", e)


async def set_offline(user_id: int) -> None:
    try:
        await get_redis().delete(keys.presence(user_id))
    except Exception as e:
        logger.warning("presence set_offline failed: %s", e)


async def is_online(user_id: int) -> bool:
    try:
        return bool(await get_redis().exists(keys.presence(user_id)))
    except Exception:
        return False
# backend/services/redis/presence_service.py
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.core.redis_client import get_redis
from . import keys

logger = logging.getLogger(__name__)
PRESENCE_TTL = 30 # seconds

async def set_online(user_id: int, room_id: Optional[int] = None) -> None:
    r = get_redis()
    payload = json.dumps({
        "online": True, "room_id": room_id,
        "ts": int(datetime.now(timezone.utc).timestamp()),
    })
    await r.set(keys.presence(user_id), payload, ex=PRESENCE_TTL)

async def heartbeat(user_id: int) -> None:
    await set_online(user_id)

async def set_offline(user_id: int) -> None:
    await get_redis().delete(keys.presence(user_id))

async def is_online(user_id: int) -> bool:
    return bool(await get_redis().exists(keys.presence(user_id)))
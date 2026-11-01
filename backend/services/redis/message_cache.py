# backend/services/redis/message_cache.py
import json
from backend.core.redis_client import get_redis
from . import keys

RECENT_LIMIT = 50

def _serialize(msg) -> dict:
    return {
        "id": msg.id, "room_id": msg.room_id, "sender_id": msg.sender_id,
        "receiver_id": msg.receiver_id, "content": msg.content,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }

async def push_message(msg) -> None:
    r = get_redis()
    key = keys.recent_messages(msg.room_id)
    await r.lpush(key, json.dumps(_serialize(msg)))
    await r.ltrim(key, 0, RECENT_LIMIT - 1)
    await r.expire(key, 3600) # 1 hour cache

async def get_recent(room_id: int) -> list[dict] | None:
    rows = await get_redis().lrange(keys.recent_messages(room_id), 0, RECENT_LIMIT - 1)
    return [json.loads(x) for x in rows] if rows else None

async def incr_unread(user_id: int, room_id: int) -> None:
    await get_redis().hincrby(keys.unread(user_id), str(room_id), 1)

async def clear_unread(user_id: int, room_id: int) -> None:
    await get_redis().hdel(keys.unread(user_id), str(room_id))

async def get_unread_counts(user_id: int) -> dict:
    raw = await get_redis().hgetall(keys.unread(user_id))
    return {int(k): int(v) for k, v in raw.items()}
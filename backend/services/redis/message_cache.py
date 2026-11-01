# backend/services/redis/message_cache.py
import json
import logging
from backend.core.redis_client import get_redis
from . import keys

logger = logging.getLogger(__name__)

RECENT_LIMIT = 50
RECENT_TTL = 3600  # 1 hour


def _serialize(msg) -> dict:
    return {
        "id": msg.id,
        "room_id": getattr(msg, "room_id", None),
        "sender_id": msg.sender_id,
        "receiver_id": msg.receiver_id,
        "content": msg.content,
        "media_url": getattr(msg, "media_url", None),
        "media_type": getattr(msg, "media_type", None),
        "message_type": getattr(msg, "message_type", "text"),
        "reply_to_message_id": getattr(msg, "reply_to_message_id", None),
        "is_read": bool(getattr(msg, "is_read", 0)),
        "is_deleted": bool(getattr(msg, "is_deleted", 0)),
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


# ---------------------------------------------------------
# Couple room messages (uses room_id)
# ---------------------------------------------------------

async def push_message(msg) -> None:
    try:
        r = get_redis()
        key = keys.recent_messages(msg.room_id)
        await r.lpush(key, json.dumps(_serialize(msg)))
        await r.ltrim(key, 0, RECENT_LIMIT - 1)
        await r.expire(key, RECENT_TTL)
    except Exception as e:
        logger.warning("push_message failed: %s", e)


async def get_recent(room_id: int) -> list[dict] | None:
    try:
        rows = await get_redis().lrange(keys.recent_messages(room_id), 0, RECENT_LIMIT - 1)
        return [json.loads(x) for x in rows] if rows else None
    except Exception as e:
        logger.warning("get_recent failed: %s", e)
        return None


async def invalidate_room(room_id: int) -> None:
    try:
        await get_redis().delete(keys.recent_messages(room_id))
    except Exception:
        pass


# ---------------------------------------------------------
# Chat DMs (uses sorted pair key)
# ---------------------------------------------------------

async def push_chat_message(msg) -> None:
    try:
        r = get_redis()
        key = keys.room_pair(msg.sender_id, msg.receiver_id)
        await r.lpush(key, json.dumps(_serialize(msg)))
        await r.ltrim(key, 0, RECENT_LIMIT - 1)
        await r.expire(key, RECENT_TTL)
    except Exception as e:
        logger.warning("push_chat_message failed: %s", e)


async def get_recent_chat(user_a: int, user_b: int, limit: int = 50) -> list[dict] | None:
    try:
        key = keys.room_pair(user_a, user_b)
        rows = await get_redis().lrange(key, 0, min(limit, RECENT_LIMIT) - 1)
        return [json.loads(x) for x in rows] if rows else None
    except Exception as e:
        logger.warning("get_recent_chat failed: %s", e)
        return None


async def invalidate_chat(user_a: int, user_b: int) -> None:
    try:
        await get_redis().delete(keys.room_pair(user_a, user_b))
    except Exception:
        pass


# ---------------------------------------------------------
# Unread counters (already existed)
# ---------------------------------------------------------

async def incr_unread(user_id: int, room_id: int) -> None:
    try:
        await get_redis().hincrby(keys.unread(user_id), str(room_id), 1)
    except Exception as e:
        logger.warning("incr_unread failed: %s", e)


async def clear_unread(user_id: int, room_id: int) -> None:
    try:
        await get_redis().hdel(keys.unread(user_id), str(room_id))
    except Exception as e:
        logger.warning("clear_unread failed: %s", e)


async def get_unread_counts(user_id: int) -> dict:
    try:
        raw = await get_redis().hgetall(keys.unread(user_id))
        return {int(k): int(v) for k, v in raw.items()}
    except Exception:
        return {}
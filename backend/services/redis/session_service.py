
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from backend.core.redis_client import get_redis
from . import keys

logger = logging.getLogger(__name__)

SESSION_TTL = 7 * 24 * 3600  # 7 days


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


async def cache_session(
    token: str,
    user_id: int,
    expires_at: datetime
) -> None:
    """Store session in Redis after login/session creation."""

    try:
        r = get_redis()

        expiry_timestamp = int(expires_at.timestamp())
        ttl = max(60, expiry_timestamp - _now_ts())

        payload = json.dumps({
            "user_id": user_id,
            "exp": expiry_timestamp
        })

        pipe = r.pipeline()

        pipe.set(
            keys.session(token),
            payload,
            ex=ttl
        )

        pipe.sadd(
            keys.user_sessions(user_id),
            token
        )

        pipe.expire(
            keys.user_sessions(user_id),
            SESSION_TTL
        )

        await pipe.execute()

    except Exception as e:
        logger.warning("Redis session cache write failed: %s", e)


async def get_session_user(token: str) -> Optional[int]:
    """Get user_id from cached session."""

    try:
        r = get_redis()

        raw = await r.get(keys.session(token))

        if not raw:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        data = json.loads(raw)

        if data.get("exp", 0) <= _now_ts():
            await invalidate_session(token, data.get("user_id"))
            return None

        return int(data["user_id"])

    except Exception as e:
        logger.warning("Redis session read failed: %s", e)
        return None


async def get_session(token: str) -> Optional[dict]:
    """Get complete session data from Redis."""

    try:
        r = get_redis()

        raw = await r.get(keys.session(token))

        if not raw:
            return None

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        data = json.loads(raw)

        if data.get("exp", 0) <= _now_ts():
            await invalidate_session(token, data.get("user_id"))
            return None

        return data

    except Exception as e:
        logger.warning("Redis session read failed: %s", e)
        return None


async def invalidate_session(
    token: str,
    user_id: Optional[int] = None
) -> None:
    """Logout — remove session from Redis."""

    try:
        r = get_redis()

        pipe = r.pipeline()

        pipe.delete(keys.session(token))

        if user_id is not None:
            pipe.srem(keys.user_sessions(user_id), token)

        await pipe.execute()

    except Exception as e:
        logger.warning("Redis session invalidate failed: %s", e)


async def delete_session(
    token: str,
    user_id: Optional[int] = None
) -> None:
    """Alias for invalidate_session to support existing auth routes."""

    await invalidate_session(token, user_id)


async def invalidate_all_user_sessions(user_id: int) -> None:
    """Logout from all devices — invalidate every user session."""

    try:
        r = get_redis()

        session_key = keys.user_sessions(user_id)

        tokens = await r.smembers(session_key)

        if not tokens:
            await r.delete(session_key)
            return

        pipe = r.pipeline()

        for token in tokens:

            if isinstance(token, bytes):
                token = token.decode("utf-8")

            pipe.delete(keys.session(token))

        pipe.delete(session_key)

        await pipe.execute()

    except Exception as e:
        logger.warning("Redis invalidate-all failed: %s", e)

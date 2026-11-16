# =========================================================
# USANEX — COUPLE REALTIME WEBSOCKET
# backend/routes/couple_realtime.py
# =========================================================

from __future__ import annotations

import asyncio
import json
import logging

from datetime import datetime, timezone


from fastapi import (
    APIRouter,
    WebSocket,
    WebSocketDisconnect,
)

from sqlalchemy.orm import Session

from ..database.database import SessionLocal
from ..database.models import User, UserSession
from ..database.couple_models import (
    CouplePresence,
    utcnow,
)

from ..routes.auth import (
    SESSION_COOKIE_NAME,
    get_valid_session,
)

# 👇 FIX: missing import that caused `NameError: name 'can_chat' is not defined`
from ..services.access_control import (
    can_chat,
    CHAT_BLOCKED_MESSAGE,
)

from ..services.redis import (
    presence_service,
    idempotency,
)

from ..services.couple_message_service import (
    get_or_create_couple_room,
    create_couple_message,
    get_couple_messages,
    mark_message_delivered,
    mark_message_seen,
    serialize_message,
)

from ..websocket.couple_manager import couple_manager


# =========================================================
# OPTIONAL AI MOOD MODEL
# =========================================================

_MOOD_MODEL_AVAILABLE = False

try:
    from ..ml.mood_model import (
        MoodClassifier,
        tokenize,
    )

    _MOOD_MODEL_AVAILABLE = True

except Exception as exc:

    logging.warning(
        "AI Mood Model could not be loaded: %s",
        exc,
    )

    class MoodClassifier:
        pass

    def tokenize(text: str):
        return set()


# =========================================================
# ROUTER
# =========================================================

router = APIRouter(tags=["Couple Realtime"])

logger = logging.getLogger(__name__)


# =========================================================
# CONSTANTS
# =========================================================

MAX_MESSAGE_LENGTH = 4000

HEARTBEAT_TIMEOUT_SECONDS = 75

HEARTBEAT_CHECK_INTERVAL_SECONDS = 15

MOOD_TTL_SECONDS = 86400

ALLOWED_MOODS = {
    "romantic",
    "deep_love",
    "happy",
    "funny",
    "sad",
    "emotional",
    "angry",
    "frustrated",
    "caring",
    "comfort",
    "flirty",
    "passionate",
    "good_night",
    "good_morning",
    "memory",
    "celebration",
    "birthday",
    "future_marriage",
    "missing_you",
    "apology",
    "appreciation",
    "game",
    "photo_memory",
    "serious_talk",
    "deep_conversation",
    "calm",
}


# =========================================================
# MOOD STATE
# =========================================================

_room_moods: dict[int, str] = {}

_mood_ai: MoodClassifier | None = None

_mood_ai_disabled = not _MOOD_MODEL_AVAILABLE


# =========================================================
# DATE HELPERS
# =========================================================

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_utc_iso(value):
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.isoformat()


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_public_id(db: Session, public_user_id: str):
    if not public_user_id:
        return None

    return (
        db.query(User)
        .filter(User.user_id == public_user_id)
        .first()
    )


# =========================================================
# AUTH
# =========================================================

async def _authenticate_ws(
    db: Session,
    session_token: str,
):
    """Return User or None. Uses the same session path as HTTP routes."""
    if not session_token:
        return None

    try:
        session = await get_valid_session(db, session_token)
    except Exception:
        logger.exception("get_valid_session failed in couple WS")
        return None

    if session is None:
        return None

    user = (
        db.query(User)
        .filter(User.id == session.user_id)
        .first()
    )

    if user is None:
        return None

    if not getattr(user, "is_active", True):
        return None

    return user


# =========================================================
# PRESENCE DATABASE HELPERS
# =========================================================

def get_or_create_presence(db: Session, user_id: int):
    presence = (
        db.query(CouplePresence)
        .filter(CouplePresence.user_id == user_id)
        .first()
    )

    if presence is not None:
        return presence

    presence = CouplePresence(user_id=user_id, is_online=False)
    db.add(presence)

    try:
        db.commit()
        db.refresh(presence)
        return presence
    except Exception:
        db.rollback()
        logger.exception("Could not create presence for user %s", user_id)
        return (
            db.query(CouplePresence)
            .filter(CouplePresence.user_id == user_id)
            .first()
        )


def save_user_online(db: Session, user_id: int, room_id: int):
    presence = get_or_create_presence(db, user_id)
    if presence is None:
        return

    try:
        presence.is_online = True
        presence.last_online_at = utcnow()
        presence.current_room_id = room_id
        presence.updated_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to save user online status: %s", user_id)


def save_user_offline(db: Session, user_id: int):
    presence = get_or_create_presence(db, user_id)
    if presence is None:
        return

    try:
        presence.is_online = False
        presence.last_seen_at = utcnow()
        presence.current_room_id = None
        presence.updated_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to save user offline status: %s", user_id)


async def get_presence_payload(db: Session, user_id: int):
    presence = (
        db.query(CouplePresence)
        .filter(CouplePresence.user_id == user_id)
        .first()
    )

    try:
        is_online = await presence_service.is_online(user_id)
    except Exception:
        logger.warning(
            "Redis presence lookup failed for user %s",
            user_id,
            exc_info=True,
        )
        is_online = bool(presence and presence.is_online)

    if is_online:
        return {"is_online": True, "last_seen": None}

    return {
        "is_online": False,
        "last_seen": (
            to_utc_iso(presence.last_seen_at)
            if presence
            else None
        ),
    }


# =========================================================
# MOOD AI
# =========================================================

def _get_mood_ai():
    global _mood_ai
    global _mood_ai_disabled

    if _mood_ai_disabled:
        return None

    if _mood_ai is not None:
        return _mood_ai

    try:
        _mood_ai = MoodClassifier()
        logger.info("Couple WS mood model loaded")
        return _mood_ai
    except Exception as exc:
        logger.warning(
            "Could not instantiate MoodClassifier: %s",
            exc,
        )
        _mood_ai_disabled = True
        return None


async def _predict_mood_async(text: str) -> str | None:
    ai = _get_mood_ai()
    if ai is None:
        return None

    try:
        return await asyncio.to_thread(ai.predict, text)
    except Exception:
        logger.exception("mood prediction failed")
        return None


# =========================================================
# MESSAGE HANDLER (per incoming `{type: "message"}` payload)
# =========================================================

async def _handle_incoming_message(
    websocket: WebSocket,
    db: Session,
    current_user: User,
    partner_id: int,
    room,
    payload: dict,
):
    content = (payload.get("content") or "").strip()
    media_url = payload.get("media_url")
    media_type = payload.get("media_type")
    message_type = payload.get("message_type") or "text"
    client_message_id = payload.get("client_message_id")
    reply_to_id = payload.get("reply_to_message_id")

    # Validation
    if not content and not media_url:
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Message cannot be empty",
                "client_message_id": client_message_id,
            })
        except Exception:
            pass
        return

    if len(content) > MAX_MESSAGE_LENGTH:
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Message too long (max {MAX_MESSAGE_LENGTH})",
                "client_message_id": client_message_id,
            })
        except Exception:
            pass
        return

    # Idempotency: same client_message_id from same user = same message
    idem_key = None
    if client_message_id:
        idem_key = f"couple:msg:{current_user.id}:{client_message_id}"
        try:
            if not await idempotency.acquire(idem_key, ttl=300):
                cached = await idempotency.get_result(idem_key)
                if cached:
                    try:
                        await websocket.send_json(cached)
                    except Exception:
                        pass
                    return
        except Exception:
            pass  # fail-open — allow duplicate processing if Redis is down

    # Persist
    try:
        new_msg = await create_couple_message(
            db=db,
            room_id=room.id,
            sender_id=current_user.id,
            receiver_id=partner_id,
            content=content or None,
            media_url=media_url,
            media_type=media_type,
            message_type=message_type,
            reply_to_message_id=reply_to_id,
        )
    except Exception:
        logger.exception("create_couple_message failed")
        try:
            await websocket.send_json({
                "type": "error",
                "message": "Could not save message",
                "client_message_id": client_message_id,
            })
        except Exception:
            pass
        return

    serialized = serialize_message(new_msg)

    # Echo to sender
    sender_event = {
        "type": "message",
        "id": new_msg.id,
        "sender_id": current_user.id,
        "client_message_id": client_message_id,
        "content": content,
        "media_url": media_url,
        "media_type": media_type,
        "message_type": message_type,
        "created_at": serialized.get("created_at"),
        "timestamp": serialized.get("created_at"),
        "status": "sent",
    }

    try:
        await websocket.send_json(sender_event)
    except Exception:
        logger.exception("failed to echo message to sender")

    # Relay to partner (cross-worker via Redis)
    partner_event = {k: v for k, v in sender_event.items() if k != "status"}
    partner_event["receiver_id"] = partner_id
    try:
        await couple_manager.send_to_user(partner_id, partner_event)
    except Exception:
        logger.exception("failed to relay message to partner")

    # Delivery receipt (best-effort)
    try:
        await asyncio.to_thread(
            mark_message_delivered, db, new_msg.id, partner_id
        )
        await couple_manager.send_to_user(current_user.id, {
            "type": "message_receipt",
            "message_id": new_msg.id,
            "client_message_id": client_message_id,
            "status": "delivered",
        })
    except Exception:
        pass

    # AI mood detection
    if content:
        mood = await _predict_mood_async(content)
        if mood and mood in ALLOWED_MOODS:
            _room_moods[room.id] = mood
            mood_event = {"type": "mood", "mood": mood}
            try:
                await websocket.send_json(mood_event)
            except Exception:
                pass
            try:
                await couple_manager.send_to_user(partner_id, mood_event)
            except Exception:
                pass

    # Cache idempotency result
    if idem_key:
        try:
            await idempotency.store_result(idem_key, sender_event, ttl=300)
        except Exception:
            pass


# =========================================================
# WEBSOCKET ENDPOINT
# =========================================================

@router.websocket("/ws/couple/{partner_user_id}")
async def couple_realtime(
    websocket: WebSocket,
    partner_user_id: str,
):
    db = SessionLocal()

    current_user: User | None = None
    partner: User | None = None
    room = None
    registered = False
    partner_id: int | None = None

    try:
        # -------------------------------------------------
        # 1. ACCEPT FIRST (so client gets proper WS close codes)
        # -------------------------------------------------
        try:
            await websocket.accept()
        except Exception:
            logger.exception("WebSocket accept failed")
            return

        # -------------------------------------------------
        # 2. AUTHENTICATE
        # -------------------------------------------------
        session_token = websocket.cookies.get(SESSION_COOKIE_NAME)
        if not session_token:
            session_token = websocket.query_params.get("token")

        current_user = await _authenticate_ws(db, session_token or "")

        if current_user is None:
            logger.warning(
                "Couple WS auth failed: partner=%s",
                partner_user_id,
            )
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "Authentication required",
                })
            except Exception:
                pass
            await websocket.close(code=4401, reason="Unauthorized")
            return

        # -------------------------------------------------
        # 3. PARTNER LOOKUP
        # -------------------------------------------------
        partner = get_user_by_public_id(db, partner_user_id)

        if partner is None or not getattr(partner, "is_active", True):
            logger.warning(
                "Couple WS partner missing: user=%s partner=%s",
                current_user.id,
                partner_user_id,
            )
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "Partner not found",
                })
            except Exception:
                pass
            await websocket.close(code=4404, reason="Partner not found")
            return

        if partner.id == current_user.id:
            logger.warning(
                "Couple WS blocked self-chat: user=%s",
                current_user.id,
            )
            await websocket.close(code=1008, reason="Cannot chat with yourself")
            return

        partner_id = partner.id

        # -------------------------------------------------
        # 4. ACCESS GATE — the line that was crashing
        # -------------------------------------------------
        try:
            can_chat_access = can_chat(db, current_user.id, partner_id)
        except Exception:
            logger.exception(
                "can_chat() failed for user=%s partner=%s",
                current_user.id,
                partner_id,
            )
            can_chat_access = False

        if not can_chat_access:
            logger.warning(
                "Couple WS denied: user=%s partner=%s not connected",
                current_user.id,
                partner_id,
            )
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": CHAT_BLOCKED_MESSAGE,
                })
            except Exception:
                pass
            await websocket.close(
                code=1008,
                reason="Connection required",
            )
            return

        # -------------------------------------------------
        # 5. REGISTER PRESENCE
        # -------------------------------------------------
        try:
            await couple_manager.connect(websocket, current_user.id)
            registered = True
        except Exception:
            logger.exception("couple_manager.connect failed")
            await websocket.close(code=1011, reason="Server error")
            return

        # -------------------------------------------------
        # 6. ROOM + PRESENCE DB
        # -------------------------------------------------
        try:
            room = await asyncio.to_thread(
                get_or_create_couple_room,
                db,
                current_user.id,
                partner_id,
            )
        except Exception:
            logger.exception("Could not prepare couple room")
            await websocket.close(code=1011, reason="Room setup failed")
            return

        save_user_online(db, current_user.id, room.id)

        # -------------------------------------------------
        # 7. INITIAL PRESENCE + MOOD
        # -------------------------------------------------
        partner_presence = await get_presence_payload(db, partner_id)

        room_mood = _room_moods.get(room.id)
        if room_mood is None:
            room_mood = "calm"
            _room_moods[room.id] = room_mood

        try:
            await websocket.send_json({
                "type": "connected",
                "partner_online": partner_presence["is_online"],
                "partner_last_seen": partner_presence["last_seen"],
                "mood": room_mood,
            })
        except Exception:
            logger.exception("failed to send connected event")

        # -------------------------------------------------
        # 8. MESSAGE HISTORY
        # -------------------------------------------------
        try:
            history = await get_couple_messages(db, room.id, limit=50)
            await websocket.send_json({
                "type": "message_history",
                "messages": [
                    serialize_message(m) for m in history
                ],
            })
        except Exception:
            logger.exception("failed to send message history")

        # -------------------------------------------------
        # 9. NOTIFY PARTNER: we're online
        # -------------------------------------------------
        try:
            await couple_manager.send_presence(
                receiver_id=partner_id,
                user_id=current_user.id,
                is_online=True,
                last_seen=None,
            )
        except Exception:
            pass

        # -------------------------------------------------
        # 10. HEARTBEAT TASK
        # -------------------------------------------------
        async def heartbeat_loop():
            try:
                while True:
                    await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)

                    age = couple_manager.heartbeat_age(websocket)
                    if (
                        age is not None
                        and age > HEARTBEAT_TIMEOUT_SECONDS
                    ):
                        logger.warning(
                            "Couple WS heartbeat timeout: user=%s",
                            current_user.id,
                        )
                        try:
                            await websocket.close(
                                code=1001,
                                reason="Heartbeat timeout",
                            )
                        except Exception:
                            pass
                        return

                    try:
                        await couple_manager.touch(
                            websocket,
                            user_id=current_user.id,
                        )
                    except Exception:
                        pass
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("heartbeat_loop crashed")

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # -------------------------------------------------
        # 11. MESSAGE LOOP
        # -------------------------------------------------
        try:
            while True:
                raw = await websocket.receive_text()

                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if not isinstance(payload, dict):
                    continue

                event_type = payload.get("type")

                # ----- PING -----
                if event_type == "ping":
                    try:
                        await websocket.send_json({"type": "pong"})
                    except Exception:
                        pass
                    continue

                # ----- TYPING -----
                if event_type in ("typing", "typing_start", "typing_stop"):
                    is_typing = (
                        event_type == "typing_start"
                        or payload.get("typing") is True
                    )
                    try:
                        await couple_manager.send_typing(
                            sender_id=current_user.id,
                            receiver_id=partner_id,
                            is_typing=is_typing,
                        )
                    except Exception:
                        pass
                    continue

                # ----- MESSAGE READ -----
                if event_type == "message_read":
                    message_id = payload.get("message_id")
                    if not message_id:
                        continue
                    try:
                        message_id_int = int(message_id)
                    except (TypeError, ValueError):
                        continue

                    try:
                        await asyncio.to_thread(
                            mark_message_seen,
                            db,
                            message_id_int,
                            current_user.id,
                        )
                    except Exception:
                        logger.exception("mark_message_seen failed")

                    try:
                        await couple_manager.send_to_user(partner_id, {
                            "type": "message_receipt",
                            "message_id": message_id_int,
                            "status": "seen",
                        })
                    except Exception:
                        pass
                    continue

                # ----- MESSAGE -----
                if event_type == "message":
                    await _handle_incoming_message(
                        websocket=websocket,
                        db=db,
                        current_user=current_user,
                        partner_id=partner_id,
                        room=room,
                        payload=payload,
                    )
                    continue

                # Unknown event type — ignore silently

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass

    # -----------------------------------------------------
    # EXCEPTIONS
    # -----------------------------------------------------
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "Couple WebSocket crashed: user=%s",
            getattr(current_user, "id", None),
        )
        try:
            await websocket.close(code=1011, reason="Server error")
        except Exception:
            pass

    # -----------------------------------------------------
    # CLEANUP
    # -----------------------------------------------------
    finally:
        if registered and current_user is not None:
            try:
                went_offline = await couple_manager.disconnect(
                    current_user.id,
                    websocket,
                )
            except Exception:
                went_offline = False

            if went_offline:
                try:
                    save_user_offline(db, current_user.id)
                except Exception:
                    pass

                last_seen = None
                try:
                    p = (
                        db.query(CouplePresence)
                        .filter(CouplePresence.user_id == current_user.id)
                        .first()
                    )
                    if p is not None and p.last_seen_at is not None:
                        last_seen = to_utc_iso(p.last_seen_at)
                except Exception:
                    pass

                if partner_id is not None:
                    try:
                        await couple_manager.send_presence(
                            receiver_id=partner_id,
                            user_id=current_user.id,
                            is_online=False,
                            last_seen=last_seen,
                        )
                    except Exception:
                        pass

        try:
            db.close()
        except Exception:
            pass


# =========================================================
# SANITY CHECK (fails at import time if the fix regresses)
# =========================================================

assert callable(can_chat), "access_control.can_chat must be imported"
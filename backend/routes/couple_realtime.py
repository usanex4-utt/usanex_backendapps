# =========================================================
# UTANEXA — COUPLE REALTIME + LAST SEEN
# File: backend/routes/couple_realtime.py
# =========================================================

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ..database.database import SessionLocal
from ..database.models import User
from ..database.couple_models import CouplePresence, utcnow

from ..routes.auth import SESSION_COOKIE_NAME, get_valid_session
from ..services.redis import presence_service, idempotency

from ..services.couple_message_service import (
    get_or_create_couple_room,
    create_couple_message,
    get_couple_messages,
    mark_message_delivered,
    mark_message_seen,
    serialize_message,
    serialize_receipt,
)

from ..websocket.couple_manager import couple_manager

# =========================================================
# SAFE AI IMPORT (Prevents Render Crash if ML folder is missing)
# =========================================================
_MOOD_MODEL_AVAILABLE = False
try:
    from ..ml.mood_model import MoodClassifier, tokenize
    _MOOD_MODEL_AVAILABLE = True
except Exception as e:
    logging.warning(f"⚠️ AI Model could not be loaded (Auto-mood disabled): {e}")
    # Dummy fallback so the server doesn't crash
    class MoodClassifier: pass
    def tokenize(text): return set()


router = APIRouter(tags=["Couple Realtime"])

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_MEDIA_URL_LENGTH = 2048
MAX_MEDIA_TYPE_LENGTH = 100
MAX_MESSAGE_TYPE_LENGTH = 50

HEARTBEAT_TIMEOUT_SECONDS = 75
HEARTBEAT_CHECK_INTERVAL_SECONDS = 15

ALLOWED_MOODS = {
    "romantic", "deep_love", "happy", "funny", "sad", "emotional", "angry",
    "frustrated", "caring", "comfort", "flirty", "passionate", "good_night",
    "good_morning", "memory", "celebration", "birthday", "future_marriage",
    "missing_you", "apology", "appreciation", "game", "photo_memory",
    "serious_talk", "deep_conversation", "calm",
}

_room_moods: dict[int, str] = {}
_MOOD_TTL_SECONDS = 86400     

_mood_ai: MoodClassifier | None = None
_mood_ai_disabled = not _MOOD_MODEL_AVAILABLE


def _get_mood_ai() -> MoodClassifier | None:
    global _mood_ai, _mood_ai_disabled

    if _mood_ai_disabled:
        return None
    if _mood_ai is not None:
        return _mood_ai

    try:
        clf = MoodClassifier()
        if clf.total_moods == 0:
            logger.warning("MoodClassifier has no training data — auto-mood disabled.")
            _mood_ai_disabled = True
            return None
        _mood_ai = clf
        logger.info("MoodClassifier loaded (%d moods, %d examples)", len(clf.mood_counts), clf.total_moods)
    except Exception as e:
        logger.error("MoodClassifier failed to load: %s", e)
        _mood_ai_disabled = True
        return None

    return _mood_ai


# =========================================================
# TIME HELPERS
# =========================================================

def utc_now_iso():
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
# USER & PRESENCE HELPERS
# =========================================================

def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_public_id(db: Session, public_user_id: str):
    if not public_user_id:
        return None
    return db.query(User).filter(User.user_id == public_user_id).first()


def get_or_create_presence(db: Session, user_id: int):
    presence = db.query(CouplePresence).filter(CouplePresence.user_id == user_id).first()

    if presence is None:
        presence = CouplePresence(user_id=user_id, is_online=False)
        db.add(presence)
        try:
            db.commit()
            db.refresh(presence)
        except Exception:
            db.rollback()
            presence = db.query(CouplePresence).filter(CouplePresence.user_id == user_id).first()
            if presence is None:
                raise
    return presence


def save_user_online(db: Session, user_id: int, room_id: int):
    presence = get_or_create_presence(db, user_id)
    presence.is_online = True
    presence.last_online_at = utcnow()
    presence.current_room_id = room_id
    presence.updated_at = utcnow()
    db.commit()
    db.refresh(presence)
    return presence


def save_user_offline(db: Session, user_id: int):
    presence = get_or_create_presence(db, user_id)
    presence.is_online = False
    presence.last_seen_at = utcnow()
    presence.current_room_id = None
    presence.updated_at = utcnow()
    db.commit()
    db.refresh(presence)
    return presence


async def get_presence_payload(db: Session, user_id: int):
    presence = db.query(CouplePresence).filter(CouplePresence.user_id == user_id).first()
    is_online = await presence_service.is_online(user_id)

    if is_online:
        return {"is_online": True, "last_seen": None}

    return {
        "is_online": False,
        "last_seen": (to_utc_iso(presence.last_seen_at) if presence is not None else None),
    }


# =========================================================
# ROOM MOOD
# =========================================================

async def _get_room_mood(room_id: int) -> str | None:
    try:
        from ..core.redis_client import get_redis
        val = await get_redis().get(f"usanex:couple:mood:{room_id}")
        if val:
            return val.decode() if isinstance(val, bytes) else val
    except Exception:
        pass
    return _room_moods.get(room_id)


async def _set_room_mood(room_id: int, mood: str) -> None:
    _room_moods[room_id] = mood
    try:
        from ..core.redis_client import get_redis
        await get_redis().set(f"usanex:couple:mood:{room_id}", mood, ex=_MOOD_TTL_SECONDS)
    except Exception:
        pass


# =========================================================
# DICTIONARY MOOD DETECTION
# =========================================================

async def _maybe_auto_mood(room, sender: User, partner: User, content: str | None) -> None:
    if not content:
        return

    ai = _get_mood_ai()
    if ai is None:
        return

    words = set(tokenize(content))
    if not words:
        return

    best_mood = None
    max_matches = 0

    for mood, word_counts in ai.word_counts.items():
        matches = sum(1 for w in words if w in word_counts)
        if matches > max_matches:
            max_matches = matches
            best_mood = mood

    if not best_mood or max_matches == 0:
        return

    previous = await _get_room_mood(room.id)
    if previous == best_mood:
        return

    await _set_room_mood(room.id, best_mood)

    mood_payload = {
        "type": "mood", "mood": best_mood, "user_id": sender.id,
        "room_id": room.id, "auto": True, "timestamp": utc_now_iso(),
    }

    await couple_manager.send_to_user(user_id=sender.id, event=mood_payload)
    await couple_manager.send_to_user(user_id=partner.id, event=mood_payload)

    print(f"🎯 DATASET MATCH: '{content}' -> {best_mood} (Matched: {max_matches})")


# =========================================================
# HEARTBEAT & ERROR
# =========================================================

async def monitor_heartbeat(websocket: WebSocket, user_id: int):
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)
            age = couple_manager.heartbeat_age(websocket)
            if age is None: return
            if age >= HEARTBEAT_TIMEOUT_SECONDS:
                try: await websocket.close(code=1001, reason="Heartbeat timeout")
                except Exception: pass
                return
    except asyncio.CancelledError: raise
    except Exception: pass


async def send_error(websocket: WebSocket, message: str):
    await websocket.send_json({"type": "error", "message": message})


# =========================================================
# WEBSOCKET ENDPOINT
# =========================================================

@router.websocket("/ws/couple/{partner_user_id}")
async def couple_realtime(websocket: WebSocket, partner_user_id: str):
    db: Session = SessionLocal()
    current_user = partner = room = heartbeat_task = None
    connected = False

    try:
        partner_user_id = partner_user_id.strip()
        if not partner_user_id or len(partner_user_id) > 100:
            await websocket.close(code=1008, reason="Invalid partner ID")
            return

        session_token = websocket.cookies.get(SESSION_COOKIE_NAME) or websocket.query_params.get("token")
        session = await get_valid_session(db=db, session_token=session_token)
        
        if session is None:
            await websocket.close(code=1008, reason="Authentication required")
            return

        session_user_id = session.get("user_id") if isinstance(session, dict) else session.user_id
        if not session_user_id:
            await websocket.close(code=1008, reason="Authentication required")
            return

        current_user = get_user(db=db, user_id=session_user_id)
        if current_user is None or not getattr(current_user, "is_active", True):
            await websocket.close(code=1008, reason="User not found or inactive")
            return

        partner = get_user_by_public_id(db=db, public_user_id=partner_user_id)
        if partner is None and partner_user_id.isdigit():
            partner = get_user(db=db, user_id=int(partner_user_id))

        if partner is None or not getattr(partner, "is_active", True):
            await websocket.close(code=1008, reason="Partner not found")
            return

        if current_user.id == partner.id:
            await websocket.close(code=1008, reason="Cannot chat with yourself")
            return

        # ROOM CREATION
        room = get_or_create_couple_room(db=db, user_one_id=current_user.id, user_two_id=partner.id)

        # CONNECT
        await couple_manager.connect(user_id=current_user.id, websocket=websocket)
        connected = True
        save_user_online(db=db, user_id=current_user.id, room_id=room.id)

        heartbeat_task = asyncio.create_task(monitor_heartbeat(websocket=websocket, user_id=current_user.id))
        partner_presence = await get_presence_payload(db=db, user_id=partner.id)

        await websocket.send_json({
            "type": "connected",
            "user": {"id": current_user.id, "username": current_user.username, "name": current_user.name},
            "partner": {"id": partner.id, "username": partner.username, "name": partner.name},
            "room": {"id": room.id},
            "room_id": room.id,
            "partner_online": partner_presence["is_online"],
            "partner_last_seen": partner_presence["last_seen"],
            "mood": await _get_room_mood(room.id),
            "timestamp": utc_now_iso(),
        })

        messages = await get_couple_messages(db=db, room_id=room.id, limit=50)
        await websocket.send_json({
            "type": "message_history",
            "room_id": room.id,
            "messages": [serialize_message(m) for m in messages],
            "timestamp": utc_now_iso(),
        })

        await couple_manager.send_presence(
            receiver_id=partner.id, user_id=current_user.id, is_online=True, last_seen=None,
        )

        # EVENT LOOP
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
                event_type = data.get("type", "").strip()
            except Exception:
                await send_error(websocket, "Invalid JSON")
                continue

            if event_type == "typing_start":
                await couple_manager.send_typing(sender_id=current_user.id, receiver_id=partner.id, is_typing=True)
            
            elif event_type == "typing_stop":
                await couple_manager.send_typing(sender_id=current_user.id, receiver_id=partner.id, is_typing=False)

            elif event_type in ("mood_change", "set_mood", "mood"):
                mood = data.get("mood", "").strip()
                if mood in ALLOWED_MOODS:
                    await _set_room_mood(room.id, mood)
                    mood_payload = {"type": "mood", "mood": mood, "user_id": current_user.id, "room_id": room.id, "auto": False}
                    await websocket.send_json(mood_payload)
                    await couple_manager.send_to_user(user_id=partner.id, event=mood_payload)
                else:
                    await send_error(websocket, "Unknown mood")

            elif event_type == "message":
                client_message_id = data.get("client_message_id")
                content = data.get("content", "").strip()
                media_url = data.get("media_url")
                media_type = data.get("media_type")
                message_type = data.get("message_type", "text")
                reply_to_message_id = data.get("reply_to_message_id")

                if not content and not media_url:
                    await websocket.send_json({"type": "error", "message": "Message empty", "client_message_id": client_message_id})
                    continue

                idem_key = f"couple:{current_user.id}:{client_message_id}" if client_message_id else None
                if idem_key and not await idempotency.acquire(idem_key):
                    cached = await idempotency.get_result(idem_key)
                    if cached: await websocket.send_json(cached)
                    continue

                try:
                    message = await create_couple_message(
                        db=db, room_id=room.id, sender_id=current_user.id, receiver_id=partner.id,
                        content=content, media_url=media_url, media_type=media_type,
                        message_type=message_type, reply_to_message_id=reply_to_message_id,
                    )
                except Exception as e:
                    if idem_key: await idempotency.release(idem_key)
                    logger.error(f"Failed to create message: {e}")
                    await send_error(websocket, "Failed to save message")
                    continue

                message_payload = {
                    "type": "message", "id": message.id, "message_id": message.id,
                    "client_message_id": client_message_id, "room_id": room.id,
                    "sender_id": message.sender_id, "receiver_id": message.receiver_id,
                    "content": message.content, "media_url": message.media_url, "media_type": message.media_type,
                    "message_type": message.message_type, "timestamp": to_utc_iso(message.created_at),
                }

                if idem_key: await idempotency.store_result(idem_key, message_payload)

                delivered = await couple_manager.send_to_user(user_id=partner.id, event=message_payload)
                await websocket.send_json(message_payload)

                if delivered:
                    receipt = mark_message_delivered(db=db, message_id=message.id, receiver_id=partner.id)
                    if receipt:
                        r_payload = serialize_receipt(receipt)
                        r_payload.update({"type": "message_receipt", "client_message_id": client_message_id, "message_id": message.id, "status": "delivered"})
                        await websocket.send_json(r_payload)
                else:
                    await websocket.send_json({"type": "message_receipt", "message_id": message.id, "client_message_id": client_message_id, "status": "sent"})

                # AUTO-MOOD
                try:
                    await _maybe_auto_mood(room=room, sender=current_user, partner=partner, content=content)
                except Exception: pass

            elif event_type == "message_delivered":
                try:
                    mid = int(data.get("message_id"))
                    receipt = mark_message_delivered(db=db, message_id=mid, receiver_id=current_user.id)
                    if receipt:
                        await couple_manager.send_to_user(user_id=partner.id, event={"type": "message_receipt", "message_id": mid, "status": "delivered"})
                except Exception: pass

            elif event_type == "message_read":
                try:
                    mid = int(data.get("message_id"))
                    receipt = mark_message_seen(db=db, message_id=mid, receiver_id=current_user.id)
                    if receipt:
                        await couple_manager.send_to_user(user_id=partner.id, event={"type": "message_receipt", "message_id": mid, "status": "seen"})
                except Exception: pass

            elif event_type == "ping":
                await couple_manager.touch(websocket)
                await presence_service.set_online(current_user.id)
                await websocket.send_json({"type": "pong", "timestamp": utc_now_iso()})

            else:
                await send_error(websocket, "Unknown event")

    except WebSocketDisconnect:
        logger.info("Couple WebSocket disconnected.")
    except Exception as e:
        logger.exception("Couple WebSocket processing failed.")
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        if current_user and connected:
            try:
                await couple_manager.disconnect(user_id=current_user.id, websocket=websocket)
                if not couple_manager.is_online(current_user.id):
                    save_user_offline(db=db, user_id=current_user.id)
                    if partner:
                        presence = db.query(CouplePresence).filter(CouplePresence.user_id == current_user.id).first()
                        await couple_manager.send_presence(receiver_id=partner.id, user_id=current_user.id, is_online=False, last_seen=(to_utc_iso(presence.last_seen_at) if presence else utc_now_iso()))
            except Exception: pass
        db.close()
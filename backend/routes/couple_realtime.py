# =========================================================
# UTANEXA — COUPLE REALTIME + LAST SEEN
# File: backend/routes/couple_realtime.py
# =========================================================

from __future__ import annotations

import asyncio
import json
import logging
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


router = APIRouter(tags=["Couple Realtime"])

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_MEDIA_URL_LENGTH = 2048
MAX_MEDIA_TYPE_LENGTH = 100
MAX_MESSAGE_TYPE_LENGTH = 50

# Heartbeat configuration
HEARTBEAT_TIMEOUT_SECONDS = 75
HEARTBEAT_CHECK_INTERVAL_SECONDS = 15

# Allowed moods (frontend MOODS list se match karna chahiye).
ALLOWED_MOODS = {
    "romantic", "deep_love", "happy", "funny", "sad", "emotional", "angry",
    "frustrated", "caring", "comfort", "flirty", "passionate", "good_night",
    "good_morning", "memory", "celebration", "birthday", "future_marriage",
    "missing_you", "apology", "appreciation", "game", "photo_memory",
    "serious_talk", "deep_conversation", "calm",
}

# Per-room current mood memory (room_id -> mood key).
_room_moods: dict[int, str] = {}


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
# USER HELPERS
# =========================================================

def get_user(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_public_id(db: Session, public_user_id: str):
    if not public_user_id:
        return None
    return db.query(User).filter(User.user_id == public_user_id).first()


# =========================================================
# PRESENCE DATABASE HELPERS
# =========================================================

def get_or_create_presence(db: Session, user_id: int):
    presence = db.query(CouplePresence).filter(CouplePresence.user_id == user_id).first()

    if presence is None:
        presence = CouplePresence(
            user_id=user_id,
            is_online=False,
            last_seen_at=None,
            last_online_at=None,
        )
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
    # Redis-backed presence is cross-worker correct.
    is_online = await presence_service.is_online(user_id)

    if is_online:
        return {"is_online": True, "last_seen": None}

    return {
        "is_online": False,
        "last_seen": (to_utc_iso(presence.last_seen_at) if presence is not None else None),
    }


# =========================================================
# HEARTBEAT MONITOR
# =========================================================

async def monitor_heartbeat(websocket: WebSocket, user_id: int):
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL_SECONDS)
            age = couple_manager.heartbeat_age(websocket)
            if age is None:
                return
            if age >= HEARTBEAT_TIMEOUT_SECONDS:
                logger.info("Couple heartbeat timed out for user %s", user_id)
                try:
                    await websocket.close(code=1001, reason="Heartbeat timeout")
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Couple heartbeat monitor failed for user %s", user_id)


# =========================================================
# SAFE ERROR RESPONSE
# =========================================================

async def send_error(websocket: WebSocket, message: str):
    await websocket.send_json({
        "type": "error",
        "message": message,
    })


# =========================================================
# WEBSOCKET
# =========================================================

@router.websocket("/ws/couple/{partner_user_id}")
async def couple_realtime(websocket: WebSocket, partner_user_id: str):
    db: Session = SessionLocal()
    current_user = None
    partner = None
    room = None
    connected = False
    heartbeat_task = None

    try:
        # INPUT VALIDATION
        partner_user_id = partner_user_id.strip()
        if not partner_user_id or len(partner_user_id) > 100:
            await websocket.close(code=1008, reason="Invalid partner ID")
            return

        # AUTHENTICATION
        session_token = websocket.cookies.get(SESSION_COOKIE_NAME)
        if not session_token:
            session_token = websocket.query_params.get("token")

        session = await get_valid_session(db=db, session_token=session_token)
        if session is None:
            await websocket.close(code=1008, reason="Authentication required")
            return

        # Normalize dict (Redis cache) vs UserSession (DB)
        if isinstance(session, dict):
            session_user_id = session.get("user_id")
            session_active = True  # only valid sessions are cached
        else:
            session_user_id = session.user_id
            session_active = getattr(session, "is_active", True)

        if not session_active or session_user_id is None:
            await websocket.close(code=1008, reason="Authentication required")
            return

        current_user = get_user(db=db, user_id=session_user_id)
        if current_user is None:
            await websocket.close(code=1008, reason="User not found")
            return
        if not getattr(current_user, "is_active", True):
            await websocket.close(code=1008, reason="Inactive account")
            return

        # PARTNER LOOKUP
        partner = get_user_by_public_id(db=db, public_user_id=partner_user_id)
        if partner is None and partner_user_id.isdigit():
            partner = get_user(db=db, user_id=int(partner_user_id))

        if partner is None:
            await websocket.close(code=1008, reason="Partner not found")
            return
        if not getattr(partner, "is_active", True):
            await websocket.close(code=1008, reason="Partner not found")
            return

        if current_user.id == partner.id:
            await websocket.close(code=1008, reason="Cannot chat with yourself")
            return

        # ROOM
        room = get_or_create_couple_room(db=db, user_one_id=current_user.id, user_two_id=partner.id)

        # CONNECT
        await couple_manager.connect(user_id=current_user.id, websocket=websocket)
        connected = True
        save_user_online(db=db, user_id=current_user.id, room_id=room.id)

        heartbeat_task = asyncio.create_task(
            monitor_heartbeat(websocket=websocket, user_id=current_user.id)
        )

        partner_presence = await get_presence_payload(db=db, user_id=partner.id)

        # CONNECTED EVENT
        await websocket.send_json({
            "type": "connected",
            "user": {
                "id": current_user.id,
                "username": current_user.username,
                "name": current_user.name,
            },
            "partner": {
                "id": partner.id,
                "username": partner.username,
                "name": partner.name,
            },
            "room": {"id": room.id},
            "room_id": room.id,
            "partner_online": partner_presence["is_online"],
            "partner_last_seen": partner_presence["last_seen"],
            "mood": _room_moods.get(room.id),
            "timestamp": utc_now_iso(),
        })

        # MESSAGE HISTORY (cache-first; may return dicts or ORM objects)
        messages = await get_couple_messages(db=db, room_id=room.id, limit=50)
        await websocket.send_json({
            "type": "message_history",
            "room_id": room.id,
            "messages": [serialize_message(message) for message in messages],
            "timestamp": utc_now_iso(),
        })

        # PRESENCE EVENT TO PARTNER
        await couple_manager.send_presence(
            receiver_id=partner.id,
            user_id=current_user.id,
            is_online=True,
            last_seen=None,
        )

        # EVENT LOOP
        while True:
            raw_data = await websocket.receive_text()

            try:
                data = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                await send_error(websocket, "Invalid JSON")
                continue

            if not isinstance(data, dict):
                await send_error(websocket, "Invalid event")
                continue

            event_type = data.get("type")

            if not isinstance(event_type, str):
                await send_error(websocket, "Event type is required")
                continue

            event_type = event_type.strip()

            # TYPING START
            if event_type == "typing_start":
                await couple_manager.send_typing(
                    sender_id=current_user.id,
                    receiver_id=partner.id,
                    is_typing=True,
                )
                continue

            # TYPING STOP
            if event_type == "typing_stop":
                await couple_manager.send_typing(
                    sender_id=current_user.id,
                    receiver_id=partner.id,
                    is_typing=False,
                )
                continue

            # MOOD CHANGE — both sides theme sync
            if event_type in ("mood_change", "set_mood", "mood"):
                mood = data.get("mood")

                if not isinstance(mood, str):
                    await send_error(websocket, "Invalid mood")
                    continue

                mood = mood.strip()

                if mood not in ALLOWED_MOODS:
                    await send_error(websocket, "Unknown mood")
                    continue

                _room_moods[room.id] = mood

                mood_payload = {
                    "type": "mood",
                    "mood": mood,
                    "user_id": current_user.id,
                    "room_id": room.id,
                    "timestamp": utc_now_iso(),
                }

                await websocket.send_json(mood_payload)
                await couple_manager.send_to_user(user_id=partner.id, event=mood_payload)
                continue

            # NEW MESSAGE
            if event_type == "message":
                client_message_id = (
                    data.get("client_message_id")
                    or data.get("client_id")
                    or data.get("message_id")
                )
                content = data.get("content")
                media_url = data.get("media_url")
                media_type = data.get("media_type")
                message_type = data.get("message_type", "text")
                reply_to_message_id = data.get("reply_to_message_id")

                if content is not None and not isinstance(content, str):
                    await send_error(websocket, "Invalid message content")
                    continue

                if media_url is not None:
                    if not isinstance(media_url, str):
                        await send_error(websocket, "Invalid media URL")
                        continue
                    media_url = media_url.strip()
                    if not media_url or len(media_url) > MAX_MEDIA_URL_LENGTH:
                        await send_error(websocket, "Invalid media URL")
                        continue

                if media_type is not None:
                    if not isinstance(media_type, str) or len(media_type) > MAX_MEDIA_TYPE_LENGTH:
                        await send_error(websocket, "Invalid media type")
                        continue

                if not isinstance(message_type, str) or not message_type or len(message_type) > MAX_MESSAGE_TYPE_LENGTH:
                    await send_error(websocket, "Invalid message type")
                    continue

                if not content and not media_url:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Message cannot be empty",
                        "client_message_id": client_message_id,
                    })
                    continue

                if content is not None:
                    content = content.strip()
                    if not content:
                        content = None
                    if content is not None and len(content) > MAX_MESSAGE_LENGTH:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Message is too long",
                            "client_message_id": client_message_id,
                        })
                        continue

                if reply_to_message_id is not None:
                    try:
                        reply_to_message_id = int(reply_to_message_id)
                    except (TypeError, ValueError):
                        await send_error(websocket, "Invalid reply message ID")
                        continue

                # -------- IDEMPOTENCY GUARD --------
                idem_key = None
                if isinstance(client_message_id, str) and client_message_id:
                    idem_key = f"couple:{current_user.id}:{client_message_id}"
                    if not await idempotency.acquire(idem_key):
                        cached_payload = await idempotency.get_result(idem_key)
                        if cached_payload:
                            # Replay the exact previous response
                            await websocket.send_json(cached_payload)
                        else:
                            await send_error(websocket, "Message is being processed")
                        continue

                # -------- CREATE MESSAGE (async, writes Redis cache) --------
                try:
                    message = await create_couple_message(
                        db=db,
                        room_id=room.id,
                        sender_id=current_user.id,
                        receiver_id=partner.id,
                        content=content,
                        media_url=media_url,
                        media_type=media_type,
                        message_type=message_type,
                        reply_to_message_id=reply_to_message_id,
                    )
                except Exception:
                    if idem_key:
                        await idempotency.release(idem_key)
                    raise

                message_payload = {
                    "type": "message",
                    "id": message.id,
                    "message_id": message.id,
                    "client_message_id": client_message_id,
                    "room_id": room.id,
                    "sender_id": message.sender_id,
                    "receiver_id": message.receiver_id,
                    "content": message.content,
                    "media_url": message.media_url,
                    "media_type": message.media_type,
                    "message_type": message.message_type,
                    "timestamp": to_utc_iso(message.created_at),
                }

                # Store the result so replays are dedup-safe
                if idem_key:
                    await idempotency.store_result(idem_key, message_payload)

                delivered = await couple_manager.send_to_user(
                    user_id=partner.id, event=message_payload
                )
                await websocket.send_json(message_payload)

                if delivered:
                    receipt = mark_message_delivered(
                        db=db, message_id=message.id, receiver_id=partner.id
                    )
                    if receipt:
                        receipt_payload = serialize_receipt(receipt)
                        receipt_payload.update({
                            "type": "message_receipt",
                            "client_message_id": client_message_id,
                            "message_id": message.id,
                            "status": "delivered",
                        })
                        await websocket.send_json(receipt_payload)
                else:
                    await websocket.send_json({
                        "type": "message_receipt",
                        "message_id": message.id,
                        "client_message_id": client_message_id,
                        "status": "sent",
                        "timestamp": utc_now_iso(),
                    })
                continue

            # MESSAGE DELIVERED
            if event_type == "message_delivered":
                try:
                    message_id = int(data.get("message_id"))
                except (TypeError, ValueError):
                    await send_error(websocket, "Invalid message ID")
                    continue

                receipt = mark_message_delivered(
                    db=db, message_id=message_id, receiver_id=current_user.id
                )
                if receipt:
                    await couple_manager.send_to_user(
                        user_id=partner.id,
                        event={
                            "type": "message_receipt",
                            "message_id": message_id,
                            "status": "delivered",
                            "timestamp": utc_now_iso(),
                        },
                    )
                continue

            # MESSAGE READ
            if event_type == "message_read":
                try:
                    message_id = int(data.get("message_id"))
                except (TypeError, ValueError):
                    await send_error(websocket, "Invalid message ID")
                    continue

                receipt = mark_message_seen(
                    db=db, message_id=message_id, receiver_id=current_user.id
                )
                if receipt:
                    await couple_manager.send_to_user(
                        user_id=partner.id,
                        event={
                            "type": "message_receipt",
                            "message_id": message_id,
                            "status": "seen",
                            "timestamp": utc_now_iso(),
                        },
                    )
                continue

            # PING — HEARTBEAT (also refreshes Redis presence TTL)
            if event_type == "ping":
                await couple_manager.touch(websocket)
                try:
                    await presence_service.set_online(current_user.id)
                except Exception:
                    pass
                await websocket.send_json({
                    "type": "pong",
                    "timestamp": utc_now_iso(),
                })
                continue

            await send_error(websocket, "Unknown event")

    except WebSocketDisconnect:
        logger.info("Couple WebSocket disconnected.")
    except Exception:
        logger.exception("Couple WebSocket processing failed.")
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if current_user is not None and connected:
            try:
                await couple_manager.disconnect(
                    user_id=current_user.id, websocket=websocket
                )
                if not couple_manager.is_online(current_user.id):
                    save_user_offline(db=db, user_id=current_user.id)
                    if partner is not None:
                        presence = (
                            db.query(CouplePresence)
                            .filter(CouplePresence.user_id == current_user.id)
                            .first()
                        )
                        await couple_manager.send_presence(
                            receiver_id=partner.id,
                            user_id=current_user.id,
                            is_online=False,
                            last_seen=(
                                to_utc_iso(presence.last_seen_at)
                                if presence else utc_now_iso()
                            ),
                        )
            except Exception:
                logger.exception("Couple WebSocket disconnect handling failed.")
        db.close()
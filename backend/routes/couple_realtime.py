
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

from ..database.models import User

from ..database.couple_models import (
    CouplePresence,
    utcnow,
)

from ..routes.auth import (
    SESSION_COOKIE_NAME,
    get_valid_session,
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

router = APIRouter(
    tags=["Couple Realtime"]
)

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

    return datetime.now(
        timezone.utc
    ).isoformat()


def to_utc_iso(value):

    if value is None:
        return None

    if value.tzinfo is None:

        value = value.replace(
            tzinfo=timezone.utc
        )

    else:

        value = value.astimezone(
            timezone.utc
        )

    return value.isoformat()


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_user(
    db: Session,
    user_id: int,
):

    return (
        db.query(User)
        .filter(
            User.id == user_id
        )
        .first()
    )


def get_user_by_public_id(
    db: Session,
    public_user_id: str,
):

    if not public_user_id:
        return None

    return (
        db.query(User)
        .filter(
            User.user_id == public_user_id
        )
        .first()
    )


# =========================================================
# PRESENCE DATABASE HELPERS
# =========================================================

def get_or_create_presence(
    db: Session,
    user_id: int,
):

    presence = (
        db.query(CouplePresence)
        .filter(
            CouplePresence.user_id == user_id
        )
        .first()
    )

    if presence is not None:
        return presence

    presence = CouplePresence(
        user_id=user_id,
        is_online=False,
    )

    db.add(presence)

    try:

        db.commit()

        db.refresh(presence)

        return presence

    except Exception:

        db.rollback()

        logger.exception(
            "Could not create presence for user %s",
            user_id,
        )

        return (
            db.query(CouplePresence)
            .filter(
                CouplePresence.user_id == user_id
            )
            .first()
        )


def save_user_online(
    db: Session,
    user_id: int,
    room_id: int,
):

    presence = get_or_create_presence(
        db,
        user_id,
    )

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

        logger.exception(
            "Failed to save user online status: %s",
            user_id,
        )


def save_user_offline(
    db: Session,
    user_id: int,
):

    presence = get_or_create_presence(
        db,
        user_id,
    )

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

        logger.exception(
            "Failed to save user offline status: %s",
            user_id,
        )


# =========================================================
# USER PRESENCE
# =========================================================

async def get_presence_payload(
    db: Session,
    user_id: int,
):

    presence = (
        db.query(CouplePresence)
        .filter(
            CouplePresence.user_id == user_id
        )
        .first()
    )

    try:

        is_online = await presence_service.is_online(
            user_id
        )

    except Exception:

        logger.warning(
            "Redis presence lookup failed for user %s",
            user_id,
            exc_info=True,
        )

        is_online = bool(
            presence and presence.is_online
        )

    if is_online:

        return {
            "is_online": True,
            "last_seen": None,
        }

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

        clf = MoodClassifier()

        if clf.total_moods == 0:

            _mood_ai_disabled = True

            return None

        _mood_ai = clf

    except Exception:

        logger.exception(
            "Could not initialize mood classifier"
        )

        _mood_ai_disabled = True

        return None

    return _mood_ai


# =========================================================
# REDIS MOOD HELPERS
# =========================================================

async def _get_room_mood(
    room_id: int,
):

    try:

        from ..core.redis_client import get_redis

        redis = get_redis()

        value = await redis.get(
            f"usanex:couple:mood:{room_id}"
        )

        if value:

            return (
                value.decode()
                if isinstance(value, bytes)
                else value
            )

    except Exception:

        logger.debug(
            "Redis mood read failed",
            exc_info=True,
        )

    return _room_moods.get(room_id)


async def _set_room_mood(
    room_id: int,
    mood: str,
):

    _room_moods[room_id] = mood

    try:

        from ..core.redis_client import get_redis

        redis = get_redis()

        await redis.set(
            f"usanex:couple:mood:{room_id}",
            mood,
            ex=MOOD_TTL_SECONDS,
        )

    except Exception:

        logger.debug(
            "Redis mood write failed",
            exc_info=True,
        )


# =========================================================
# AUTOMATIC MOOD DETECTION
# =========================================================

async def _maybe_auto_mood(
    room,
    sender: User,
    partner: User,
    content: str | None,
):

    if not content:
        return

    ai = _get_mood_ai()

    if ai is None:
        return

    words = set(
        tokenize(content)
    )

    if not words:
        return

    best_mood = None

    max_matches = 0

    for mood, word_counts in ai.word_counts.items():

        matches = sum(
            1
            for word in words
            if word in word_counts
        )

        if matches > max_matches:

            max_matches = matches

            best_mood = mood

    if not best_mood or max_matches == 0:
        return

    current_mood = await _get_room_mood(
        room.id
    )

    if current_mood == best_mood:
        return

    await _set_room_mood(
        room.id,
        best_mood,
    )

    payload = {
        "type": "mood",
        "mood": best_mood,
        "user_id": sender.id,
        "room_id": room.id,
        "auto": True,
    }

    await couple_manager.send_to_user(
        user_id=sender.id,
        event=payload,
    )

    await couple_manager.send_to_user(
        user_id=partner.id,
        event=payload,
    )

    logger.info(
        "Mood detected: %s -> %s",
        content,
        best_mood,
    )


# =========================================================
# MESSAGE SERIALIZATION
# =========================================================

def _msg_to_dict(message):

    if isinstance(message, dict):
        return message

    response = serialize_message(message)

    if isinstance(response, dict):
        return response

    if hasattr(response, "model_dump"):

        return response.model_dump(
            mode="json"
        )

    if hasattr(response, "dict"):

        return response.dict()

    return response


# =========================================================
# HEARTBEAT MONITOR
# =========================================================

async def monitor_heartbeat(
    websocket: WebSocket,
    user_id: int,
):

    try:

        while True:

            await asyncio.sleep(
                HEARTBEAT_CHECK_INTERVAL_SECONDS
            )

            age = couple_manager.heartbeat_age(
                websocket
            )

            if age is None:
                return

            if age >= HEARTBEAT_TIMEOUT_SECONDS:

                logger.warning(
                    "Heartbeat timeout for user %s",
                    user_id,
                )

                try:

                    await websocket.close(
                        code=1001,
                        reason="Heartbeat timeout",
                    )

                except Exception:

                    pass

                return

    except asyncio.CancelledError:

        raise

    except Exception:

        logger.exception(
            "Heartbeat monitor failed"
        )


# =========================================================
# WEBSOCKET ENDPOINT
# =========================================================

@router.websocket(
    "/ws/couple/{partner_user_id}"
)
async def couple_realtime(
    websocket: WebSocket,
    partner_user_id: str,
):

    db: Session = SessionLocal()

    current_user = None

    partner = None

    room = None

    heartbeat_task = None

    connected = False

    try:

        # -------------------------------------------------
        # ACCEPT CONNECTION
        # -------------------------------------------------

        await websocket.accept()

        # -------------------------------------------------
        # VALIDATE PARTNER ID
        # -------------------------------------------------

        partner_user_id = partner_user_id.strip()

        if (
            not partner_user_id
            or len(partner_user_id) > 100
        ):

            await websocket.send_json({
                "type": "error",
                "message": "Invalid partner ID",
            })

            await websocket.close(
                code=1008
            )

            return

        # -------------------------------------------------
        # AUTHENTICATION
        # -------------------------------------------------

        session_token = (
            websocket.cookies.get(
                SESSION_COOKIE_NAME
            )
            or websocket.query_params.get("token")
        )

        session = await get_valid_session(
            db=db,
            session_token=session_token,
        )

        if session is None:

            await websocket.send_json({
                "type": "error",
                "message": "Authentication required",
            })

            await websocket.close(
                code=1008
            )

            return

        session_user_id = (
            session.get("user_id")
            if isinstance(session, dict)
            else session.user_id
        )

        current_user = get_user(
            db=db,
            user_id=session_user_id,
        )

        if (
            current_user is None
            or not getattr(
                current_user,
                "is_active",
                True,
            )
        ):

            await websocket.send_json({
                "type": "error",
                "message": "User not found or inactive",
            })

            await websocket.close(
                code=1008
            )

            return

        # -------------------------------------------------
        # FIND PARTNER
        # -------------------------------------------------

        partner = get_user_by_public_id(
            db=db,
            public_user_id=partner_user_id,
        )

        if (
            partner is None
            and partner_user_id.isdigit()
        ):

            partner = get_user(
                db=db,
                user_id=int(partner_user_id),
            )

        if (
            partner is None
            or not getattr(
                partner,
                "is_active",
                True,
            )
        ):

            await websocket.send_json({
                "type": "error",
                "message": "Partner not found",
            })

            await websocket.close(
                code=1008
            )

            return

        if current_user.id == partner.id:

            await websocket.send_json({
                "type": "error",
                "message": "Cannot chat with yourself",
            })

            await websocket.close(
                code=1008
            )

            return

        # -------------------------------------------------
        # CREATE OR GET COUPLE ROOM
        # -------------------------------------------------

        room = get_or_create_couple_room(
            db=db,
            user_one_id=current_user.id,
            user_two_id=partner.id,
        )

        # -------------------------------------------------
        # CONNECT WEBSOCKET
        # -------------------------------------------------

        await couple_manager.connect(
            user_id=current_user.id,
            websocket=websocket,
        )

        connected = True

        save_user_online(
            db=db,
            user_id=current_user.id,
            room_id=room.id,
        )

        # -------------------------------------------------
        # START HEARTBEAT
        # -------------------------------------------------

        heartbeat_task = asyncio.create_task(
            monitor_heartbeat(
                websocket=websocket,
                user_id=current_user.id,
            )
        )

        # -------------------------------------------------
        # PARTNER PRESENCE
        # -------------------------------------------------

        partner_presence = await get_presence_payload(
            db=db,
            user_id=partner.id,
        )

        # -------------------------------------------------
        # CONNECTION RESPONSE
        # -------------------------------------------------

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

            "room": {
                "id": room.id,
            },

            "room_id": room.id,

            "partner_online": (
                partner_presence["is_online"]
            ),

            "partner_last_seen": (
                partner_presence["last_seen"]
            ),

            "mood": await _get_room_mood(
                room.id
            ),

            "timestamp": utc_now_iso(),

        })

        # -------------------------------------------------
        # MESSAGE HISTORY
        # -------------------------------------------------

        messages = await get_couple_messages(
            db=db,
            room_id=room.id,
            limit=50,
        )

        await websocket.send_json({

            "type": "message_history",

            "room_id": room.id,

            "messages": [
                _msg_to_dict(message)
                for message in messages
            ],

            "timestamp": utc_now_iso(),

        })

        # -------------------------------------------------
        # NOTIFY PARTNER ONLINE
        # -------------------------------------------------

        await couple_manager.send_presence(
            receiver_id=partner.id,
            user_id=current_user.id,
            is_online=True,
            last_seen=None,
        )

        # =================================================
        # MAIN WEBSOCKET EVENT LOOP
        # =================================================

        while True:

            raw_data = await websocket.receive_text()

            try:

                data = json.loads(raw_data)

                if not isinstance(data, dict):
                    continue

                event_type = data.get(
                    "type",
                    "",
                )

                if not isinstance(event_type, str):
                    continue

                event_type = event_type.strip()

            except (
                json.JSONDecodeError,
                TypeError,
            ):

                continue

            # ---------------------------------------------
            # TYPING START
            # ---------------------------------------------

            if event_type == "typing_start":

                await couple_manager.send_typing(
                    sender_id=current_user.id,
                    receiver_id=partner.id,
                    is_typing=True,
                )

            # ---------------------------------------------
            # TYPING STOP
            # ---------------------------------------------

            elif event_type == "typing_stop":

                await couple_manager.send_typing(
                    sender_id=current_user.id,
                    receiver_id=partner.id,
                    is_typing=False,
                )

            # ---------------------------------------------
            # MOOD CHANGE
            # ---------------------------------------------

            elif event_type in (
                "mood_change",
                "set_mood",
                "mood",
            ):

                mood = data.get(
                    "mood",
                    "",
                )

                if (
                    isinstance(mood, str)
                    and mood in ALLOWED_MOODS
                ):

                    await _set_room_mood(
                        room.id,
                        mood,
                    )

                    payload = {

                        "type": "mood",

                        "mood": mood,

                        "user_id": current_user.id,

                        "room_id": room.id,

                    }

                    await websocket.send_json(
                        payload
                    )

                    await couple_manager.send_to_user(
                        user_id=partner.id,
                        event=payload,
                    )

            # ---------------------------------------------
            # SEND MESSAGE
            # ---------------------------------------------

            elif event_type == "message":

                client_id = data.get(
                    "client_message_id"
                )

                content = data.get(
                    "content",
                    "",
                )

                media_url = data.get(
                    "media_url"
                )

                media_type = data.get(
                    "media_type"
                )

                if not isinstance(content, str):
                    continue

                content = content.strip()

                if len(content) > MAX_MESSAGE_LENGTH:

                    await websocket.send_json({

                        "type": "error",

                        "message": "Message is too long",

                        "client_message_id": client_id,

                    })

                    continue

                if not content and not media_url:
                    continue

                # Validate media fields.

                if (
                    media_url is not None
                    and not isinstance(media_url, str)
                ):
                    continue

                if (
                    media_type is not None
                    and not isinstance(media_type, str)
                ):
                    continue

                if (
                    media_url is not None
                    and len(media_url) > 2048
                ):
                    continue

                if (
                    media_type is not None
                    and len(media_type) > 50
                ):
                    continue

                if client_id is not None:

                    if (
                        not isinstance(client_id, str)
                        or not client_id.strip()
                        or len(client_id) > 100
                    ):

                        continue

                # -----------------------------------------
                # IDEMPOTENCY
                # -----------------------------------------

                idem_key = (
                    f"couple:{current_user.id}:{client_id}"
                    if client_id
                    else None
                )

                if (
                    idem_key
                    and not await idempotency.acquire(
                        idem_key
                    )
                ):

                    logger.debug(
                        "Duplicate couple message: %s",
                        idem_key,
                    )

                    continue

                # -----------------------------------------
                # SAVE MESSAGE
                # -----------------------------------------

                try:

                    message = await create_couple_message(

                        db=db,

                        room_id=room.id,

                        sender_id=current_user.id,

                        receiver_id=partner.id,

                        content=content,

                        media_url=media_url,

                        media_type=media_type,

                    )

                except Exception:

                    if idem_key:

                        try:

                            await idempotency.release(
                                idem_key
                            )

                        except Exception:

                            logger.exception(
                                "Idempotency release failed"
                            )

                    logger.exception(
                        "Failed to create couple message"
                    )

                    await websocket.send_json({

                        "type": "error",

                        "message": (
                            "Message could not be saved. "
                            "Try again."
                        ),

                        "client_message_id": client_id,

                    })

                    continue

                # -----------------------------------------
                # MESSAGE PAYLOAD
                # -----------------------------------------

                payload = {

                    "type": "message",

                    "id": message.id,

                    "message_id": message.id,

                    "client_message_id": client_id,

                    "room_id": room.id,

                    "sender_id": message.sender_id,

                    "receiver_id": message.receiver_id,

                    "content": message.content,

                    "media_url": message.media_url,

                    "media_type": message.media_type,

                    "timestamp": to_utc_iso(
                        message.created_at
                    ),

                }

                # -----------------------------------------
                # STORE IDEMPOTENCY RESULT
                # -----------------------------------------

                if idem_key:

                    try:

                        await idempotency.store_result(
                            idem_key,
                            payload,
                        )

                    except Exception:

                        logger.exception(
                            "Failed to store idempotency result"
                        )

                # -----------------------------------------
                # SEND TO PARTNER
                # -----------------------------------------

                delivered = await couple_manager.send_to_user(
                    user_id=partner.id,
                    event=payload,
                )

                # -----------------------------------------
                # SEND TO SENDER
                # -----------------------------------------

                await websocket.send_json(
                    payload
                )

                # -----------------------------------------
                # MESSAGE RECEIPT
                # -----------------------------------------

                if delivered:

                    result = mark_message_delivered(
                        db=db,
                        message_id=message.id,
                        receiver_id=partner.id,
                    )

                    if result:

                        await websocket.send_json({

                            "type": "message_receipt",

                            "message_id": message.id,

                            "status": "delivered",

                        })

                else:

                    await websocket.send_json({

                        "type": "message_receipt",

                        "message_id": message.id,

                        "status": "sent",

                    })

                # -----------------------------------------
                # AUTOMATIC MOOD DETECTION
                # -----------------------------------------

                try:

                    await _maybe_auto_mood(
                        room,
                        current_user,
                        partner,
                        content,
                    )

                except Exception:

                    logger.exception(
                        "Automatic mood detection failed"
                    )

            # ---------------------------------------------
            # MESSAGE DELIVERED
            # ---------------------------------------------

            elif event_type == "message_delivered":

                message_id = data.get(
                    "message_id"
                )

                try:

                    if message_id is not None:

                        mark_message_delivered(
                            db=db,
                            message_id=int(message_id),
                            receiver_id=current_user.id,
                        )

                except (
                    ValueError,
                    TypeError,
                ):

                    continue

            # ---------------------------------------------
            # MESSAGE READ / SEEN
            # ---------------------------------------------

            elif event_type == "message_read":

                message_id = data.get(
                    "message_id"
                )

                try:

                    if message_id is not None:

                        message_id = int(message_id)

                        mark_message_seen(
                            db=db,
                            message_id=message_id,
                            receiver_id=current_user.id,
                        )

                        await couple_manager.send_to_user(

                            user_id=partner.id,

                            event={

                                "type": "message_receipt",

                                "message_id": message_id,

                                "status": "seen",

                            },

                        )

                except (
                    ValueError,
                    TypeError,
                ):

                    continue

            # ---------------------------------------------
            # PING / PONG
            # ---------------------------------------------

            elif event_type == "ping":

                await couple_manager.touch(
                    websocket
                )

                await websocket.send_json({

                    "type": "pong",

                    "timestamp": utc_now_iso(),

                })

    # =====================================================
    # DISCONNECTION
    # =====================================================

    except WebSocketDisconnect:

        logger.info(
            "Couple WebSocket disconnected: user=%s",
            getattr(current_user, "id", None),
        )

    except Exception:

        logger.exception(
            "Couple WebSocket crashed: user=%s",
            getattr(current_user, "id", None),
        )

    # =====================================================
    # CLEANUP
    # =====================================================

    finally:

        # -------------------------------------------------
        # STOP HEARTBEAT TASK
        # -------------------------------------------------

        if heartbeat_task:

            heartbeat_task.cancel()

            try:

                await heartbeat_task

            except asyncio.CancelledError:

                pass

            except Exception:

                logger.debug(
                    "Heartbeat cleanup error",
                    exc_info=True,
                )

        # -------------------------------------------------
        # DISCONNECT USER
        # -------------------------------------------------

        if current_user and connected:

            try:

                await couple_manager.disconnect(
                    user_id=current_user.id,
                    websocket=websocket,
                )

                # Only mark offline if no other socket
                # remains connected for this user.

                if not couple_manager.is_online(
                    current_user.id
                ):

                    save_user_offline(
                        db=db,
                        user_id=current_user.id,
                    )

                    if partner:

                        await couple_manager.send_presence(

                            receiver_id=partner.id,

                            user_id=current_user.id,

                            is_online=False,

                            last_seen=utc_now_iso(),

                        )

            except Exception:

                logger.exception(
                    "Couple WebSocket cleanup failed"
                )

        # -------------------------------------------------
        # CLOSE DATABASE SESSION
        # -------------------------------------------------

        try:

            db.close()

        except Exception:

            logger.exception(
                "Database session cleanup failed"
            )

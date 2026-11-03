# backend/routes/couple_chat.py
# =========================================================
# USANEX — COUPLE CHAT ROUTES
# =========================================================

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

# ---- Database ----
from backend.database.database import get_db
from backend.database.models import User
from backend.database.couple_models import (
    CoupleRoom,
    CoupleMessage,
    CoupleMessageReceipt,
)

# ---- Auth dependency ----
# get_current_user_from_request lives in backend/routes/auth.py
from backend.routes.auth import get_current_user_from_request

# ---- Services ----
from backend.services.redis import message_cache, idempotency
from backend.services.couple_message_service import (
    get_or_create_couple_room,
    create_couple_message,
    serialize_message,
)

# ---- WebSocket ----
from backend.websocket.couple_manager import couple_manager as manager

# ---- ML ----
from backend.ml.mood_model import MoodClassifier

# ---- Schemas ----
from backend.schemas import (
    CoupleMessageCreate,
    CoupleMessageResponse,
    CoupleMessageReceiptResponse,
    CoupleRoomResponse,
    CouplePresenceResponse,
    CoupleTypingEvent,
)


router = APIRouter(prefix="/api/couple", tags=["Couple Chat"])
mood_ai = MoodClassifier()


# =========================================================
# ENDPOINT 1: Send message via explicit room_id (path param)
# =========================================================
@router.post(
    "/rooms/{room_id}/messages",
    response_model=CoupleMessageResponse,
    summary="Send message to a specific couple room",
)
async def send_message_by_room(
    room_id: int,
    payload: CoupleMessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):
    # 1. Room must exist
    room = db.query(CoupleRoom).filter(CoupleRoom.id == room_id).first()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")

    # 2. Sender & receiver must both belong to the room
    if current_user.id not in (room.user_one_id, room.user_two_id):
        raise HTTPException(status_code=403, detail="Not authorized for this room")

    if payload.receiver_id not in (room.user_one_id, room.user_two_id):
        raise HTTPException(status_code=403, detail="Receiver not in this room")

    # 3. Persist + cache (service handles Redis safely)
    msg = await create_couple_message(
        db=db,
        room_id=room_id,
        sender_id=current_user.id,
        receiver_id=payload.receiver_id,
        content=payload.content,
        media_url=payload.media_url,
        media_type=payload.media_type,
        message_type=payload.message_type,
        reply_to_message_id=payload.reply_to_message_id,
    )

    return serialize_message(msg)


# =========================================================
# ENDPOINT 2: Send message (idempotent, auto-room, AI mood)
# =========================================================
@router.post(
    "/messages",
    response_model=dict,
    summary="Send couple message with idempotency + AI mood signal",
)
async def send_message(
    payload: CoupleMessageCreate,
    idem_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):
    sender_id = current_user.id

    # 1. Idempotency — duplicate requests ignored
    if not await idempotency.check_and_lock(idem_key):
        return {"status": "duplicate"}

    # 2. Room auto-create / fetch
    try:
        room = await run_in_threadpool(
            get_or_create_couple_room, db, sender_id, payload.receiver_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not prepare couple room: {exc}",
        )

    # 3. Persist message via service (handles cache + unread safely)
    new_msg = await create_couple_message(
        db=db,
        room_id=room.id,
        sender_id=sender_id,
        receiver_id=payload.receiver_id,
        content=payload.content,
        media_url=payload.media_url,
        media_type=payload.media_type,
        message_type=payload.message_type,
        reply_to_message_id=payload.reply_to_message_id,
    )

    # 4. Delivery receipt (threadpool — DB block na ho)
    def _create_receipt():
        receipt = CoupleMessageReceipt(
            message_id=new_msg.id,
            receiver_id=payload.receiver_id,
        )
        db.add(receipt)
        db.commit()
        return receipt

    try:
        await run_in_threadpool(_create_receipt)
    except Exception as e:
        print(f"⚠️ Receipt create failed for msg {new_msg.id}: {e}")

    # 5. WebSocket push to both parties
    ws_payload = {
        "type": "message",
        "id": new_msg.id,
        "room_id": room.id,
        "sender_id": sender_id,
        "receiver_id": payload.receiver_id,
        "content": payload.content,
        "media_url": payload.media_url,
        "media_type": payload.media_type,
        "message_type": new_msg.message_type,
        "created_at": new_msg.created_at.isoformat() if new_msg.created_at else None,
    }
    await manager.send_personal_message(payload.receiver_id, ws_payload)
    await manager.send_personal_message(sender_id, ws_payload)

    # 6. AI Mood detection (only if content present)
    if payload.content:
        try:
            detected_mood = await run_in_threadpool(
                mood_ai.predict, payload.content
            )
        except Exception as e:
            print(f"⚠️ Mood classifier failed: {e}")
            detected_mood = None

        if detected_mood:
            print(f"🧠 AI Detected Mood: {detected_mood}")
            mood_payload = {"type": "mood", "mood": detected_mood}
            await manager.send_personal_message(payload.receiver_id, mood_payload)
            await manager.send_personal_message(sender_id, mood_payload)

    return {
        "status": "success",
        "msg_id": new_msg.id,
        "room_id": room.id,
    }
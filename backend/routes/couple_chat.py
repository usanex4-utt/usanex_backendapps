
# =========================================================
# USANEX — COUPLE CHAT ROUTES
# =========================================================

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from fastapi import UploadFile, File
# ---- Database ----
from backend.database.database import get_db
from backend.database.models import User
from backend.database.couple_models import (
    CoupleRoom,
    CoupleMessage,
    CoupleMessageReceipt,
)

# ---- Auth dependency ----
from backend.routes.auth import get_current_user_from_request

# ---- Access Control ----
from backend.services.access_control import (
    can_chat,
    CHAT_BLOCKED_MESSAGE,
)

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
# ENDPOINT 1: Send message via explicit room_id
# =========================================================


@router.post("/upload-media")
async def upload_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_from_request),
):
    # Reuse the existing upload validator from chat.py
    from backend.routes.chat import save_upload
    media_url, media_type = await save_upload(file)
    return {
        "success": True,
        "media_url": media_url,
        "media_type": media_type,
    }







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

    sender_id = current_user.id

    # 1. Connection validation
    if not can_chat(db, sender_id, payload.receiver_id):
        raise HTTPException(
            status_code=403,
            detail=CHAT_BLOCKED_MESSAGE,
        )

    # 2. Room must exist
    room = (
        db.query(CoupleRoom)
        .filter(CoupleRoom.id == room_id)
        .first()
    )

    if room is None:
        raise HTTPException(
            status_code=404,
            detail="Room not found",
        )

    # 3. Sender must belong to room
    if sender_id not in (room.user_one_id, room.user_two_id):
        raise HTTPException(
            status_code=403,
            detail="Not authorized for this room",
        )

    # 4. Receiver must belong to room
    if payload.receiver_id not in (
        room.user_one_id,
        room.user_two_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Receiver not in this room",
        )

    # 5. Sender cannot send message to themselves
    if sender_id == payload.receiver_id:
        raise HTTPException(
            status_code=400,
            detail="You cannot chat with yourself.",
        )

    # 6. Persist message
    msg = await create_couple_message(
        db=db,
        room_id=room_id,
        sender_id=sender_id,
        receiver_id=payload.receiver_id,
        content=payload.content,
        media_url=payload.media_url,
        media_type=payload.media_type,
        message_type=payload.message_type,
        reply_to_message_id=payload.reply_to_message_id,
    )

    # 7. Return serialized message
    return serialize_message(msg)


# =========================================================
# ENDPOINT 2: Send message
# Idempotency + Auto Room + AI Mood + Connection Check
# =========================================================

@router.post(
    "/messages",
    response_model=dict,
    summary="Send couple message with connection validation",
)
async def send_message(
    payload: CoupleMessageCreate,
    idem_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):

    sender_id = current_user.id
    receiver_id = payload.receiver_id

    # =====================================================
    # 1. CONNECTION VALIDATION
    # =====================================================

    if not can_chat(db, sender_id, receiver_id):
        raise HTTPException(
            status_code=403,
            detail=CHAT_BLOCKED_MESSAGE,
        )

    # =====================================================
    # 2. IDEMPOTENCY
    # =====================================================

    if not await idempotency.check_and_lock(idem_key):
        return {
            "status": "duplicate",
        }

    try:

        # =================================================
        # 3. GET OR CREATE COUPLE ROOM
        # =================================================

        room = await run_in_threadpool(
            get_or_create_couple_room,
            db,
            sender_id,
            receiver_id,
        )

        # =================================================
        # 4. CREATE MESSAGE
        # =================================================

        new_msg = await create_couple_message(
            db=db,
            room_id=room.id,
            sender_id=sender_id,
            receiver_id=receiver_id,
            content=payload.content,
            media_url=payload.media_url,
            media_type=payload.media_type,
            message_type=payload.message_type,
            reply_to_message_id=payload.reply_to_message_id,
        )

        # =================================================
        # 5. CREATE DELIVERY RECEIPT
        # =================================================

        def _create_receipt():
            receipt = CoupleMessageReceipt(
                message_id=new_msg.id,
                receiver_id=receiver_id,
            )

            db.add(receipt)
            db.commit()
            db.refresh(receipt)

            return receipt

        try:
            await run_in_threadpool(_create_receipt)

        except Exception as e:
            print(
                f"Receipt creation failed for message "
                f"{new_msg.id}: {e}"
            )
            db.rollback()

        # =================================================
        # 6. WEBSOCKET MESSAGE PUSH
        # =================================================

        ws_payload = {
            "type": "message",
            "id": new_msg.id,
            "room_id": room.id,
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "content": payload.content,
            "media_url": payload.media_url,
            "media_type": payload.media_type,
            "message_type": new_msg.message_type,
            "created_at": (
                new_msg.created_at.isoformat()
                if new_msg.created_at
                else None
            ),
        }

        await manager.send_personal_message(
            receiver_id,
            ws_payload,
        )

        await manager.send_personal_message(
            sender_id,
            ws_payload,
        )

        # =================================================
        # 7. AI MOOD DETECTION
        # =================================================

        if payload.content:

            try:
                detected_mood = await run_in_threadpool(
                    mood_ai.predict,
                    payload.content,
                )

            except Exception as e:
                print(f"Mood classifier failed: {e}")
                detected_mood = None

            if detected_mood:

                print(
                    f"AI Detected Mood: {detected_mood}"
                )

                mood_payload = {
                    "type": "mood",
                    "mood": detected_mood,
                }

                await manager.send_personal_message(
                    receiver_id,
                    mood_payload,
                )

                await manager.send_personal_message(
                    sender_id,
                    mood_payload,
                )

        # =================================================
        # 8. RESPONSE
        # =================================================

        return {
            "status": "success",
            "msg_id": new_msg.id,
            "room_id": room.id,
        }

    except HTTPException:
        raise

    except Exception as e:
        db.rollback()

        print(f"Couple message error: {e}")

        raise HTTPException(
            status_code=500,
            detail="Failed to send couple message.",
        )
@router.post("/upload-media")
async def upload_couple_media(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user_from_request),
):
    """
    Upload-only endpoint. Saves file to /static/uploads/chat/
    Returns media_url + media_type. NO message row is created.
    The client then sends a WS {type:'message', media_url, media_type}
    which creates the CoupleMessage row.
    """
    if current_user is None:
        raise HTTPException(401, "Login required")

    media_url, media_type = await save_upload(file)

    return {
        "success": True,
        "media_url": media_url,
        "media_type": media_type,
    }

# backend/routes/couple_chat.py
from fastapi import APIRouter, Depends, Header, HTTPException
from backend.database.database import get_db
from backend.database.couple_models import CoupleMessage
from backend.services.redis import message_cache, idempotency
from backend.websocket.couple_manager import manager

router = APIRouter()

@router.post("/messages")
async def send_message(
    sender_id: int,
    receiver_id: int,
    room_id: int,
    content: str,
    idem_key: str = Header(..., alias="Idempotency-Key"), # Prevents double sends
    db: Session = Depends(get_db)
):
    # 1. Idempotency Check
    if not await idempotency.check_and_lock(idem_key):
        return {"status": "duplicate", "message": "Already processing"}

    # 2. Save to DB
    new_msg = CoupleMessage(
        room_id=room_id, sender_id=sender_id, 
        receiver_id=receiver_id, content=content
    )
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)

    # 3. Cache in Redis (Recent 50 msgs list)
    await message_cache.push_message(new_msg)
    
    # 4. Increment Receiver's Unread Counter in Redis
    await message_cache.incr_unread(receiver_id, room_id)

    # 5. Send Real-time via WebSocket (Pub/Sub)
    ws_payload = {
        "id": new_msg.id,
        "room_id": room_id,
        "sender_id": sender_id,
        "content": content,
        "created_at": new_msg.created_at.isoformat()
    }
    await manager.send_personal_message(receiver_id, ws_payload)

    return {"status": "success", "msg_id": new_msg.id}
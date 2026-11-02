# backend/routes/couple_chat.py
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.database.database import get_db
from backend.database.couple_models import CoupleMessage, CoupleMessageReceipt, CoupleRoom
from backend.services.redis import message_cache, idempotency
from backend.websocket.couple_manager import couple_manager as manager
from backend.ml.mood_model import MoodClassifier

router = APIRouter(prefix="/api/couple", tags=["Couple Chat"]) # 👈 Prefix add kiya
mood_ai = MoodClassifier()

class MessageCreate(BaseModel):
    receiver_id: int
    room_id: int
    content: str

@router.post("/messages")
async def send_message(
    payload: MessageCreate,
    idem_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    sender_id: int = 1 # 👈 NOTE: Baad mein isko Depends(get_current_user) se replace karein
):
    # 1. Idempotency Check (Duplicate messages rokne ke liye)
    if not await idempotency.check_and_lock(idem_key):
        return {"status": "duplicate"}

    # 2. DB Validation (Kya sender aur receiver sach mein is room ka hissa hain?)
    room = db.query(CoupleRoom).filter(CoupleRoom.id == payload.room_id).first()
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    if sender_id not in (room.user_one_id, room.user_two_id) or payload.receiver_id not in (room.user_one_id, room.user_two_id):
        raise HTTPException(status_code=403, detail="Not authorized for this room")

    # 3. DB Save (Threadpool mein taaki async event loop block na ho)
    def save_message_to_db():
        new_msg = CoupleMessage(
            room_id=payload.room_id, 
            sender_id=sender_id, 
            receiver_id=payload.receiver_id, 
            content=payload.content
        )
        db.add(new_msg)
        db.commit()
        db.refresh(new_msg)

        # Receipt create karein (Delivered tracking ke liye)
        receipt = CoupleMessageReceipt(
            message_id=new_msg.id,
            receiver_id=payload.receiver_id
        )
        db.add(receipt)
        db.commit()
        return new_msg

    new_msg = await run_in_threadpool(save_message_to_db)

    # 4. Redis Cache Update
    await message_cache.push_message(new_msg)
    await message_cache.incr_unread(payload.receiver_id, payload.room_id)

    # 5. WebSocket Event
    ws_payload = {
        "type": "message",
        "id": new_msg.id,
        "room_id": payload.room_id,
        "sender_id": sender_id,
        "content": payload.content,
        "created_at": new_msg.created_at.isoformat()
    }
    await manager.send_personal_message(payload.receiver_id, ws_payload)
    await manager.send_personal_message(sender_id, ws_payload)

    # 6. AI Mood Detection (Threadpool mein taaki main thread block na ho)
    def get_mood():
        return mood_ai.predict(payload.content)

    detected_mood = await run_in_threadpool(get_mood)
    
    if detected_mood:
        print(f"🧠 AI Detected Mood: {detected_mood}")
        mood_payload = {
            "type": "mood",
            "mood": detected_mood
        }
        # Dono users ko mood signal bhejein
        await manager.send_personal_message(payload.receiver_id, mood_payload)
        await manager.send_personal_message(sender_id, mood_payload)

    return {"status": "success", "msg_id": new_msg.id}
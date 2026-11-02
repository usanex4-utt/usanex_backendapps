# backend/routes/couple_chat.py
from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session
from backend.database.database import get_db
from backend.database.couple_models import CoupleMessage
from backend.services.redis import message_cache, idempotency
from backend.websocket.couple_manager import couple_manager as manager
from backend.ml.mood_model import MoodClassifier # 🧠 AI Import kiya

router = APIRouter()
mood_ai = MoodClassifier() # 🧠 AI Load kiya

@router.post("/messages")
async def send_message(
    sender_id: int,
    receiver_id: int,
    room_id: int,
    content: str,
    idem_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db)
):
    if not await idempotency.check_and_lock(idem_key):
        return {"status": "duplicate"}

    new_msg = CoupleMessage(
        room_id=room_id, sender_id=sender_id, 
        receiver_id=receiver_id, content=content
    )
    db.add(new_msg)
    db.commit()
    db.refresh(new_msg)

    await message_cache.push_message(new_msg)
    await message_cache.incr_unread(receiver_id, room_id)

    # Message payload
    ws_payload = {
        "type": "message",
        "id": new_msg.id,
        "room_id": room_id,
        "sender_id": sender_id,
        "content": content,
        "created_at": new_msg.created_at.isoformat()
    }
    await manager.send_personal_message(receiver_id, ws_payload)
    await manager.send_personal_message(sender_id, ws_payload)

    # 🧠 AI MOOD DETECTION MAGIC YAHAN HAI
    detected_mood = mood_ai.predict(content)
    if detected_mood:
        print(f"🧠 AI Detected Mood: {detected_mood}") # Terminal me check karne ke liye
        mood_payload = {
            "type": "mood",
            "mood": detected_mood
        }
        # Sender aur Receiver dono ko signal bhejo
        await manager.send_personal_message(receiver_id, mood_payload)
        await manager.send_personal_message(sender_id, mood_payload)

    return {"status": "success", "msg_id": new_msg.id}
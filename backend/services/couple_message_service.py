# backend/services/couple_message_service.py
from typing import Optional, Union
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from ..schemas.couple import CoupleMessageResponse, CoupleMessageReceiptResponse
from ..database.couple_models import (
    CoupleRoom,
    CoupleMessage,
    CoupleMessageReceipt,
    utcnow,
)
from .redis import message_cache


def get_or_create_couple_room(db: Session, user_one_id: int, user_two_id: int) -> CoupleRoom:
    a, b = sorted([user_one_id, user_two_id])
    
    room = db.query(CoupleRoom).filter(CoupleRoom.user_one_id == a, CoupleRoom.user_two_id == b).first()
    if room is not None:
        return room

    room = CoupleRoom(user_one_id=a, user_two_id=b, is_active=True)
    db.add(room)
    try:
        db.commit()
        db.refresh(room)
        return room
    except SQLAlchemyError as e:
        db.rollback()
        # Check if parallel request created it
        room = db.query(CoupleRoom).filter(CoupleRoom.user_one_id == a, CoupleRoom.user_two_id == b).first()
        if room is not None:
            return room
        raise Exception(f"Database Trigger Blocked Room Creation: {str(e)}")


async def create_couple_message(
    db: Session, room_id: int, sender_id: int, receiver_id: int,
    content: Optional[str], media_url: Optional[str], media_type: Optional[str],
    message_type: str = "text", reply_to_message_id: Optional[int] = None,
) -> CoupleMessage:
    msg = CoupleMessage(
        room_id=room_id, sender_id=sender_id, receiver_id=receiver_id,
        content=content, media_url=media_url, media_type=media_type,
        message_type=message_type, reply_to_message_id=reply_to_message_id,
        is_deleted=False, is_edited=False, created_at=utcnow(), updated_at=utcnow(),
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    # 🚨 FIX: Agar Redis down hai toh message send hone se na ruke
    try:
        await message_cache.push_message(msg)
        await message_cache.incr_unread(receiver_id, room_id)
    except Exception as e:
        print(f"⚠️ Redis Cache failed, but message saved to DB: {e}")
        
    return msg


async def get_couple_messages(db: Session, room_id: int, limit: int = 50) -> list[Union[CoupleMessage, dict]]:
    try:
        cached = await message_cache.get_recent(room_id)
        if cached is not None:
            return list(reversed(cached[:limit]))
    except Exception:
        pass

    rows = (
        db.query(CoupleMessage)
        .filter(CoupleMessage.room_id == room_id, CoupleMessage.is_deleted.is_(False))
        .order_by(CoupleMessage.created_at.desc(), CoupleMessage.id.desc())
        .limit(limit).all()
    )
    rows_chrono = list(reversed(rows))

    try:
        for m in rows_chrono:
            await message_cache.push_message(m)
    except Exception:
        pass

    return rows_chrono


def _get_or_create_receipt(db: Session, message_id: int, receiver_id: int) -> CoupleMessageReceipt:
    row = (
        db.query(CoupleMessageReceipt)
        .filter(CoupleMessageReceipt.message_id == message_id, CoupleMessageReceipt.receiver_id == receiver_id)
        .first()
    )
    if row is not None:
        return row

    row = CoupleMessageReceipt(
        message_id=message_id, receiver_id=receiver_id,
        delivered=False, seen=False, created_at=utcnow(), updated_at=utcnow(),
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row
    except SQLAlchemyError:
        db.rollback()
        return db.query(CoupleMessageReceipt).filter(CoupleMessageReceipt.message_id == message_id, CoupleMessageReceipt.receiver_id == receiver_id).first()


def mark_message_delivered(db: Session, message_id: int, receiver_id: int) -> CoupleMessageReceipt:
    row = _get_or_create_receipt(db, message_id, receiver_id)
    if row and not row.delivered:
        row.delivered = True
        row.delivered_at = utcnow()
        row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
    return row


def mark_message_seen(db: Session, message_id: int, receiver_id: int) -> CoupleMessageReceipt:
    row = _get_or_create_receipt(db, message_id, receiver_id)
    changed = False
    if row and not row.delivered:
        row.delivered = True
        row.delivered_at = utcnow()
        changed = True
    if row and not row.seen:
        row.seen = True
        row.seen_at = utcnow()
        changed = True
    if changed:
        row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
    return row



def serialize_message(m: Union[CoupleMessage, dict]) -> dict:
    """Always return a plain dict so WebSocket / JSON can serialize it."""
    if isinstance(m, dict):
        # already a dict from Redis cache
        return {
            "id": m.get("id"),
            "room_id": m.get("room_id"),
            "sender_id": m.get("sender_id"),
            "receiver_id": m.get("receiver_id"),
            "content": m.get("content"),
            "media_url": m.get("media_url"),
            "media_type": m.get("media_type"),
            "message_type": m.get("message_type") or "text",
            "reply_to_message_id": m.get("reply_to_message_id"),
            "is_deleted": bool(m.get("is_deleted", False)),
            "is_edited": bool(m.get("is_edited", False)),
            "ai_mode": m.get("ai_mode"),
            "ai_emotion": m.get("ai_emotion"),
            "ai_intent": m.get("ai_intent"),
            "ai_intensity": m.get("ai_intensity"),
            "ai_theme": m.get("ai_theme"),
            "created_at": m.get("created_at"),
            "updated_at": m.get("updated_at"),
        }

    return {
        "id": m.id,
        "room_id": m.room_id,
        "sender_id": m.sender_id,
        "receiver_id": m.receiver_id,
        "content": m.content,
        "media_url": m.media_url,
        "media_type": m.media_type,
        "message_type": m.message_type or "text",
        "reply_to_message_id": m.reply_to_message_id,
        "is_deleted": bool(m.is_deleted),
        "is_edited": bool(m.is_edited),
        "ai_mode": m.ai_mode,
        "ai_emotion": m.ai_emotion,
        "ai_intent": m.ai_intent,
        "ai_intensity": m.ai_intensity,
        "ai_theme": m.ai_theme,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }

def serialize_receipt(r: CoupleMessageReceipt) -> CoupleMessageReceiptResponse:
    return CoupleMessageReceiptResponse.model_validate(r)
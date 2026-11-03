# backend/schemas/couple.py
from datetime import datetime
from typing import Optional, List
from .common import BaseSchema


# ---------- ROOM ----------
class CoupleRoomResponse(BaseSchema):
    id: int
    user_one_id: int
    user_two_id: int
    is_active: bool
    relationship_started_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# ---------- MESSAGE ----------
class CoupleMessageCreate(BaseSchema):
    receiver_id: int
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    message_type: str = "text"
    reply_to_message_id: Optional[int] = None


class CoupleMessageResponse(BaseSchema):
    id: int
    room_id: int
    sender_id: int
    receiver_id: int
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    message_type: str
    reply_to_message_id: Optional[int] = None
    is_deleted: bool = False
    is_edited: bool = False
    ai_mode: Optional[str] = None
    ai_emotion: Optional[str] = None
    ai_intent: Optional[str] = None
    ai_intensity: Optional[float] = None
    ai_theme: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CoupleMessageReceiptResponse(BaseSchema):
    id: int
    message_id: int
    receiver_id: int
    delivered: bool
    delivered_at: Optional[datetime] = None
    seen: bool
    seen_at: Optional[datetime] = None


# ---------- PRESENCE / EVENTS ----------
class CouplePresenceResponse(BaseSchema):
    user_id: int
    is_online: bool
    last_seen_at: Optional[datetime] = None
    current_room_id: Optional[int] = None


class CoupleTypingEvent(BaseSchema):
    user_id: int
    typing: bool


# ---------- MEMORY / AI ----------
class CoupleMemoryResponse(BaseSchema):
    id: int
    room_id: int
    created_by_user_id: int
    memory_type: str
    title: Optional[str] = None
    content: str
    importance: float
    ai_generated: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CoupleAIEventResponse(BaseSchema):
    id: int
    room_id: int
    message_id: Optional[int] = None
    user_id: int
    mode: Optional[str] = None
    secondary_mode: Optional[str] = None
    emotion: Optional[str] = None
    intent: Optional[str] = None
    intensity: Optional[float] = None
    theme: Optional[str] = None
    animation: Optional[str] = None
    response_style: Optional[str] = None
    ai_response: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    processing_time_ms: Optional[int] = None
    success: bool
    error_message: Optional[str] = None
    created_at: datetime
# backend/schemas/__init__.py
from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional
from .common import BaseSchema, MessageResponse
class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

# ---------------- COUPLE SCHEMAS ----------------

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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class CoupleMessageReceiptResponse(BaseSchema):
    id: int
    message_id: int
    receiver_id: int
    delivered: bool
    delivered_at: Optional[datetime] = None
    seen: bool
    seen_at: Optional[datetime] = None

class CoupleRoomResponse(BaseSchema):
    id: int
    user_one_id: int
    user_two_id: int
    is_active: bool
    relationship_started_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class CouplePresenceResponse(BaseSchema):
    user_id: int
    is_online: bool
    last_seen_at: Optional[datetime] = None
    current_room_id: Optional[int] = None

class CoupleTypingEvent(BaseSchema):
    user_id: int
    typing: bool

    __all__ = [
    "BaseSchema",
    "MessageResponse",
]
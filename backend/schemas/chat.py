# backend/schemas/chat.py
from datetime import datetime
from typing import Optional, List
from .common import BaseSchema


class ChatMessageCreate(BaseSchema):
    receiver_id: int
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None


class ChatMessageResponse(BaseSchema):
    id: int
    sender_id: int
    receiver_id: int
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    is_read: int
    is_deleted: int
    created_at: datetime
    updated_at: datetime


class ChatConversationResponse(BaseSchema):
    user_id: int
    last_message: Optional[ChatMessageResponse] = None
    unread_count: int = 0
    messages: List[ChatMessageResponse] = []
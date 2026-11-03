# backend/schemas/nexa.py
from typing import Optional, List
from .common import BaseSchema


class NexaMessage(BaseSchema):
    role: str  # "user" | "assistant"
    content: str


class NexaChatRequest(BaseSchema):
    message: str
    user_name: str = "User"
    conversation_id: Optional[str] = None


class NexaChatResponse(BaseSchema):
    reply: str
    conversation_id: Optional[str] = None
    history: Optional[List[NexaMessage]] = None
# backend/schemas/post.py
from datetime import datetime
from typing import Optional
from .common import BaseSchema


class PostCreate(BaseSchema):
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_type: str = "text"
    visibility: str = "public"


class PostResponse(BaseSchema):
    id: int
    user_id: int
    content: Optional[str] = None
    media_url: Optional[str] = None
    media_type: str
    visibility: str
    created_at: datetime
    updated_at: datetime
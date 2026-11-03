# backend/schemas/admin.py
from datetime import datetime
from typing import Optional, List
from .common import BaseSchema
from .user import UserPublicResponse


class MomentCreate(BaseSchema):
    content: str
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    visibility: str = "public"


class MomentResponse(BaseSchema):
    id: int
    user_id: int
    content: str
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    visibility: str
    created_at: datetime
    updated_at: datetime


class AdminMomentCreate(MomentCreate):
    """Admin can post on behalf of / feature a user."""
    featured: bool = False
    user_id: Optional[int] = None
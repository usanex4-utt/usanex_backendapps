# backend/schemas/status.py
from datetime import datetime
from typing import Optional
from .common import BaseSchema


class StatusCreate(BaseSchema):
    media_url: str
    media_type: str = "image"
    caption: Optional[str] = None


class StatusResponse(BaseSchema):
    id: int
    user_id: int
    media_url: str
    media_type: str
    caption: Optional[str] = None
    created_at: datetime
    expires_at: datetime


class StatusViewResponse(BaseSchema):
    id: int
    status_id: int
    viewer_id: int
    viewed_at: datetime
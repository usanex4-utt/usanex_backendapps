# backend/schemas/reel.py
from datetime import datetime
from typing import Optional, List
from pydantic import Field

from .common import BaseSchema


class ReelCreate(BaseSchema):
    video_url: str
    thumbnail_url: Optional[str] = None
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    visibility: str = "public"


class ReelResponse(BaseSchema):
    id: int
    user_id: int
    video_url: str
    thumbnail_url: Optional[str] = None
    duration: float
    caption: Optional[str] = None
    hashtags: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    visibility: str
    status: str
    views_count: int
    unique_views_count: int
    completed_views_count: int
    replay_count: int
    share_count: int
    save_count: int
    download_count: int
    comment_count: int
    one_star_count: int
    two_star_count: int
    three_star_count: int
    interested_count: int
    not_interested_count: int
    ai_processed: bool
    ai_category: Optional[str] = None
    is_safe: bool
    created_at: datetime
    updated_at: datetime


class ReelCommentCreate(BaseSchema):
    content: str = Field(..., min_length=1)
    parent_comment_id: Optional[int] = None


class ReelCommentResponse(BaseSchema):
    id: int
    user_id: int
    reel_id: int
    parent_comment_id: Optional[int] = None
    content: str
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


class ReelInteractionEvent(BaseSchema):
    reel_id: int
    creator_id: int
    event_type: str
    session_id: Optional[str] = None
    feed_position: Optional[int] = None
    source: Optional[str] = None
    watch_time: float = 0
    watch_percentage: float = 0
    completed: bool = False
    replayed: bool = False
    replay_count: int = 0
    star_rating: int = 0
    interested: bool = False
    not_interested: bool = False
    saved: bool = False
    downloaded: bool = False
    shared: bool = False
    commented: bool = False
    muted: bool = False


class ReelRatingRequest(BaseSchema):
    rating: int = Field(..., ge=1, le=3)


class ReelReportRequest(BaseSchema):
    reason: str = Field(..., max_length=100)
    description: Optional[str] = None
# backend/schemas/user.py
from datetime import datetime
from typing import Optional, List
from pydantic import Field

from .common import BaseSchema


# -------------------- WRITE --------------------

class UserCreate(BaseSchema):
    name: str = Field(..., min_length=1, max_length=100)
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str = Field(..., min_length=6, max_length=128)
    username: Optional[str] = Field(None, max_length=30)


class UserUpdate(BaseSchema):
    name: Optional[str] = Field(None, max_length=100)
    bio: Optional[str] = Field(None, max_length=500)
    website: Optional[str] = Field(None, max_length=500)
    instagram: Optional[str] = Field(None, max_length=500)
    social_link: Optional[str] = Field(None, max_length=500)
    profile_photo: Optional[str] = Field(None, max_length=1000)


# -------------------- READ --------------------

class UserResponse(BaseSchema):
    id: int
    username: str
    user_id: str
    name: str
    mobile: str
    profile_photo: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    social_link: Optional[str] = None
    is_active: bool
    is_verified: bool
    is_admin: bool = False
    last_seen_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class UserPublicResponse(BaseSchema):
    """Public view — hides sensitive fields like mobile."""
    id: int
    username: str
    user_id: str
    name: str
    profile_photo: Optional[str] = None
    bio: Optional[str] = None
    website: Optional[str] = None
    instagram: Optional[str] = None
    social_link: Optional[str] = None
    last_seen_at: Optional[datetime] = None


class UserProfileResponse(UserPublicResponse):
    """Extended public profile with connection context."""
    is_connected: bool = False
    is_following: bool = False
    is_followed_by: bool = False
    connection_category: Optional[str] = None
    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0
    reels_count: int = 0


class UserListResponse(BaseSchema):
    users: List[UserPublicResponse]
    total: int
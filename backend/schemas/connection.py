# backend/schemas/connection.py
from datetime import datetime
from typing import Optional, List
from pydantic import Field

from .common import BaseSchema
from .user import UserPublicResponse


class ConnectionRequestCreate(BaseSchema):
    receiver_id: int


class ConnectionVerifyRequest(BaseSchema):
    connection_request_id: int
    code: str = Field(..., min_length=4, max_length=16)


class ConnectionRequestResponse(BaseSchema):
    id: int
    sender_id: int
    receiver_id: int
    status: str
    created_at: datetime
    updated_at: datetime


class ConnectionNotificationResponse(BaseSchema):
    id: int
    receiver_id: int
    sender_id: int
    connection_request_id: int
    verification_id: Optional[int] = None
    notification_type: str
    encrypted_code: Optional[str] = None
    is_read: int
    created_at: datetime
    sender: Optional[UserPublicResponse] = None


class ConnectionResponse(BaseSchema):
    id: int
    user_one_id: int
    user_two_id: int
    status: str
    created_at: datetime
    updated_at: datetime


class ConnectionCategoryUpdate(BaseSchema):
    connected_user_id: int
    category: str = Field(..., pattern="^(friend|family|bestfriend|other)$")


class ConnectionWithUserResponse(BaseSchema):
    connection: ConnectionResponse
    user: UserPublicResponse
    category: Optional[str] = None
# backend/schemas/auth.py
from datetime import datetime
from pydantic import Field

from .common import BaseSchema
from .user import UserResponse


class SendOTPRequest(BaseSchema):
    identifier: str = Field(..., min_length=3, max_length=100)
    purpose: str = Field(..., pattern="^(register|login|reset_password)$")


class VerifyOTPRequest(BaseSchema):
    identifier: str
    otp: str = Field(..., min_length=6, max_length=6)
    purpose: str


class RegisterRequest(BaseSchema):
    name: str = Field(..., min_length=1, max_length=100)
    mobile: str = Field(..., min_length=6, max_length=20)
    password: str = Field(..., min_length=6, max_length=128)
    otp: str = Field(..., min_length=6, max_length=6)


class LoginRequest(BaseSchema):
    identifier: str  # mobile OR username
    password: str


class SessionResponse(BaseSchema):
    session_token: str
    user_id: int
    expires_at: datetime


class AuthResponse(BaseSchema):
    user: UserResponse
    session_token: str
    expires_at: datetime
# =========================================================
# USANEX — ADMIN SCHEMAS
# backend/schemas/admin_dash.py
# =========================================================

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Mobile format: optional +, 7–15 digits
MOBILE_PATTERN = re.compile(r"^\+?[1-9]\d{6,14}$")


def _clean_mobile(value: str) -> str:
    v = (value or "").strip().replace(" ", "").replace("-", "")
    if not MOBILE_PATTERN.match(v):
        raise ValueError("Enter a valid mobile number (7–15 digits, optional +)")
    return v


# =========================================================
# REGISTER
# =========================================================

class AdminRegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    mobile_number: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v):
        return _clean_mobile(v)

    @model_validator(mode="after")
    def _passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


# =========================================================
# VERIFY OTP
# =========================================================

class AdminVerifyOTPRequest(BaseModel):
    mobile_number: str = Field(min_length=7, max_length=20)
    otp: str = Field(min_length=4, max_length=8)

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v):
        return _clean_mobile(v)


# =========================================================
# RESEND OTP
# =========================================================

class AdminResendOTPRequest(BaseModel):
    mobile_number: str = Field(min_length=7, max_length=20)

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v):
        return _clean_mobile(v)


# =========================================================
# LOGIN
# =========================================================

class AdminLoginRequest(BaseModel):
    mobile_number: str = Field(min_length=7, max_length=20)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v):
        return _clean_mobile(v)


# =========================================================
# FORGOT / RESET PASSWORD
# =========================================================

class AdminForgotPasswordRequest(BaseModel):
    mobile_number: str = Field(min_length=7, max_length=20)

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v):
        return _clean_mobile(v)


class AdminResetPasswordRequest(BaseModel):
    mobile_number: str = Field(min_length=7, max_length=20)
    otp: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v):
        return _clean_mobile(v)


# =========================================================
# CHANGE PASSWORD
# =========================================================

class AdminChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# =========================================================
# LOGOUT
# =========================================================

class AdminLogoutRequest(BaseModel):
    revoke_all: bool = False


# =========================================================
# OUTPUT
# =========================================================

class AdminPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mobile_number: str
    full_name: str
    is_verified: bool
    is_active: bool
    is_super_admin: bool
    last_login_at: Optional[datetime] = None
    created_at: datetime


class AdminAuthResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
    expires_at: datetime
    admin: AdminPublic


class AdminMessageResponse(BaseModel):
    detail: str


class AdminOTPMessageResponse(BaseModel):
    """Returns the OTP so the frontend can display it (dev mode)."""
    detail: str
    otp: Optional[str] = None


class AdminSessionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
"""
USANEX — ADMIN DATABASE MODELS
backend/database/admin_models.py

Admin authentication is isolated from the main User table.

Models:
    AdminUser    : Admin accounts
    AdminSession : Bearer token sessions
    AdminOTP     : Verification / password-reset OTPs (keyed by mobile_number)
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


# =========================================================
# UTC DATETIME
# =========================================================

def utcnow():
    return datetime.now(timezone.utc)


# =========================================================
# ADMIN USER
# =========================================================

class AdminUser(Base):

    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)

    mobile_number = Column(
        String(20),
        unique=True,
        nullable=False,
        index=True,
    )

    full_name = Column(String(150), nullable=False)

    # Argon2 password hash
    password_hash = Column(String(255), nullable=False)

    is_verified = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_super_admin = Column(Boolean, default=False, nullable=False)

    last_login_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    sessions = relationship(
        "AdminSession",
        back_populates="admin",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<AdminUser(id={self.id}, mobile='{self.mobile_number}')>"


# =========================================================
# ADMIN SESSION
# =========================================================

class AdminSession(Base):

    __tablename__ = "admin_sessions"

    id = Column(Integer, primary_key=True, index=True)

    admin_id = Column(
        Integer,
        ForeignKey("admin_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    user_agent = Column(String(500), nullable=True)
    ip_address = Column(String(100), nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    admin = relationship("AdminUser", back_populates="sessions")

    __table_args__ = (
        Index("ix_admin_sessions_admin_active", "admin_id", "is_active"),
    )

    def __repr__(self):
        return f"<AdminSession(id={self.id}, admin_id={self.admin_id})>"


# =========================================================
# ADMIN OTP
# =========================================================

class AdminOTP(Base):

    __tablename__ = "admin_otps"

    id = Column(Integer, primary_key=True, index=True)

    mobile_number = Column(String(20), nullable=False, index=True)

    # Allowed purposes:
    # verify_email
    # reset_password
    purpose = Column(String(30), nullable=False)

    otp_hash = Column(String(128), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)

    created_at = Column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "mobile_number",
            "purpose",
            name="uq_admin_otp_mobile_purpose",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_admin_otp_attempts_positive",
        ),
        CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')",
            name="ck_admin_otp_valid_purpose",
        ),
        Index(
            "ix_admin_otp_mobile_purpose",
            "mobile_number",
            "purpose",
        ),
    )

    def __repr__(self):
        return (
            f"<AdminOTP(id={self.id}, mobile='{self.mobile_number}', "
            f"purpose='{self.purpose}')>"
        )
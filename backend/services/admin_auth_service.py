# =========================================================
# USANEX — ADMIN AUTH SERVICE
# backend/services/admin_auth_service.py
# =========================================================
"""
Isolated admin authentication (mobile_number based).

- Passwords   : Argon2
- Sessions    : opaque random token, sha256-hashed at rest
- OTP         : 6-digit, sha256-hashed, 10 min TTL, 5 attempts
- Redis cache : session lookup fast-path (fail-open)
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.redis_client import get_redis
from backend.database.admin_models import (
    AdminUser,
    AdminSession,
    AdminOTP,
    utcnow,
)
from .auth_service import hash_password, verify_password

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------
# CONSTANTS
# -----------------------------------------------------------------

SESSION_TTL_DAYS = 7
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

OTP_PURPOSE_VERIFY = "verify_email"
OTP_PURPOSE_RESET = "reset_password"


# -----------------------------------------------------------------
# HASHING
# -----------------------------------------------------------------

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _admin_session_key(token_hash: str) -> str:
    return f"usanex:admin_session:{token_hash}"


# -----------------------------------------------------------------
# LOOKUP
# -----------------------------------------------------------------

def get_admin_by_mobile(db: Session, mobile: str) -> Optional[AdminUser]:
    return (
        db.query(AdminUser)
        .filter(AdminUser.mobile_number == mobile.strip())
        .first()
    )


def get_admin_by_id(db: Session, admin_id: int) -> Optional[AdminUser]:
    return db.query(AdminUser).filter(AdminUser.id == admin_id).first()


# -----------------------------------------------------------------
# CREATE ADMIN
# -----------------------------------------------------------------

def _is_duplicate_mobile_error(orig: str) -> bool:
    """
    Heuristic that fires ONLY for real unique-mobile conflicts.

    Matches (case-insensitive) all common dialects:
      postgres : duplicate key value violates unique constraint "admin_users_mobile_number_key"
      sqlite   : UNIQUE constraint failed: admin_users.mobile_number
      mysql    : Duplicate entry '...' for key 'admin_users.mobile_number'
    """
    if "mobile_number" not in orig:
        return False
    if "unique" not in orig and "duplicate" not in orig:
        return False
    return True


def create_admin(
    db: Session,
    mobile_number: str,
    full_name: str,
    password: str,
) -> AdminUser:
    mobile_number = mobile_number.strip()

    if get_admin_by_mobile(db, mobile_number) is not None:
        raise ValueError("An admin with this mobile number already exists")

    admin = AdminUser(
        mobile_number=mobile_number,
        full_name=full_name.strip(),
        password_hash=hash_password(password),
        is_verified=False,
        is_active=True,
        is_super_admin=False,
    )
    db.add(admin)
    try:
        db.commit()
        db.refresh(admin)
    except IntegrityError as exc:
        db.rollback()
        orig = str(getattr(exc, "orig", exc)).lower()

        # Always log the real error so it's never masked.
        logger.error(
            "[CREATE ADMIN] IntegrityError for mobile=%s | orig=%s",
            mobile_number,
            orig,
        )

        # Only surface as "already exists" for genuine unique-mobile clashes.
        if _is_duplicate_mobile_error(orig):
            raise ValueError(
                "An admin with this mobile number already exists"
            )

        # Anything else (NOT NULL on legacy email column, bad FK, etc.)
        # bubbles up as a 500 with the real traceback. That's intentional:
        # silence is worse than a loud failure while you debug.
        raise

    return admin


# -----------------------------------------------------------------
# OTP
# -----------------------------------------------------------------

def _purge_existing_otp(db: Session, mobile: str, purpose: str) -> None:
    db.query(AdminOTP).filter(
        AdminOTP.mobile_number == mobile.strip(),
        AdminOTP.purpose == purpose,
    ).delete(synchronize_session=False)
    db.commit()


def create_admin_otp(db: Session, mobile_number: str, purpose: str) -> str:
    mobile = mobile_number.strip()
    _purge_existing_otp(db, mobile, purpose)

    otp = "".join(secrets.choice("0123456789") for _ in range(6))

    row = AdminOTP(
        mobile_number=mobile,
        purpose=purpose,
        otp_hash=_sha256(otp),
        expires_at=utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
        attempts=0,
        is_used=False,
    )
    db.add(row)
    db.commit()

    logger.info("Admin OTP generated for %s purpose=%s", mobile, purpose)
    return otp


def verify_admin_otp(
    db: Session,
    mobile_number: str,
    otp: str,
    purpose: str,
) -> bool:
    mobile = mobile_number.strip()
    row = (
        db.query(AdminOTP)
        .filter(
            AdminOTP.mobile_number == mobile,
            AdminOTP.purpose == purpose,
        )
        .first()
    )
    if row is None or row.is_used:
        return False

    now = utcnow()

    if row.expires_at and now > row.expires_at:
        row.is_used = True
        db.commit()
        return False

    if (row.attempts or 0) >= OTP_MAX_ATTEMPTS:
        row.is_used = True
        db.commit()
        return False

    row.attempts = (row.attempts or 0) + 1

    if not secrets.compare_digest(row.otp_hash, _sha256(otp)):
        db.commit()
        return False

    row.is_used = True
    db.commit()
    return True


# -----------------------------------------------------------------
# VERIFY EMAIL / STATUS
# -----------------------------------------------------------------

def mark_admin_verified(db: Session, admin: AdminUser) -> AdminUser:
    admin.is_verified = True
    admin.updated_at = utcnow()
    db.commit()
    db.refresh(admin)
    return admin


# -----------------------------------------------------------------
# PASSWORD
# -----------------------------------------------------------------

def authenticate_admin(
    db: Session, mobile_number: str, password: str
) -> Optional[AdminUser]:
    admin = get_admin_by_mobile(db, mobile_number)
    if admin is None:
        return None
    if not admin.is_active:
        return None
    if not verify_password(password, admin.password_hash):
        return None
    return admin


def set_admin_password(
    db: Session, admin: AdminUser, new_password: str
) -> AdminUser:
    admin.password_hash = hash_password(new_password)
    admin.updated_at = utcnow()
    db.commit()
    db.refresh(admin)
    return admin


# -----------------------------------------------------------------
# SESSIONS
# -----------------------------------------------------------------

def create_admin_session(
    db: Session,
    admin: AdminUser,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> tuple[str, AdminSession]:
    raw_token = secrets.token_urlsafe(48)
    token_hash = _sha256(raw_token)
    expires_at = utcnow() + timedelta(days=SESSION_TTL_DAYS)

    session_row = AdminSession(
        admin_id=admin.id,
        token_hash=token_hash,
        expires_at=expires_at,
        is_active=True,
        user_agent=(user_agent or "")[:500] or None,
        ip_address=(ip_address or "")[:100] or None,
        created_at=utcnow(),
    )
    db.add(session_row)

    admin.last_login_at = utcnow()
    db.commit()
    db.refresh(session_row)
    db.refresh(admin)

    return raw_token, session_row


async def warm_admin_session_cache(
    raw_token: str,
    admin_id: int,
    expires_at: datetime,
) -> None:
    try:
        token_hash = _sha256(raw_token)
        ttl = max(60, int((expires_at - utcnow()).total_seconds()))
        await get_redis().set(
            _admin_session_key(token_hash),
            str(admin_id),
            ex=ttl,
        )
    except Exception as e:
        logger.warning("Admin session cache warm failed: %s", e)


def get_admin_by_token(
    db: Session, raw_token: str
) -> Optional[AdminUser]:
    if not raw_token:
        return None

    token_hash = _sha256(raw_token)
    row = (
        db.query(AdminSession)
        .filter(AdminSession.token_hash == token_hash)
        .first()
    )
    if row is None or not row.is_active:
        return None
    if row.expires_at and row.expires_at <= utcnow():
        row.is_active = False
        db.commit()
        return None

    row.last_used_at = utcnow()
    db.commit()

    admin = get_admin_by_id(db, row.admin_id)
    if admin is None or not admin.is_active:
        return None
    return admin


def revoke_admin_session(db: Session, raw_token: str) -> None:
    token_hash = _sha256(raw_token)
    row = (
        db.query(AdminSession)
        .filter(AdminSession.token_hash == token_hash)
        .first()
    )
    if row is not None:
        row.is_active = False
        db.commit()


def revoke_all_admin_sessions(db: Session, admin_id: int) -> int:
    count = (
        db.query(AdminSession)
        .filter(
            AdminSession.admin_id == admin_id,
            AdminSession.is_active.is_(True),
        )
        .update(
            {AdminSession.is_active: False},
            synchronize_session=False,
        )
    )
    db.commit()
    return int(count or 0)


async def invalidate_admin_session_cache(raw_token: str) -> None:
    try:
        token_hash = _sha256(raw_token)
        await get_redis().delete(_admin_session_key(token_hash))
    except Exception:
        pass
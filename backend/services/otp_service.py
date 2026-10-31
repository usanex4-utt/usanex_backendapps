# =========================================================
# USANEX — OTP SERVICE
# =========================================================

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..database.models import OTPVerification


OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_HASH_ITERATIONS = 100_000


def _hash_otp(otp: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        otp.encode("utf-8"),
        salt.encode("utf-8"),
        OTP_HASH_ITERATIONS,
    ).hex()


def _store(salt: str, digest: str) -> str:
    return f"{salt}:{digest}"


def _split(stored: str) -> tuple[str, str] | tuple[None, None]:
    try:
        salt, digest = stored.split(":", 1)
        return salt, digest
    except (ValueError, AttributeError):
        return None, None


def create_otp(db: Session, identifier: str, purpose: str) -> str:
    """Invalidate previous codes, issue a fresh 6-digit OTP."""

    db.query(OTPVerification).filter(
        OTPVerification.identifier == identifier,
        OTPVerification.purpose == purpose,
        OTPVerification.is_used.is_(False),
    ).delete(synchronize_session=False)
    db.commit()

    otp = "".join(secrets.choice("0123456789") for _ in range(6))
    salt = secrets.token_hex(16)
    digest = _hash_otp(otp, salt)

    row = OTPVerification(
        identifier=identifier,
        otp_hash=_store(salt, digest),
        purpose=purpose,
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_TTL_MINUTES),
        attempts=0,
        is_used=False,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()

    return otp


def verify_otp(
    db: Session,
    identifier: str,
    otp: str,
    purpose: str,
) -> bool:

    row = (
        db.query(OTPVerification)
        .filter(
            OTPVerification.identifier == identifier,
            OTPVerification.purpose == purpose,
            OTPVerification.is_used.is_(False),
        )
        .order_by(OTPVerification.id.desc())
        .first()
    )

    if row is None:
        return False

    now = datetime.utcnow()

    if row.expires_at and now > row.expires_at:
        return False

    if (row.attempts or 0) >= OTP_MAX_ATTEMPTS:
        return False

    row.attempts = (row.attempts or 0) + 1

    salt, stored = _split(row.otp_hash or "")
    if salt is None:
        db.commit()
        return False

    if not secrets.compare_digest(_hash_otp(otp, salt), stored):
        db.commit()
        return False

    row.is_used = True
    db.commit()
    return True
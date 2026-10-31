# =========================================================
# USANEX — AUTH HELPERS (password hashing, IDs, usernames)
# =========================================================

import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy.orm import Session

from ..database.models import User


_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
    except Exception:
        return False


def generate_user_id() -> str:
    """10-digit numeric public ID."""
    return "".join(secrets.choice(string.digits) for _ in range(10))


def _sanitize_name(name: str) -> str:
    first = name.strip().split()[0] if name.strip() else "User"
    return "".join(ch for ch in first.title() if ch.isalpha()) or "User"


def generate_username(name: str, db: Session) -> str:
    """FirstName + 5 digits + @usa — unique against users table."""
    base = _sanitize_name(name)

    for _ in range(25):
        suffix = "".join(
            secrets.choice(string.digits) for _ in range(5)
        )
        candidate = f"{base}{suffix}@usa"

        exists = (
            db.query(User.id)
            .filter(User.username == candidate)
            .first()
        )
        if exists is None:
            return candidate

    raise RuntimeError("Unable to generate a unique username.")
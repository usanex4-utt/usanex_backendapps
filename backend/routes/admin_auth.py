# =========================================================
# USANEX — ADMIN AUTH ROUTES
# backend/routes/admin_auth.py
# =========================================================
"""
Admin-only auth surface. Mounts under /api/admin/auth/*.

Register / verify-otp / resend-otp / login / logout / me / sessions /
change-password / forgot-password / reset-password.

DEV:
  - Requests from localhost skip rate limiting entirely when
    ENVIRONMENT is not "production".
  - OTPs are returned in the JSON response for on-screen display.
    REMOVE the `otp=otp` lines (and the dev endpoint at the bottom)
    before shipping.

PROD:
  - Set ENVIRONMENT=production in your .env / environment to enforce
    rate limits on all hosts.
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.redis_client import get_redis
from backend.database.database import get_db
from backend.database.admin_models import AdminUser, AdminSession
from backend.schemas.admin_dash import (
    AdminAuthResponse,
    AdminChangePasswordRequest,
    AdminForgotPasswordRequest,
    AdminLoginRequest,
    AdminLogoutRequest,
    AdminMessageResponse,
    AdminOTPMessageResponse,
    AdminPublic,
    AdminRegisterRequest,
    AdminResendOTPRequest,
    AdminResetPasswordRequest,
    AdminSessionInfo,
    AdminVerifyOTPRequest,
)
from backend.services import admin_auth_service as svc
from backend.services.redis import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/auth", tags=["Admin Auth"])

_bearer = HTTPBearer(auto_error=False)


# =========================================================
# ENVIRONMENT / DEV DETECTION
# =========================================================

_ENV = (os.getenv("ENVIRONMENT") or "development").strip().lower()
_IS_PROD = _ENV in ("production", "prod")
_IS_DEV = not _IS_PROD

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_local(request: Request) -> bool:
    return _client_ip(request) in _LOCAL_HOSTS


# =========================================================
# RATE LIMIT
# =========================================================

async def _rate_limit(request: Request, action: str, limit: int, window: int):
    # Dev bypass — never throttle your own machine.
    if _IS_DEV and _is_local(request):
        return

    allowed, _ = await rate_limit.hit(action, _client_ip(request), limit, window)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )


# =========================================================
# AUTH DEPENDENCY
# =========================================================

def get_current_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> AdminUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin = svc.get_admin_by_token(db, credentials.credentials)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return admin


def require_super_admin(
    admin: AdminUser = Depends(get_current_admin),
) -> AdminUser:
    if not admin.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super-admin access required",
        )
    return admin


# =========================================================
# HELPERS
# =========================================================

def _issue_session(
    db: Session,
    admin: AdminUser,
    request: Request,
) -> tuple[str, AdminSession]:
    ua = request.headers.get("user-agent")
    ip = _client_ip(request) or None
    return svc.create_admin_session(db, admin, user_agent=ua, ip_address=ip)


# =========================================================
# REGISTER
# =========================================================

@router.post(
    "/register",
    response_model=AdminOTPMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_register(
    payload: AdminRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_register", limit=20, window=3600)

    try:
        admin = svc.create_admin(
            db,
            mobile_number=payload.mobile_number,
            full_name=payload.full_name,
            password=payload.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    otp = svc.create_admin_otp(db, admin.mobile_number, svc.OTP_PURPOSE_VERIFY)
    logger.info("[ADMIN REGISTER] OTP for %s: %s", admin.mobile_number, otp)

    return AdminOTPMessageResponse(
        detail="Admin registered. Enter the OTP shown to verify.",
        otp=otp,  # DEV ONLY — remove in production
    )


# =========================================================
# VERIFY OTP
# =========================================================

@router.post("/verify-otp", response_model=AdminAuthResponse)
async def admin_verify_otp(
    payload: AdminVerifyOTPRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_verify_otp", limit=10, window=600)

    admin = svc.get_admin_by_mobile(db, payload.mobile_number)
    if admin is None:
        raise HTTPException(status_code=404, detail="Admin not found")

    if admin.is_verified:
        raise HTTPException(status_code=400, detail="Admin already verified")

    ok = svc.verify_admin_otp(
        db, admin.mobile_number, payload.otp, svc.OTP_PURPOSE_VERIFY
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    admin = svc.mark_admin_verified(db, admin)
    raw_token, session_row = _issue_session(db, admin, request)

    try:
        await svc.warm_admin_session_cache(
            raw_token, admin.id, session_row.expires_at
        )
    except Exception:
        pass

    return AdminAuthResponse(
        token=raw_token,
        expires_at=session_row.expires_at,
        admin=AdminPublic.model_validate(admin),
    )


# =========================================================
# RESEND OTP
# =========================================================

@router.post("/resend-otp", response_model=AdminOTPMessageResponse)
async def admin_resend_otp(
    payload: AdminResendOTPRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_resend_otp", limit=20, window=600)

    admin = svc.get_admin_by_mobile(db, payload.mobile_number)
    if admin is None or admin.is_verified:
        return AdminOTPMessageResponse(
            detail="If the mobile number exists, an OTP has been sent.",
            otp=None,
        )

    otp = svc.create_admin_otp(db, admin.mobile_number, svc.OTP_PURPOSE_VERIFY)
    logger.info("[ADMIN RESEND OTP] %s: %s", admin.mobile_number, otp)

    return AdminOTPMessageResponse(
        detail="New OTP generated. Enter the code shown to verify.",
        otp=otp,  # DEV ONLY
    )


# =========================================================
# LOGIN
# =========================================================

@router.post("/login", response_model=AdminAuthResponse)
async def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_login", limit=10, window=600)

    admin = svc.authenticate_admin(
        db, payload.mobile_number, payload.password
    )
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid mobile number or password",
        )

    if not admin.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin mobile not verified. Please verify OTP first.",
        )

    raw_token, session_row = _issue_session(db, admin, request)

    try:
        await svc.warm_admin_session_cache(
            raw_token, admin.id, session_row.expires_at
        )
    except Exception:
        pass

    return AdminAuthResponse(
        token=raw_token,
        expires_at=session_row.expires_at,
        admin=AdminPublic.model_validate(admin),
    )


# =========================================================
# ME
# =========================================================

@router.get("/me", response_model=AdminPublic)
def admin_me(admin: AdminUser = Depends(get_current_admin)):
    return AdminPublic.model_validate(admin)


# =========================================================
# SESSIONS
# =========================================================

@router.get("/sessions", response_model=list[AdminSessionInfo])
def admin_sessions(
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(AdminSession)
        .filter(
            AdminSession.admin_id == admin.id,
            AdminSession.is_active.is_(True),
        )
        .order_by(AdminSession.created_at.desc())
        .all()
    )
    return [AdminSessionInfo.model_validate(r) for r in rows]


# =========================================================
# LOGOUT
# =========================================================

@router.post("/logout", response_model=AdminMessageResponse)
async def admin_logout(
    payload: AdminLogoutRequest,
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    raw_token = credentials.credentials if credentials else None

    if payload.revoke_all:
        count = svc.revoke_all_admin_sessions(db, admin.id)
        if raw_token:
            await svc.invalidate_admin_session_cache(raw_token)
        return AdminMessageResponse(detail=f"Revoked {count} admin session(s).")

    if raw_token:
        svc.revoke_admin_session(db, raw_token)
        await svc.invalidate_admin_session_cache(raw_token)

    return AdminMessageResponse(detail="Logged out.")


# =========================================================
# CHANGE PASSWORD
# =========================================================

@router.post("/change-password", response_model=AdminMessageResponse)
async def admin_change_password(
    payload: AdminChangePasswordRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_change_pw", limit=10, window=3600)

    if not svc.authenticate_admin(
        db, admin.mobile_number, payload.current_password
    ):
        raise HTTPException(
            status_code=400, detail="Current password is incorrect"
        )

    svc.set_admin_password(db, admin, payload.new_password)
    svc.revoke_all_admin_sessions(db, admin.id)

    return AdminMessageResponse(
        detail="Password changed. Please log in again."
    )


# =========================================================
# FORGOT PASSWORD
# =========================================================

@router.post("/forgot-password", response_model=AdminOTPMessageResponse)
async def admin_forgot_password(
    payload: AdminForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_forgot_pw", limit=20, window=3600)

    admin = svc.get_admin_by_mobile(db, payload.mobile_number)
    otp: Optional[str] = None
    if admin is not None and admin.is_active:
        otp = svc.create_admin_otp(
            db, admin.mobile_number, svc.OTP_PURPOSE_RESET
        )
        logger.info("[ADMIN FORGOT PW] %s: %s", admin.mobile_number, otp)

    return AdminOTPMessageResponse(
        detail=(
            "Reset code generated. Enter the code shown to continue."
            if otp
            else "If the mobile number exists, a reset code has been sent."
        ),
        otp=otp,  # DEV ONLY
    )


# =========================================================
# RESET PASSWORD
# =========================================================

@router.post("/reset-password", response_model=AdminMessageResponse)
async def admin_reset_password(
    payload: AdminResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    await _rate_limit(request, "admin_reset_pw", limit=20, window=3600)

    admin = svc.get_admin_by_mobile(db, payload.mobile_number)
    if admin is None or not admin.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    ok = svc.verify_admin_otp(
        db, admin.mobile_number, payload.otp, svc.OTP_PURPOSE_RESET
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    svc.set_admin_password(db, admin, payload.new_password)
    svc.revoke_all_admin_sessions(db, admin.id)

    return AdminMessageResponse(
        detail="Password reset successful. Please log in."
    )


# =========================================================
# DEV-ONLY: clear rate-limit counters for the calling IP
# =========================================================

@router.post(
    "/__dev__/clear-ratelimit",
    include_in_schema=False,
    response_model=AdminMessageResponse,
)
async def dev_clear_ratelimit(request: Request):
    """
    Nuke every rate-limit key for this client's IP. Only available when
    ENVIRONMENT != production. Returns 404 in prod so it doesn't exist.
    """
    if _IS_PROD:
        raise HTTPException(status_code=404, detail="Not found")

    actions = (
        "admin_register",
        "admin_login",
        "admin_verify_otp",
        "admin_resend_otp",
        "admin_forgot_pw",
        "admin_reset_pw",
        "admin_change_pw",
    )
    ip = _client_ip(request)
    cleared = 0

    try:
        r = get_redis()
        for action in actions:
            for pattern in (
                f"usanex:rate:*{action}*{ip}*",
                f"usanex:rate:{action}:{ip}",
                f"usanex:rate:{action}:*",
            ):
                keys = await r.keys(pattern)
                if keys:
                    await r.delete(*keys)
                    cleared += len(keys)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Redis error: {e}"
        )

    return AdminMessageResponse(
        detail=f"Cleared {cleared} rate-limit key(s) for {ip}."
    )
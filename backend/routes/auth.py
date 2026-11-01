
# =========================================================
# USANEX — AUTHENTICATION ROUTES
# File: backend/routes/auth.py
# =========================================================

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database.database import get_db
from ..database.models import User, UserSession

from ..services.auth_service import (
    generate_user_id,
    generate_username,
    hash_password,
    verify_password,
)

from ..services.otp_service import create_otp, verify_otp

# Redis services
from ..services.redis import session_service, otp_service


router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"],
)


# =========================================================
# CONFIGURATION
# =========================================================

SESSION_COOKIE_NAME = "usanex_session"

SESSION_DURATION_DAYS = 30
SESSION_DURATION_SECONDS = SESSION_DURATION_DAYS * 24 * 60 * 60

OTP_LOGIN_DURATION_MINUTES = 10


# =========================================================
# TIME HELPER
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================
# SESSION HELPERS
# =========================================================

def create_user_session(db: Session, user_id: int) -> tuple:
    session_token = secrets.token_urlsafe(48)

    now = utc_now()

    expires_at = now + timedelta(
        days=SESSION_DURATION_DAYS
    )

    new_session = UserSession(
        session_token=session_token,
        user_id=user_id,
        expires_at=expires_at,
        created_at=now,
    )

    db.add(new_session)
    db.commit()
    db.refresh(new_session)

    return session_token, expires_at


async def create_authenticated_session(
    db: Session,
    user_id: int,
    response: Response,
):
    session_token, expires_at = create_user_session(
        db=db,
        user_id=user_id,
    )

    # Save session in Redis
    await session_service.cache_session(
        session_token,
        user_id,
        expires_at,
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_DURATION_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )

    return session_token


async def get_valid_session(
    db: Session,
    session_token: str | None,
):
    if not session_token:
        return None

    # 1. Check Redis first
    cached_session = await session_service.get_session(
        session_token
    )

    if cached_session:
        return cached_session

    # 2. Database fallback
    session = (
        db.query(UserSession)
        .filter(
            UserSession.session_token == session_token
        )
        .first()
    )

    if session is None:
        return None

    expires_at = session.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(
            tzinfo=timezone.utc
        )

    if datetime.now(timezone.utc) >= expires_at:
        db.delete(session)
        db.commit()

        await session_service.delete_session(
            session_token
        )

        return None

    # Restore cache
    await session_service.cache_session(
        session_token,
        session.user_id,
        expires_at,
    )

    return session


# =========================================================
# CURRENT USER HELPER
# =========================================================

async def get_current_user_from_request(
    request: Request,
    db: Session = Depends(get_db),
):
    session_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    # Native mobile support
    if not session_token:
        authorization = request.headers.get(
            "authorization", ""
        )

        if authorization.lower().startswith("bearer "):
            session_token = authorization[7:].strip()

    session = await get_valid_session(
        db=db,
        session_token=session_token,
    )

    if session is None:
        return None

    # Redis cache can return a dictionary
    if isinstance(session, dict):
        user_id = session.get("user_id")
    else:
        user_id = session.user_id

    return (
        db.query(User)
        .filter(User.id == user_id)
        .first()
    )


# =========================================================
# USER SERIALIZATION
# =========================================================

def serialize_user(user: User):
    return {
        "id": user.id,
        "username": user.username,
        "user_id": user.user_id,
        "name": user.name,
        "mobile": user.mobile,
        "profile_photo": user.profile_photo,
    }


# =========================================================
# REGISTER — SEND OTP
# =========================================================

class RegisterOTPRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mobile: str = Field(min_length=5, max_length=20)
    password: str = Field(min_length=6, max_length=128)


@router.post("/register/send-otp")
async def register_send_otp(
    request: RegisterOTPRequest,
    db: Session = Depends(get_db),
):
    name = request.name.strip()
    mobile = request.mobile.strip()

    if not name or not mobile:
        raise HTTPException(
            status_code=400,
            detail="Name and mobile are required",
        )

    existing_user = (
        db.query(User)
        .filter(User.mobile == mobile)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=409,
            detail="Mobile number is already registered",
        )

    # Generate OTP in Redis
    otp = await otp_service.generate_otp(
        "register",
        mobile,
    )

    return {
        "success": True,
        "message": "Registration OTP generated",
        # Development only. Remove in production.
        "development_otp": otp,
    }


# =========================================================
# REGISTER — VERIFY OTP
# =========================================================

class RegisterVerifyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    mobile: str = Field(min_length=5, max_length=20)
    password: str = Field(min_length=6, max_length=128)
    otp: str = Field(min_length=6, max_length=6)


@router.post("/register/verify")
async def register_verify(
    request: RegisterVerifyRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    name = request.name.strip()
    mobile = request.mobile.strip()

    existing_user = (
        db.query(User)
        .filter(User.mobile == mobile)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=409,
            detail="Mobile number is already registered",
        )

    # Verify Redis OTP
    verified = await otp_service.verify_otp(
        "register",
        mobile,
        request.otp.strip(),
    )

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP",
        )

    username = generate_username(
        name=name,
        db=db,
    )

    while True:
        user_id = generate_user_id()

        existing_user_id = (
            db.query(User)
            .filter(User.user_id == user_id)
            .first()
        )

        if existing_user_id is None:
            break

    new_user = User(
        username=username,
        user_id=user_id,
        name=name,
        mobile=mobile,
        password_hash=hash_password(
            request.password
        ),
        profile_photo=None,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Automatically login after registration
    session_token = await create_authenticated_session(
        db,
        new_user.id,
        response,
    )

    return {
        "success": True,
        "message": "Account created successfully",
        "user": serialize_user(new_user),
        "session_token": session_token,
    }


# =========================================================
# PASSWORD LOGIN
# =========================================================

class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


@router.post("/login")
async def login(
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    identifier = request.identifier.strip()

    user = (
        db.query(User)
        .filter(
            (User.username == identifier)
            | (User.mobile == identifier)
        )
        .first()
    )

    if user is None or not verify_password(
        password=request.password,
        password_hash=user.password_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid username/mobile or password",
        )

    # Invalidate previous sessions
    old_sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id)
        .all()
    )

    for old_session in old_sessions:
        await session_service.delete_session(
            old_session.session_token
        )

    db.query(UserSession).filter(
        UserSession.user_id == user.id
    ).delete(synchronize_session=False)

    db.commit()

    session_token = await create_authenticated_session(
        db,
        user.id,
        response,
    )

    return {
        "success": True,
        "message": "Login successful",
        "user": serialize_user(user),
        "session_token": session_token,
    }


# =========================================================
# MOBILE OTP LOGIN — SEND OTP
# =========================================================

class MobileOTPRequest(BaseModel):
    mobile: str = Field(min_length=5, max_length=20)


@router.post("/send-otp")
async def send_otp(
    request: MobileOTPRequest,
    db: Session = Depends(get_db),
):
    mobile = request.mobile.strip()

    user = (
        db.query(User)
        .filter(User.mobile == mobile)
        .first()
    )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Account not found",
        )

    otp = await otp_service.generate_otp(
        "login",
        mobile,
    )

    # TODO: Integrate SMS provider here.
    # Never print OTP in production logs.

    return {
        "success": True,
        "message": "OTP sent successfully",
        "development_otp": otp,
    }


# =========================================================
# MOBILE OTP LOGIN — VERIFY OTP
# =========================================================

class MobileOTPVerifyRequest(BaseModel):
    mobile: str = Field(min_length=5, max_length=20)
    otp: str = Field(min_length=6, max_length=6)


@router.post("/login/otp")
async def login_with_otp(
    request: MobileOTPVerifyRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    mobile = request.mobile.strip()

    verified = await otp_service.verify_otp(
        "login",
        mobile,
        request.otp.strip(),
    )

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP",
        )

    user = (
        db.query(User)
        .filter(User.mobile == mobile)
        .first()
    )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Account not found",
        )

    session_token = await create_authenticated_session(
        db,
        user.id,
        response,
    )

    return {
        "success": True,
        "message": "OTP login successful",
        "user": serialize_user(user),
        "session_token": session_token,
    }


# =========================================================
# CURRENT USER
# =========================================================

@router.get("/me")
async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_current_user_from_request(
        request,
        db,
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return {
        "success": True,
        "user": serialize_user(user),
    }


# =========================================================
# LOGOUT
# =========================================================

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    session_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not session_token:
        authorization = request.headers.get(
            "authorization", ""
        )

        if authorization.lower().startswith("bearer "):
            session_token = authorization[7:].strip()

    if session_token:
        session = (
            db.query(UserSession)
            .filter(
                UserSession.session_token == session_token
            )
            .first()
        )

        if session:
            db.delete(session)
            db.commit()

        # Remove Redis cache
        await session_service.delete_session(
            session_token
        )

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )

    return {
        "success": True,
        "message": "Logged out successfully",
    }


# =========================================================
# FORGOT PASSWORD — SEND OTP
# =========================================================

class ForgotPasswordOTPRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=100)


@router.post("/forgot-password/send-otp")
async def forgot_password_send_otp(
    request: ForgotPasswordOTPRequest,
    db: Session = Depends(get_db),
):
    identifier = request.identifier.strip()

    user = (
        db.query(User)
        .filter(
            (User.username == identifier)
            | (User.mobile == identifier)
        )
        .first()
    )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Account not found",
        )

    otp = await otp_service.generate_otp(
        "forgot_password",
        user.mobile,
    )

    return {
        "success": True,
        "message": "Password reset OTP generated",
        "development_otp": otp,
    }


# =========================================================
# FORGOT PASSWORD — RESET
# =========================================================

class ForgotPasswordResetRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=100)
    otp: str = Field(min_length=6, max_length=6)
    new_password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)


@router.post("/forgot-password/reset")
async def forgot_password_reset(
    request: ForgotPasswordResetRequest,
    db: Session = Depends(get_db),
):
    identifier = request.identifier.strip()

    if request.new_password != request.confirm_password:
        raise HTTPException(
            status_code=400,
            detail="Passwords do not match",
        )

    user = (
        db.query(User)
        .filter(
            (User.username == identifier)
            | (User.mobile == identifier)
        )
        .first()
    )

    if user is None:
        raise HTTPException(
            status_code=404,
            detail="Account not found",
        )

    # OTP is always associated with registered mobile
    verified = await otp_service.verify_otp(
        "forgot_password",
        user.mobile,
        request.otp.strip(),
    )

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP",
        )

    user.password_hash = hash_password(
        request.new_password
    )

    old_sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id)
        .all()
    )

    for old_session in old_sessions:
        await session_service.delete_session(
            old_session.session_token
        )

    db.query(UserSession).filter(
        UserSession.user_id == user.id
    ).delete(synchronize_session=False)

    db.commit()

    return {
        "success": True,
        "message": "Password reset successfully",
    }

from __future__ import annotations
from ..services.reel_recommendation import get_recommended_reels
import json
import os
import uuid
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import cloudinary
import cloudinary.uploader
import cloudinary.utils

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.redis_client import get_redis
from ..database.database import get_db
from ..database.models import (
    User,
    UserSession,
    Reel,
    ReelComment,
    ReelCommentLike,
    ReelReport,
    ReelSave,
    ReelDownload,
    ReelShare,
    ReelLike,
    ReelInteraction,
    UserReelHistory,
    ReelAIFeature,
)
from ..services.redis import session_service

# =========================================================
# ROUTER
# =========================================================

router = APIRouter(
    prefix="/api/reels",
    tags=["Reels"],
)

# =========================================================
# CONSTANTS
# =========================================================

DEFAULT_FEED_LIMIT = 10
MAX_FEED_LIMIT = 50
MAX_REEL_LIST_LIMIT = 100

MAX_COMMENT_LENGTH = 2000
MAX_REPORT_DESCRIPTION = 2000
MAX_CAPTION_LENGTH = 5000
MAX_HASHTAGS_LENGTH = 2000

MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB

ALLOWED_VIDEO_TYPES = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "video/x-matroska",
}

ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
}

FEED_CACHE_TTL = 30
FEED_CACHE_PATTERN = "usanex:reels:feed:*"

# =========================================================
# CLOUDINARY
# =========================================================

def configure_cloudinary() -> bool:
    """Configure Cloudinary from CLOUDINARY_URL or separate env vars."""
    cloudinary_url = os.getenv("CLOUDINARY_URL")
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    api_key = os.getenv("CLOUDINARY_API_KEY")
    api_secret = os.getenv("CLOUDINARY_API_SECRET")

    try:
        if cloudinary_url:
            cloudinary.config(cloudinary_url=cloudinary_url, secure=True)
        elif cloud_name and api_key and api_secret:
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )
        else:
            return False

        config = cloudinary.config()
        return bool(config.cloud_name and config.api_key and config.api_secret)
    except Exception:
        return False


CLOUDINARY_CONFIGURED = configure_cloudinary()


def cloudinary_ready() -> bool:
    global CLOUDINARY_CONFIGURED
    if CLOUDINARY_CONFIGURED:
        return True
    CLOUDINARY_CONFIGURED = configure_cloudinary()
    return CLOUDINARY_CONFIGURED


async def invalidate_feed_cache() -> None:
    """Best-effort: clear all cached feed pages."""
    try:
        r = get_redis()
        async for key in r.scan_iter(match=FEED_CACHE_PATTERN, count=200):
            await r.delete(key)
    except Exception:
        pass


# =========================================================
# TIME
# =========================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# =========================================================
# AUTH (Redis-first)
# =========================================================

async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get("usanex_session")
    if not token:
        token = request.cookies.get("session_token")

    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    cached = await session_service.get_session(token)
    if cached:
        user = (
            db.query(User)
            .filter(User.id == cached.get("user_id"))
            .first()
        )
        if user is None:
            await session_service.delete_session(token, cached.get("user_id"))
            raise HTTPException(status_code=401, detail="User not found")
        if not getattr(user, "is_active", True):
            raise HTTPException(status_code=403, detail="Inactive account")
        return user

    session = db.query(UserSession).filter(UserSession.session_token == token).first()
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid session")

    if not getattr(session, "is_active", True):
        raise HTTPException(status_code=401, detail="Invalid session")

    expires_at = session.expires_at
    if expires_at is None:
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid session")

    if getattr(expires_at, "tzinfo", None) is not None:
        expires_at = expires_at.replace(tzinfo=None)

    if utc_now() >= expires_at:
        db.delete(session)
        db.commit()
        await session_service.delete_session(token, session.user_id)
        raise HTTPException(status_code=401, detail="Session expired")

    user = db.query(User).filter(User.id == session.user_id).first()
    if user is None:
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=401, detail="User not found")

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Inactive account")

    expires_at_aware = expires_at.replace(tzinfo=timezone.utc)
    await session_service.cache_session(token, user.id, expires_at_aware)

    return user


# =========================================================
# SERIALIZATION
# =========================================================

def serialize_user(user: Optional[User]) -> Optional[dict[str, Any]]:
    if user is None:
        return None
    return {
        "id": user.id,
        "username": user.username,
        "user_id": user.user_id,
        "name": user.name,
        "profile_photo": user.profile_photo,
    }


def serialize_reel(reel: Reel, creator: Optional[User] = None) -> dict[str, Any]:
    return {
        "id": reel.id,
        "user_id": reel.user_id,
        "video_url": reel.video_url,
        "thumbnail_url": reel.thumbnail_url,
        "cloudinary_public_id": getattr(reel, "cloudinary_public_id", None),
        "duration": float(reel.duration or 0),
        "file_size": int(reel.file_size or 0),
        "caption": reel.caption,
        "hashtags": reel.hashtags,
        "language": reel.language,
        "category": reel.category,
        "visibility": reel.visibility,
        "status": reel.status,
        "views_count": int(reel.views_count or 0),
        "unique_views_count": int(reel.unique_views_count or 0),
        "completed_views_count": int(reel.completed_views_count or 0),
        "replay_count": int(reel.replay_count or 0),
        "share_count": int(reel.share_count or 0),
        "save_count": int(reel.save_count or 0),
        "download_count": int(reel.download_count or 0),
        "comment_count": int(reel.comment_count or 0),
        "likes_count": int(reel.likes_count or 0),
        "interested_count": int(reel.interested_count or 0),
        "not_interested_count": int(reel.not_interested_count or 0),
        "ai_processed": bool(getattr(reel, "ai_processed", False)),
        "ai_processing_status": getattr(reel, "ai_processing_status", None),
        "ai_category": getattr(reel, "ai_category", None),
        "ai_confidence": getattr(reel, "ai_confidence", None),
        "is_safe": bool(getattr(reel, "is_safe", True)),
        "created_at": reel.created_at.isoformat() if reel.created_at else None,
        "updated_at": reel.updated_at.isoformat() if reel.updated_at else None,
        "creator": serialize_user(creator),
    }


# =========================================================
# REQUEST MODELS
# =========================================================

class WatchEventRequest(BaseModel):
    watch_time: float = Field(default=0, ge=0, le=86400)
    watch_percentage: float = Field(default=0, ge=0, le=100)
    completed: bool = False
    replayed: bool = False
    replay_count: int = Field(default=0, ge=0, le=100)
    session_id: Optional[str] = Field(default=None, max_length=100)
    feed_position: Optional[int] = Field(default=None, ge=0, le=10000)
    source: Optional[str] = Field(default=None, max_length=50)


class ShareRequest(BaseModel):
    share_type: Optional[str] = Field(default=None, max_length=50)


class CommentRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_COMMENT_LENGTH)
    parent_comment_id: Optional[int] = None


class ReportRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=MAX_REPORT_DESCRIPTION)


# =========================================================
# HELPERS
# =========================================================

def get_reel_or_404(db: Session, reel_id: int) -> Reel:
    reel = db.query(Reel).filter(Reel.id == reel_id).first()
    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")
    return reel


def get_creator(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def ensure_reel_access(reel: Reel, current_user: User) -> None:
    if reel.user_id == current_user.id:
        return
    if reel.visibility != "public" or reel.status != "published" or not getattr(reel, "is_safe", True):
        raise HTTPException(status_code=404, detail="Reel not found")


def create_interaction(db: Session, *, user_id: int, reel: Reel, event_type: str, **kwargs) -> ReelInteraction:
    now = utc_now()
    interaction = ReelInteraction(
        user_id=user_id,
        reel_id=reel.id,
        creator_id=reel.user_id,
        event_type=event_type,
        created_at=now,
        updated_at=now,
        **kwargs
    )
    db.add(interaction)
    db.flush()
    return interaction


def update_reel_history(db: Session, user_id: int, reel: Reel, watch_time: float, watch_percentage: float, completed: bool, replay_count: int) -> tuple[UserReelHistory, bool]:
    history = db.query(UserReelHistory).filter(
        UserReelHistory.user_id == user_id,
        UserReelHistory.reel_id == reel.id,
    ).first()

    now = utc_now()

    if history is None:
        history = UserReelHistory(
            user_id=user_id,
            reel_id=reel.id,
            first_seen_at=now,
            last_seen_at=now,
            times_seen=1,
            total_watch_time=watch_time,
            max_watch_percentage=watch_percentage,
            completed_count=(1 if completed else 0),
            replay_count=replay_count,
        )
        db.add(history)
        return history, True

    history.last_seen_at = now
    history.times_seen = (history.times_seen or 0) + 1
    history.total_watch_time = (history.total_watch_time or 0) + watch_time
    history.max_watch_percentage = max(history.max_watch_percentage or 0, watch_percentage)
    if completed:
        history.completed_count = (history.completed_count or 0) + 1
    history.replay_count = (history.replay_count or 0) + replay_count
    return history, False


def normalize_optional_text(value: Optional[str], max_length: int) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:max_length]


def delete_cloudinary_video(public_id: Optional[str]) -> None:
    if not public_id or not cloudinary_ready():
        return
    try:
        cloudinary.uploader.destroy(public_id, resource_type="video", invalidate=True)
    except Exception:
        pass


# =========================================================
# HEALTH
# =========================================================

@router.get("/health")
def reels_health():
    return {
        "success": True,
        "service": "reels",
        "status": "online",
        "storage": "cloudinary",
        "cloudinary_configured": cloudinary_ready(),
    }


# =========================================================
# SERVICE STATUS (moved above /{reel_id} to avoid shadow)
# =========================================================

@router.get("/service/status")
def service_status():
    return {
        "success": True,
        "service": "Usanex Reels",
        "status": "online",
        "storage": "Cloudinary",
        "cloudinary_configured": cloudinary_ready(),
    }


# =========================================================
# FEED (30s Redis cache)
# =========================================================

@router.get("/feed")
async def get_reels_feed(
    limit: int = Query(DEFAULT_FEED_LIMIT, ge=1, le=MAX_FEED_LIMIT),
    cursor: Optional[int] = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    offset = cursor or 0

    cache_key = (
        f"usanex:reels:feed:"
        f"{current_user.id}:{limit}:{offset}"
    )

    # Redis cache
    try:
        cached = await get_redis().get(cache_key)

        if cached:
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8")

            return json.loads(cached)

    except Exception:
        pass

    # Recommended reels
    reels_db = get_recommended_reels(
        db=db,
        user_id=current_user.id,
        limit=limit,
        exclude_reel_ids=[],
        offset=offset,
    )

    # Serialize reels
    reels = [
        serialize_reel(reel, get_creator(db, reel.user_id))
        for reel in reels_db
    ]

    # Next page cursor (offset pagination)
    next_cursor = (
        offset + len(reels_db)
        if len(reels_db) == limit
        else None
    )

    response = {
        "success": True,
        "reels": reels,
        "count": len(reels),
        "next_cursor": next_cursor,
    }

    # Save Redis cache
    try:
        await get_redis().set(
            cache_key,
            json.dumps(response, default=str),
            ex=FEED_CACHE_TTL,
        )
    except Exception:
        pass

    return response
    



# =========================================================
# MY REELS
# =========================================================

@router.get("/me/list")
def get_my_reels(
    limit: int = Query(50, ge=1, le=MAX_REEL_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reels_db = db.query(Reel).filter(Reel.user_id == current_user.id).order_by(Reel.id.desc()).offset(offset).limit(limit).all()
    return {
        "success": True,
        "reels": [serialize_reel(reel, current_user) for reel in reels_db],
        "count": len(reels_db),
        "owner": serialize_user(current_user),
    }


# =========================================================
# USER REELS
# =========================================================

@router.get("/user/{user_id}/list")
def get_user_reels(
    user_id: str,
    limit: int = Query(50, ge=1, le=MAX_REEL_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile_user = db.query(User).filter(User.user_id == user_id).first()
    if profile_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    reels_db = db.query(Reel).filter(
        Reel.user_id == profile_user.id,
        Reel.status == "published",
        Reel.visibility == "public",
        Reel.is_safe.is_(True),
    ).order_by(Reel.id.desc()).offset(offset).limit(limit).all()

    return {
        "success": True,
        "reels": [serialize_reel(reel, profile_user) for reel in reels_db],
        "count": len(reels_db),
        "owner": serialize_user(profile_user),
    }


# =========================================================
# SAVED REELS
# =========================================================

@router.get("/me/saved")
def get_saved_reels(
    limit: int = Query(50, ge=1, le=MAX_REEL_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.query(ReelSave).filter(ReelSave.user_id == current_user.id).order_by(ReelSave.created_at.desc()).offset(offset).limit(limit).all()
    reels = []
    for row in rows:
        reel = db.query(Reel).filter(Reel.id == row.reel_id).first()
        if reel:
            reels.append(serialize_reel(reel, get_creator(db, reel.user_id)))
    return {"success": True, "reels": reels, "count": len(reels)}


# =========================================================
# UPLOAD REEL
# =========================================================

@router.post("/upload")
async def upload_reel(
    video: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    hashtags: Optional[str] = Form(default=None),
    language: Optional[str] = Form(default=None),
    category: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not cloudinary_ready():
        raise HTTPException(status_code=503, detail="Cloudinary is not configured")

    filename = video.filename or "video.mp4"
    extension = Path(filename).suffix.lower()
    content_type = (video.content_type or "").lower()

    if content_type not in ALLOWED_VIDEO_TYPES and extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported video format")

    caption_value = normalize_optional_text(caption, MAX_CAPTION_LENGTH)
    hashtags_value = normalize_optional_text(hashtags, MAX_HASHTAGS_LENGTH)
    language_value = normalize_optional_text(language, 50)
    category_value = normalize_optional_text(category, 100) or "reels"

    temp_path = None
    total_size = 0
    cloudinary_public_id = None

    try:
        suffix = extension if extension else ".mp4"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = temp_file.name
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_VIDEO_SIZE:
                    raise HTTPException(status_code=413, detail="Video size exceeds 2 GB limit")
                temp_file.write(chunk)

        if total_size <= 0:
            raise HTTPException(status_code=400, detail="Video file is empty")

        public_id = "reel_" + uuid.uuid4().hex
        upload_result = cloudinary.uploader.upload_large(
            temp_path,
            resource_type="video",
            folder="usanex/reels",
            public_id=public_id,
            overwrite=False,
            chunk_size=20_000_000,
        )

        video_url = upload_result.get("secure_url")
        if not video_url:
            raise RuntimeError("Cloudinary did not return video URL")

        cloudinary_public_id = upload_result.get("public_id")
        duration = float(upload_result.get("duration") or 0)
        thumbnail_url = None

        if cloudinary_public_id:
            try:
                thumbnail_result = cloudinary.utils.cloudinary_url(
                    cloudinary_public_id,
                    resource_type="video",
                    secure=True,
                    format="jpg",
                )
                thumbnail_url = thumbnail_result[0] if isinstance(thumbnail_result, tuple) else thumbnail_result
            except Exception:
                thumbnail_url = None

        now = utc_now()
        reel = Reel(
            user_id=current_user.id,
            video_url=video_url,
            thumbnail_url=thumbnail_url,
            duration=duration,
            file_size=total_size,
            caption=caption_value,
            hashtags=hashtags_value,
            language=language_value,
            category=category_value,
            visibility="public",
            status="published",
            views_count=0, unique_views_count=0, completed_views_count=0, replay_count=0,
            share_count=0, save_count=0, download_count=0, comment_count=0,
            interested_count=0, not_interested_count=0,
            ai_processed=False, ai_processing_status="pending", is_safe=True,
            created_at=now, updated_at=now,
        )

        if hasattr(reel, "cloudinary_public_id"):
            reel.cloudinary_public_id = cloudinary_public_id

        db.add(reel)
        db.commit()
        db.refresh(reel)

        await invalidate_feed_cache()

        return {"success": True, "message": "Reel uploaded successfully", "storage": "cloudinary", "cloudinary_public_id": cloudinary_public_id, "reel": serialize_reel(reel, current_user)}

    except HTTPException:
        db.rollback()
        if cloudinary_public_id:
            delete_cloudinary_video(cloudinary_public_id)
        raise
    except Exception as exc:
        db.rollback()
        if cloudinary_public_id:
            delete_cloudinary_video(cloudinary_public_id)
        raise HTTPException(status_code=500, detail=f"Reel upload failed: {str(exc)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try: os.remove(temp_path)
            except Exception: pass
        try: await video.close()
        except Exception: pass


# =========================================================
# SINGLE REEL
# =========================================================

@router.get("/{reel_id}")
def get_reel(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)
    return {"success": True, "reel": serialize_reel(reel, get_creator(db, reel.user_id))}


# =========================================================
# WATCH
# =========================================================

@router.post("/{reel_id}/watch")
def watch_reel(reel_id: int, payload: WatchEventRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    history = db.query(UserReelHistory).filter(UserReelHistory.user_id == current_user.id, UserReelHistory.reel_id == reel_id).first()
    is_unique_view = (history is None)

    reel.views_count = (reel.views_count or 0) + 1
    if is_unique_view: reel.unique_views_count = (reel.unique_views_count or 0) + 1
    if payload.completed: reel.completed_views_count = (reel.completed_views_count or 0) + 1

    replay_increment = 0
    if payload.replayed:
        replay_increment = max(1, payload.replay_count)
        reel.replay_count = (reel.replay_count or 0) + replay_increment

    update_reel_history(db, current_user.id, reel, payload.watch_time, payload.watch_percentage, payload.completed, replay_increment)
    interaction = create_interaction(
        db, user_id=current_user.id, reel=reel, event_type="replay" if payload.replayed else ("complete" if payload.completed else "watch"),
        session_id=payload.session_id, feed_position=payload.feed_position, source=payload.source,
        watch_time=payload.watch_time, watch_percentage=payload.watch_percentage,
        completed=payload.completed, replayed=payload.replayed, replay_count=replay_increment
    )
    db.commit()

    return {"success": True, "interaction_id": interaction.id, "is_unique_view": is_unique_view, "views_count": reel.views_count, "unique_views_count": reel.unique_views_count, "completed_views_count": reel.completed_views_count, "replay_count": reel.replay_count}


# =========================================================
# REEL LIKE / UNLIKE
# =========================================================


@router.post("/{reel_id}/like")
def like_reel(
    reel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reel = (
        db.query(Reel)
        .filter(Reel.id == reel_id)
        .with_for_update()
        .first()
    )

    if reel is None:
        raise HTTPException(status_code=404, detail="Reel not found")

    ensure_reel_access(reel, current_user)

    existing = db.query(ReelLike).filter(
        ReelLike.user_id == current_user.id,
        ReelLike.reel_id == reel_id,
    ).first()

    if existing:
        return {
            "success": True,
            "liked": True,
            "likes_count": reel.likes_count or 0,
        }

    db.add(ReelLike(
        user_id=current_user.id,
        reel_id=reel_id,
        created_at=utc_now(),
    ))

    reel.likes_count = (reel.likes_count or 0) + 1

    interaction = create_interaction(
        db,
        user_id=current_user.id,
        reel=reel,
        event_type="like",
    )

    db.commit()

    return {
        "success": True,
        "liked": True,
        "likes_count": reel.likes_count,
        "interaction_id": interaction.id,
    }

@router.delete("/{reel_id}/like")
def unlike_reel(
    reel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reel = (
    db.query(Reel)
    .filter(Reel.id == reel_id)
    .with_for_update()
    .first()
)

if reel is None:
    raise HTTPException(status_code=404, detail="Reel not found")

ensure_reel_access(reel, current_user)

    existing = db.query(ReelLike).filter(
        ReelLike.user_id == current_user.id,
        ReelLike.reel_id == reel_id,
    ).first()

    if existing:
        db.delete(existing)
        reel.likes_count = max(0, (reel.likes_count or 0) - 1)

        create_interaction(
            db,
            user_id=current_user.id,
            reel=reel,
            event_type="unlike",
        )

        db.commit()

    return {
        "success": True,
        "liked": False,
        "likes_count": reel.likes_count or 0,
    }


@router.get("/{reel_id}/liked")
def reel_like_status(
    reel_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reel = (
    db.query(Reel)
    .filter(Reel.id == reel_id)
    .with_for_update()
    .first()
)

if reel is None:
    raise HTTPException(status_code=404, detail="Reel not found")

ensure_reel_access(reel, current_user)

    liked = db.query(ReelLike.id).filter(
        ReelLike.user_id == current_user.id,
        ReelLike.reel_id == reel_id,
    ).first() is not None

    return {
        "success": True,
        "liked": liked,
        "likes_count": reel.likes_count or 0,
    }


# =========================================================
# SAVE
# =========================================================

@router.post("/{reel_id}/save")
def save_reel(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    existing = db.query(ReelSave).filter(ReelSave.user_id == current_user.id, ReelSave.reel_id == reel_id).first()
    if existing:
        return {"success": True, "saved": True, "save_count": reel.save_count or 0}

    db.add(ReelSave(user_id=current_user.id, reel_id=reel_id, created_at=utc_now()))
    reel.save_count = (reel.save_count or 0) + 1
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="save", saved=True)
    db.commit()
    return {"success": True, "saved": True, "save_count": reel.save_count, "interaction_id": interaction.id}


@router.delete("/{reel_id}/save")
def unsave_reel(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    saved = db.query(ReelSave).filter(ReelSave.user_id == current_user.id, ReelSave.reel_id == reel_id).first()
    if saved is None:
        return {"success": True, "saved": False, "save_count": reel.save_count or 0}

    db.delete(saved)
    reel.save_count = max(0, (reel.save_count or 0) - 1)
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="unsave")
    db.commit()
    return {"success": True, "saved": False, "save_count": reel.save_count, "interaction_id": interaction.id}


@router.get("/{reel_id}/saved")
def saved_status(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)
    saved = db.query(ReelSave).filter(ReelSave.user_id == current_user.id, ReelSave.reel_id == reel_id).first()
    return {"success": True, "saved": saved is not None}


# =========================================================
# DOWNLOAD & SHARE
# =========================================================

@router.post("/{reel_id}/download")
def download_reel(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)
    db.add(ReelDownload(user_id=current_user.id, reel_id=reel_id, created_at=utc_now()))
    reel.download_count = (reel.download_count or 0) + 1
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="download", downloaded=True)
    db.commit()
    return {"success": True, "download_count": reel.download_count, "video_url": reel.video_url, "interaction_id": interaction.id}


@router.post("/{reel_id}/share")
def share_reel(reel_id: int, payload: ShareRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)
    share_type = payload.share_type.strip() if payload.share_type else None
    db.add(ReelShare(user_id=current_user.id, reel_id=reel_id, share_type=share_type, created_at=utc_now()))
    reel.share_count = (reel.share_count or 0) + 1
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="share", shared=True)
    db.commit()
    return {"success": True, "share_count": reel.share_count, "interaction_id": interaction.id}


# =========================================================
# INTERESTED / NOT INTERESTED
# =========================================================

def _set_reel_interest(reel_id: int, interested_choice: bool, db: Session, current_user: User):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    rows = db.query(ReelInteraction).filter(
        ReelInteraction.user_id == current_user.id,
        ReelInteraction.reel_id == reel_id,
        (ReelInteraction.interested.is_(True) | ReelInteraction.not_interested.is_(True)),
    ).all()

    had_interested = any(row.interested for row in rows)
    had_not_interested = any(row.not_interested for row in rows)

    if (interested_choice and had_interested and not had_not_interested) or (not interested_choice and had_not_interested and not had_interested):
        return {"success": True, "interested": had_interested, "not_interested": had_not_interested, "interested_count": reel.interested_count or 0, "not_interested_count": reel.not_interested_count or 0}

    if had_interested: reel.interested_count = max(0, (reel.interested_count or 0) - 1)
    if had_not_interested: reel.not_interested_count = max(0, (reel.not_interested_count or 0) - 1)

    for row in rows:
        row.interested = False
        row.not_interested = False

    if interested_choice: reel.interested_count = (reel.interested_count or 0) + 1
    else: reel.not_interested_count = (reel.not_interested_count or 0) + 1

    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="interested" if interested_choice else "not_interested", interested=interested_choice, not_interested=not interested_choice)
    db.commit()
    return {"success": True, "interested": interested_choice, "not_interested": not interested_choice, "interested_count": reel.interested_count or 0, "not_interested_count": reel.not_interested_count or 0, "interaction_id": interaction.id}


@router.post("/{reel_id}/interested")
def mark_interested(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _set_reel_interest(reel_id, True, db, current_user)


@router.post("/{reel_id}/not-interested")
def mark_not_interested(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _set_reel_interest(reel_id, False, db, current_user)


# =========================================================
# COMMENTS
# =========================================================

@router.post("/{reel_id}/comments")
def add_comment(reel_id: int, payload: CommentRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    content = payload.content.strip()
    if not content: raise HTTPException(status_code=400, detail="Comment cannot be empty")

    if payload.parent_comment_id is not None:
        parent = db.query(ReelComment).filter(ReelComment.id == payload.parent_comment_id, ReelComment.reel_id == reel_id, ReelComment.is_deleted.is_(False)).first()
        if parent is None: raise HTTPException(status_code=404, detail="Parent comment not found")

    comment = ReelComment(user_id=current_user.id, reel_id=reel_id, parent_comment_id=payload.parent_comment_id, content=content, is_deleted=False, created_at=utc_now())
    db.add(comment)
    reel.comment_count = (reel.comment_count or 0) + 1
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="comment", commented=True)
    db.commit()
    db.refresh(comment)

    return {"success": True, "comment": {"id": comment.id, "user_id": comment.user_id, "reel_id": comment.reel_id, "content": comment.content, "created_at": comment.created_at.isoformat() if comment.created_at else None, "user": serialize_user(current_user)}, "interaction_id": interaction.id}


@router.get("/{reel_id}/comments")
def get_comments(reel_id: int, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    comments = db.query(ReelComment).filter(ReelComment.reel_id == reel_id, ReelComment.is_deleted.is_(False)).order_by(ReelComment.created_at.desc(), ReelComment.id.desc()).offset(offset).limit(limit).all()
    response = []

    for comment in comments:
        user = get_creator(db, comment.user_id)
        likes_count = db.query(ReelCommentLike).filter(ReelCommentLike.comment_id == comment.id).count()
        liked = db.query(ReelCommentLike.id).filter(ReelCommentLike.comment_id == comment.id, ReelCommentLike.user_id == current_user.id).first() is not None
        response.append({
            "id": comment.id,
            "user_id": comment.user_id,
            "reel_id": comment.reel_id,
            "parent_comment_id": comment.parent_comment_id,
            "likes_count": likes_count,
            "liked": liked,
            "content": comment.content,
            "created_at": comment.created_at.isoformat() if comment.created_at else None,
            "user": serialize_user(user),
        })

    return {"success": True, "comments": response, "count": len(response), "limit": limit, "offset": offset}


@router.post("/comments/{comment_id}/like")
def toggle_comment_like(comment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    comment = db.query(ReelComment).filter(ReelComment.id == comment_id, ReelComment.is_deleted.is_(False)).first()
    if comment is None: raise HTTPException(status_code=404, detail="Comment not found")

    reel = get_reel_or_404(db, comment.reel_id)
    ensure_reel_access(reel, current_user)

    existing = db.query(ReelCommentLike).filter(ReelCommentLike.comment_id == comment_id, ReelCommentLike.user_id == current_user.id).first()
    if existing:
        db.delete(existing)
        liked = False
    else:
        db.add(ReelCommentLike(comment_id=comment_id, user_id=current_user.id))
        liked = True

    db.commit()
    likes_count = db.query(ReelCommentLike).filter(ReelCommentLike.comment_id == comment_id).count()
    return {"success": True, "comment_id": comment_id, "liked": liked, "likes_count": likes_count}


@router.delete("/{reel_id}/comments/{comment_id}")
def delete_comment(reel_id: int, comment_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    comment = db.query(ReelComment).filter(ReelComment.id == comment_id, ReelComment.reel_id == reel_id).first()

    if comment is None: raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != current_user.id: raise HTTPException(status_code=403, detail="You can delete only your own comment")
    if comment.is_deleted: return {"success": True, "message": "Comment already deleted"}

    comment.is_deleted = True
    reel.comment_count = max(0, (reel.comment_count or 0) - 1)
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="comment_delete")
    db.commit()
    return {"success": True, "message": "Comment deleted successfully", "comment_id": comment.id, "comment_count": reel.comment_count, "interaction_id": interaction.id}


# =========================================================
# REPORT & DELETE
# =========================================================

@router.post("/{reel_id}/report")
def report_reel(reel_id: int, payload: ReportRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    reason = payload.reason.strip()
    if not reason: raise HTTPException(status_code=400, detail="Report reason is required")

    description = payload.description.strip() if payload.description else None
    report = ReelReport(user_id=current_user.id, reel_id=reel_id, reason=reason, description=description, created_at=utc_now())
    db.add(report)
    interaction = create_interaction(db, user_id=current_user.id, reel=reel, event_type="report")
    db.commit()
    return {"success": True, "message": "Reel reported successfully", "report_id": report.id, "interaction_id": interaction.id}


@router.delete("/{reel_id}")
async def delete_my_reel(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    if reel.user_id != current_user.id: raise HTTPException(status_code=403, detail="You can delete only your own reel")

    cloudinary_public_id = getattr(reel, "cloudinary_public_id", None)
    cloudinary_delete_error = None

    if cloudinary_public_id and cloudinary_ready():
        try:
            result = cloudinary.uploader.destroy(cloudinary_public_id, resource_type="video")
            result_status = result.get("result") if isinstance(result, dict) else None
            if result_status not in {"ok", "not found"}: cloudinary_delete_error = result_status
        except Exception as exc:
            cloudinary_delete_error = str(exc)

    reel.visibility = "deleted"
    reel.status = "deleted"
    reel.updated_at = utc_now()
    db.commit()

    await invalidate_feed_cache()

    return {"success": True, "message": "Reel deleted successfully", "cloudinary_deleted": cloudinary_delete_error is None}


# =========================================================
# REEL STATUS & AI STATUS
# =========================================================

@router.get("/{reel_id}/status")
def reel_status(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    saved = db.query(ReelSave).filter(ReelSave.user_id == current_user.id, ReelSave.reel_id == reel_id).first() is not None
    liked = db.query(ReelLike.id).filter(
    ReelLike.user_id == current_user.id,
    ReelLike.reel_id == reel_id,
).first() is not None
    interested = db.query(ReelInteraction).filter(ReelInteraction.user_id == current_user.id, ReelInteraction.reel_id == reel_id, ReelInteraction.interested.is_(True)).first() is not None
    not_interested = db.query(ReelInteraction).filter(ReelInteraction.user_id == current_user.id, ReelInteraction.reel_id == reel_id, ReelInteraction.not_interested.is_(True)).first() is not None

    return {
        "success": True,
        "reel_id": reel_id,
        "saved": saved,
        "liked": liked,
        "likes_count": int(reel.likes_count or 0),
        "interested": interested,
        "not_interested": not_interested,
    }


@router.get("/{reel_id}/ai-status")
def reel_ai_status(reel_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reel = get_reel_or_404(db, reel_id)
    ensure_reel_access(reel, current_user)

    ai_feature = db.query(ReelAIFeature).filter(ReelAIFeature.reel_id == reel_id).order_by(ReelAIFeature.id.desc()).first()

    return {
        "success": True,
        "reel_id": reel_id,
        "ai_processed": bool(reel.ai_processed),
        "ai_processing_status": reel.ai_processing_status,
        "ai_category": reel.ai_category,
        "ai_confidence": reel.ai_confidence,
        "is_safe": bool(reel.is_safe),
        "ai_feature": {"id": ai_feature.id, "feature_type": getattr(ai_feature, "feature_type", None), "feature_value": getattr(ai_feature, "feature_value", None)} if ai_feature else None,
    }

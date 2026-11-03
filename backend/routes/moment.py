from fastapi import Query
import json
import logging
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database.database import get_db
from ..services.supabase_storage import (
    upload_moment_media,
    delete_moment_media,
)
from .auth import get_current_user_from_request


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/moments",
    tags=["Nex Moments"],
)


# --------------------------------------------------
# AUTHENTICATION
# --------------------------------------------------

async def get_authenticated_user(
    request: Request,
    db: Session,
):
    user = await get_current_user_from_request(request, db)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Login required",
        )

    return user


# --------------------------------------------------
# AUDIENCE VALIDATION AND SELECTED USERS
# --------------------------------------------------

NORMAL_AUDIENCES = {
    "connections",
    "following",
    "followers",
    "selected_people",
    "only_me",
}


def validate_moment_audience(
    audience: str,
    moment_scope: str,
    selected_user_ids: list[str],
):
    if moment_scope == "couple":
        if audience != "only_me" or selected_user_ids:
            raise HTTPException(
                status_code=400,
                detail="Couple Moment must use only_me audience",
            )
        return

    if moment_scope != "normal":
        raise HTTPException(
            status_code=400,
            detail="Invalid Moment scope",
        )

    if audience not in NORMAL_AUDIENCES:
        raise HTTPException(
            status_code=400,
            detail="Invalid Moment audience",
        )

    if audience == "selected_people" and not selected_user_ids:
        raise HTTPException(
            status_code=400,
            detail="Select at least one person",
        )

    if audience != "selected_people" and selected_user_ids:
        raise HTTPException(
            status_code=400,
            detail="Selected users are only allowed for selected_people audience",
        )


def save_moment_audience(
    db: Session,
    moment_id: int,
    selected_user_ids: list[str],
    creator_user_id: str,
):
    # Remove whitespace and duplicate IDs
    selected_user_ids = list(
        dict.fromkeys(
            user_id.strip()
            for user_id in selected_user_ids
            if user_id and user_id.strip()
        )
    )

    if len(selected_user_ids) > 500:
        raise HTTPException(
            status_code=400,
            detail="Maximum 500 selected users allowed",
        )

    if selected_user_ids:
        valid_users = db.execute(
            text("""
                SELECT user_id
                FROM public.users
                WHERE user_id = ANY(:user_ids)
            """),
            {
                "user_ids": selected_user_ids,
            },
        ).scalars().all()

        if set(valid_users) != set(selected_user_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more selected users do not exist",
            )

    # Replace the existing selected audience list
    db.execute(
        text("""
            DELETE FROM public.nex_moment_audience
            WHERE moment_id = :moment_id
        """),
        {
            "moment_id": moment_id,
        },
    )

    if selected_user_ids:
        db.execute(
            text("""
                INSERT INTO public.nex_moment_audience
                    (moment_id, allowed_user_id)
                VALUES
                    (:moment_id, :user_id)
            """),
            [
                {
                    "moment_id": moment_id,
                    "user_id": user_id,
                }
                for user_id in selected_user_ids
            ],
        )
# --------------------------------------------------
# AUDIENCE USERS LIST
# --------------------------------------------------

@router.get("/audience-users")
async def get_audience_users(
    request: Request,
    type: Literal[
        "connections",
        "following",
        "followers",
    ] = Query("connections"),
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    try:
        rows = db.execute(
            text("""
                WITH current_user AS (
                    SELECT id, user_id
                    FROM public.users
                    WHERE user_id = :user_id
                )
                SELECT DISTINCT
                    u.user_id,
                    u.name,
                    u.username,
                    u.profile_photo
                FROM public.users u
                CROSS JOIN current_user me
                WHERE
                    u.id != me.id
                    AND (
                        (
                            :audience_type = 'connections'
                            AND EXISTS (
                                SELECT 1
                                FROM public.user_connections c
                                WHERE c.status = 'connected'
                                  AND (
                                      (
                                          c.user_one_id = me.id
                                          AND c.user_two_id = u.id
                                      )
                                      OR
                                      (
                                          c.user_two_id = me.id
                                          AND c.user_one_id = u.id
                                      )
                                  )
                            )
                        )
                        OR
                        (
                            :audience_type = 'following'
                            AND EXISTS (
                                SELECT 1
                                FROM public.user_follows f
                                WHERE f.follower_id = me.id
                                  AND f.following_id = u.id
                            )
                        )
                        OR
                        (
                            :audience_type = 'followers'
                            AND EXISTS (
                                SELECT 1
                                FROM public.user_follows f
                                WHERE f.follower_id = u.id
                                  AND f.following_id = me.id
                            )
                        )
                    )
                ORDER BY u.name ASC
            """),
            {
                "user_id": user.user_id,
                "audience_type": type,
            },
        ).mappings().all()

        return {
            "success": True,
            "type": type,
            "count": len(rows),
            "users": [dict(row) for row in rows],
        }

    except Exception:
        logger.exception("Audience users fetch failed")

        raise HTTPException(
            status_code=500,
            detail="Unable to fetch audience users",
        )

# --------------------------------------------------
# HEALTH
# --------------------------------------------------

@router.get("/health")
async def moment_health(
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    return {
        "success": True,
        "message": "Nex Moment backend is running",
        "user_id": user.user_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------
# REQUEST MODELS
# --------------------------------------------------

class CreateMomentRequest(BaseModel):
    caption: str = Field(min_length=1, max_length=2000)

    audience: Literal[
        "connections",
        "following",
        "followers",
        "selected_people",
        "only_me",
    ] = "connections"

    moment_scope: Literal["normal", "couple"] = "normal"

    selected_user_ids: list[str] = Field(default_factory=list)


class UpdateMomentRequest(BaseModel):
    caption: str = Field(min_length=1, max_length=2000)

    audience: Literal[
        "connections",
        "following",
        "followers",
        "selected_people",
        "only_me",
    ]

    moment_scope: Literal["normal", "couple"]

    selected_user_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------
# CREATE TEXT MOMENT
# --------------------------------------------------

@router.post("/create")
async def create_moment(
    payload: CreateMomentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    selected_user_ids = payload.selected_user_ids

    validate_moment_audience(
        payload.audience,
        payload.moment_scope,
        selected_user_ids,
    )

    try:
        result = db.execute(
            text("""
                INSERT INTO nex_moments
                (
                    user_id,
                    moment_type,
                    caption,
                    audience,
                    moment_scope,
                    moderation_status,
                    created_at,
                    expires_at
                )
                VALUES (
                    :user_id,
                    'text',
                    :caption,
                    :audience,
                    :moment_scope,
                    'active',
                    NOW(),
                    NOW() + INTERVAL '24 hours'
                )
                RETURNING
                    id,
                    user_id,
                    caption,
                    audience,
                    moment_scope,
                    created_at,
                    expires_at
            """),
            {
                "user_id": user.user_id,
                "caption": payload.caption.strip(),
                "audience": payload.audience,
                "moment_scope": payload.moment_scope,
            },
        ).mappings().first()

        if not result:
            raise HTTPException(
                status_code=500,
                detail="Moment could not be created",
            )

        save_moment_audience(
            db,
            result["id"],
            selected_user_ids,
        )

        db.commit()

        return {
            "success": True,
            "message": "Moment created successfully",
            "moment": dict(result),
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        logger.exception("Moment creation failed")

        raise HTTPException(
            status_code=500,
            detail="Moment creation failed",
        )


# --------------------------------------------------
# MOMENT FEED
# --------------------------------------------------

@router.get("/feed")
async def get_moment_feed(
    request: Request,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="Limit must be between 1 and 100",
        )

    try:
        rows = db.execute(
            text("""
                SELECT
                    id,
                    user_id,
                    moment_type,
                    caption,
                    media_url,
                    audience,
                    moment_scope,
                    created_at,
                    expires_at
                FROM nex_moments
                WHERE
                    expires_at > NOW()
                    AND archived_at IS NULL
                    AND deleted_at IS NULL
                    AND moderation_status = 'active'
                    AND (
                        user_id = :viewer_id
                        OR (
                            moment_scope = 'normal'
                            AND can_view_normal_moment(
                                id,
                                :viewer_id
                            )
                        )
                        OR (
                            moment_scope = 'couple'
                            AND can_view_couple_moment(
                                id,
                                :viewer_id
                            )
                        )
                    )
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {
                "viewer_id": user.user_id,
                "limit": limit,
            },
        ).mappings().all()

        return {
            "success": True,
            "count": len(rows),
            "moments": [dict(row) for row in rows],
        }

    except Exception:
        logger.exception("Unable to fetch Moments")

        raise HTTPException(
            status_code=500,
            detail="Unable to fetch Moments",
        )


# --------------------------------------------------
# DELETE MOMENT
# --------------------------------------------------

@router.delete("/{moment_id}")
async def delete_moment(
    moment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    try:
        result = db.execute(
            text("""
                UPDATE nex_moments
                SET deleted_at = NOW()
                WHERE id = :moment_id
                  AND user_id = :user_id
                  AND deleted_at IS NULL
                RETURNING id, moderation_status, caption
            """),
            {
                "moment_id": moment_id,
                "user_id": user.user_id,
            },
        ).mappings().first()

        if result is None:
            db.rollback()

            raise HTTPException(
                status_code=404,
                detail="Moment not found or you cannot delete it",
            )

        db.execute(
            text("""
                INSERT INTO nex_moment_audit_logs
                (
                    moment_id,
                    user_id,
                    action,
                    old_data,
                    new_data,
                    action_at
                )
                VALUES (
                    :moment_id,
                    :user_id,
                    'moment_deleted',
                    CAST(:old_data AS jsonb),
                    CAST(:new_data AS jsonb),
                    NOW()
                )
            """),
            {
                "moment_id": moment_id,
                "user_id": user.user_id,
                "old_data": json.dumps({
                    "deleted_at": None,
                    "moderation_status": result["moderation_status"],
                }),
                "new_data": json.dumps({
                    "deleted_at": "NOW",
                    "action": "user_soft_delete",
                }),
            },
        )

        db.commit()

        return {
            "success": True,
            "message": "Moment deleted successfully",
            "moment_id": moment_id,
        }

    except HTTPException:
        raise

    except Exception:
        db.rollback()
        logger.exception("Moment deletion failed")

        raise HTTPException(
            status_code=500,
            detail="Moment deletion failed",
        )


# --------------------------------------------------
# UPDATE MOMENT
# --------------------------------------------------

@router.put("/{moment_id}")
async def update_moment(
    moment_id: int,
    payload: UpdateMomentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    validate_moment_audience(
        payload.audience,
        payload.moment_scope,
        payload.selected_user_ids,
    )

    try:
        result = db.execute(
            text("""
                UPDATE nex_moments
                SET
                    caption = :caption,
                    audience = :audience,
                    moment_scope = :moment_scope
                WHERE id = :moment_id
                  AND user_id = :user_id
                  AND deleted_at IS NULL
                  AND archived_at IS NULL
                  AND expires_at > NOW()
                  AND moderation_status = 'active'
                RETURNING
                    id,
                    user_id,
                    caption,
                    audience,
                    moment_scope,
                    created_at,
                    expires_at
            """),
            {
                "moment_id": moment_id,
                "user_id": user.user_id,
                "caption": payload.caption.strip(),
                "audience": payload.audience,
                "moment_scope": payload.moment_scope,
            },
        ).mappings().first()

        if result is None:
            db.rollback()

            raise HTTPException(
                status_code=404,
                detail="Moment not found or cannot be edited",
            )

        save_moment_audience(
            db,
            moment_id,
            payload.selected_user_ids,
        )

        db.commit()

        return {
            "success": True,
            "message": "Moment updated successfully",
            "moment": dict(result),
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        logger.exception("Moment update failed")

        raise HTTPException(
            status_code=500,
            detail="Moment update failed",
        )


# --------------------------------------------------
# MEDIA VALIDATION
# --------------------------------------------------

ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/webp": ("image", ".webp"),
    "image/gif": ("image", ".gif"),
    "video/mp4": ("video", ".mp4"),
    "video/webm": ("video", ".webm"),
}

MAX_MEDIA_SIZE = 50 * 1024 * 1024


def detect_media_type(data: bytes):
    """Check basic file signatures."""

    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"

    if (
        len(data) >= 12
        and data[0:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"

    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"

    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"

    return None


# --------------------------------------------------
# UPLOAD MEDIA MOMENT
# --------------------------------------------------

@router.post("/upload")
async def upload_moment(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    audience: Literal[
        "connections",
        "following",
        "followers",
        "selected_people",
        "only_me",
    ] = Form("connections"),
    moment_scope: Literal["normal"] = Form("normal"),
    selected_user_ids: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

    validate_moment_audience(
        audience,
        moment_scope,
        selected_user_ids,
    )

    storage_uploaded = False
    file_path = None

    try:
        if len(caption) > 2000:
            raise HTTPException(
                status_code=400,
                detail="Caption cannot exceed 2000 characters",
            )

        file_bytes = await file.read(MAX_MEDIA_SIZE + 1)

        if not file_bytes:
            raise HTTPException(
                status_code=400,
                detail="Empty file is not allowed",
            )

        if len(file_bytes) > MAX_MEDIA_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Maximum file size is 50 MB",
            )

        mime_type = detect_media_type(file_bytes)

        if mime_type is None or mime_type not in ALLOWED_MEDIA_TYPES:
            raise HTTPException(
                status_code=415,
                detail="Unsupported or invalid media file",
            )

        if file.content_type and file.content_type != mime_type:
            raise HTTPException(
                status_code=415,
                detail="File content type does not match actual file",
            )

        media_type, extension = ALLOWED_MEDIA_TYPES[mime_type]

        file_path = (
            f"{user.user_id}/"
            f"{uuid.uuid4().hex}{extension}"
        )

        upload_moment_media(
            file_bytes=file_bytes,
            file_path=file_path,
            content_type=mime_type,
        )

        storage_uploaded = True

        moment_result = db.execute(
            text("""
                INSERT INTO nex_moments
                (
                    user_id,
                    moment_type,
                    caption,
                    media_url,
                    audience,
                    moment_scope,
                    moderation_status,
                    created_at,
                    expires_at
                )
                VALUES (
                    :user_id,
                    :moment_type,
                    :caption,
                    :media_url,
                    :audience,
                    :moment_scope,
                    'active',
                    NOW(),
                    NOW() + INTERVAL '24 hours'
                )
                RETURNING id
            """),
            {
                "user_id": user.user_id,
                "moment_type": media_type,
                "caption": caption.strip(),
                "media_url": file_path,
                "audience": audience,
                "moment_scope": moment_scope,
            },
        ).mappings().first()

        if not moment_result:
            raise RuntimeError("Moment database insert failed")

        moment_id = moment_result["id"]

        db.execute(
            text("""
                INSERT INTO nex_moment_media
                (
                    moment_id,
                    uploader_user_id,
                    media_type,
                    storage_path,
                    original_file_name,
                    mime_type,
                    file_size_bytes
                )
                VALUES (
                    :moment_id,
                    :user_id,
                    :media_type,
                    :storage_path,
                    :original_file_name,
                    :mime_type,
                    :file_size
                )
            """),
            {
                "moment_id": moment_id,
                "user_id": user.user_id,
                "media_type": media_type,
                "storage_path": file_path,
                "original_file_name": Path(
                    (file.filename or "upload").replace("\\", "/")
                ).name[:255],
                "mime_type": mime_type,
                "file_size": len(file_bytes),
            },
        )

        save_moment_audience(
            db,
            moment_id,
            selected_user_ids,
        )

        db.commit()

        return {
            "success": True,
            "message": "Moment media uploaded successfully",
            "moment": {
                "id": moment_id,
                "user_id": user.user_id,
                "media_type": media_type,
                "storage_path": file_path,
                "mime_type": mime_type,
                "file_size_bytes": len(file_bytes),
            },
        }

    except HTTPException:
        db.rollback()

        if storage_uploaded and file_path:
            try:
                delete_moment_media(file_path)
            except Exception:
                logger.exception("Moment media storage cleanup failed")

        raise

    except Exception:
        db.rollback()
        logger.exception("Moment media upload failed")

        if storage_uploaded and file_path:
            try:
                delete_moment_media(file_path)
            except Exception:
                logger.exception("Moment media storage cleanup failed")

        raise HTTPException(
            status_code=500,
            detail="Moment media upload failed",
        )

    finally:
        await file.close()

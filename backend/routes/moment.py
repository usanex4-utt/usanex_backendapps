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


@router.post("/create")
async def create_moment(
    payload: CreateMomentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

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
                RETURNING id, user_id, caption, audience,
                          moment_scope, created_at, expires_at
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
                (moment_id, user_id, action, old_data, new_data, action_at)
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


@router.put("/{moment_id}")
async def update_moment(
    moment_id: int,
    payload: UpdateMomentRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

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
                RETURNING id, user_id, caption, audience,
                          moment_scope, created_at, expires_at
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

        db.commit()

        return {
            "success": True,
            "message": "Moment updated successfully",
            "moment": dict(result),
        }

    except HTTPException:
        raise

    except Exception:
        db.rollback()
        logger.exception("Moment update failed")
        raise HTTPException(
            status_code=500,
            detail="Moment update failed",
        )


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


@router.post("/upload")
async def upload_moment(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(""),
    audience: Literal["connections", "only_me"] = Form("connections"),
    moment_scope: Literal["normal"] = Form("normal"),
    db: Session = Depends(get_db),
):
    user = await get_authenticated_user(request, db)

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

        storage_uploaded = False

        try:
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

        except Exception:
            db.rollback()
            logger.exception("Moment media upload failed")

            if storage_uploaded:
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

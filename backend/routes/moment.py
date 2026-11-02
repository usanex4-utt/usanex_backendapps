import json
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timezone

from ..database.database import get_db
from .auth import get_current_user_from_request


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

from pydantic import BaseModel, Field
from typing import Literal


class CreateMomentRequest(BaseModel):
    caption: str = Field(min_length=1, max_length=2000)
    audience: Literal["connections", "only_me"] = "connections"
    moment_scope: Literal["normal", "couple"] = "normal"


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
                    AND (archived_at IS NULL)
                    AND (deleted_at IS NULL)
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
        raise HTTPException(
            status_code=500,
            detail="Moment deletion failed",
        )

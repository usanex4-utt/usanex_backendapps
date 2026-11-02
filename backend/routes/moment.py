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

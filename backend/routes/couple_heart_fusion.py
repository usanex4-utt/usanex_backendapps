
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.database import get_db
from backend.database.models import User
from backend.database.couple_models import (
    CoupleRoom,
    CoupleHeartFusion,
    utcnow,
)
from backend.routes.auth import get_current_user_from_request
from backend.services.access_control import can_chat


router = APIRouter(
    prefix="/api/couple/heart-fusion",
    tags=["Couple Heart Fusion"],
)


class CreateFusionRequest(BaseModel):
    partner_id: int = Field(gt=0)
    first_heart: dict


@router.post("/sessions")
def create_fusion_session(
    payload: CreateFusionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):
    user_id = current_user.id
    partner_id = payload.partner_id

    if user_id == partner_id:
        raise HTTPException(400, "You cannot start a fusion with yourself.")

    if not can_chat(db, user_id, partner_id):
        raise HTTPException(
            403,
            "Fusion is available only for connected partners.",
        )

    partner = db.query(User).filter(
        User.id == partner_id,
        User.is_active == True,
    ).first()

    if partner is None:
        raise HTTPException(404, "Partner not found.")

    room = db.query(CoupleRoom).filter(
        CoupleRoom.is_active == True,
        CoupleRoom.user_one_id == min(user_id, partner_id),
        CoupleRoom.user_two_id == max(user_id, partner_id),
    ).first()

    if room is None:
        raise HTTPException(404, "Couple room not found.")

    existing = db.query(CoupleHeartFusion).filter(
        CoupleHeartFusion.room_id == room.id,
        CoupleHeartFusion.status.in_(["waiting", "countdown", "fusing", "rating"]),
    ).first()

    if existing:
        if existing.created_by_user_id != user_id:
            raise HTTPException(
                409,
                "A fusion session is already active.",
            )
        return {
            "success": True,
            "message": "Existing fusion session restored.",
            "session": {
                "id": existing.id,
                "status": existing.status,
                "first_heart": existing.first_heart,
                "second_heart": existing.second_heart,
                "fusion_seed": existing.fusion_seed,
                "background_seed": existing.background_seed,
            },
        }

    session = CoupleHeartFusion(
        room_id=room.id,
        user_one_id=room.user_one_id,
        user_two_id=room.user_two_id,
        created_by_user_id=user_id,
        first_heart=payload.first_heart,
        second_heart=None,
        fusion_seed=secrets.token_urlsafe(32),
        background_seed=secrets.token_urlsafe(24),
        user_one_started=False,
        user_two_started=False,
        status="waiting",
        created_at=utcnow(),
        updated_at=utcnow(),
    )

    try:
        db.add(session)
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not create fusion session.")

    return {
        "success": True,
        "message": "Fusion session created. Waiting for partner heart.",
        "session": {
            "id": session.id,
            "status": session.status,
            "first_heart": session.first_heart,
            "second_heart": session.second_heart,
            "fusion_seed": session.fusion_seed,
            "background_seed": session.background_seed,
        },
    }
  


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

class SubmitSecondHeartRequest(BaseModel):
    second_heart: dict


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



@router.post("/sessions/{session_id}/heart")
def submit_second_heart(
    session_id: int,
    payload: SubmitSecondHeartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):
    user_id = current_user.id

    session = (
        db.query(CoupleHeartFusion)
        .filter(CoupleHeartFusion.id == session_id)
        .with_for_update()
        .first()
    )

    if session is None:
        raise HTTPException(404, "Fusion session not found.")

    if user_id not in (session.user_one_id, session.user_two_id):
        raise HTTPException(403, "You are not part of this fusion.")

    if user_id == session.created_by_user_id:
        raise HTTPException(
            403,
            "You already selected the first heart.",
        )

    if not can_chat(
        db,
        session.user_one_id,
        session.user_two_id,
    ):
        raise HTTPException(
            403,
            "Fusion requires connected partners.",
        )

    if session.second_heart is not None:
        return {
            "success": True,
            "message": "Second heart already submitted.",
            "session_id": session.id,
            "second_heart": session.second_heart,
            "status": session.status,
        }

    if session.status != "waiting":
        raise HTTPException(
            409,
            "This fusion session is no longer accepting hearts.",
        )

    session.second_heart = payload.second_heart
    session.updated_at = utcnow()

    try:
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        raise HTTPException(
            500,
            "Could not submit second heart.",
        )

    return {
        "success": True,
        "message": "Second heart submitted successfully.",
        "session_id": session.id,
        "first_heart": session.first_heart,
        "second_heart": session.second_heart,
        "fusion_seed": session.fusion_seed,
        "background_seed": session.background_seed,
        "status": session.status,
    }



@router.get("/sessions/{session_id}")
def get_fusion_session(
    session_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):
    user_id = current_user.id

    session = db.query(CoupleHeartFusion).filter(
        CoupleHeartFusion.id == session_id
    ).first()

    if session is None:
        raise HTTPException(
            404,
            "Fusion session not found.",
        )

    if user_id not in (
        session.user_one_id,
        session.user_two_id,
    ):
        raise HTTPException(
            403,
            "You are not part of this fusion.",
        )

    if not can_chat(
        db,
        session.user_one_id,
        session.user_two_id,
    ):
        raise HTTPException(
            403,
            "Fusion requires connected partners.",
        )

    return {
        "success": True,
        "session": {
            "id": session.id,
            "room_id": session.room_id,
            "created_by_user_id": session.created_by_user_id,
            "first_heart": session.first_heart,
            "second_heart": session.second_heart,
            "fusion_seed": session.fusion_seed,
            "background_seed": session.background_seed,
            "surprise_heart": session.surprise_heart,
            "user_one_started": session.user_one_started,
            "user_two_started": session.user_two_started,
            "user_one_started_at": session.user_one_started_at,
            "user_two_started_at": session.user_two_started_at,
            "countdown_started_at": session.countdown_started_at,
            "fusion_started_at": session.fusion_started_at,
            "completed_at": session.completed_at,
            "status": session.status,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        },
    }
  

from datetime import timezone
import secrets
import random
from fastapi import APIRouter, Depends, HTTPException,BackgroundTasks

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database.database import get_db
from backend.database.models import User
from backend.database.couple_models import (
    CoupleRoom,
    CoupleHeartFusion,
    CoupleHeartFusionRating,
    utcnow,
)
from backend.routes.auth import get_current_user_from_request
from backend.services.access_control import can_chat
def to_utc_iso(value):
  if value is None:
     return None
  return value.replace(tzinfo=timezone.utc).isoformat()
from backend.websocket.couple_manager import couple_manager


router = APIRouter(
    prefix="/api/couple/heart-fusion",
    tags=["Couple Heart Fusion"],
)


class CreateFusionRequest(BaseModel):
    partner_id: int = Field(gt=0)
    first_heart: dict

class SubmitSecondHeartRequest(BaseModel):
    second_heart: dict

class SubmitFusionRatingRequest(BaseModel):
    rating: int = Field(ge=1, le=3)


@router.post("/sessions")
def create_fusion_session(
    payload: CreateFusionRequest,
    background_tasks: BackgroundTasks,
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
        event = {
        "type": "heart_fusion_updated",
        "action": "session_created",
        "session_id": session.id,
        "status": session.status,
        "created_by_user_id": user_id,
    }

    background_tasks.add_task(
        couple_manager.send_to_user,
        user_id,
        event,
    )
    background_tasks.add_task(
        couple_manager.send_to_user,
        partner_id,
        event,
    )
        

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


@router.get("/rooms/{partner_id}/active-session")
def get_active_fusion_session(
    partner_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_from_request),
):
    user_id = current_user.id

    if user_id == partner_id:
        raise HTTPException(400, "Invalid partner.")

    if not can_chat(db, user_id, partner_id):
        raise HTTPException(403, "Fusion requires connected partners.")

    room = db.query(CoupleRoom).filter(
        CoupleRoom.is_active == True,
        CoupleRoom.user_one_id == min(user_id, partner_id),
        CoupleRoom.user_two_id == max(user_id, partner_id),
    ).first()

    if room is None:
        raise HTTPException(404, "Couple room not found.")

    session = (
        db.query(CoupleHeartFusion)
        .filter(
            CoupleHeartFusion.room_id == room.id,
            CoupleHeartFusion.status.in_(
                ["waiting", "countdown", "fusing", "rating"]
            ),
        )
        .order_by(CoupleHeartFusion.created_at.desc())
        .first()
    )

    return {
        "success": True,
        "session_id": session.id if session else None,
        "status": session.status if session else None,
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

    ratings = (
        db.query(CoupleHeartFusionRating)
        .filter(CoupleHeartFusionRating.fusion_id == session.id)
        .all()
    )

    user_one_rating = next(
        (r.rating for r in ratings if r.user_id == session.user_one_id),
        None,
    )
    user_two_rating = next(
        (r.rating for r in ratings if r.user_id == session.user_two_id),
        None,
    )

    return {
        "success": True,
        "server_now": to_utc_iso(utcnow()),
        "session": {
            "id": session.id,
            "room_id": session.room_id,
            "created_by_user_id": session.created_by_user_id,
            "user_one_id": session.user_one_id,
            "user_two_id": session.user_two_id,
            "first_heart": session.first_heart,
            "second_heart": session.second_heart,
            "fusion_seed": session.fusion_seed,
            "background_seed": session.background_seed,
            "surprise_heart": session.surprise_heart,
            "user_one_started": session.user_one_started,
            "user_two_started": session.user_two_started,
            "user_one_started_at": session.user_one_started_at,
            "user_two_started_at": session.user_two_started_at,
            "countdown_started_at": to_utc_iso(session.countdown_started_at),
            "fusion_started_at": to_utc_iso(session.fusion_started_at),
            "user_one_fusion_completed": session.user_one_fusion_completed,
            "user_two_fusion_completed": session.user_two_fusion_completed,
            "user_one_fusion_completed_at": session.user_one_fusion_completed_at,
            "user_two_fusion_completed_at": session.user_two_fusion_completed_at,
            "user_one_rating": user_one_rating,
            "user_two_rating": user_two_rating,
            "ratings_submitted": len(ratings),
            "both_rated": len(ratings) == 2,
            "completed_at": session.completed_at,
            "status": session.status,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        },
    }



@router.post("/sessions/{session_id}/start")
def start_fusion(
    session_id: int,
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

    if not can_chat(db, session.user_one_id, session.user_two_id):
        raise HTTPException(403, "Fusion requires connected partners.")

    if session.status != "waiting":
        raise HTTPException(409, "Fusion is not waiting for Start.")

    if not session.first_heart or not session.second_heart:
        raise HTTPException(409, "Both hearts must be selected first.")

    now = utcnow()

    if user_id == session.user_one_id:
        session.user_one_started = True
        if session.user_one_started_at is None:
            session.user_one_started_at = now
    else:
        session.user_two_started = True
        if session.user_two_started_at is None:
            session.user_two_started_at = now

    if session.user_one_started and session.user_two_started:
        session.status = "countdown"
        session.countdown_started_at = now

    session.updated_at = now

    try:
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not start fusion.")

    return {
        "success": True,
        "message": (
            "Both partners are ready. Countdown started."
            if session.status == "countdown"
            else "Your Start is saved. Waiting for partner."
        ),
        "session_id": session.id,
        "user_one_started": session.user_one_started,
        "user_two_started": session.user_two_started,
        "countdown_started_at": to_utc_iso(session.countdown_started_at),
        "status": session.status,
    }


@router.post("/sessions/{session_id}/fuse")
def create_surprise_fusion(
    session_id: int,
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

    if not can_chat(db, session.user_one_id, session.user_two_id):
        raise HTTPException(403, "Fusion requires connected partners.")

    if session.surprise_heart is not None:
        return {
            "success": True,
            "message": "Existing Surprise Crystal Heart restored.",
            "surprise_heart": session.surprise_heart,
            "status": session.status,
        }

    if session.status != "countdown":
        raise HTTPException(409, "Fusion countdown has not started.")

    now = utcnow()

    if session.countdown_started_at is None:
        raise HTTPException(409, "Countdown timestamp is missing.")

    elapsed = (now - session.countdown_started_at).total_seconds()

    if elapsed < 3:
        raise HTTPException(
            409,
            "Please wait for the 3-second countdown to finish.",
        )

    styles = [
        {
            "name": "Ruby Galaxy",
            "colors": ["#ff1744", "#ff5c8a", "#8e0038"],
            "glow": "#ff1744",
        },
        {
            "name": "Ocean Diamond",
            "colors": ["#00e5ff", "#2979ff", "#120078"],
            "glow": "#00e5ff",
        },
        {
            "name": "Golden Eternity",
            "colors": ["#fff176", "#ffb300", "#ff6f00"],
            "glow": "#ffd54f",
        },
        {
            "name": "Purple Dream",
            "colors": ["#ea80fc", "#9c27b0", "#4a148c"],
            "glow": "#ce93d8",
        },
        {
            "name": "Emerald Love",
            "colors": ["#69f0ae", "#00c853", "#00695c"],
            "glow": "#69f0ae",
        },
        {
            "name": "Rose Crystal",
            "colors": ["#ff80ab", "#f50057", "#880e4f"],
            "glow": "#ff80ab",
        },
        {
            "name": "Ice Diamond",
            "colors": ["#e1f5fe", "#4fc3f7", "#0277bd"],
            "glow": "#80d8ff",
        },
        {
            "name": "Rainbow Soul",
            "colors": ["#ff5252", "#7c4dff", "#00e5ff"],
            "glow": "#ff80ab",
        },
    ]

    patterns = [
        "sparkle", "crystal", "galaxy",
        "diamond", "stardust", "aurora",
    ]

    shapes = [
        "classic", "royal", "double",
        "infinity", "crystal",
    ]

    rng = random.Random(session.fusion_seed)
    style = rng.choice(styles)

    surprise_heart = {
        "id": f"surprise-heart-{session.id}",
        "seed": session.fusion_seed,
        "name": style["name"],
        "colors": style["colors"],
        "glow": style["glow"],
        "pattern": rng.choice(patterns),
        "shape": rng.choice(shapes),
        "sparkleCount": 12 + rng.randrange(25),
        "particleCount": 20 + rng.randrange(31),
        "rotation": rng.randrange(31) - 15,
        "createdAt": now.isoformat(),
    }

    session.surprise_heart = surprise_heart
    session.fusion_started_at = now
    session.status = "fusing"
    session.updated_at = now

    try:
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not create Surprise Crystal Heart.")

    return {
        "success": True,
        "message": "Surprise Crystal Heart created successfully.",
        "surprise_heart": session.surprise_heart,
        "status": session.status,
    }



@router.post("/sessions/{session_id}/complete")
def complete_fusion_animation(
    session_id: int,
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

    if not can_chat(db, session.user_one_id, session.user_two_id):
        raise HTTPException(403, "Fusion requires connected partners.")

    if session.surprise_heart is None:
        raise HTTPException(409, "Surprise Crystal Heart is not ready.")

    if session.status not in ("fusing", "rating"):
        raise HTTPException(409, "Fusion animation is not active.")

    now = utcnow()

    if user_id == session.user_one_id:
        session.user_one_fusion_completed = True
        if session.user_one_fusion_completed_at is None:
            session.user_one_fusion_completed_at = now
    else:
        session.user_two_fusion_completed = True
        if session.user_two_fusion_completed_at is None:
            session.user_two_fusion_completed_at = now

    if (
        session.user_one_fusion_completed
        and session.user_two_fusion_completed
    ):
        session.status = "rating"

    session.updated_at = now

    try:
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not save fusion completion.")
        
    

    return {
        "success": True,
        "session_id": session.id,
        "user_one_fusion_completed": session.user_one_fusion_completed,
        "user_two_fusion_completed": session.user_two_fusion_completed,
        "both_completed": (
            session.user_one_fusion_completed
            and session.user_two_fusion_completed
        ),
        "status": session.status,
    }




@router.post("/sessions/{session_id}/rating")
def submit_fusion_rating(
    session_id: int,
    payload: SubmitFusionRatingRequest,
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

    if not can_chat(db, session.user_one_id, session.user_two_id):
        raise HTTPException(403, "Fusion requires connected partners.")

    if session.status not in ("rating", "completed"):
        raise HTTPException(409, "Both partners must complete the fusion first.")

    existing_rating = (
        db.query(CoupleHeartFusionRating)
        .filter(
            CoupleHeartFusionRating.fusion_id == session.id,
            CoupleHeartFusionRating.user_id == user_id,
        )
        .first()
    )

    if existing_rating is not None:
        raise HTTPException(409, "You have already submitted your rating.")

    rating = CoupleHeartFusionRating(
        fusion_id=session.id,
        room_id=session.room_id,
        user_id=user_id,
        rating=payload.rating,
    )

    db.add(rating)

    try:
        db.flush()

        rating_count = (
            db.query(CoupleHeartFusionRating)
            .filter(CoupleHeartFusionRating.fusion_id == session.id)
            .count()
        )

        if rating_count >= 2:
            session.status = "completed"
            session.completed_at = utcnow()

        session.updated_at = utcnow()
        db.commit()
        db.refresh(session)

    except Exception:
        db.rollback()
        raise HTTPException(500, "Could not save your rating.")

    return {
        "success": True,
        "message": "Your rating has been saved.",
        "session_id": session.id,
        "your_rating": payload.rating,
        "ratings_submitted": rating_count,
        "both_rated": rating_count >= 2,
        "status": session.status,
    }
    
    

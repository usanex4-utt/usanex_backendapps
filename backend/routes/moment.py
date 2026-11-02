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

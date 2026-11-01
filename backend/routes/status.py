from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from datetime import datetime, timedelta
from typing import Optional
import uuid

# Cloudinary import from reels
from .reels import cloudinary_ready, delete_cloudinary_video
import cloudinary.uploader

from ..database.database import get_db
from ..database.models import User, UserStatus, StatusView, UserConnection, UserConnectionCategory
from .auth import get_current_user_from_request

router = APIRouter(prefix="/api/status", tags=["Status"])

# --- UPLOAD STATUS ---
@router.post("/upload")
async def upload_status(
    request: Request,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    user = await get_current_user_from_request(request, db)
    if not user: raise HTTPException(status_code=401, detail="Auth required")

    if not cloudinary_ready():
        raise HTTPException(status_code=503, detail="Cloudinary not configured")

    # Upload to Cloudinary
    try:
        content = await file.read()
        res = cloudinary.uploader.upload(
            content,
            folder="usanex/statuses",
            resource_type="auto",
            public_id=f"status_{uuid.uuid4().hex}"
        )
        media_url = res.get("secure_url")
        media_type = "video" if res.get("resource_type") == "video" else "image"
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    # 24 hours expiry
    expires = datetime.utcnow() + timedelta(hours=24)

    new_status = UserStatus(
        user_id=user.id,
        media_url=media_url,
        media_type=media_type,
        caption=caption,
        expires_at=expires
    )
    db.add(new_status)
    db.commit()
    db.refresh(new_status)

    return {"success": True, "status_id": new_status.id, "media_url": media_url}

# --- FETCH FEED ---
@router.get("/feed")
async def get_status_feed(
    request: Request,
    db: Session = Depends(get_db)
):
    user = await get_current_user_from_request(request, db)
    if not user: raise HTTPException(status_code=401, detail="Auth required")

    now = datetime.utcnow()

    # 1. Mera Status
    my_statuses = db.query(UserStatus).filter(
        UserStatus.user_id == user.id,
        UserStatus.expires_at > now
    ).order_by(UserStatus.created_at.desc()).all()

    # 2. Connections fetch karo
    connections = db.query(UserConnection).filter(
        UserConnection.status == "connected",
        or_(UserConnection.user_one_id == user.id, UserConnection.user_two_id == user.id)
    ).all()

    conn_ids = []
    for c in connections:
        conn_ids.append(c.user_two_id if c.user_one_id == user.id else c.user_one_id)

    # 3. Connections ke statuses fetch karo
    friend_statuses = db.query(UserStatus).filter(
        UserStatus.user_id.in_(conn_ids),
        UserStatus.expires_at > now
    ).order_by(UserStatus.created_at.desc()).all()

    # 4. Group by User
    grouped = {}
    for s in friend_statuses:
        if s.user_id not in grouped:
            creator = db.query(User).filter(User.id == s.user_id).first()
            if not creator: continue
            
            # Check if viewed
            viewed = db.query(StatusView).filter(
                StatusView.status_id == s.id, 
                StatusView.viewer_id == user.id
            ).first() is not None
            
            grouped[s.user_id] = {
                "user_id": creator.user_id,
                "username": creator.username,
                "profile_photo": creator.profile_photo,
                "has_unviewed": not viewed,
                "statuses": []
            }
        grouped[s.user_id]["statuses"].append({
            "id": s.id,
            "media_url": s.media_url,
            "media_type": s.media_type,
            "caption": s.caption,
            "created_at": s.created_at.isoformat()
        })

    # Mera status format karo
    my_formatted = []
    if my_statuses:
        my_formatted = [{
            "id": s.id, "media_url": s.media_url, "media_type": s.media_type,
            "caption": s.caption, "created_at": s.created_at.isoformat()
        } for s in my_statuses]

    return {
        "success": True,
        "my_status": {
            "user_id": user.user_id,
            "username": user.username,
            "profile_photo": user.profile_photo,
            "statuses": my_formatted
        },
        "friends": list(grouped.values())
    }

# --- MARK VIEWED ---
@router.post("/view/{status_id}")
async def mark_viewed(
    status_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    user = await get_current_user_from_request(request, db)
    if not user: raise HTTPException(status_code=401)

    existing = db.query(StatusView).filter(
        StatusView.status_id == status_id,
        StatusView.viewer_id == user.id
    ).first()

    if not existing:
        db.add(StatusView(status_id=status_id, viewer_id=user.id))
        db.commit()

    return {"success": True}
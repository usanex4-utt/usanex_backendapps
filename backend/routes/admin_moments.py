
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from ..database.database import get_db
from .auth import get_current_user_from_request


router = APIRouter(
    prefix="/api/admin/moments",
    tags=["Admin Nex Moments"],
)


class ModerationRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


def require_admin(user=Depends(get_current_user_from_request)):
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")

    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")

    return user


@router.get("/moderation-list")
def moderation_list(
    status: str = Query(default="all", pattern="^(all|hidden|removed)$"),
    limit: int = Query(default=50, ge=1, le=100),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    conditions = ["moderation_status IN ('hidden', 'removed')"]
    params = {"limit": limit}

    if status != "all":
        conditions.append("moderation_status = :status")
        params["status"] = status

    query = text(f"""
        SELECT id, user_id, caption, moment_type, audience,
               moderation_status, admin_moderation_reason,
               admin_hidden_at, admin_hidden_by, created_at, expires_at
        FROM nex_moments
        WHERE {" AND ".join(conditions)}
        ORDER BY admin_hidden_at DESC NULLS LAST, created_at DESC
        LIMIT :limit
    """)

    rows = db.execute(query, params).mappings().all()
    return {"items": [dict(row) for row in rows]}


def moderate_moment(
    moment_id: int,
    action: str,
    reason: str,
    admin_user_id: str,
    db: Session,
):
    if action not in ("hidden", "removed"):
        raise HTTPException(status_code=400, detail="Invalid moderation action")

    try:
        row = db.execute(
            text("""
                SELECT id, moderation_status
                FROM nex_moments
                WHERE id = :moment_id
                FOR UPDATE
            """),
            {"moment_id": moment_id},
        ).mappings().first()

        if row is None:
            raise HTTPException(status_code=404, detail="Moment not found")

        old_status = row["moderation_status"]

        if old_status == "removed":
            raise HTTPException(status_code=409, detail="Moment already removed")

        if action == "hidden" and old_status == "hidden":
            raise HTTPException(status_code=409, detail="Moment already hidden")

        db.execute(
            text("""
                UPDATE nex_moments
                SET moderation_status = :status,
                    admin_hidden_at = NOW(),
                    admin_hidden_by = :admin_id,
                    admin_moderation_reason = :reason
                WHERE id = :moment_id
            """),
            {
                "status": action,
                "admin_id": admin_user_id,
                "reason": reason,
                "moment_id": moment_id,
            },
        )

        db.execute(
            text("""
                INSERT INTO nex_moment_audit_logs
                (moment_id, admin_user_id, action, old_status, new_status, reason, created_at)
                VALUES
                (:moment_id, :admin_id, :action, :old_status, :new_status, :reason, NOW())
            """),
            {
                "moment_id": moment_id,
                "admin_id": admin_user_id,
                "action": "moment_" + action,
                "old_status": old_status,
                "new_status": action,
                "reason": reason,
            },
        )

        db.commit()
        return {
            "success": True,
            "moment_id": moment_id,
            "moderation_status": action,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Moderation operation failed")


@router.post("/{moment_id}/hide")
def hide_moment(
    moment_id: int,
    payload: ModerationRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return moderate_moment(
        moment_id, "hidden", payload.reason, admin.user_id, db
    )


@router.post("/{moment_id}/remove")
def remove_moment(
    moment_id: int,
    payload: ModerationRequest,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    return moderate_moment(
        moment_id, "removed", payload.reason, admin.user_id, db
    )


@router.post("/{moment_id}/unhide")
def unhide_moment(
    moment_id: int,
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        result = db.execute(
            text("""
                UPDATE nex_moments
                SET moderation_status = 'active',
                    admin_hidden_at = NULL,
                    admin_hidden_by = NULL,
                    admin_moderation_reason = NULL
                WHERE id = :moment_id
                  AND moderation_status = 'hidden'
                RETURNING id
            """),
            {"moment_id": moment_id},
        ).first()

        if result is None:
            db.rollback()
            raise HTTPException(
                status_code=404,
                detail="Hidden Moment not found",
            )

        db.execute(
            text("""
                INSERT INTO nex_moment_audit_logs
                (moment_id, admin_user_id, action, old_status, new_status, reason, created_at)
                VALUES
                (:moment_id, :admin_id, 'moment_unhidden', 'hidden', 'active',
                 'Admin unhidden Moment', NOW())
            """),
            {"moment_id": moment_id, "admin_id": admin.user_id},
        )

        db.commit()
        return {"success": True, "moment_id": moment_id, "moderation_status": "active"}

    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unhide operation failed")


@router.get("/audit-logs")
def audit_logs(
    limit: int = Query(default=50, ge=1, le=100),
    moment_id: int | None = Query(default=None, ge=1),
    admin=Depends(require_admin),
    db: Session = Depends(get_db),
):
    params = {"limit": limit}
    condition = ""

    if moment_id is not None:
        condition = "WHERE moment_id = :moment_id"
        params["moment_id"] = moment_id

    rows = db.execute(
        text(f"""
            SELECT id, moment_id, admin_user_id, action,
                   old_status, new_status, reason, created_at
            FROM nex_moment_audit_logs
            {condition}
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        params,
    ).mappings().all()

    return {"items": [dict(row) for row in rows]}
            

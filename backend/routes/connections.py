from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database.database import get_db
from ..database.models import (
    User,
    UserFollow,
    ConnectionRequest,
    ConnectionVerification,
    ConnectionNotification,
    UserConnection,
    UserConnectionCategory,
)
from .auth import get_current_user_from_request


router = APIRouter(
    prefix="/api/connections",
    tags=["Connections"],
)


# ============================================================
# CONSTANTS
# ============================================================

VALID_PERSONAL_CATEGORIES = {
    "friend",
    "family",
}


# ============================================================
# REQUEST MODELS
# ============================================================

class ConnectionRequestBody(BaseModel):
    user_id: str


class FollowBody(BaseModel):
    user_id: str


class LegacyFollowBody(BaseModel):
    requester_id: Optional[str] = None
    target_user_id: Optional[str] = None
    requester_user_id: Optional[str] = None
    target_id: Optional[str] = None


class ActionBody(BaseModel):
    notification_id: int
    user_id: Optional[str] = None


class NotificationReadBody(BaseModel):
    notification_id: int
    user_id: Optional[str] = None


class PersonalCategoryBody(BaseModel):
    connected_user_id: str
    category: str


# ============================================================
# AUTHENTICATION HELPER
# ============================================================

def get_authenticated_user(
    request: Request,
    db: Session,
):
    user = get_current_user_from_request(
        request=request,
        db=db,
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    return user


# ============================================================
# CATEGORY HELPER
# ============================================================

def normalize_personal_category(
    category: str,
) -> str:

    if not category:
        raise HTTPException(
            status_code=400,
            detail="Category is required",
        )

    value = category.strip().lower()

    aliases = {
        "friend": "friend",
        "friends": "friend",
        "family": "family",
    }

    normalized = aliases.get(value)

    if normalized is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid personal category. "
                "Allowed categories: friend, family"
            ),
        )

    return normalized


# ============================================================
# CONNECTION HELPER
# ============================================================

def get_pair_connection(
    db: Session,
    user_a_id: int,
    user_b_id: int,
):
    return (
        db.query(UserConnection)
        .filter(
            UserConnection.status == "connected",
            or_(
                (
                    UserConnection.user_one_id == user_a_id
                )
                & (
                    UserConnection.user_two_id == user_b_id
                ),
                (
                    UserConnection.user_one_id == user_b_id
                )
                & (
                    UserConnection.user_two_id == user_a_id
                ),
            ),
        )
        .order_by(
            UserConnection.id.desc()
        )
        .first()
    )


# ============================================================
# REQUEST HELPER
# ============================================================

def get_latest_request(
    db: Session,
    sender_id: int,
    receiver_id: int,
):
    return (
        db.query(ConnectionRequest)
        .filter(
            ConnectionRequest.sender_id == sender_id,
            ConnectionRequest.receiver_id == receiver_id,
        )
        .order_by(
            ConnectionRequest.id.desc()
        )
        .first()
    )


# ============================================================
# NOTIFICATION HELPER
# ============================================================

def get_latest_notification_for_request(
    db: Session,
    request_id: int,
):
    return (
        db.query(ConnectionNotification)
        .filter(
            ConnectionNotification.connection_request_id
            == request_id
        )
        .order_by(
            ConnectionNotification.id.desc()
        )
        .first()
    )


# ============================================================
# PERSONAL CATEGORY HELPER
# ============================================================

def get_personal_category(
    db: Session,
    user_id: int,
    connected_user_id: int,
):
    return (
        db.query(UserConnectionCategory)
        .filter(
            UserConnectionCategory.user_id == user_id,
            UserConnectionCategory.connected_user_id
            == connected_user_id,
        )
        .first()
    )


# ============================================================
# FOLLOW HELPER
# ============================================================

def get_follow(
    db: Session,
    follower_id: int,
    following_id: int,
):
    return (
        db.query(UserFollow)
        .filter(
            UserFollow.follower_id == follower_id,
            UserFollow.following_id == following_id,
        )
        .first()
    )


# ============================================================
# FOLLOW USER
# ============================================================

@router.post("/follow")
def follow_user(
    body: FollowBody,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    target_user = (
        db.query(User)
        .filter(
            User.user_id == body.user_id.strip()
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot follow yourself",
        )

    existing_follow = get_follow(
        db,
        current_user.id,
        target_user.id,
    )

    if existing_follow:

        return {
            "success": True,
            "status": "following",
            "message": "Already following",
            "user_id": target_user.user_id,
        }

    follow = UserFollow(
        follower_id=current_user.id,
        following_id=target_user.id,
        created_at=datetime.utcnow(),
    )

    db.add(follow)
    db.commit()
    db.refresh(follow)

    return {
        "success": True,
        "status": "following",
        "message": "User followed successfully",
        "user_id": target_user.user_id,
    }


# ============================================================
# UNFOLLOW USER
# ============================================================

@router.post("/unfollow")
def unfollow_user(
    body: FollowBody,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    target_user = (
        db.query(User)
        .filter(
            User.user_id == body.user_id.strip()
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot unfollow yourself",
        )

    existing_follow = get_follow(
        db,
        current_user.id,
        target_user.id,
    )

    if existing_follow:

        db.delete(existing_follow)
        db.commit()

    return {
        "success": True,
        "status": "none",
        "message": "User unfollowed successfully",
        "user_id": target_user.user_id,
    }


# ============================================================
# FOLLOW STATUS
# ============================================================

@router.get("/follow-status/{user_id}")
def get_follow_status(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    target_user = (
        db.query(User)
        .filter(
            User.user_id == user_id.strip()
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if target_user.id == current_user.id:
        return {
            "success": True,
            "is_following": False,
            "is_self": True,
        }

    existing_follow = get_follow(
        db,
        current_user.id,
        target_user.id,
    )

    return {
        "success": True,
        "is_following": existing_follow is not None,
        "is_self": False,
    }


# ============================================================
# FOLLOW COUNTS
# ============================================================

@router.get("/follow-counts/{user_id}")
def get_follow_counts(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    get_authenticated_user(
        request,
        db,
    )

    target_user = (
        db.query(User)
        .filter(
            User.user_id == user_id.strip()
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    followers = (
        db.query(UserFollow)
        .filter(
            UserFollow.following_id == target_user.id
        )
        .count()
    )

    following = (
        db.query(UserFollow)
        .filter(
            UserFollow.follower_id == target_user.id
        )
        .count()
    )

    return {
        "success": True,
        "followers": followers,
        "following": following,
    }


# ============================================================
# SEND CONNECTION REQUEST
# ============================================================

@router.post("/request")
def send_connection_request(
    body: ConnectionRequestBody,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    target_user = (
        db.query(User)
        .filter(
            User.user_id == body.user_id.strip()
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot connect with yourself",
        )

    existing_connection = get_pair_connection(
        db,
        current_user.id,
        target_user.id,
    )

    if existing_connection:

        personal_category = get_personal_category(
            db,
            current_user.id,
            target_user.id,
        )

        return {
            "success": True,
            "status": "connected",
            "message": "Already connected",
            "category": (
                personal_category.category
                if personal_category
                else None
            ),
        }

    outgoing = get_latest_request(
        db,
        current_user.id,
        target_user.id,
    )

    incoming = get_latest_request(
        db,
        target_user.id,
        current_user.id,
    )

    if outgoing and outgoing.status == "pending":

        return {
            "success": True,
            "status": "pending_sent",
            "message": "Connection request already sent",
            "request_id": outgoing.id,
        }

    if incoming and incoming.status == "pending":

        return {
            "success": True,
            "status": "pending_received",
            "message": (
                "This user has already sent you a request"
            ),
            "request_id": incoming.id,
        }

    now = datetime.utcnow()

    if outgoing and outgoing.status == "rejected":

        outgoing.status = "pending"
        outgoing.updated_at = now

        request_row = outgoing

    else:

        request_row = ConnectionRequest(
            sender_id=current_user.id,
            receiver_id=target_user.id,
            status="pending",
            created_at=now,
            updated_at=now,
        )

        db.add(request_row)
        db.flush()

    notification = ConnectionNotification(
        receiver_id=target_user.id,
        sender_id=current_user.id,
        connection_request_id=request_row.id,
        verification_id=None,
        notification_type="connection_request",
        encrypted_code=None,
        is_read=0,
        created_at=now,
    )

    db.add(notification)

    db.commit()

    return {
        "success": True,
        "status": "pending_sent",
        "message": "Connection request sent",
        "request_id": request_row.id,
        "notification_id": notification.id,
    }


# ============================================================
# LEGACY CONNECTION FOLLOW ROUTE
# ============================================================

@router.post("/follow-request")
def follow_request_compatibility(
    body: LegacyFollowBody,
    request: Request,
    db: Session = Depends(get_db),
):
    target_id = (
        body.target_user_id
        or body.target_id
    )

    if not target_id:
        raise HTTPException(
            status_code=400,
            detail="Target user ID is required",
        )

    return send_connection_request(
        ConnectionRequestBody(
            user_id=target_id
        ),
        request,
        db,
    )


# ============================================================
# ACCEPT REQUEST
# ============================================================

@router.post("/request/{request_id}/accept")
def accept_connection_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    connection_request = (
        db.query(ConnectionRequest)
        .filter(
            ConnectionRequest.id == request_id
        )
        .first()
    )

    if connection_request is None:
        raise HTTPException(
            status_code=404,
            detail="Connection request not found",
        )

    if connection_request.receiver_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot accept this request",
        )

    if connection_request.status == "accepted":

        existing_connection = get_pair_connection(
            db,
            connection_request.sender_id,
            connection_request.receiver_id,
        )

        if existing_connection:

            return {
                "success": True,
                "status": "connected",
                "message": "Connection already accepted",
                "connection": True,
            }

        raise HTTPException(
            status_code=400,
            detail=(
                "Request was accepted but "
                "connection was not found"
            ),
        )

    if connection_request.status != "pending":
        raise HTTPException(
            status_code=400,
            detail="This request is no longer pending",
        )

    now = datetime.utcnow()

    connection_request.status = "accepted"
    connection_request.updated_at = now

    existing_connection = get_pair_connection(
        db,
        connection_request.sender_id,
        connection_request.receiver_id,
    )

    if existing_connection is None:

        user_connection = UserConnection(
            user_one_id=connection_request.sender_id,
            user_two_id=connection_request.receiver_id,
            status="connected",
            created_at=now,
            updated_at=now,
        )

        db.add(user_connection)

    else:

        existing_connection.updated_at = now

    original_notification = (
        get_latest_notification_for_request(
            db,
            connection_request.id,
        )
    )

    if original_notification:
        original_notification.is_read = 1

    accepted_notification = ConnectionNotification(
        receiver_id=connection_request.sender_id,
        sender_id=current_user.id,
        connection_request_id=connection_request.id,
        verification_id=None,
        notification_type="connection_accepted",
        encrypted_code=None,
        is_read=0,
        created_at=now,
    )

    db.add(accepted_notification)

    db.commit()

    return {
        "success": True,
        "status": "connected",
        "message": "Connection accepted",
        "connection": True,
    }


# ============================================================
# REJECT REQUEST
# ============================================================

@router.post("/request/{request_id}/reject")
def reject_connection_request(
    request_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    connection_request = (
        db.query(ConnectionRequest)
        .filter(
            ConnectionRequest.id == request_id
        )
        .first()
    )

    if connection_request is None:
        raise HTTPException(
            status_code=404,
            detail="Connection request not found",
        )

    if connection_request.receiver_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot reject this request",
        )

    if connection_request.status != "pending":

        return {
            "success": True,
            "status": connection_request.status,
            "message": "Request is no longer pending",
        }

    now = datetime.utcnow()

    connection_request.status = "rejected"
    connection_request.updated_at = now

    original_notification = (
        get_latest_notification_for_request(
            db,
            connection_request.id,
        )
    )

    if original_notification:
        original_notification.is_read = 1

    rejected_notification = ConnectionNotification(
        receiver_id=connection_request.sender_id,
        sender_id=current_user.id,
        connection_request_id=connection_request.id,
        verification_id=None,
        notification_type="connection_rejected",
        encrypted_code=None,
        is_read=0,
        created_at=now,
    )

    db.add(rejected_notification)

    db.commit()

    return {
        "success": True,
        "status": "rejected",
        "message": "Connection request rejected",
    }


# ============================================================
# LEGACY ACCEPT
# ============================================================

@router.post("/accept")
def accept_compatibility(
    body: ActionBody,
    request: Request,
    db: Session = Depends(get_db),
):
    return accept_connection_request(
        body.notification_id,
        request,
        db,
    )


# ============================================================
# LEGACY REJECT
# ============================================================

@router.post("/reject")
def reject_compatibility(
    body: ActionBody,
    request: Request,
    db: Session = Depends(get_db),
):
    return reject_connection_request(
        body.notification_id,
        request,
        db,
    )


# ============================================================
# CONNECTION REQUESTS
# ============================================================

@router.get("/requests")
def get_connection_requests(
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    rows = (
        db.query(ConnectionRequest)
        .filter(
            ConnectionRequest.receiver_id
            == current_user.id,
            ConnectionRequest.status == "pending",
        )
        .order_by(
            ConnectionRequest.id.desc()
        )
        .all()
    )

    result = []

    for row in rows:

        sender = (
            db.query(User)
            .filter(
                User.id == row.sender_id
            )
            .first()
        )

        if sender is None:
            continue

        result.append({
            "id": row.id,
            "request_id": row.id,
            "sender_id": sender.id,
            "sender_user_id": sender.user_id,
            "sender_username": sender.username,
            "sender_name": sender.name,
            "sender_profile_photo": sender.profile_photo,
            "receiver_user_id": current_user.user_id,
            "status": row.status,
            "created_at": (
                row.created_at.isoformat()
                if row.created_at
                else None
            ),
        })

    return {
        "success": True,
        "requests": result,
    }


# ============================================================
# NOTIFICATIONS
# ============================================================

@router.get("/notifications")
def get_connection_notifications(
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    rows = (
        db.query(ConnectionNotification)
        .filter(
            ConnectionNotification.receiver_id
            == current_user.id,
        )
        .order_by(
            ConnectionNotification.id.desc()
        )
        .limit(100)
        .all()
    )

    result = []

    for row in rows:

        sender = (
            db.query(User)
            .filter(
                User.id == row.sender_id
            )
            .first()
        )

        if sender is None:
            continue

        request_row = (
            db.query(ConnectionRequest)
            .filter(
                ConnectionRequest.id
                == row.connection_request_id
            )
            .first()
        )

        if row.notification_type == "connection_request":

            message = (
                f"Request sent by @{sender.username} "
                f"({sender.user_id})"
            )

        elif row.notification_type == "connection_accepted":

            message = (
                f"@{sender.username} accepted your "
                f"connection request."
            )

        elif row.notification_type == "connection_rejected":

            message = (
                f"@{sender.username} rejected your "
                f"connection request."
            )

        else:

            message = "You have a new notification."

        result.append({
            "id": row.id,
            "notification_id": row.id,
            "type": row.notification_type,
            "notification_type": row.notification_type,

            "sender_id": sender.id,
            "sender_user_id": sender.user_id,
            "sender_username": sender.username,
            "sender_name": sender.name,

            "sender_profile_picture": sender.profile_photo,

            "message": message,

            "connection_request_id": (
                request_row.id
                if request_row
                else row.connection_request_id
            ),

            "request_id": (
                request_row.id
                if request_row
                else row.connection_request_id
            ),

            "is_read": bool(row.is_read),

            "created_at": (
                row.created_at.isoformat()
                if row.created_at
                else None
            ),
        })

    return {
        "success": True,
        "notifications": result,
    }


# ============================================================
# LEGACY NOTIFICATIONS
# ============================================================

@router.get("/all")
def get_all_notifications(
    request: Request,
    db: Session = Depends(get_db),
):
    return get_connection_notifications(
        request,
        db,
    )


# ============================================================
# MARK NOTIFICATION READ
# ============================================================

@router.post("/notifications/read")
def mark_notification_read(
    body: NotificationReadBody,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    notification = (
        db.query(ConnectionNotification)
        .filter(
            ConnectionNotification.id
            == body.notification_id,
            ConnectionNotification.receiver_id
            == current_user.id,
        )
        .first()
    )

    if notification is None:
        raise HTTPException(
            status_code=404,
            detail="Notification not found",
        )

    notification.is_read = 1

    db.commit()

    return {
        "success": True,
        "message": "Notification marked as read",
    }


# ============================================================
# LEGACY READ
# ============================================================

@router.post("/read")
def mark_read_compatibility(
    body: NotificationReadBody,
    request: Request,
    db: Session = Depends(get_db),
):
    return mark_notification_read(
        body,
        request,
        db,
    )


# ============================================================
# PERSONAL CATEGORY
# ============================================================

@router.post("/category")
def set_personal_category(
    body: PersonalCategoryBody,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    connected_user = (
        db.query(User)
        .filter(
            User.user_id == body.connected_user_id.strip()
        )
        .first()
    )

    if connected_user is None:
        raise HTTPException(
            status_code=404,
            detail="Connected user not found",
        )

    if connected_user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot categorize yourself",
        )

    connection = get_pair_connection(
        db,
        current_user.id,
        connected_user.id,
    )

    if connection is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "You can categorize only a connected user"
            ),
        )

    category = normalize_personal_category(
        body.category
    )

    now = datetime.utcnow()

    existing_category = get_personal_category(
        db,
        current_user.id,
        connected_user.id,
    )

    if existing_category:

        existing_category.category = category
        existing_category.updated_at = now

    else:

        existing_category = UserConnectionCategory(
            user_id=current_user.id,
            connected_user_id=connected_user.id,
            category=category,
            created_at=now,
            updated_at=now,
        )

        db.add(existing_category)

    db.commit()

    return {
        "success": True,
        "message": "Personal category updated",
        "connected_user_id": connected_user.user_id,
        "category": category,
    }


# ============================================================
# REMOVE PERSONAL CATEGORY
# ============================================================

@router.delete("/category/{connected_user_id}")
def remove_personal_category(
    connected_user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    connected_user = (
        db.query(User)
        .filter(
            User.user_id == connected_user_id.strip()
        )
        .first()
    )

    if connected_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    existing_category = get_personal_category(
        db,
        current_user.id,
        connected_user.id,
    )

    if existing_category:

        db.delete(existing_category)
        db.commit()

    return {
        "success": True,
        "message": "Personal category removed",
        "connected_user_id": connected_user.user_id,
        "category": None,
    }


# ============================================================
# HOME CONNECTIONS
# ============================================================

@router.get("")
def get_connections(
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = get_authenticated_user(
        request,
        db,
    )

    rows = (
        db.query(UserConnection)
        .filter(
            UserConnection.status == "connected",
            or_(
                UserConnection.user_one_id
                == current_user.id,
                UserConnection.user_two_id
                == current_user.id,
            ),
        )
        .order_by(
            UserConnection.id.desc()
        )
        .all()
    )

    users = []
    seen = set()

    for row in rows:

        if row.user_one_id == current_user.id:
            other_id = row.user_two_id
        else:
            other_id = row.user_one_id

        if other_id in seen:
            continue

        seen.add(other_id)

        user = (
            db.query(User)
            .filter(
                User.id == other_id
            )
            .first()
        )

        if user is None:
            continue

        personal_category = get_personal_category(
            db,
            current_user.id,
            user.id,
        )

        category = (
            personal_category.category
            if personal_category
            else None
        )

        users.append({
            "user_id": user.user_id,
            "username": user.username,
            "name": user.name,
            "profile_photo": user.profile_photo,

            "category": category,
            "connection_category": category,
            "connection_type": category,

            "connection_status": "connected",
            "is_connected": True,
        })

    return {
        "success": True,
        "users": users,
        "connections": users,
    }

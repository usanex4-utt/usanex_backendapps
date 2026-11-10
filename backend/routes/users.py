from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database.database import get_db
from ..database.models import (
    User,
    UserFollow,
    ConnectionRequest,
    UserConnection,
)
from .auth import get_current_user_from_request


router = APIRouter(
    prefix="/api/users",
    tags=["Users"],
)


# =========================================================
# CONNECTION STATUS
# =========================================================

def get_connection_status(
    db: Session,
    current_user_id: int,
    target_user_id: int,
):
    """
    Relationship status between current user and target user.

    Possible values:
        connected
        following
        requested
        request
        rejected
        none
    """

    # -----------------------------------------------------
    # CONNECTED
    # -----------------------------------------------------

    connection = (
        db.query(UserConnection)
        .filter(
            UserConnection.status == "connected",
            or_(
                (
                    UserConnection.user_one_id
                    == current_user_id
                )
                & (
                    UserConnection.user_two_id
                    == target_user_id
                ),
                (
                    UserConnection.user_one_id
                    == target_user_id
                )
                & (
                    UserConnection.user_two_id
                    == current_user_id
                ),
            ),
        )
        .first()
    )

    if connection is not None:
        return "connected"

    # -----------------------------------------------------
    # FOLLOWING
    # -----------------------------------------------------

    following = (
        db.query(UserFollow)
        .filter(
            UserFollow.follower_id
            == current_user_id,
            UserFollow.following_id
            == target_user_id,
        )
        .first()
    )

    if following is not None:
        return "following"

    # -----------------------------------------------------
    # CONNECTION REQUEST
    # -----------------------------------------------------

    request_row = (
        db.query(ConnectionRequest)
        .filter(
            or_(
                (
                    ConnectionRequest.sender_id
                    == current_user_id
                )
                & (
                    ConnectionRequest.receiver_id
                    == target_user_id
                ),
                (
                    ConnectionRequest.sender_id
                    == target_user_id
                )
                & (
                    ConnectionRequest.receiver_id
                    == current_user_id
                ),
            )
        )
        .order_by(
            ConnectionRequest.id.desc()
        )
        .first()
    )

    if request_row is not None:

        if (
            request_row.status == "pending"
            and request_row.sender_id
            == current_user_id
        ):
            return "requested"

        if (
            request_row.status == "pending"
            and request_row.receiver_id
            == current_user_id
        ):
            return "request"

        if request_row.status == "rejected":
            return "rejected"

    return "none"


# =========================================================
# FOLLOW CHECK
# =========================================================

def is_following(
    db: Session,
    follower_id: int,
    following_id: int,
) -> bool:

    row = (
        db.query(UserFollow)
        .filter(
            UserFollow.follower_id == follower_id,
            UserFollow.following_id == following_id,
        )
        .first()
    )

    return row is not None


# =========================================================
# FOLLOWERS COUNT
# =========================================================

def get_followers_count(
    db: Session,
    user_id: int,
) -> int:

    count = (
        db.query(func.count(UserFollow.id))
        .filter(
            UserFollow.following_id == user_id
        )
        .scalar()
    )

    return int(count or 0)


# =========================================================
# FOLLOWING COUNT
# =========================================================

def get_following_count(
    db: Session,
    user_id: int,
) -> int:

    count = (
        db.query(func.count(UserFollow.id))
        .filter(
            UserFollow.follower_id == user_id
        )
        .scalar()
    )

    return int(count or 0)


# =========================================================
# FOLLOW USER
# =========================================================

@router.post("/{user_id}/follow")
async def follow_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Follow another Usanex user.
    """

    current_user = await get_current_user_from_request(
        request=request,
        db=db,
    )

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    # -----------------------------------------------------
    # TARGET USER
    # -----------------------------------------------------

    target_user = (
        db.query(User)
        .filter(
            User.user_id == user_id
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # -----------------------------------------------------
    # CANNOT FOLLOW SELF
    # -----------------------------------------------------

    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=400,
            detail="You cannot follow yourself",
        )

    # -----------------------------------------------------
    # ALREADY FOLLOWING
    # -----------------------------------------------------

    existing_follow = (
        db.query(UserFollow)
        .filter(
            UserFollow.follower_id
            == current_user.id,

            UserFollow.following_id
            == target_user.id,
        )
        .first()
    )

    if existing_follow is not None:

        return {
            "success": True,
            "message": "Already following",
            "following": True,
            "followers_count":
                get_followers_count(
                    db,
                    target_user.id,
                ),
        }

    # -----------------------------------------------------
    # CREATE FOLLOW
    # -----------------------------------------------------

    follow = UserFollow(
        follower_id=current_user.id,
        following_id=target_user.id,
        created_at=datetime.utcnow(),
    )

    db.add(follow)

    db.commit()

    db.refresh(follow)

    # -----------------------------------------------------
    # RESPONSE
    # -----------------------------------------------------

    return {
        "success": True,
        "message": "User followed successfully",
        "following": True,
        "followers_count":
            get_followers_count(
                db,
                target_user.id,
            ),
        "following_count":
            get_following_count(
                db,
                current_user.id,
            ),
    }


# =========================================================
# UNFOLLOW USER
# =========================================================

@router.delete("/{user_id}/follow")
async def unfollow_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Unfollow another Usanex user.
    """

    current_user = await get_current_user_from_request(
        request=request,
        db=db,
    )

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    # -----------------------------------------------------
    # TARGET USER
    # -----------------------------------------------------

    target_user = (
        db.query(User)
        .filter(
            User.user_id == user_id
        )
        .first()
    )

    if target_user is None:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # -----------------------------------------------------
    # FIND FOLLOW
    # -----------------------------------------------------

    follow = (
        db.query(UserFollow)
        .filter(
            UserFollow.follower_id
            == current_user.id,

            UserFollow.following_id
            == target_user.id,
        )
        .first()
    )

    # -----------------------------------------------------
    # NOT FOLLOWING
    # -----------------------------------------------------

    if follow is None:

        return {
            "success": True,
            "message": "You are not following this user",
            "following": False,
            "followers_count":
                get_followers_count(
                    db,
                    target_user.id,
                ),
        }

    # -----------------------------------------------------
    # DELETE FOLLOW
    # -----------------------------------------------------

    db.delete(follow)

    db.commit()

    # -----------------------------------------------------
    # RESPONSE
    # -----------------------------------------------------

    return {
        "success": True,
        "message": "User unfollowed successfully",
        "following": False,
        "followers_count":
            get_followers_count(
                db,
                target_user.id,
            ),
        "following_count":
            get_following_count(
                db,
                current_user.id,
            ),
    }


# =========================================================
# FOLLOW STATUS
# =========================================================

@router.get("/{user_id}/follow-status")
async def follow_status(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Get follow status between current user
    and target user.
    """

    current_user = await get_current_user_from_request(
        request=request,
        db=db,
    )

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    target_user = (
        db.query(User)
        .filter(
            User.user_id == user_id
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
            "is_self": True,
            "following": False,
            "followers_count":
                get_followers_count(
                    db,
                    target_user.id,
                ),
            "following_count":
                get_following_count(
                    db,
                    target_user.id,
                ),
        }

    following = is_following(
        db=db,
        follower_id=current_user.id,
        following_id=target_user.id,
    )

    return {
        "success": True,
        "is_self": False,
        "following": following,
        "followers_count":
            get_followers_count(
                db,
                target_user.id,
            ),
        "following_count":
            get_following_count(
                db,
                target_user.id,
            ),
    }


# =========================================================
# PEOPLE / HOME LIST
# =========================================================

@router.get("/people")
async def get_people(
    request: Request,
    limit: int = Query(
        default=20,
        ge=1,
        le=50,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    db: Session = Depends(get_db),
):
    """
    Get users for Home / People list.

    Current logged-in user is excluded.
    """

    current_user = await get_current_user_from_request(
        request=request,
        db=db,
    )

    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    users = (
        db.query(User)
        .filter(
            User.id != current_user.id
        )
        .order_by(
            User.id.desc()
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []

    for user in users:

        connection_status = (
            get_connection_status(
                db=db,
                current_user_id=current_user.id,
                target_user_id=user.id,
            )
        )

        following = is_following(
            db=db,
            follower_id=current_user.id,
            following_id=user.id,
        )

        result.append(
            {
                "id": user.id,

                "username":
                    user.username,

                "user_id":
                    user.user_id,

                "name":
                    user.name,

                "profile_photo":
                    user.profile_photo,

                "bio":
                    user.bio,

                "connection_status":
                    connection_status,

                "following":
                    following,

                "followers_count":
                    get_followers_count(
                        db,
                        user.id,
                    ),

                "following_count":
                    get_following_count(
                        db,
                        user.id,
                    ),
            }
        )

    return {
        "success": True,

        "users": result,

        "pagination": {
            "limit": limit,
            "offset": offset,
            "count": len(result),
        },
    }
@router.get("/{user_id}/followers")
async def get_user_followers(
    user_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    current_user = await get_current_user_from_request(request=request, db=db)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    target = db.query(User).filter(User.user_id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    rows = (
        db.query(User)
        .join(UserFollow, UserFollow.follower_id == User.id)
        .filter(UserFollow.following_id == target.id)
        .order_by(UserFollow.created_at.desc())
        .offset(offset).limit(limit).all()
    )

    users = []
    for u in rows:
        following = is_following(db=db, follower_id=current_user.id, following_id=u.id)
        users.append({
            "user_id": u.user_id,
            "name": u.name,
            "username": u.username,
            "profile_photo": u.profile_photo,
            "is_following": following,  # True = already following → hide Follow back
        })

    return {"success": True, "users": users}


@router.get("/{user_id}/following")
async def get_user_following(
    user_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    current_user = await get_current_user_from_request(request=request, db=db)
    if current_user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    target = db.query(User).filter(User.user_id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    rows = (
        db.query(User)
        .join(UserFollow, UserFollow.following_id == User.id)
        .filter(UserFollow.follower_id == target.id)
        .order_by(UserFollow.created_at.desc())
        .offset(offset).limit(limit).all()
    )

    users = []
    for u in rows:
        following = is_following(db=db, follower_id=current_user.id, following_id=u.id)
        users.append({
            "user_id": u.user_id,
            "name": u.name,
            "username": u.username,
            "profile_photo": u.profile_photo,
            "is_following": following,
        })

    return {"success": True, "users": users}
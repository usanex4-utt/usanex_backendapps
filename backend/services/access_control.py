# backend/services/access_control.py
"""
Central content visibility + chat rules for Usanex.

Rules:
1. Owner always sees own content.
2. Public account → any logged-in user can see posts/reels.
3. Private account → viewer needs mutual follow OR accepted connection.
4. Chat (private + couple) requires accepted connection only.
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.database.models import User, UserConnection, UserFollow


CHAT_BLOCKED_MESSAGE = (
    "You can only chat after your connection request is accepted."
)

PRIVATE_CONTENT_MESSAGE = (
    "This account is private. Follow and get a follow-back to see posts and reels."
)


def is_connected(db: Session, user_a_id: int, user_b_id: int) -> bool:
    if user_a_id == user_b_id:
        return True
    row = (
        db.query(UserConnection.id)
        .filter(
            UserConnection.status == "connected",
            or_(
                (UserConnection.user_one_id == user_a_id)
                & (UserConnection.user_two_id == user_b_id),
                (UserConnection.user_one_id == user_b_id)
                & (UserConnection.user_two_id == user_a_id),
            ),
        )
        .first()
    )
    return row is not None


def is_following(db: Session, follower_id: int, following_id: int) -> bool:
    if follower_id == following_id:
        return False
    row = (
        db.query(UserFollow.id)
        .filter(
            UserFollow.follower_id == follower_id,
            UserFollow.following_id == following_id,
        )
        .first()
    )
    return row is not None


def is_mutual_follow(db: Session, user_a_id: int, user_b_id: int) -> bool:
    return is_following(db, user_a_id, user_b_id) and is_following(
        db, user_b_id, user_a_id
    )


def can_view_profile_content(db: Session, viewer: User, owner: User) -> bool:
    if viewer is None or owner is None:
        return False
    if viewer.id == owner.id:
        return True
    if not bool(getattr(owner, "is_private", False)):
        return True
    if is_mutual_follow(db, viewer.id, owner.id):
        return True
    if is_connected(db, viewer.id, owner.id):
        return True
    return False


def can_chat(db: Session, user_a_id: int, user_b_id: int) -> bool:
    return is_connected(db, user_a_id, user_b_id)


def follow_relationship_payload(db: Session, current_user: User, target: User) -> dict:
    if current_user.id == target.id:
        return {
            "is_self": True,
            "is_following": False,
            "is_followed_by": False,
            "is_mutual": False,
            "show_follow_back": False,
            "is_connected": True,
            "is_private": bool(getattr(target, "is_private", False)),
            "can_view_content": True,
        }

    following = is_following(db, current_user.id, target.id)
    followed_by = is_following(db, target.id, current_user.id)
    mutual = following and followed_by
    connected = is_connected(db, current_user.id, target.id)
    private = bool(getattr(target, "is_private", False))

    return {
        "is_self": False,
        "is_following": following,
        "is_followed_by": followed_by,
        "is_mutual": mutual,
        "show_follow_back": followed_by and not following,
        "is_connected": connected,
        "is_private": private,
        "can_view_content": can_view_profile_content(db, current_user, target),
    }
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..database.models import (
    Reel,
    UserReelHistory,
    ReelInteraction,
    UserFollow,
)


DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_CANDIDATES = 500


def _safe_number(value) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _freshness_score(created_at: Optional[datetime]) -> float:
    if not created_at:
        return 0.0

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - created_at).total_seconds() / 3600,
    )

    return 20.0 * math.exp(-age_hours / 168)


def _popularity_score(reel: Reel) -> float:
    views = _safe_number(reel.views_count)
    completed = _safe_number(reel.completed_views_count)
    replays = _safe_number(reel.replay_count)

    saves = _safe_number(reel.save_count)
    shares = _safe_number(reel.share_count)
    comments = _safe_number(reel.comment_count)
    interested = _safe_number(reel.interested_count)
    not_interested = _safe_number(reel.not_interested_count)

    reach_score = min(15.0, math.log1p(views) * 2.0)

    engagement = (
        saves * 3.0
        + shares * 3.5
        + comments * 2.0
        + interested * 2.0
        + replays * 0.5
        - not_interested * 3.0
    )

    engagement_score = min(
        20.0,
        max(0.0, math.log1p(max(0.0, engagement)) * 4.0),
    )

    completion_rate = min(1.0, completed / views) if views else 0.0
    completion_score = completion_rate * 10.0

    return min(45.0, reach_score + engagement_score + completion_score)


def _personal_score(
    reel_id: int,
    history,
    interactions,
) -> float:
    score = 0.0

    if history:
        watch_time = _safe_number(history.total_watch_time)
        watch_percentage = _safe_number(history.max_watch_percentage)
        completed_count = _safe_number(history.completed_count)
        replay_count = _safe_number(history.replay_count)

        score += min(8.0, math.log1p(watch_time) * 1.5)
        score += min(8.0, watch_percentage / 100.0 * 8.0)
        score += min(6.0, completed_count * 2.0)
        score += min(6.0, replay_count * 2.0)

    for interaction in interactions:
        event_type = interaction.event_type

        if interaction.interested:
            score += 8.0

        elif interaction.not_interested:
            score -= 15.0

        if interaction.saved:
            score += 6.0

        if interaction.shared:
            score += 5.0

        if interaction.commented:
            score += 3.0

        if interaction.star_rating:
            score += min(5.0, _safe_number(interaction.star_rating))

        if interaction.muted:
            score -= 2.0

    return max(-25.0, min(40.0, score))


def _rank_with_creator_diversity(scored_reels):
    remaining = list(scored_reels)
    ranked = []
    recent_creators = []

    while remaining:
        selected_index = None

        for index, item in enumerate(remaining):
            creator_id = item[1].user_id

            if creator_id not in recent_creators[-2:]:
                selected_index = index
                break

        if selected_index is None:
            selected_index = 0

        selected = remaining.pop(selected_index)
        ranked.append(selected[1])
        recent_creators.append(selected[1].user_id)

    return ranked


def get_recommended_reels(
    db: Session,
    user_id: int,
    limit: int = DEFAULT_LIMIT,
    exclude_reel_ids: Optional[list[int]] = None,
    offset: int = 0,
) -> list[Reel]:
    """
    Personalized recommendation with:
    - Not Interested exclusion
    - Watch history
    - User interactions
    - Followed creator priority
    - Creator diversity
    - Offset pagination
    """

    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    excluded_ids = set(exclude_reel_ids or [])

    # Base public and safe reels
    query = db.query(Reel).filter(
        Reel.visibility == "public",
        Reel.status == "published",
        Reel.is_safe.is_(True),
    )

    # Exclude Not Interested reels
    not_interested_ids = (
        db.query(ReelInteraction.reel_id)
        .filter(
            ReelInteraction.user_id == user_id,
            ReelInteraction.not_interested.is_(True),
        )
    )

    query = query.filter(
        ~Reel.id.in_(not_interested_ids)
    )

    if excluded_ids:
        query = query.filter(
            ~Reel.id.in_(excluded_ids)
        )

    candidates = (
        query.order_by(Reel.id.desc())
        .limit(MAX_CANDIDATES)
        .all()
    )

    if not candidates:
        return []

    candidate_ids = [r.id for r in candidates]

    # Watch history
    history_rows = (
        db.query(UserReelHistory.reel_id)
        .filter(
            UserReelHistory.user_id == user_id,
            UserReelHistory.reel_id.in_(candidate_ids),
        )
        .all()
    )

    seen_ids = {row[0] for row in history_rows}

    # User interactions
    interactions = (
        db.query(ReelInteraction)
        .filter(
            ReelInteraction.user_id == user_id,
            ReelInteraction.reel_id.in_(candidate_ids),
        )
        .order_by(ReelInteraction.created_at.desc())
        .all()
    )

    interaction_map = {}

    for item in interactions:
        interaction_map.setdefault(item.reel_id, []).append(item)

    # Followed creators
    creator_ids = list({r.user_id for r in candidates})

    followed_rows = (
        db.query(UserFollow.following_id)
        .filter(
            UserFollow.follower_id == user_id,
            UserFollow.following_id.in_(creator_ids),
        )
        .all()
    )

    followed_ids = {row[0] for row in followed_rows}

    # Score candidates
    scored = []

    for reel in candidates:
        seen = reel.id in seen_ids
        score = _reel_score(reel, seen=seen)

        if reel.user_id in followed_ids:
            score += 10.0

        for interaction in interaction_map.get(reel.id, []):
            if interaction.interested:
                score += 8.0
            if interaction.saved:
                score += 6.0
            if interaction.shared:
                score += 5.0
            if interaction.commented:
                score += 3.0

        scored.append((score, reel))

    scored.sort(
        key=lambda item: (item[0], item[1].id),
        reverse=True,
    )

    ranked = _rank_with_creator_diversity(scored)

    return ranked[offset:offset + limit]
    

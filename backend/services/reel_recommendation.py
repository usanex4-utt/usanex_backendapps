
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..database.models import Reel, UserReelHistory


DEFAULT_LIMIT = 10
MAX_LIMIT = 50
MAX_CANDIDATES = 500


def _safe_number(value) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _freshness_score(created_at: Optional[datetime]) -> float:
    """Newer reels receive a higher freshness score."""
    if not created_at:
        return 0.0

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - created_at).total_seconds() / 3600,
    )

    # Score decreases gradually over time.
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

    # Logarithmic scoring prevents very popular reels
    # from dominating every recommendation.
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


def _reel_score(reel: Reel, seen: bool = False) -> float:
    score = (
        _freshness_score(reel.created_at)
        + _popularity_score(reel)
    )

    # Avoid repeatedly recommending already watched reels.
    if seen:
        score *= 0.25

    return round(score, 4)


def _rank_with_creator_diversity(scored_reels):
    """Avoid showing too many consecutive reels from one creator."""
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
) -> list[Reel]:
    """
    Initial scoring-based recommendation service.

    Uses existing Reel counters and UserReelHistory.
    Does not create or modify database records.
    """

    limit = max(1, min(int(limit), MAX_LIMIT))
    excluded_ids = set(exclude_reel_ids or [])

    query = db.query(Reel).filter(
        Reel.visibility == "public",
        Reel.status == "published",
        Reel.is_safe.is_(True),
    )

    if excluded_ids:
        query = query.filter(~Reel.id.in_(excluded_ids))

    candidates = (
        query.order_by(Reel.id.desc())
        .limit(MAX_CANDIDATES)
        .all()
    )

    if not candidates:
        return []

    candidate_ids = [reel.id for reel in candidates]

    history_rows = (
        db.query(UserReelHistory.reel_id)
        .filter(
            UserReelHistory.user_id == user_id,
            UserReelHistory.reel_id.in_(candidate_ids),
        )
        .all()
    )

    seen_ids = {row[0] for row in history_rows}

    unseen = []
    previously_seen = []

    for reel in candidates:
        is_seen = reel.id in seen_ids
        item = (_reel_score(reel, seen=is_seen), reel)

        if is_seen:
            previously_seen.append(item)
        else:
            unseen.append(item)

    # Prefer unseen reels. Use previously seen reels only
    # when there are not enough unseen candidates.
    unseen.sort(key=lambda item: item[0], reverse=True)
    previously_seen.sort(key=lambda item: item[0], reverse=True)

    combined = unseen + previously_seen

    selected = _rank_with_creator_diversity(combined)

    return selected[:limit]
  

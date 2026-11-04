import logging

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import (
    get_supabase_client,
    delete_moment_media,
)

logger = logging.getLogger(__name__)


def cleanup_expired_normal_moments():
    """
    Permanently delete expired Normal Moment media.
    Couple Moments are never touched.
    """

    supabase = get_supabase_client()

    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT
                    mm.id AS media_id,
                    mm.storage_path,
                    mm.thumbnail_path
                FROM nex_moments nm
                JOIN nex_moment_media mm
                    ON mm.moment_id = nm.id
                WHERE nm.moment_scope = 'normal'
                  AND nm.expires_at <= NOW()
            """)
        )

        media_rows = [dict(row._mapping) for row in result]

    deleted_count = 0

    for media in media_rows:
        paths = list(dict.fromkeys(
            path for path in (
                media["storage_path"],
                media["thumbnail_path"],
            ) if path
        ))

        if not paths:
            continue

        try:
            supabase.storage.from_("nex-moments").remove(paths)

            with engine.begin() as conn:
                conn.execute(
                    text("""
                        DELETE FROM nex_moment_media
                        WHERE id = :media_id
                    """),
                    {"media_id": media["media_id"]},
                )

            deleted_count += 1

        except Exception:
            logger.exception(
                "Failed to clean expired Moment media ID %s",
                media["media_id"],
            )

    logger.info(
        "Normal Moment cleanup completed. Deleted media records: %s",
        deleted_count,
    )

    return deleted_count


import logging

from sqlalchemy import text

from ..database.database import engine

logger = logging.getLogger(__name__)


def cleanup_expired_normal_moments(dry_run=True):
    """
    Find expired Normal Moment media.

    Dry run is enabled by default.
    No Storage or database records are deleted.
    """

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT nm.id AS moment_id,
                   mm.id AS media_id,
                   mm.storage_path,
                   mm.thumbnail_path,
                   nm.expires_at
            FROM nex_moments nm
            JOIN nex_moment_media mm
              ON mm.moment_id = nm.id
            WHERE nm.moment_scope = 'normal'
              AND nm.expires_at <= NOW()
            ORDER BY nm.expires_at ASC
        """))

        media_rows = [dict(row._mapping) for row in result]

    logger.info(
        "Found %s expired Normal Moment media records",
        len(media_rows)
    )

    for media in media_rows:
        logger.info(
            "DRY RUN: moment_id=%s media_id=%s "
            "storage_path=%s thumbnail_path=%s",
            media["moment_id"],
            media["media_id"],
            media["storage_path"],
            media["thumbnail_path"]
        )

    if dry_run:
        logger.info(
            "DRY RUN COMPLETE: No files or database records deleted."
        )
        return {
            "dry_run": True,
            "found": len(media_rows),
            "deleted": 0
        }

    raise RuntimeError(
        "Real deletion is disabled until dry-run results are verified."
    )

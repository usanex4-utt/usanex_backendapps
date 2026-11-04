import logging

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import get_supabase_client

logger = logging.getLogger(__name__)


def cleanup_expired_normal_moments():
    """Delete expired Normal Moment media from Supabase Storage."""

    supabase = get_supabase_client()

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT mm.id AS media_id,
                   mm.storage_path,
                   mm.thumbnail_path
            FROM nex_moments nm
            JOIN nex_moment_media mm
              ON mm.moment_id = nm.id
            WHERE nm.moment_scope = 'normal'
              AND nm.expires_at <= NOW()
        """))

        media_rows = [dict(row._mapping) for row in result]

    deleted_count = 0

    for media in media_rows:
        paths = list(dict.fromkeys(
            p for p in (
                media["storage_path"],
                media["thumbnail_path"]
            ) if p
        ))

        if not paths:
            continue

        try:
            response = supabase.storage.from_(
                "nex-moments"
            ).remove(paths)

            if not isinstance(response, list):
                raise RuntimeError("Invalid Storage deletion response")

            removed_paths = {
                item.get("name")
                for item in response
                if isinstance(item, dict)
            }

            if not set(paths).issubset(removed_paths):
                raise RuntimeError(
                    "Not all requested media files were confirmed deleted"
                )

            with engine.begin() as conn:
                conn.execute(text("""
                    DELETE FROM nex_moment_media
                    WHERE id = :media_id
                """), {"media_id": media["media_id"]})

            deleted_count += 1

        except Exception:
            logger.exception(
                "Cleanup failed for media ID %s",
                media["media_id"]
            )

    logger.info("Cleanup deleted %s media records", deleted_count)
    return deleted_count

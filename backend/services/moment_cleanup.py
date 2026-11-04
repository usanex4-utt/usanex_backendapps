
import logging

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import get_supabase_client

logger = logging.getLogger(__name__)


def storage_file_exists(supabase, file_path):
    """Check whether an exact file still exists in Storage."""

    folder, filename = (
        file_path.rsplit("/", 1)
        if "/" in file_path
        else ("", file_path)
    )

    files = supabase.storage.from_("nex-moments").list(
        folder,
        {"search": filename, "limit": 100}
    )

    return any(
        isinstance(item, dict)
        and item.get("name") == filename
        for item in files
    )


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
            path for path in (
                media["storage_path"],
                media["thumbnail_path"]
            )
            if path
        ))

        if not paths:
            logger.warning(
                "No Storage paths for media ID %s",
                media["media_id"]
            )
            continue

        try:
            bucket = supabase.storage.from_("nex-moments")

            # Request deletion. HTTP/API errors raise exceptions.
            response = bucket.remove(paths)

            if not isinstance(response, list):
                raise RuntimeError(
                    "Unexpected Storage deletion response"
                )

            # Verify every requested file is absent.
            remaining = [
                path for path in paths
                if storage_file_exists(supabase, path)
            ]

            if remaining:
                raise RuntimeError(
                    f"Storage files still exist: {remaining}"
                )

            # Delete metadata only after Storage verification.
            with engine.begin() as conn:
                conn.execute(text("""
                    DELETE FROM nex_moment_media
                    WHERE id = :media_id
                """), {
                    "media_id": media["media_id"]
                })

            deleted_count += 1

        except Exception:
            logger.exception(
                "Cleanup failed for media ID %s",
                media["media_id"]
            )

    logger.info(
        "Cleanup deleted %s media records",
        deleted_count
    )

    return deleted_count

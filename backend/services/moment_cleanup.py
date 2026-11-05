
import logging

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import (
    get_supabase_client,
    _supabase_configured,
    LOCAL_UPLOAD_ROOT,
    LOCAL_URL_PREFIX,
)

logger = logging.getLogger(__name__)

def storage_file_exists(bucket, file_path):
    if not file_path:
        return False

    # Local filesystem check
    if file_path.startswith(LOCAL_URL_PREFIX + "/"):
        relative_path = file_path[len(LOCAL_URL_PREFIX) + 1:]
        local_path = LOCAL_UPLOAD_ROOT / relative_path
        return local_path.is_file()

    # Supabase Storage check
    if bucket is None:
        return False

    if "/" not in file_path:
        return False

    folder, filename = file_path.rsplit("/", 1)

    files = bucket.list(
        folder,
        {"search": filename, "limit": 100}
    )

    return any(
        isinstance(item, dict) and item.get("name") == filename
        for item in files
    )


def cleanup_expired_normal_moments(dry_run=True):
    """Delete expired Normal Moment media while preserving history."""

   

    # Step 1: Mark expired Moments as archived.
    archived_count = 0

    if not dry_run:
        with engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE nex_moments
                SET archived_at = COALESCE(archived_at, expires_at)
                WHERE moment_scope = 'normal'
                  AND expires_at <= NOW()
                  AND deleted_at IS NULL
                RETURNING id
            """))

            archived_count = len(result.fetchall())
    else:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT COUNT(*)
                FROM nex_moments
                WHERE moment_scope = 'normal'
                  AND expires_at <= NOW()
                  AND deleted_at IS NULL
                  AND archived_at IS NULL
            """))

            archived_count = result.scalar() or 0
    # Step 2: Find all expired Normal Moment media.
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                nm.id AS moment_id,
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

    if dry_run:
        return {
            "dry_run": True,
            "archived": archived_count,
            "found": len(media_rows),
            "deleted_files": 0,
            "failed": 0
        }

    bucket = get_supabase_client().storage.from_("nex-moments")

    deleted_files = 0
    failed = 0

    for media in media_rows:
        paths = list(dict.fromkeys(
            path for path in (
                media["storage_path"],
                media["thumbnail_path"]
            ) if path
        ))

        if not paths:
            logger.warning(
                "No storage paths: media_id=%s",
                media["media_id"]
            )
            failed += 1
            continue

        try:
            existing_paths = [
                path for path in paths
                if storage_file_exists(bucket, path)
            ]

            if existing_paths:
                response = bucket.remove(existing_paths)

                if not isinstance(response, list):
                    raise RuntimeError(
                        "Invalid Storage deletion response"
                    )

                remaining = [
                    path for path in paths
                    if storage_file_exists(bucket, path)
                ]

                if remaining:
                    raise RuntimeError(
                        f"Storage verification failed: {remaining}"
                    )

                deleted_files += len(existing_paths)

            logger.info(
                "CLEANUP SUCCESS: moment_id=%s media_id=%s",
                media["moment_id"],
                media["media_id"]
            )

            # IMPORTANT:
            # Do not delete nex_moment_media records.
            # Preserve all media metadata for future analysis.

        except Exception:
            failed += 1
            logger.exception(
                "Cleanup failed: media_id=%s",
                media["media_id"]
            )

    return {
        "dry_run": False,
        "archived": archived_count,
        "found": len(media_rows),
        "deleted_files": deleted_files,
        "failed": failed
    }

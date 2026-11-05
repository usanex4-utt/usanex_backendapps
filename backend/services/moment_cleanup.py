
import logging
from pathlib import Path

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import (
    get_supabase_client,
    _supabase_configured,
    LOCAL_UPLOAD_ROOT,
    LOCAL_URL_PREFIX,
    BUCKET,
)

logger = logging.getLogger(__name__)


def _get_local_path(file_path):
    """Resolve a stored media path safely inside local upload folder."""
    if not file_path:
        return None

    relative_path = file_path

    if relative_path.startswith(LOCAL_URL_PREFIX + "/"):
        relative_path = relative_path[len(LOCAL_URL_PREFIX) + 1:]

    relative_path = relative_path.lstrip("/")

    # Prevent directory traversal outside the upload folder.
    root = LOCAL_UPLOAD_ROOT.resolve()
    local_path = (root / relative_path).resolve()

    try:
        local_path.relative_to(root)
    except ValueError:
        raise ValueError("Invalid local media path")

    return local_path


def storage_file_exists(bucket, file_path):
    """Check whether media exists in local or Supabase storage."""
    if not file_path:
        return False

    local_path = _get_local_path(file_path)

    if local_path and local_path.is_file():
        return True

    # Local URL paths are not Supabase object keys.
    if file_path.startswith(LOCAL_URL_PREFIX + "/"):
        return False

    if bucket is None or "/" not in file_path:
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


def _delete_media_file(bucket, file_path):
    """Delete a file from whichever storage contains it."""
    if not file_path:
        return 0

    deleted = 0

    local_path = _get_local_path(file_path)

    # Delete local copy, if present.
    if local_path and local_path.is_file():
        local_path.unlink()
        deleted += 1

    # Delete Supabase copy, if present.
    is_local_url = file_path.startswith(LOCAL_URL_PREFIX + "/")

    if bucket is not None and not is_local_url:
        if storage_file_exists(bucket, file_path):
            bucket.remove([file_path])
            deleted += 1

    # Verify that no copy remains.
    if storage_file_exists(bucket, file_path):
        raise RuntimeError(
            f"Media still exists after deletion: {file_path}"
        )

    return deleted


def cleanup_expired_normal_moments(dry_run=True):
    """Archive expired Normal Moments and delete their media, preserving history."""

    # Step 1: Archive expired Moments.
    archived_count = 0

    if dry_run:
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

    else:
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

    # Step 2: Find expired Moment media.
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

    # Step 3: Dry run — no database or storage changes.
    if dry_run:
        return {
            "dry_run": True,
            "archived": archived_count,
            "found": len(media_rows),
            "deleted_files": 0,
            "failed": 0,
        }

    # Step 4: Connect to Supabase when configured.
    bucket = None

    if _supabase_configured():
        bucket = get_supabase_client().storage.from_(BUCKET)

    deleted_files = 0
    failed = 0

    # Step 5: Delete expired media.
    for media in media_rows:
        paths = list(dict.fromkeys(
            path for path in (
                media["storage_path"],
                media["thumbnail_path"],
            )
            if path
        ))

        if not paths:
            logger.warning(
                "No storage paths: media_id=%s",
                media["media_id"],
            )
            failed += 1
            continue

        try:
            media_deleted = 0

            for path in paths:
                media_deleted += _delete_media_file(bucket, path)

            deleted_files += media_deleted

            logger.info(
                "CLEANUP SUCCESS: moment_id=%s media_id=%s deleted_files=%s",
                media["moment_id"],
                media["media_id"],
                media_deleted,
            )

            # IMPORTANT:
            # Keep nex_moment_media records.
            # Keep all media metadata and engagement history.

        except Exception:
            failed += 1

            logger.exception(
                "Cleanup failed: moment_id=%s media_id=%s",
                media["moment_id"],
                media["media_id"],
            )

    return {
        "dry_run": False,
        "archived": archived_count,
        "found": len(media_rows),
        "deleted_files": deleted_files,
        "failed": failed,
    }

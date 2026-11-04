
import logging

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import get_supabase_client

logger = logging.getLogger(__name__)


def storage_file_exists(bucket, file_path):
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
    """Clean expired Normal Moment media only."""

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

    bucket = get_supabase_client().storage.from_("nex-moments")

    deleted_records = 0
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
                "Skipped media_id=%s: no storage paths",
                media["media_id"]
            )
            failed += 1
            continue

        try:
            if dry_run:
                for path in paths:
                    logger.info(
                        "DRY RUN: media_id=%s path=%s exists=%s",
                        media["media_id"],
                        path,
                        storage_file_exists(bucket, path)
                    )
                continue

            response = bucket.remove(paths)

            if not isinstance(response, list):
                raise RuntimeError("Invalid Storage deletion response")

            remaining = [
                path for path in paths
                if storage_file_exists(bucket, path)
            ]

            if remaining:
                raise RuntimeError(
                    f"Storage verification failed: {remaining}"
                )

            with engine.begin() as conn:
                conn.execute(
                    text("""
                        DELETE FROM nex_moment_media
                        WHERE id = :media_id
                    """),
                    {"media_id": media["media_id"]}
                )

            deleted_records += 1
            deleted_files += len(response)

            logger.info(
                "CLEANUP SUCCESS: moment_id=%s media_id=%s",
                media["moment_id"],
                media["media_id"]
            )

        except Exception:
            failed += 1
            logger.exception(
                "Cleanup failed for media_id=%s",
                media["media_id"]
            )

    return {
        "dry_run": dry_run,
        "found": len(media_rows),
        "deleted_records": deleted_records,
        "deleted_files": deleted_files,
        "failed": failed
    }

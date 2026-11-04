
import logging

from sqlalchemy import text

from ..database.database import engine
from .supabase_storage import get_supabase_client

logger = logging.getLogger(__name__)


def storage_file_exists(bucket, file_path):
    """Check whether an exact file exists in Supabase Storage."""

    folder, filename = file_path.rsplit("/", 1)

    files = bucket.list(
        folder,
        {
            "search": filename,
            "limit": 100
        }
    )

    return any(
        isinstance(item, dict) and item.get("name") == filename
        for item in files
    )


def cleanup_expired_normal_moments(dry_run=True):
    """Verify expired Normal Moment files without deleting anything."""

    if not dry_run:
        raise RuntimeError("Storage verification only. Deletion is disabled.")

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

    verified = []

    for media in media_rows:
        paths = list(dict.fromkeys(
            path for path in (
                media["storage_path"],
                media["thumbnail_path"]
            ) if path
        ))

        for path in paths:
            exists = storage_file_exists(bucket, path)

            logger.info(
                "STORAGE CHECK: moment_id=%s media_id=%s path=%s exists=%s",
                media["moment_id"],
                media["media_id"],
                path,
                exists
            )

            verified.append({
                "moment_id": media["moment_id"],
                "media_id": media["media_id"],
                "storage_path": path,
                "exists": exists
            })

    result = {
        "dry_run": True,
        "found": len(media_rows),
        "checked_files": len(verified),
        "existing_files": sum(1 for item in verified if item["exists"]),
        "missing_files": sum(1 for item in verified if not item["exists"]),
        "files": verified,
        "deleted": 0
    }

    logger.info("STORAGE VERIFICATION COMPLETE: %s", result)

    return result

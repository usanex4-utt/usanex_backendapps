# backend/services/supabase_storage.py
"""
Moment media storage.

Modes (auto-selected per call):
  1. Supabase Storage (when SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set
     AND the `supabase` package is installed)
  2. Local filesystem  →  frontend/static/uploads/moments/{user_id}/{uuid}.ext
     served by FastAPI at  /static/uploads/moments/...

`upload_moment_media()` returns the value to store in `nex_moments.media_url`:
  - Supabase mode: relative key like "user_id/uuid.jpg"  (resolved via signed URL)
  - Local mode:    URL path like "/static/uploads/moments/user_id/uuid.jpg"
"""
import logging
import os
from functools import lru_cache
from pathlib import Path

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    create_client = None
    Client = None
    SUPABASE_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── local fallback paths ────────────────────────────────
LOCAL_UPLOAD_ROOT = (
    Path(__file__).resolve().parents[1]  # yeh backend folder ko point karega
    / "static" / "uploads" / "moments"
)
LOCAL_URL_PREFIX = "/static/uploads/moments"
BUCKET = "nex-moments"


# ── mode detection ──────────────────────────────────────
def _supabase_configured() -> bool:
    """True only if the package is importable AND env vars are set."""
    if not SUPABASE_AVAILABLE:
        return False
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return bool(url and key)


@lru_cache(maxsize=1)
def get_supabase_client():
    if not SUPABASE_AVAILABLE:
        raise RuntimeError(
            "supabase package is not installed. Run: pip install supabase"
        )
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url:
        raise RuntimeError("SUPABASE_URL is missing")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is missing")
    return create_client(url, key)


def upload_moment_media(file_bytes: bytes, file_path: str, content_type: str) -> str:
    # Supabase mode if configured
    if _supabase_configured():
        try:
            supabase = get_supabase_client()
            supabase.storage.from_(BUCKET).upload(
                path=file_path, file=file_bytes,
                file_options={"content-type": content_type, "upsert": False},
            )
            return file_path
        except Exception:
            logger.warning("Supabase upload failed, falling back to local", exc_info=True)

    # Local fallback
    local_path = LOCAL_UPLOAD_ROOT / file_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(file_bytes)
    return f"{LOCAL_URL_PREFIX}/{file_path}"


def delete_moment_media(file_path: str) -> None:
    """
    Delete the given file. file_path may be:
      - a relative key     "5645163913/abc.jpg"                       (Supabase)
      - a URL path         "/static/uploads/moments/5645163913/abc.jpg"  (local)
    Tries both safely.
    """
    if not file_path:
        return

    # Normalize: strip leading "/" and known local prefix
    rel = file_path.lstrip("/")
    if rel.startswith("static/uploads/moments/"):
        rel = rel[len("static/uploads/moments/"):]
    elif rel.startswith("uploads/moments/"):
        rel = rel[len("uploads/moments/"):]
    elif rel.startswith("moments/"):
        rel = rel[len("moments/"):]

    # ── Try local first (safe even if it does not exist) ──
    local_path = LOCAL_UPLOAD_ROOT / rel
    try:
        if local_path.exists():
            local_path.unlink()
            return
    except Exception:
        logger.warning("local delete failed for %s", local_path, exc_info=True)

    # ── Fallback to Supabase ───────────────────────────
    if _supabase_configured():
        try:
            get_supabase_client().storage.from_(BUCKET).remove([file_path])
        except Exception:
            logger.warning(
                "Supabase delete failed for %s", file_path, exc_info=True
            )
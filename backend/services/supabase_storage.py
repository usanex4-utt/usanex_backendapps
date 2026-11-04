# backend/services/supabase_storage.py
"""
Moment media storage.

Modes:
  1. Supabase Storage (Production / When configured)
  2. Local filesystem  (Development / Localhost only)
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

# ── local fallback paths (sirf local dev ke liye) ─────────
LOCAL_UPLOAD_ROOT = (
    Path(__file__).resolve().parents[1]
    / "static" / "uploads" / "moments"
)
LOCAL_URL_PREFIX = "/static/uploads/moments"
BUCKET = "nex-moments"

def _supabase_configured() -> bool:
    """Check karta hai ki Supabase setup hai ya nahi."""
    if not SUPABASE_AVAILABLE:
        return False
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return bool(url and key)

@lru_cache(maxsize=1)
def get_supabase_client():
    if not SUPABASE_AVAILABLE:
        raise RuntimeError("supabase package is not installed. Run: pip install supabase")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url:
        raise RuntimeError("SUPABASE_URL is missing")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is missing")
    return create_client(url, key)

def upload_moment_media(file_bytes: bytes, file_path: str, content_type: str) -> str:
    """
    File upload karta hai. 
    Production mein sirf Supabase use hoga.
    Local mein disk par save hoga.
    """
    env = os.getenv("ENVIRONMENT", "production").lower()
    
    # 1. Agar Supabase configured hai
    if _supabase_configured():
        try:
            supabase = get_supabase_client()
            supabase.storage.from_(BUCKET).upload(
                path=file_path, file=file_bytes,
                file_options={"content-type": content_type, "upsert": False},
            )
            return file_path
        except Exception as e:
            logger.error(f"Supabase upload failed: {e}")
            # Production mein fallback mat karo, seedha error do
            if env == "production":
                raise RuntimeError(f"Cloud storage upload failed: {str(e)}")
            logger.warning("Supabase failed, falling back to local storage (Dev Mode).")

    # 2. Agar Supabase configured NAHI hai aur Production hai -> Error do
    elif env == "production":
        raise RuntimeError("Supabase is not configured. Cannot upload media in production.")

    # 3. Local fallback (sirf development ke liye)
    try:
        local_path = LOCAL_UPLOAD_ROOT / file_path
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(file_bytes)
        return f"{LOCAL_URL_PREFIX}/{file_path}"
    except Exception as e:
        logger.error(f"Local file write failed: {e}")
        raise RuntimeError(f"Local storage failed: {str(e)}")

def delete_moment_media(file_path: str) -> None:
    """
    File ko delete karta hai (chahe woh Supabase mein ho ya Local disk mein).
    """
    if not file_path:
        return

    # Path normalize karo
    rel = file_path.lstrip("/")
    if rel.startswith("static/uploads/moments/"):
        rel = rel[len("static/uploads/moments/"):]
    elif rel.startswith("uploads/moments/"):
        rel = rel[len("uploads/moments/"):]
    elif rel.startswith("moments/"):
        rel = rel[len("moments/"):]

    # 1. Local disk se delete karne ki koshish karo (agar exist karta hai)
    local_path = LOCAL_UPLOAD_ROOT / rel
    try:
        if local_path.exists():
            local_path.unlink()
    except Exception:
        logger.warning("Local delete failed for %s", local_path, exc_info=True)

    # 2. Supabase se delete karne ki koshish karo
    if _supabase_configured():
        try:
            get_supabase_client().storage.from_(BUCKET).remove([file_path])
        except Exception:
            logger.warning("Supabase delete failed for %s", file_path, exc_info=True)
# backend/services/supabase_storage.py
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

# ── local fallback paths ─────────
LOCAL_UPLOAD_ROOT = (
    Path(__file__).resolve().parents[1]
    / "static" / "uploads" / "moments"
)
LOCAL_URL_PREFIX = "/static/uploads/moments"
BUCKET = "nex-moments"

def _supabase_configured() -> bool:
    if not SUPABASE_AVAILABLE:
        return False
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return bool(url and key)

@lru_cache(maxsize=1)
def get_supabase_client():
    if not SUPABASE_AVAILABLE:
        raise RuntimeError("supabase package is not installed.")
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase keys missing.")
    return create_client(url, key)

def upload_moment_media(file_bytes: bytes, file_path: str, content_type: str) -> str:
    """
    Upload file. Uses Supabase if configured. 
    Falls back to local disk if on local machine.
    """
    # Render apne aap "RENDER" env variable set karta hai.
    is_render = os.getenv("RENDER") is not None
    
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
            if is_render:
                raise RuntimeError(f"Cloud storage upload failed: {str(e)}")
            logger.warning("Supabase failed, falling back to local storage.")

    # 2. Agar Supabase NAHI hai aur Render par hain -> Error
    elif is_render:
        raise RuntimeError("Supabase is not configured on Render. Add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")

    # 3. Local fallback (Laptop par file save karega)
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
    if not file_path:
        return

    rel = file_path.lstrip("/")
    if rel.startswith("static/uploads/moments/"):
        rel = rel[len("static/uploads/moments/"):]

    # 1. Local delete
    local_path = LOCAL_UPLOAD_ROOT / rel
    try:
        if local_path.exists():
            local_path.unlink()
    except Exception:
        pass

    # 2. Supabase delete
    if _supabase_configured():
        try:
            get_supabase_client().storage.from_(BUCKET).remove([file_path])
        except Exception:
            logger.warning(f"Supabase delete failed for {file_path}")
# backend/services/supabase_storage.py
import os
from functools import lru_cache

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    create_client = None
    Client = None
    SUPABASE_AVAILABLE = False


@lru_cache(maxsize=1)
def get_supabase_client():
    """
    Create and reuse Supabase client.
    Service role key is used only on backend.
    """
    if not SUPABASE_AVAILABLE:
        raise RuntimeError(
            "supabase package is not installed. Run: pip install supabase"
        )

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing")

    if not supabase_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is missing")

    return create_client(supabase_url, supabase_key)


def upload_moment_media(file_bytes: bytes, file_path: str, content_type: str):
    """Upload media to private nex-moments bucket."""
    supabase = get_supabase_client()
    return supabase.storage.from_("nex-moments").upload(
        path=file_path,
        file=file_bytes,
        file_options={
            "content-type": content_type,
            "upsert": False,
        },
    )


def delete_moment_media(file_path: str):
    """Delete uploaded media from private Supabase Storage."""
    supabase = get_supabase_client()
    return supabase.storage.from_("nex-moments").remove([file_path])
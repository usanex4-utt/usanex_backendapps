import os
from functools import lru_cache

from supabase import create_client, Client


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Create and reuse Supabase client.
    Service role key is used only on backend.
    """

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing")

    if not supabase_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is missing")

    return create_client(
        supabase_url,
        supabase_key
    )


def upload_moment_media(
    file_bytes: bytes,
    file_path: str,
    content_type: str
):
    """
    Upload media to private nex-moments bucket.
    """

    supabase = get_supabase_client()

    result = supabase.storage.from_("nex-moments").upload(
        path=file_path,
        file=file_bytes,
        file_options={
            "content-type": content_type,
            "upsert": "false",
        },
    )

    return result

def delete_moment_media(file_path: str):
    """Delete uploaded media from private Supabase Storage."""

    supabase = get_supabase_client()

    return supabase.storage.from_("nex-moments").remove(
        [file_path]
    )


from __future__ import annotations

import os
import urllib.parse
from functools import lru_cache

import cloudinary
import cloudinary.uploader
import cloudinary.utils


# ==========================================
# CLOUDINARY CONFIGURATION
# ==========================================

@lru_cache(maxsize=1)
def _init_cloudinary():
    """
    Initialize destination Cloudinary account.
    Supports CLOUDINARY_URL or environment variables.
    """

    cloudinary_url = os.getenv("CLOUDINARY_URL")

    if cloudinary_url:
        config = cloudinary.config(
            cloudinary_url=cloudinary_url,
            secure=True,
        )
    else:
        cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
        api_key = os.getenv("CLOUDINARY_API_KEY")
        api_secret = os.getenv("CLOUDINARY_API_SECRET")

        if not all([cloud_name, api_key, api_secret]):
            raise RuntimeError(
                "Cloudinary configuration missing. "
                "Set CLOUDINARY_URL or CLOUDINARY_CLOUD_NAME, "
                "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
            )

        config = cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
            secure=True,
        )

    return config


# ==========================================
# LOGO CONFIGURATION
# ==========================================

LOGO_PUBLIC_ID = os.getenv(
    "CLOUDINARY_LOGO_PUBLIC_ID",
    "utanexa_logo",
)

LOGO_SOURCE_URL = (
    "https://res.cloudinary.com/xqbosxje/image/upload/"
    "v1794860442/utanexa_logo.png"
)


# ==========================================
# UPLOAD LOGO TO VIDEO ACCOUNT
# ==========================================

def upload_utanexa_logo() -> dict:
    """
    Copy logo from xqbosxje account to configured
    destination account (usg5hlyz).

    Run this function once after configuring credentials.
    """

    _init_cloudinary()

    result = cloudinary.uploader.upload(
        LOGO_SOURCE_URL,
        public_id=LOGO_PUBLIC_ID,
        resource_type="image",
        overwrite=True,
        invalidate=True,
    )

    return {
        "success": True,
        "public_id": result.get("public_id"),
        "secure_url": result.get("secure_url"),
    }


# ==========================================
# BUILD WATERMARKED VIDEO URL
# ==========================================

# ==========================================
# BUILD WATERMARKED VIDEO URL
# ==========================================

def build_watermarked_video_url(
    public_id: str,
    username: str,
) -> str:
    """
    Generate watermarked video URL matching the UI exactly.
    Logo on the left with white border, White Text on the right with shadow.
    """
    if not public_id or not public_id.strip():
        raise ValueError("Video public_id is required")

    if not username or not username.strip():
        raise ValueError("Username is required")

    _init_cloudinary()

    safe_username = urllib.parse.quote(username.strip(), safe="")

    transformation = [
        # 1. Username Text Overlay (White Color + Dark Shadow)
        {
            "overlay": f"text:Arial_14_bold:{safe_username}",
            "color": "white",         # 👈 Text pure white
            "shadow": "80,2,2",       # 👈 Dark shadow (Opacity, X, Y) taaki bright bg pe dikhe
        },
        {
            "flags": "layer_apply",
            "gravity": "north_east",
            "x": 20,       # Right margin
            "y": 36,       # Vertically centered with logo
        },

        # 2. Logo Overlay with Border and Rounded Corners (Left Side)
        {
            "overlay": LOGO_PUBLIC_ID,
        },
        {
            "width": 34,   # Logo size
            "height": 34,
            "crop": "fit",
            "radius": 11,  # Rounded corners matching UI
            "border": "1.5px_solid_rgb:FFFFFF", # White border matching UI
            "flags": "layer_apply",
            "gravity": "north_east",
            "x": 160,      # Placed to the left, leaving space for text
            "y": 24,       # Top margin
        },

        # 3. Video Quality
        {
            "quality": "auto:good",
            "fetch_format": "auto",
        },
    ]

    video_url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        resource_type="video",
        type="upload",
        transformation=transformation,
        secure=True,
    )

    return video_url
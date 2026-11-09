
# =========================================================
# USANEX — CENTRAL CONFIG
# UPSTASH REDIS CONFIGURATION
# =========================================================

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# =========================================================
# LOAD ENVIRONMENT VARIABLES
# =========================================================

BASE_DIR = Path(__file__).resolve().parents[1]

ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)


# =========================================================
# SETTINGS CLASS
# =========================================================

class Settings:

    def __init__(self):

        # -------------------------------------------------
        # APPLICATION SETTINGS
        # -------------------------------------------------

        self.app_name = os.getenv(
            "APP_NAME",
            "Usanex"
        )

        self.environment = os.getenv(
            "ENVIRONMENT",
            "development"
        )

        self.secret_key = os.getenv(
            "SECRET_KEY",
            "change-me"
        )

        # -------------------------------------------------
        # DATABASE SETTINGS
        # -------------------------------------------------

        self.database_url = os.getenv(
            "DATABASE_URL",
            ""
        )

        # -------------------------------------------------
        # CORS SETTINGS
        # -------------------------------------------------

        self.cors_origins = os.getenv(
            "CORS_ORIGINS",
            "*"
        )

        # -------------------------------------------------
        # SESSION SETTINGS
        # -------------------------------------------------

        self.session_cookie_name = os.getenv(
            "SESSION_COOKIE_NAME",
            "usanex_session"
        )

        self.session_duration_days = int(
            os.getenv(
                "SESSION_DURATION_DAYS",
                "30"
            )
        )

        # -------------------------------------------------
        # CLOUDINARY SETTINGS
        # -------------------------------------------------

        self.cloudinary_url = os.getenv(
            "CLOUDINARY_URL",
            ""
        )

        self.cloudinary_cloud_name = os.getenv(
            "CLOUDINARY_CLOUD_NAME",
            ""
        )

        self.cloudinary_api_key = os.getenv(
            "CLOUDINARY_API_KEY",
            ""
        )

        self.cloudinary_api_secret = os.getenv(
            "CLOUDINARY_API_SECRET",
            ""
        )

        # -------------------------------------------------
        # UPSTASH REDIS SETTINGS
        # -------------------------------------------------

        self.redis_url = os.getenv(
            "REDIS_URL",
            ""
        ).strip()

        # Validate Redis URL format when configured
        if self.redis_url:

            if not self.redis_url.startswith(
                ("rediss://", "redis://")
            ):

                raise ValueError(
                    "Invalid REDIS_URL. "
                    "Use rediss:// for Upstash Redis."
                )

            if self.redis_url.startswith("redis://"):

                logger.warning(
                    "REDIS_URL is using redis:// without TLS. "
                    "For Upstash production, use rediss://."
                )

    # =====================================================
    # CORS ORIGINS LIST
    # =====================================================

    @property
    def cors_origins_list(self) -> list[str]:

        raw = self.cors_origins.strip()

        if raw == "*":
            return ["*"]

        return [
            item.strip()
            for item in raw.split(",")
            if item.strip()
        ]


# =========================================================
# GLOBAL SETTINGS INSTANCE
# =========================================================

settings = Settings()

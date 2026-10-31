# =========================================================
# USANEX — CENTRAL CONFIG
# =========================================================

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class Settings:
    def __init__(self):
        self.app_name = os.getenv("APP_NAME", "Usanex")
        self.environment = os.getenv("ENVIRONMENT", "development")
        self.secret_key = os.getenv("SECRET_KEY", "change-me")

        self.database_url = os.getenv("DATABASE_URL", "")

        self.cors_origins = os.getenv("CORS_ORIGINS", "*")

        self.session_cookie_name = os.getenv(
            "SESSION_COOKIE_NAME", "usanex_session"
        )
        self.session_duration_days = int(
            os.getenv("SESSION_DURATION_DAYS", "30")
        )

        self.cloudinary_url = os.getenv("CLOUDINARY_URL", "")
        self.cloudinary_cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
        self.cloudinary_api_key = os.getenv("CLOUDINARY_API_KEY", "")
        self.cloudinary_api_secret = os.getenv(
            "CLOUDINARY_API_SECRET", ""
        )

    @property
    def cors_origins_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [item.strip() for item in raw.split(",") if item.strip()]


settings = Settings()
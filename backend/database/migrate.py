
from sqlalchemy import inspect, text

from .database import engine
from . import models


def run_migrations():
    """
    Add missing columns to existing database tables.

    Existing tables/data are NOT deleted.
    """

    inspector = inspect(engine)

    for table_name, table in models.Base.metadata.tables.items():

        # Table doesn't exist yet.
        # server.py create_all() will create it.
        if not inspector.has_table(table_name):
            continue

        existing_columns = {
            column["name"]
            for column in inspector.get_columns(table_name)
        }

        for column in table.columns:

            if column.name in existing_columns:
                continue

            column_type = column.type.compile(
                dialect=engine.dialect
            )
        # Ensure existing Reel likes_count values are safe
    inspector = inspect(engine)

    if inspector.has_table("reels"):
        reel_columns = {
            column["name"]
            for column in inspector.get_columns("reels")
        }

        if "likes_count" in reel_columns:
            with engine.begin() as connection:
                connection.execute(text("""
                    UPDATE reels
                    SET likes_count = 0
                    WHERE likes_count IS NULL
                """))

                connection.execute(text("""
                    ALTER TABLE reels
                    ALTER COLUMN likes_count SET DEFAULT 0
                """))

                connection.execute(text("""
                    ALTER TABLE reels
                    ALTER COLUMN likes_count SET NOT NULL
                """))

    print("[Usanex Migration] Completed successfully.")

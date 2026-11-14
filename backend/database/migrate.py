
from sqlalchemy import inspect, text

from .database import engine
from . import models


def run_migrations():
    """
    Add missing columns and safely enforce Reel Like uniqueness.
    Never delete existing data automatically.
    """

    inspector = inspect(engine)

    for table_name, table in models.Base.metadata.tables.items():

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

            sql = f"""
                ALTER TABLE "{table_name}"
                ADD COLUMN "{column.name}" {column_type}
            """

            print(
                f"[Usanex Migration] Adding "
                f"{table_name}.{column.name}"
            )

            with engine.begin() as connection:
                connection.execute(text(sql))

    # Initialize Reel likes_count safely.
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

    # Ensure unique Like per user and Reel.
    inspector = inspect(engine)

    if inspector.has_table("reel_likes"):

        like_columns = {
            column["name"]
            for column in inspector.get_columns("reel_likes")
        }

        required_columns = {"user_id", "reel_id"}

        if required_columns.issubset(like_columns):

            constraints = inspector.get_unique_constraints(
                "reel_likes"
            )

            has_unique = any(
                set(item.get("column_names") or [])
                == required_columns
                for item in constraints
            )

            named = next(
                (
                    item for item in constraints
                    if item.get("name")
                    == "uq_reel_like_user_reel"
                ),
                None,
            )

            if named and set(
                named.get("column_names") or []
            ) != required_columns:
                raise RuntimeError(
                    "Existing Reel Like constraint has "
                    "unexpected columns. Manual review required."
                )

            if not has_unique:

                with engine.begin() as connection:

                    duplicate = connection.execute(text("""
                        SELECT user_id, reel_id, COUNT(*) AS row_count
                        FROM reel_likes
                        GROUP BY user_id, reel_id
                        HAVING COUNT(*) > 1
                        LIMIT 1
                    """)).first()

                    if duplicate:
                        raise RuntimeError(
                            "Duplicate Reel Likes found: "
                            f"user_id={duplicate[0]}, "
                            f"reel_id={duplicate[1]}. "
                            "No data was deleted. "
                            "Resolve duplicates manually."
                        )

                    connection.execute(text("""
                        DO $$
                        BEGIN
                            ALTER TABLE reel_likes
                            ADD CONSTRAINT uq_reel_like_user_reel
                            UNIQUE (user_id, reel_id);
                        EXCEPTION
                            WHEN duplicate_object THEN
                                NULL;
                        END
                        $$;
                    """))

                print(
                    "[Usanex Migration] Unique Reel Like "
                    "constraint ensured."
                )

            # Ensure lookup indexes.
            with engine.begin() as connection:

                connection.execute(text("""
                    CREATE INDEX IF NOT EXISTS
                    ix_reel_likes_reel_created
                    ON reel_likes (reel_id, created_at)
                """))

                connection.execute(text("""
                    CREATE INDEX IF NOT EXISTS
                    ix_reel_likes_user_created
                    ON reel_likes (user_id, created_at)
                """))

    print("[Usanex Migration] Completed successfully.")

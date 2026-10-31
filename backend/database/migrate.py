
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

            # Existing rows may already be present.
            # Therefore add missing columns as nullable first.
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

    print("[Usanex Migration] Completed successfully.")

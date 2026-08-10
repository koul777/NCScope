from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.db import Base, engine
import app.models  # noqa: F401


def _migrate_sqlite_question_quality_schema(bind: Engine) -> None:
    """Apply the additive migration needed by existing local databases.

    ``Base.metadata.create_all`` does not alter tables that already exist.
    NCScope is distributed as a local SQLite app without Alembic, so small
    backward-compatible migrations stay explicit and idempotent here.
    """
    if bind.dialect.name != "sqlite":
        return

    inspector = inspect(bind)
    if "question_quality_reviews" not in inspector.get_table_names():
        return
    if "active" in {column["name"] for column in inspector.get_columns("question_quality_reviews")}:
        return

    try:
        with bind.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE question_quality_reviews "
                    "ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1"
                )
            )
    except OperationalError:
        # Concurrent local workers may both observe the old schema. Treat a
        # column added by the other worker as success, but preserve real errors.
        refreshed = inspect(bind)
        columns = {column["name"] for column in refreshed.get_columns("question_quality_reviews")}
        if "active" not in columns:
            raise


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_question_quality_schema(engine)


if __name__ == "__main__":
    init_db()
    print("DB initialized")


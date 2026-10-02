from __future__ import annotations

import os
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from models.schema import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/video_analytics")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if missing, then add any columns the models gained since
    the tables were created. This covers the common "added a nullable or
    defaulted column" evolution without wiping data; renames, type changes
    and drops still need a real migration tool (Alembic)."""
    _wait_for_database()
    Base.metadata.create_all(engine)
    add_missing_columns()


def _wait_for_database(timeout_seconds: float = 30.0) -> None:
    """Containers often start before the database accepts connections.
    Retry for a short while instead of crashing on the first refusal."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with engine.connect():
                return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1.0)


def add_missing_columns() -> list[str]:
    """ALTER TABLE ... ADD COLUMN for model columns absent from the live DB.
    Returns the "table.column" names added."""
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=engine.dialect)}'
                if column.server_default is not None:
                    ddl += f" DEFAULT {column.server_default.arg!r}" if isinstance(column.server_default.arg, str) else ""
                conn.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
    return added


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

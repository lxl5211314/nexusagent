from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    _settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)

if _settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from app.db import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)
    _ensure_tenant_columns()


# Columns added after v0.1 shipped: existing databases need a forward migration.
# ``create_all`` only creates missing tables, it never alters an existing one.
_TENANT_TABLES = ("knowledge_bases", "runs", "tool_invocations", "eval_runs")


def _ensure_tenant_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in _TENANT_TABLES:
            if table not in tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table)}
            if "tenant_id" in present:
                continue
            conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN tenant_id VARCHAR(32) DEFAULT 'local'")
            )
            conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant_id ON {table} (tenant_id)")
            )

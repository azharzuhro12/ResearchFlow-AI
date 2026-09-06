"""Database engine, session management, and startup bootstrap (Step 9).

SQLite is the persistent source of truth for schedules, execution history,
and report metadata. APScheduler stays a pure runtime engine: its jobs are
rebuilt from the database on every startup (no persistent jobstore).

Rules enforced here:
- The engine is created lazily from config.DATABASE_URL (tests point it at
  a temporary file; production defaults to <project>/data/researchflow.db).
- ``create_all`` only ever CREATES missing tables — this module never
  drops, recreates, or deletes a database.
- SQLite foreign keys are ON (required for ON DELETE SET NULL history
  preservation) and WAL journaling improves concurrent read behaviour.
- Sessions are short-lived: open → commit/rollback → close around each
  unit of work. No session is ever held across GLM/web/embedding/report/
  Discord calls.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core import config
from app.database.models import Base

logger = logging.getLogger(__name__)

_SQLITE_URL_PREFIX = "sqlite:///"


class PersistenceError(Exception):
    """The database could not be reached or written.

    `message` is client-safe: no SQL statements, no filesystem paths, no
    stack traces, no connection strings.
    """

    def __init__(self, message: str = "Storage is temporarily unavailable.") -> None:
        super().__init__(message)
        self.message = message


# Process-wide engine + session factory (created lazily, reset only in
# tests — see reset_engine_for_tests).
_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    """The process-wide engine; tables are created idempotently on build."""
    global _engine, _session_factory
    if _engine is None:
        url = config.DATABASE_URL
        _ensure_sqlite_parent_dir(url)
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False}
            if url.startswith("sqlite")
            else {},
        )
        if engine.dialect.name == "sqlite":
            _install_sqlite_pragmas(engine)
        # Idempotent bootstrap (CREATE TABLE IF NOT EXISTS semantics):
        # never drops or alters existing tables.
        Base.metadata.create_all(engine)
        _engine = engine
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        logger.info("Database ready (SQLite persistence enabled)")
    return _engine


def get_session_factory() -> sessionmaker:
    """Session factory bound to the process engine."""
    get_engine()
    assert _session_factory is not None  # created together with the engine
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """One short transaction: commit on success, rollback on error.

    SQLAlchemy problems are converted to a client-safe PersistenceError;
    everything else propagates after a rollback.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        logger.warning("Database operation failed and was rolled back")
        raise PersistenceError() from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_database() -> None:
    """Startup hook — build the engine, directory, and tables (idempotent).

    Called before the scheduler starts so persisted schedules can be
    restored (see scheduler_lifespan). Safe to call repeatedly.
    """
    get_engine()


def reset_engine_for_tests() -> None:
    """Dispose and forget the cached engine (test isolation ONLY).

    Combined with pointing config.DATABASE_URL at a temporary file, this
    guarantees pytest never touches the production data/researchflow.db.
    """
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def to_db(value: datetime | None) -> datetime | None:
    """Aware datetime → naive UTC for storage (SQLite has no tz type)."""
    if value is None:
        return None
    if value.tzinfo is None:  # defensive — callers pass aware values
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def from_db(value: datetime | None) -> datetime | None:
    """Stored naive UTC → timezone-aware UTC datetime."""
    if value is None:
        return None
    if value.tzinfo is not None:  # defensive — stored values are naive
        return value.astimezone(UTC)
    return value.replace(tzinfo=UTC)


def _ensure_sqlite_parent_dir(url: str) -> None:
    """Create the parent directory of a file-backed SQLite database."""
    if not url.startswith(_SQLITE_URL_PREFIX):
        return
    path = Path(url[len(_SQLITE_URL_PREFIX) :])
    parent = path.parent
    if str(parent) not in ("", "/") and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)


def _install_sqlite_pragmas(engine: Engine) -> None:
    """Per-connection SQLite pragmas (foreign keys + WAL journaling)."""

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

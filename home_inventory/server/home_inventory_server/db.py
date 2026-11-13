"""Engine/session management and migrations."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

_LOGGER = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _pnkey(value):
    if value is None:
        return None
    return "".join(ch for ch in str(value).lower() if ch.isascii() and ch.isalnum())


def make_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
        # Part numbers compared without spaces, dashes or case ("AB-12 3" = "ab123").
        dbapi_conn.create_function("hinv_pnkey", 1, _pnkey, deterministic=True)

    return engine


def alembic_config(url: str) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(url)).get_current_head()


def run_migrations(engine: Engine, db_path: Path | None, backup_dir: Path | None) -> None:
    """Upgrade the schema to head, snapshotting the database first.

    The snapshot is taken with SQLite's online backup API so it is
    consistent even though the database runs in WAL mode.
    """
    url = str(engine.url)
    current = current_revision(engine)
    head = head_revision(url)
    if current == head:
        return
    if db_path and db_path.exists() and current is not None and backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(backup_dir.glob(f"pre-migration-{current}-*.sqlite3"))
        if existing:
            # Keep the first snapshot taken from this revision: if a previous
            # attempt failed, later snapshots would not be pristine.
            _LOGGER.info("Keeping existing pre-migration snapshot %s", existing[0])
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            target = backup_dir / f"pre-migration-{current}-{stamp}.sqlite3"
            snapshot_sqlite(engine, target)
            _LOGGER.info("Database snapshot before migration written to %s", target)
            _prune(backup_dir, "pre-migration-", keep=5)
    _LOGGER.info("Migrating database %s -> %s", current, head)
    _upgrade_atomically(url)


def _upgrade_atomically(url: str) -> None:
    """Run all pending migrations in ONE transaction with foreign keys off.

    * Python's sqlite3 driver commits before each DDL statement by default,
      so a failing migration could leave earlier steps applied while the
      revision stays unchanged.  Taking manual control of BEGIN/COMMIT makes
      SQLite's DDL fully transactional: a failure rolls everything back.
    * SQLite rebuilds tables for some schema changes; with foreign keys on,
      dropping the old table would fire ON DELETE actions or fail.  They are
      switched off for the migration (the documented SQLite procedure) and
      the result is verified with ``PRAGMA foreign_key_check``.
    """
    mig_engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(mig_engine, "connect")
    def _connect(dbapi_conn, _record):  # pragma: no cover - trivial
        dbapi_conn.isolation_level = None  # driver must not manage transactions
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
        # Part numbers compared without spaces, dashes or case ("AB-12 3" = "ab123").
        dbapi_conn.create_function("hinv_pnkey", 1, _pnkey, deterministic=True)

    @event.listens_for(mig_engine, "begin")
    def _begin(conn):  # pragma: no cover - trivial
        conn.exec_driver_sql("BEGIN")

    cfg = alembic_config(url)
    try:
        with mig_engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            problems = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError(f"Migration left broken references: {problems[:5]}")
    finally:
        mig_engine.dispose()


def snapshot_sqlite(engine: Engine, target: Path) -> None:
    import sqlite3

    target.parent.mkdir(parents=True, exist_ok=True)
    raw = engine.raw_connection()
    try:
        src: sqlite3.Connection = raw.driver_connection  # type: ignore[assignment]
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        raw.close()


def _prune(directory: Path, prefix: str, keep: int) -> None:
    files = sorted(directory.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink(missing_ok=True)


class Database:
    def __init__(self, url: str) -> None:
        self.engine = make_engine(url)
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Iterator[Session]:
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def dispose(self) -> None:
        self.engine.dispose()


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

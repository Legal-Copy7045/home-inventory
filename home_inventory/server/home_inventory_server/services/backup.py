"""Portable export / import of the complete inventory.

Home Assistant backups are the primary mechanism (the App's ``/data``
directory — database and media — is included in every Home Assistant
backup).  This module adds an administrator-initiated archive that can be
downloaded from Settings and restored into a fresh installation, e.g. when
moving between Home Assistant OS and a Container install.

Archive layout (zip)::

    manifest.json            format version, schema revision, created_at
    inventory.sqlite3        consistent snapshot via SQLite backup API
    media/...                photos and documents

Restoring treats the archive as untrusted input:

1. The zip directory is checked before anything is extracted: allow-listed
   names only, no absolute/traversal/backslash paths, no symlinks or special
   files, no duplicates, limited entry count, per-file and total sizes and
   compression ratio (zip bombs).
2. Entries are extracted by streaming into a staging directory next to the
   live data, counting the real bytes written (header sizes can lie).
3. The manifest and database are validated (SQLite header, integrity and
   foreign keys, required tables, a schema revision this version knows),
   then the staged database is upgraded to the current schema - still in
   staging.
4. Only then is the current data exported to a safety archive and swapped
   with the staged data by renames on the same filesystem.  If anything
   fails during the swap, including re-opening the database, the previous
   data is put back.  Staging and rollback directories are always removed.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import Engine

from ..db import alembic_config, current_revision, make_engine, run_migrations, snapshot_sqlite

_LOGGER = logging.getLogger(__name__)

FORMAT = "home-inventory-backup"
FORMAT_VERSION = 1
MB = 1024 * 1024

REQUIRED_TABLES = {"alembic_version", "users", "locations", "containers", "products", "items", "attachments"}
_MEDIA_NAME = re.compile(r"^media/(?:[A-Za-z0-9_-][A-Za-z0-9._-]*/)*[A-Za-z0-9_-][A-Za-z0-9._-]*/?$")


@dataclass(frozen=True)
class Limits:
    """Archive limits; ``for_upload`` derives them from the upload limit."""

    max_upload: int = 2048 * MB
    max_total: int = 4096 * MB
    max_entries: int = 200_000
    max_database: int = 2048 * MB
    max_media_file: int = 200 * MB
    max_manifest: int = 64 * 1024
    max_ratio: int = 100  # uncompressed / compressed, for entries over ratio_floor
    ratio_floor: int = 10 * MB

    @classmethod
    def for_upload(cls, max_upload_mb: int) -> Limits:
        upload = max_upload_mb * MB
        return cls(max_upload=upload, max_total=max(2 * upload, 1024 * MB), max_database=max(upload, 256 * MB))


def export_archive(engine: Engine, media_dir: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "inventory.sqlite3"
        snapshot_sqlite(engine, snap)
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "schema_revision": current_revision(engine),
            "created_at": datetime.now(UTC).isoformat(),
        }
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(snap, "inventory.sqlite3")
            if media_dir.exists():
                for f in sorted(media_dir.rglob("*")):
                    if f.is_file() and not f.is_symlink():
                        z.write(f, f"media/{f.relative_to(media_dir).as_posix()}", compress_type=zipfile.ZIP_STORED)
    return target


class RestoreError(ValueError):
    pass


def _known_revisions() -> set[str]:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    return {rev.revision for rev in script.walk_revisions()}


def _check_entry(info: zipfile.ZipInfo, limits: Limits) -> str:
    """Return the entry's role ('manifest', 'database', 'media', 'dir') or raise."""
    name = info.filename
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise RestoreError("Archive contains unsafe paths")
    parts = PurePosixPath(name).parts
    if any(p in ("..", ".") for p in parts):
        raise RestoreError("Archive contains unsafe paths")
    mode = info.external_attr >> 16
    if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise RestoreError("Archive contains links or special files")
    if info.flag_bits & 0x1:
        raise RestoreError("Encrypted archives are not supported")
    if name == "manifest.json":
        role, cap = "manifest", limits.max_manifest
    elif name == "inventory.sqlite3":
        role, cap = "database", limits.max_database
    elif _MEDIA_NAME.match(name):
        role, cap = ("dir", 0) if name.endswith("/") else ("media", limits.max_media_file)
    else:
        raise RestoreError(f"Unexpected file in archive: {name[:80]}")
    if info.file_size > cap:
        raise RestoreError(f"File in archive is too large: {name[:80]}")
    if info.file_size > limits.ratio_floor and info.file_size > limits.max_ratio * max(info.compress_size, 1):
        raise RestoreError("Archive is suspiciously compressed (possible zip bomb)")
    return role


def validate_archive(path: Path, limits: Limits | None = None) -> dict:
    """Check the archive's directory and manifest without extracting it."""
    limits = limits or Limits()
    if path.stat().st_size > limits.max_upload:
        raise RestoreError(f"Backup is larger than the {limits.max_upload // MB} MB limit")
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as err:
        raise RestoreError("Not a Home Inventory backup archive") from err
    with z:
        infos = z.infolist()
        if len(infos) > limits.max_entries:
            raise RestoreError("Archive contains too many files")
        seen: set[str] = set()
        total = 0
        roles: dict[str, int] = {}
        for info in infos:
            if info.filename in seen:
                raise RestoreError("Archive contains duplicate entries")
            seen.add(info.filename)
            role = _check_entry(info, limits)
            roles[role] = roles.get(role, 0) + 1
            total += info.file_size
            if total > limits.max_total:
                raise RestoreError("Archive contents are too large")
        if roles.get("manifest") != 1 or roles.get("database") != 1:
            raise RestoreError("Archive is missing manifest or database")
        try:
            manifest = json.loads(z.read("manifest.json"))
        except (ValueError, UnicodeDecodeError) as err:
            raise RestoreError("Backup manifest is not valid") from err
    return _check_manifest(manifest)


def _check_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise RestoreError("Unrecognised archive format")
    version = manifest.get("format_version")
    if not isinstance(version, int) or version < 1:
        raise RestoreError("Backup manifest is not valid")
    if version > FORMAT_VERSION:
        raise RestoreError("This backup was made by a newer version of Home Inventory; update the App first")
    revision = manifest.get("schema_revision")
    if not isinstance(revision, str) or not revision:
        raise RestoreError("Backup manifest is not valid")
    if revision not in _known_revisions():
        raise RestoreError("This backup was made by a newer version of Home Inventory; update the App first")
    return manifest


def _extract(path: Path, staging: Path, limits: Limits) -> None:
    """Stream allow-listed entries into ``staging``, enforcing real sizes."""
    written = 0
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            role = _check_entry(info, limits)
            target = staging / PurePosixPath(info.filename)
            if staging.resolve() not in target.resolve().parents:
                raise RestoreError("Archive contains unsafe paths")
            if role == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            cap = limits.max_manifest if role == "manifest" else limits.max_database if role == "database" else limits.max_media_file
            target.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with z.open(info) as src, open(target, "xb") as dst:
                while chunk := src.read(MB):
                    size += len(chunk)
                    written += len(chunk)
                    if size > cap or written > limits.max_total:
                        raise RestoreError("Archive contents are larger than declared")
                    dst.write(chunk)


def _check_database(db_file: Path, manifest: dict) -> None:
    with open(db_file, "rb") as fh:
        if fh.read(16) != b"SQLite format 3\x00":
            raise RestoreError("Backup database is not a SQLite database")
    try:
        con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise RestoreError("Backup database cannot be opened") from err
    try:
        ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise RestoreError("Backup database failed integrity check")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED_TABLES <= tables:
            raise RestoreError("Backup database is incomplete")
        if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RestoreError("Backup database has broken references")
        rows = con.execute("SELECT version_num FROM alembic_version").fetchall()
        if [r[0] for r in rows] != [manifest["schema_revision"]]:
            raise RestoreError("Backup database does not match its manifest")
    except sqlite3.DatabaseError as err:
        raise RestoreError("Backup database is damaged") from err
    finally:
        con.close()


def _prepare(path: Path, staging: Path, limits: Limits) -> dict:
    manifest = validate_archive(path, limits)
    _extract(path, staging, limits)
    db_file = staging / "inventory.sqlite3"
    _check_database(db_file, manifest)
    # Bring the staged copy to the current schema before it goes live.
    engine = make_engine(f"sqlite:///{db_file}")
    try:
        run_migrations(engine, None, None)
    except Exception as err:
        raise RestoreError("Backup database could not be upgraded to this version") from err
    finally:
        engine.dispose()
    (staging / "media").mkdir(exist_ok=True)
    return manifest


_DB_SUFFIXES = ("", "-wal", "-shm")


def restore_archive(
    path: Path,
    db_path: Path,
    media_dir: Path,
    backup_dir: Path,
    engine: Engine,
    reopen: Callable[[], None] | None = None,
    limits: Limits | None = None,
) -> dict:
    """Replace the live database and media with the archive contents.

    ``reopen`` re-opens the application's database after the swap; if it
    fails, the previous data is restored and re-opened instead.  When it is
    omitted the caller must reconnect afterwards.
    """
    limits = limits or Limits()
    data_dir = db_path.parent
    staging = Path(tempfile.mkdtemp(prefix=".restore-staging-", dir=data_dir))
    rollback = Path(tempfile.mkdtemp(prefix=".restore-rollback-", dir=data_dir))
    swapped = False
    try:
        manifest = _prepare(path, staging, limits)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safety = export_archive(engine, media_dir, backup_dir / f"before-restore-{stamp}.zip")

        engine.dispose()
        try:
            for suffix in _DB_SUFFIXES:
                live = Path(f"{db_path}{suffix}")
                if live.exists():
                    live.rename(rollback / f"db{suffix}")
            if media_dir.exists():
                media_dir.rename(rollback / "media")
            swapped = True
            for suffix in _DB_SUFFIXES:
                staged = staging / f"inventory.sqlite3{suffix}"
                if staged.exists():
                    staged.rename(Path(f"{db_path}{suffix}"))
            (staging / "media").rename(media_dir)
            if reopen is not None:
                reopen()
        except Exception as err:
            if swapped:
                _LOGGER.exception("Restore failed while switching data; putting the previous data back")
                _rollback(rollback, db_path, media_dir)
                if reopen is not None:
                    reopen()
            raise RestoreError("Restore failed; your previous data has been kept") from err
        return {"restored": manifest, "safety_backup": safety.name}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(rollback, ignore_errors=True)


def _rollback(rollback: Path, db_path: Path, media_dir: Path) -> None:
    for suffix in _DB_SUFFIXES:
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    if media_dir.exists():
        shutil.rmtree(media_dir)
    for suffix in _DB_SUFFIXES:
        saved = rollback / f"db{suffix}"
        if saved.exists():
            saved.rename(Path(f"{db_path}{suffix}"))
    if (rollback / "media").exists():
        (rollback / "media").rename(media_dir)
    else:
        media_dir.mkdir(parents=True, exist_ok=True)

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
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from ..db import current_revision, snapshot_sqlite

FORMAT = "home-inventory-backup"
FORMAT_VERSION = 1


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
                    if f.is_file():
                        z.write(f, f"media/{f.relative_to(media_dir).as_posix()}", compress_type=zipfile.ZIP_STORED)
    return target


class RestoreError(ValueError):
    pass


def validate_archive(path: Path) -> dict:
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as err:
        raise RestoreError("Not a Home Inventory backup archive") from err
    with z:
        names = set(z.namelist())
        if "manifest.json" not in names or "inventory.sqlite3" not in names:
            raise RestoreError("Archive is missing manifest or database")
        manifest = json.loads(z.read("manifest.json"))
        if manifest.get("format") != FORMAT:
            raise RestoreError("Unrecognised archive format")
        for n in names:
            p = Path(n)
            if p.is_absolute() or ".." in p.parts:
                raise RestoreError("Archive contains unsafe paths")
    return manifest


def restore_archive(path: Path, db_path: Path, media_dir: Path, backup_dir: Path, engine: Engine) -> dict:
    """Replace the live database and media with the archive contents.

    The current data is first exported to ``backup_dir`` so a restore can be
    undone.  The caller must dispose the engine afterwards (the App restarts
    its database connection).
    """
    manifest = validate_archive(path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safety = export_archive(engine, media_dir, backup_dir / f"before-restore-{stamp}.zip")
    engine.dispose()
    with tempfile.TemporaryDirectory(dir=db_path.parent) as tmp:
        tmpdir = Path(tmp)
        with zipfile.ZipFile(path) as z:
            z.extractall(tmpdir)
        # integrity check on the incoming database
        con = sqlite3.connect(tmpdir / "inventory.sqlite3")
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            con.close()
        if ok != "ok":
            raise RestoreError("Backup database failed integrity check")
        for suffix in ("", "-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        shutil.move(str(tmpdir / "inventory.sqlite3"), db_path)
        new_media = tmpdir / "media"
        old_media = media_dir.with_name(media_dir.name + ".old")
        if old_media.exists():
            shutil.rmtree(old_media)
        if media_dir.exists():
            media_dir.rename(old_media)
        if new_media.exists():
            shutil.move(str(new_media), media_dir)
        else:
            media_dir.mkdir(parents=True, exist_ok=True)
        if old_media.exists():
            shutil.rmtree(old_media)
    return {"restored": manifest, "safety_backup": safety.name}

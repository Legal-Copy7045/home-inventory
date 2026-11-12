"""Backup restore treats archives as untrusted input."""

from __future__ import annotations

import io
import json
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from home_inventory_server.services import backup as backup_svc


def _export(env) -> bytes:
    env.admin.location("Shed")
    r = env.admin.get("/api/v1/backup/export")
    assert r.status_code == 200
    return r.content


def _rebuild(original: bytes, drop=(), add=(), manifest=None, db_bytes=None) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(original))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            if info.filename in drop:
                continue
            data = src.read(info)
            if info.filename == "manifest.json" and manifest is not None:
                data = json.dumps(manifest).encode()
            if info.filename == "inventory.sqlite3" and db_bytes is not None:
                data = db_bytes
            z.writestr(info.filename, data)
        for item in add:
            if isinstance(item, zipfile.ZipInfo):
                z.writestr(item, b"x")
            else:
                name, data = item
                z.writestr(name, data)
    return out.getvalue()


def _restore(env, data: bytes):
    return env.admin.post("/api/v1/backup/restore", files={"file": ("b.zip", data, "application/zip")})


def _locations(env):
    return [loc["name"] for loc in env.admin.ok("get", "/api/v1/locations")]


def _manifest(original: bytes) -> dict:
    return json.loads(zipfile.ZipFile(io.BytesIO(original)).read("manifest.json"))


def test_valid_restore_still_works(env):
    archive = _export(env)
    env.admin.location("Garage")
    assert _restore(env, archive).status_code == 200
    names = str(_locations(env))
    assert "Shed" in names and "Garage" not in names


def test_restore_is_admin_only(env):
    archive = _export(env)
    member = env.user("u-sam", "Sam Member")
    member.get("/api/v1/me")
    r = member.post("/api/v1/backup/restore", files={"file": ("b.zip", archive, "application/zip")})
    assert r.status_code == 403


def test_oversized_upload_rejected_before_parsing(env):
    env.settings.backup_max_mb = 1
    big = b"0" * (2 * 1024 * 1024)
    r = _restore(env, big)
    assert r.status_code == 413
    assert "limit" in r.json()["detail"]


def test_ordinary_uploads_limited_too(env):
    env.settings.max_upload_mb = 1
    r = env.admin.post("/api/v1/attachments", files={"file": ("x.jpg", b"0" * (3 * 1024 * 1024))}, data={"kind": "photo"})
    assert r.status_code == 413


@pytest.mark.parametrize(
    "name",
    ["../escape.txt", "/etc/passwd", "media/../../x", "media\\..\\x", "C:/x", "notes.txt", "media/.hidden/../../a"],
)
def test_unsafe_or_unexpected_paths_rejected(env, name):
    archive = _rebuild(_export(env), add=[(name, b"x")])
    r = _restore(env, archive)
    assert r.status_code == 422, name
    assert "Shed" in str(_locations(env))  # nothing changed


def test_symlink_rejected(env):
    info = zipfile.ZipInfo("media/aa/link.jpg")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    r = _restore(env, _rebuild(_export(env), add=[info]))
    assert r.status_code == 422 and "links" in r.json()["detail"]


def test_zip_bomb_rejected(env):
    bomb = b"\0" * (30 * 1024 * 1024)  # compresses ~1000:1
    r = _restore(env, _rebuild(_export(env), add=[("media/aa/bomb.jpg", bomb)]))
    assert r.status_code == 422 and "zip bomb" in r.json()["detail"]


def test_too_many_entries_and_total_size(env, tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(_rebuild(_export(env), add=[(f"media/aa/{i}.jpg", b"x") for i in range(20)]))
    with pytest.raises(backup_svc.RestoreError, match="too many"):
        backup_svc.validate_archive(archive, backup_svc.Limits(max_entries=10))
    with pytest.raises(backup_svc.RestoreError, match="too large"):
        backup_svc.validate_archive(archive, backup_svc.Limits(max_total=1000))


def test_lying_header_sizes_caught_while_extracting(env, tmp_path, monkeypatch):
    real_check = backup_svc._check_entry
    # Pretend the header passed (as a forged size field would), so only the
    # byte counting during extraction can catch the oversized file.
    monkeypatch.setattr(backup_svc, "_check_entry", lambda info, limits: real_check(info, backup_svc.Limits()))
    archive = tmp_path / "a.zip"
    archive.write_bytes(_rebuild(_export(env), add=[("media/aa/big.jpg", b"y" * 5000)]))
    # Simulate a header that under-reports: extraction enforces the real size.
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(backup_svc.RestoreError, match="larger than declared"):
        backup_svc._extract(archive, staging, backup_svc.Limits(max_media_file=4000, max_total=10**9, ratio_floor=10**9))


def test_missing_files_and_bad_manifest(env):
    original = _export(env)
    assert _restore(env, _rebuild(original, drop=["inventory.sqlite3"])).status_code == 422
    assert _restore(env, _rebuild(original, drop=["manifest.json"])).status_code == 422
    m = _manifest(original)
    assert _restore(env, _rebuild(original, manifest={**m, "format": "other"})).status_code == 422
    newer = _restore(env, _rebuild(original, manifest={**m, "format_version": 99}))
    assert newer.status_code == 422 and "newer" in newer.json()["detail"]
    future = _restore(env, _rebuild(original, manifest={**m, "schema_revision": "9999_future"}))
    assert future.status_code == 422 and "newer" in future.json()["detail"]
    assert _restore(env, _rebuild(original, manifest=["not", "a", "dict"])).status_code == 422
    assert _restore(env, b"not a zip").status_code == 422
    assert "Shed" in str(_locations(env))


def test_malformed_and_incomplete_databases(env, tmp_path):
    original = _export(env)
    assert _restore(env, _rebuild(original, db_bytes=b"garbage" * 100)).status_code == 422
    # a valid SQLite file that isn't an inventory
    other = tmp_path / "other.sqlite3"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE notes (x)")
    con.commit()
    con.close()
    r = _restore(env, _rebuild(original, db_bytes=other.read_bytes()))
    assert r.status_code == 422 and "incomplete" in r.json()["detail"]
    # revision in the database disagrees with the manifest
    db_file = tmp_path / "inv.sqlite3"
    db_file.write_bytes(zipfile.ZipFile(io.BytesIO(original)).read("inventory.sqlite3"))
    con = sqlite3.connect(db_file)
    con.execute("UPDATE alembic_version SET version_num='0001'")
    con.commit()
    con.close()
    r = _restore(env, _rebuild(original, db_bytes=db_file.read_bytes()))
    assert r.status_code == 422 and "manifest" in r.json()["detail"]
    assert "Shed" in str(_locations(env))


def test_failed_swap_rolls_back_and_cleans_up(env, monkeypatch):
    archive = _export(env)
    env.admin.location("Garage")
    calls = {"n": 0}
    real_reopen = env.state.reopen_database

    def flaky_reopen():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated failure opening restored database")
        real_reopen()

    monkeypatch.setattr(env.state, "reopen_database", flaky_reopen)
    env.ingress_app.state.reopen_database = flaky_reopen
    r = _restore(env, archive)
    assert r.status_code == 422 and "previous data has been kept" in r.json()["detail"]
    names = str(_locations(env))
    assert "Garage" in names  # still the pre-restore data
    data_dir: Path = env.settings.data_dir
    assert not [p.name for p in data_dir.iterdir() if p.name.startswith(".restore-")]
    assert list(env.settings.backup_dir.glob("before-restore-*.zip"))


def test_temporary_files_removed_after_rejection(env):
    _restore(env, b"not a zip")
    _restore(env, _rebuild(_export(env), add=[("../x", b"x")]))
    assert not [p.name for p in env.settings.data_dir.iterdir() if p.name.startswith(".restore-")]

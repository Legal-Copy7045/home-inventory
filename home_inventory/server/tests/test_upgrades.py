"""Upgrade safety: realistic 0.1.1 databases, recovery from a half-applied
0.1.2/0.1.3 upgrade, atomic migrations and snapshot retention."""

from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, text

from home_inventory_server import db as db_mod
from home_inventory_server.db import Database, alembic_config, current_revision, head_revision, run_migrations

NOW = "2026-11-11 00:00:00"


def _db_at_0001_with_data(path: Path) -> Database:
    url = f"sqlite:///{path}"
    d = Database(url)
    cfg = alembic_config(url)
    with d.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
    with d.engine.begin() as c:
        c.execute(text("INSERT INTO locations (id,name,sort_order,created_at,updated_at) VALUES "
                       "(1,'Shed',0,:t,:t),(2,'Rear Right Shelving',0,:t,:t),(3,'Middle Shelf',0,:t,:t)"), {"t": NOW})
        c.execute(text("UPDATE locations SET parent_id=1 WHERE id=2"))
        c.execute(text("UPDATE locations SET parent_id=2 WHERE id=3"))
        c.execute(text("INSERT INTO containers (id,qr_code,name,number,location_id,temporarily_moved,created_at,updated_at) "
                       "VALUES (1,'ABCD2345','Hoses','1',3,0,:t,:t)"), {"t": NOW})
        c.execute(text("INSERT INTO products (id,name,identification_status,created_at,updated_at) VALUES (1,'Hose','manual',:t,:t)"), {"t": NOW})
        c.execute(text("INSERT INTO items (id,product_id,quantity,status,container_id,location_id,created_at,updated_at) VALUES "
                       "(1,1,4,'stored',1,NULL,:t,:t),(2,1,1,'stored',NULL,3,:t,:t)"), {"t": NOW})
        c.execute(text("INSERT INTO attachments (id,kind,location_id,stored_path,mime_type,size_bytes,sha256,created_at) "
                       "VALUES (1,'photo',2,'ab/x.jpg','image/jpeg',10,'0',:t)"), {"t": NOW})
    return d


def _state(d: Database):
    with d.engine.connect() as c:
        return {
            "items": c.execute(text("SELECT id, container_id, location_id, quantity FROM items ORDER BY id")).fetchall(),
            "containers": c.execute(text("SELECT id, location_id FROM containers")).fetchall(),
            "locations": c.execute(text("SELECT id, parent_id, name FROM locations ORDER BY id")).fetchall(),
            "attachments": c.execute(text("SELECT id, location_id FROM attachments")).fetchall(),
        }


def test_upgrade_from_0_1_1_with_nested_locations(tmp_path):
    """The case that broke on a real install: nested locations with
    containers and items in them."""
    d = _db_at_0001_with_data(tmp_path / "inv.sqlite3")
    before = _state(d)
    run_migrations(d.engine, tmp_path / "inv.sqlite3", tmp_path / "b")
    assert current_revision(d.engine) == head_revision(str(d.engine.url))
    assert _state(d) == before  # nothing moved, nulled or deleted
    with d.engine.connect() as c:
        codes = [r[0] for r in c.execute(text("SELECT qr_code FROM locations"))]
        assert all(len(x) == 8 for x in codes) and len(set(codes)) == 3
        assert "ABCD2345" not in codes
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1  # app connections still enforce FKs
    d.dispose()


def test_recovers_from_half_applied_upgrade(tmp_path):
    """0.1.2/0.1.3 could leave products.url and locations.qr_code added while
    the revision stayed at 0001; every restart then failed with
    'duplicate column name: url'."""
    d = _db_at_0001_with_data(tmp_path / "inv.sqlite3")
    with d.engine.begin() as c:
        c.execute(text("ALTER TABLE products ADD COLUMN url VARCHAR(2000)"))
        c.execute(text("ALTER TABLE locations ADD COLUMN qr_code VARCHAR(16)"))
        c.execute(text("UPDATE locations SET qr_code='QQQQ2222' WHERE id=1"))
        c.execute(text("UPDATE locations SET qr_code='ABCD2345' WHERE id=2"))  # clashes with a container code
    before = _state(d)
    run_migrations(d.engine, tmp_path / "inv.sqlite3", tmp_path / "b")
    assert current_revision(d.engine) == head_revision(str(d.engine.url))
    assert _state(d) == before
    with d.engine.connect() as c:
        codes = dict(c.execute(text("SELECT id, qr_code FROM locations")).fetchall())
    assert codes[1] == "QQQQ2222"  # kept
    assert codes[3] and len(set(codes.values())) == 3
    idx = {i["name"]: i for i in inspect(d.engine).get_indexes("locations")}
    assert idx["ix_locations_qr_code"]["unique"]
    d.dispose()


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    d = _db_at_0001_with_data(tmp_path / "inv.sqlite3")
    real_upgrade = command.upgrade

    def broken_upgrade(cfg, rev):
        conn = cfg.attributes["connection"]
        conn.exec_driver_sql("ALTER TABLE products ADD COLUMN half_done TEXT")
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(db_mod.command, "upgrade", broken_upgrade)
    for _ in range(3):  # e.g. watchdog restarts
        with pytest.raises(RuntimeError):
            run_migrations(d.engine, tmp_path / "inv.sqlite3", tmp_path / "b")
    cols = {c["name"] for c in inspect(d.engine).get_columns("products")}
    assert "half_done" not in cols  # DDL rolled back with the transaction
    assert current_revision(d.engine) == "0001"
    snaps = list((tmp_path / "b").glob("pre-migration-0001-*.sqlite3"))
    assert len(snaps) == 1  # the pristine snapshot is kept, not replaced each restart
    monkeypatch.setattr(db_mod.command, "upgrade", real_upgrade)
    run_migrations(d.engine, tmp_path / "inv.sqlite3", tmp_path / "b")
    assert current_revision(d.engine) == head_revision(str(d.engine.url))
    d.dispose()

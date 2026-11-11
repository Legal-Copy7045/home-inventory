"""Expiry & consumables, visual maps, events, dashboard, backup/restore,
migrations and upgrade persistence."""

import asyncio
import io
import sqlite3
import zipfile
from datetime import date, timedelta

from sqlalchemy import inspect

from home_inventory_server.db import Database, current_revision, head_revision, run_migrations
from home_inventory_server.models import Base
from home_inventory_server.services import stats as stats_svc
from tests.conftest import Env

# ---------------------------------------------------------------- expiry & stock


def test_expiry_dates(env):
    u = env.admin
    today = date.today()
    pantry = u.location("Pantry")
    milk = u.item("Tinned tomatoes", quantity=6, location_id=pantry["id"], expires_on=(today + timedelta(days=5)).isoformat())
    u.item("Paracetamol", expires_on=(today - timedelta(days=2)).isoformat())
    u.item("Rice", expires_on=(today + timedelta(days=300)).isoformat())
    soon = u.ok("get", "/api/v1/expiring", params={"days": 14})
    assert [i["name"] for i in soon] == ["Paracetamol", "Tinned tomatoes"]
    assert soon[0]["expired"] is True and soon[1]["days_to_expiry"] == 5
    st = u.ok("get", "/api/v1/stats")
    assert st["expired"] == 1 and st["expiring_soon"] == 1
    dash = u.ok("get", "/api/v1/dashboard")
    assert len(dash["expiring"]) == 2
    r = u.ok("get", "/api/v1/search", params={"q": "what is expiring soon"})
    assert r["interpretation"]["intent"] == "expiry" and r["total_matches"] == 3
    # notifier fires once
    db = env.state.db.SessionLocal()
    assert stats_svc.expiry_check(db, 14) == 2
    assert stats_svc.expiry_check(db, 14) == 0
    db.close()
    # changing the date re-arms the notice
    u.ok("patch", f"/api/v1/items/{milk['id']}", json={"expires_on": (today + timedelta(days=3)).isoformat()})
    db = env.state.db.SessionLocal()
    assert stats_svc.expiry_check(db, 14) == 1
    db.close()


def test_low_stock_and_quick_adjust(env):
    u = env.admin
    events = []
    env.state.bus.publish = lambda t, d: events.append((t, d))
    a = u.item("AA batteries", quantity=6, min_quantity=4)
    b = u.item("AA batteries", product_id=a["product_id"], quantity=2)
    assert a["min_quantity"] == 4
    r = u.ok("post", f"/api/v1/items/{a['id']}/adjust", json={"delta": -3})
    assert r["quantity"] == 3
    assert not any(t == "low_stock" for t, _ in events)  # 3 + 2 = 5 >= 4
    u.ok("post", f"/api/v1/items/{b['id']}/adjust", json={"delta": -2, "note": "TV remote"})
    low = [d for t, d in events if t == "low_stock"]
    assert low == [{"product_id": a["product_id"], "name": "AA batteries", "quantity": 3, "min_quantity": 4}]
    assert u.ok("get", "/api/v1/stats")["low_stock"] == 1
    assert u.ok("get", "/api/v1/low-stock")[0]["quantity"] == 3
    # notifies once, re-arms after restock
    u.ok("post", f"/api/v1/items/{a['id']}/adjust", json={"delta": -1})
    assert len([1 for t, _ in events if t == "low_stock"]) == 1
    u.ok("post", f"/api/v1/items/{a['id']}/adjust", json={"delta": 10})
    assert u.ok("get", "/api/v1/stats")["low_stock"] == 0
    assert u.post(f"/api/v1/items/{a['id']}/adjust", json={"delta": -100}).status_code == 409
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": b["id"]})["events"]
    assert hist[0]["summary"].startswith("Used 2 × AA batteries") and "TV remote" in hist[0]["summary"]


# ---------------------------------------------------------------- visual maps


def test_visual_maps(env):
    u = env.admin
    garage = u.location("Garage")
    shelf = u.location("Upper shelf", garage["id"])
    box = u.container("Bin", "7", location_id=shelf["id"])
    photo = u.upload()
    m = u.ok("post", "/api/v1/maps", json={"title": "Garage wall", "location_id": garage["id"], "photo_id": photo["id"]})
    m = u.ok("put", f"/api/v1/maps/{m['id']}/markers", json={"markers": [
        {"x": 0.25, "y": 0.4, "location_id": shelf["id"], "label": "Upper shelf"},
        {"x": 0.7, "y": 0.5, "container_id": box["id"]},
    ]})
    assert [mk["target"]["type"] for mk in m["markers"]] == ["location", "container"]
    assert u.put(f"/api/v1/maps/{m['id']}/markers", json={"markers": [{"x": 1.5, "y": 0, "location_id": shelf["id"]}]}).status_code == 422
    assert u.put(f"/api/v1/maps/{m['id']}/markers", json={"markers": [{"x": 0.1, "y": 0.1}]}).status_code == 422
    # container page links to the map
    assert u.ok("get", f"/api/v1/containers/{box['id']}")["maps"][0]["id"] == m["id"]
    # several maps per location
    photo2 = u.upload()
    u.ok("post", "/api/v1/maps", json={"title": "Garage left", "location_id": garage["id"], "photo_id": photo2["id"]})
    assert len(u.ok("get", "/api/v1/maps", params={"location_id": garage["id"]})) == 2
    # map photo cannot be deleted while in use
    assert u.delete(f"/api/v1/attachments/{photo['id']}").status_code == 409


# ---------------------------------------------------------------- events


def test_events_published_after_commit(env):
    events = []
    env.state.bus.publish = lambda t, d: events.append((t, d))
    u = env.admin
    shed = u.location("Shed")
    box = u.container("Box", "1", location_id=shed["id"])
    item = u.item("Saw", container_id=box["id"])
    u.ok("post", f"/api/v1/items/{item['id']}/move", json={"location_id": shed["id"]})
    u.ok("post", f"/api/v1/items/{item['id']}/loan", json={"borrower": "Ann"})
    loan = u.ok("get", "/api/v1/loans")[0]
    u.ok("post", f"/api/v1/loans/{loan['id']}/return", json={})
    u.ok("delete", f"/api/v1/items/{item['id']}")
    types = [t for t, _ in events]
    for expected in ("item_added", "container_inventory_updated", "item_moved", "item_loaned", "item_returned", "item_removed"):
        assert expected in types
    moved = next(d for t, d in events if t == "item_moved")
    assert moved["from"] == "Shed › Box #1" and moved["to"] == "Shed" and moved["user"] == "Alex Admin"
    # failed operation publishes nothing
    n = len(events)
    u.post(f"/api/v1/items/{item['id']}/move", json={"location_id": 99999})
    assert len(events) == n


def test_warranty_event_once(env):
    events = []
    env.state.bus.publish = lambda t, d: events.append((t, d))
    env.admin.item("Dishwasher", warranty_expires=(date.today() + timedelta(days=10)).isoformat())
    db = env.state.db.SessionLocal()
    assert stats_svc.warranty_check(db, 30) == 1
    assert stats_svc.warranty_check(db, 30) == 0
    db.close()
    assert [t for t, _ in events if t == "warranty_expiring"] == ["warranty_expiring"]


def test_event_bus_fanout():
    from home_inventory_server.events import EventBus

    async def main():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        q = bus.subscribe()
        bus.publish("item_added", {"x": 1})
        msg = await asyncio.wait_for(q.get(), 1)
        assert msg["type"] == "item_added" and msg["data"] == {"x": 1}
        await asyncio.to_thread(bus.publish, "item_moved", {"y": 2})
        msg = await asyncio.wait_for(q.get(), 1)
        assert msg["type"] == "item_moved"

    asyncio.run(main())


def test_event_stream_api_only(env):
    assert env.admin.get("/api/v1/events/stream").status_code == 403


# ---------------------------------------------------------------- dashboard


def test_dashboard(env):
    u = env.admin
    u.item("Thing")
    d = u.ok("get", "/api/v1/dashboard")
    assert d["stats"]["total_items"] == 1
    assert d["recent_activity"][0]["action"] == "created"


# ---------------------------------------------------------------- backup / restore / persistence


def test_backup_export_restore(tmp_path):
    e = Env(tmp_path)
    u = e.admin
    photo = u.upload()
    item = u.item("Precious vase", photo_ids=[photo["id"]])
    r = u.get("/api/v1/backup/export")
    assert r.status_code == 200
    archive = r.content
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = z.namelist()
    assert "manifest.json" in names and "inventory.sqlite3" in names
    assert any(n.startswith("media/") for n in names)
    # change things after the backup
    u.ok("delete", f"/api/v1/items/{item['id']}")
    u.item("Added later")
    res = u.ok("post", "/api/v1/backup/restore", files={"file": ("b.zip", archive, "application/zip")})
    assert res["safety_backup"].startswith("before-restore-")
    items = u.ok("get", "/api/v1/items")["items"]
    assert [i["name"] for i in items] == ["Precious vase"]
    assert u.get("/" + items[0]["photo"]["url"]).status_code == 200
    assert u.post("/api/v1/backup/restore", files={"file": ("x.zip", b"not a zip")}).status_code == 422
    e.state.db.dispose()


def test_restart_and_upgrade_persistence(tmp_path):
    e = Env(tmp_path)
    photo = e.admin.upload()
    e.admin.item("Survives restarts", photo_ids=[photo["id"]])
    token = e.state.api_token
    e.state.db.dispose()
    # "Restart" = new process with the same /data directory
    e2 = Env(tmp_path)
    items = e2.admin.ok("get", "/api/v1/items")["items"]
    assert items[0]["name"] == "Survives restarts"
    assert e2.admin.get("/" + items[0]["photo"]["url"]).status_code == 200
    assert e2.state.api_token == token  # integration keeps working
    e2.state.db.dispose()


def test_migrations_match_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    db = Database(f"sqlite:///{tmp_path}/m.sqlite3")
    run_migrations(db.engine, None, None)
    assert current_revision(db.engine) == head_revision(str(db.engine.url))
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
    tables = set(inspect(db.engine).get_table_names())
    assert {"items", "products", "containers", "locations", "history_events", "loans", "map_markers"} <= tables
    db.dispose()


def test_pre_migration_snapshot(tmp_path):
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    con.execute("INSERT INTO alembic_version VALUES ('0000')")
    con.commit()
    con.close()
    db = Database(f"sqlite:///{path}")
    try:
        run_migrations(db.engine, path, tmp_path / "backups")
    except Exception:
        pass  # unknown revision 0000 - we only check the snapshot was taken first
    assert list((tmp_path / "backups").glob("pre-migration-0000-*.sqlite3"))
    db.dispose()


def test_database_constraints(env):
    """Low-level guards even if the API layer were bypassed."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    from home_inventory_server.models import Container, Item, Location, Product

    db = env.state.db.SessionLocal()
    loc = Location(name="A")
    db.add(loc)
    db.flush()
    p = Product(name="P")
    db.add(p)
    db.flush()
    c = Container(name="C", qr_code="ZZZZZZZZ", location_id=loc.id)
    db.add(c)
    db.flush()
    db.add(Item(product_id=p.id, quantity=1, container_id=c.id, location_id=loc.id))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    db.add(Item(product_id=p.id, quantity=-1))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    db.close()


def test_settings_fall_back_to_s6_saved_environment(tmp_path, monkeypatch):
    """Home Assistant base images (s6-overlay) may start the App with an empty
    environment; settings must still be found."""
    from home_inventory_server import config

    s6 = tmp_path / "s6"
    s6.mkdir()
    (s6 / "SUPERVISOR_TOKEN").write_text("tok123\n")
    (s6 / "HINV_DATA_DIR").write_text(str(tmp_path / "d"))
    monkeypatch.setattr(config, "S6_ENV_DIR", s6)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.delenv("HINV_DATA_DIR", raising=False)
    s = config.Settings.load()
    assert s.supervisor_token == "tok123"
    assert s.data_dir == tmp_path / "d"

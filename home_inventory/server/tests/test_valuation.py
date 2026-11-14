"""AI value estimates for many items: scopes, progress, sharing per product."""

from __future__ import annotations

import time

from home_inventory_server.services import valuation
from tests.conftest import Env, FakeProvider

EST = {"replacement": 100.0, "used_low": 40, "used_high": 60, "basis": "Used.", "confidence": 0.5}


def _wait(u):
    for _ in range(200):
        j = u.ok("get", "/api/v1/value-estimates")["job"]
        if j and j["status"] not in ("pending", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError("never finished")


def _setup(tmp_path, monkeypatch):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    tools = u.ok("post", "/api/v1/categories", json={"name": "Tools"})
    drill = u.item("Drill", category="Tools", quantity=2)
    u.item("Saw", category="Tools", purchase_price="25.00")
    u.item("Lamp", category="Lighting")
    return env, fake, u, tools, drill


def test_scopes_preview_and_run(tmp_path, monkeypatch):
    env, fake, u, tools, drill = _setup(tmp_path, monkeypatch)
    pv = lambda **q: u.ok("get", "/api/v1/value-estimates/preview", params=q)  # noqa: E731
    assert pv(scope="unpriced")["items"] == 2          # drill, lamp
    assert pv(scope="all")["items"] == 3
    assert pv(scope="category", category_id=tools["id"])["items"] == 2
    assert u.get("/api/v1/value-estimates/preview", params={"scope": "category"}).status_code == 422

    fake.responses.extend([dict(EST), dict(EST)])
    started = u.post("/api/v1/value-estimates", json={"scope": "unpriced"}).json()["job"]
    assert started["total"] == 2
    j = _wait(u)
    assert j["status"] == "finished" and j["done"] == 2 and j["failed"] == 0
    assert j["estimated_total"] == 300.0  # 2 drills × 100 + lamp 100
    got = u.ok("get", f"/api/v1/items/{drill['id']}")
    assert got["ai_metadata"]["value_estimate"]["replacement"] == 100.0
    # Estimated recently: skipped next time unless asked
    assert pv(scope="unpriced")["items"] == 0
    assert pv(scope="unpriced", redo_recent="true")["items"] == 2
    assert u.post("/api/v1/value-estimates", json={"scope": "unpriced"}).status_code == 422
    env.state.db.dispose()


def test_same_product_in_two_places_is_one_request(tmp_path, monkeypatch):
    env, fake, u, tools, drill = _setup(tmp_path, monkeypatch)
    shed = u.location("Shed")
    u.ok("post", "/api/v1/items", json={"product_id": drill["product_id"], "quantity": 1, "location_id": shed["id"]})
    fake.responses.extend([dict(EST), dict(EST), dict(EST)])
    u.post("/api/v1/value-estimates", json={"scope": "all"}).json()
    j = _wait(u)
    assert j["done"] == 4 and len(fake.calls) == 3
    env.state.db.dispose()


def test_members_cant_start_a_run_and_one_at_a_time(tmp_path, monkeypatch):
    env, fake, u, tools, drill = _setup(tmp_path, monkeypatch)
    sam = env.user("u-sam", "Sam Member")
    sam.get("/api/v1/me")
    assert sam.post("/api/v1/value-estimates", json={"scope": "all"}).status_code == 403
    assert sam.get("/api/v1/value-estimates/preview", params={"scope": "all"}).status_code == 200
    db = env.state.db.SessionLocal()
    valuation._put(db, {"status": "running", "boot": valuation.BOOT})
    db.close()
    assert u.post("/api/v1/value-estimates", json={"scope": "all"}).status_code == 409
    env.state.db.dispose()


def test_a_job_finishing_mid_poll_is_not_reported_as_interrupted(env, monkeypatch):
    """The status poll judged a running job by reading it twice; if it
    finished in between, the poll said "interrupted (the App restarted)"."""
    snapshots = iter([{"status": "running", "boot": valuation.BOOT, "done": 1}, {"status": "finished", "done": 2}])
    monkeypatch.setattr(valuation, "job", lambda db: next(snapshots))
    assert env.admin.ok("get", "/api/v1/value-estimates")["job"]["status"] == "running"
    # A job left over from before a restart is still reported as interrupted.
    monkeypatch.setattr(valuation, "job", lambda db: {"status": "running", "boot": "an-earlier-boot"})
    assert env.admin.ok("get", "/api/v1/value-estimates")["job"]["status"] == "failed"

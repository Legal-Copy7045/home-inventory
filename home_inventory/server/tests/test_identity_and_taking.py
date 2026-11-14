"""Requests without a Home Assistant user, stable "taken by" ownership, and
putting several things away at once."""

from __future__ import annotations

from alembic import command
from sqlalchemy import text

from home_inventory_server.db import Database, alembic_config
from tests.test_customers import _customer, _job

# ---------------------------------------------------------------- identity


def test_contextless_integration_requests_never_see_customer_data(env):
    job = _job(env.admin, _customer(env.admin))
    hook = env.integration()  # bearer token, no Home Assistant user
    assert hook.get("/api/v1/customers").status_code == 403
    assert hook.get(f"/api/v1/repair-jobs/{job['id']}").status_code == 403
    assert hook.post("/api/v1/customers", json={"name": "Mallory"}).status_code == 403
    # Customer equipment is hidden from its searches too.
    found = hook.ok("get", "/api/v1/search", params={"q": "Toro"})["results"]
    assert not [r for r in found if "Toro" in (r.get("name") or "")]
    # Household inventory still works for automations (read and change).
    item = env.admin.item("Spare fuses", quantity=4)
    assert hook.ok("post", f"/api/v1/items/{item['id']}/adjust", json={"delta": -1})["quantity"] == 3
    # The integration's sensors still get the repair totals (no names).
    stats = hook.ok("get", "/api/v1/stats")
    assert stats["repairs_in_progress"] == 1
    # Its "repair ready" message may read that one job...
    msg = env.integration(**{"X-Home-Inventory-Purpose": "customer-message"})
    assert msg.ok("get", f"/api/v1/repair-jobs/{job['id']}")["customer"]["phone"] == "555-0100"
    # ...but the purpose opens nothing else.
    assert msg.get("/api/v1/customers").status_code == 403
    assert msg.post("/api/v1/customers", json={"name": "Mallory"}).status_code == 403


def test_contextless_requests_are_never_administrators(env):
    hook = env.integration()
    assert hook.get("/api/v1/users").status_code == 403
    assert hook.ok("get", "/api/v1/me")["role"] == "member"


def test_integration_requests_with_a_user_keep_attribution(env):
    item = env.admin.item("Socket set")
    pat = env.integration(**{"X-Remote-User-Id": "0123456789abcdef0123456789abcdef", "X-Remote-User-Display-Name": "Pat",
                             "X-Remote-User-Is-Admin": "false"})
    pat.ok("post", f"/api/v1/items/{item['id']}/take")
    assert env.admin.ok("get", f"/api/v1/items/{item['id']}")["taken_by"] == "Pat"
    # A member through the integration sees customers as in the app.
    assert pat.get("/api/v1/customers").status_code == 200


def test_invalid_or_reserved_identity_headers_are_refused(env):
    for bad in ("system", "integration", "pat<script>", "x" * 65):
        assert env.integration(**{"X-Remote-User-Id": bad}).get("/api/v1/me").status_code == 401, bad
    no_token = env.integration()
    no_token.headers.pop("Authorization")
    assert no_token.get("/api/v1/me").status_code == 401


def test_ingress_without_identity_is_refused(env):
    anon = env.user("u-x", "X")
    anon.headers.pop("X-Remote-User-Id")
    assert anon.get("/api/v1/me").status_code == 401


# ---------------------------------------------------------------- taking


def test_someone_elses_taken_item_is_not_silently_taken_again(env):
    item = env.admin.item("Torque wrench")
    sam = env.user("u-sam", "Sam Smith")
    env.admin.ok("post", f"/api/v1/items/{item['id']}/take")
    r = sam.post(f"/api/v1/items/{item['id']}/take")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "already_taken" and detail["taken_by"] == "Alex Admin"
    assert "already taken by Alex Admin" in detail["message"]
    # Taking it over is an explicit choice, and is recorded.
    over = sam.ok("post", f"/api/v1/items/{item['id']}/take", params={"transfer": "true"})
    assert over["mine"] is True and over["taken_by"] == "Sam Smith"
    assert env.admin.ok("get", "/api/v1/taken")["items"][0]["mine"] is False
    history = env.admin.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})["events"]
    assert any("taken over from Alex Admin by Sam Smith" in h["summary"] for h in history)
    # Taking your own again changes nothing.
    assert sam.ok("post", f"/api/v1/items/{item['id']}/take")["taken_by"] == "Sam Smith"


def test_ownership_follows_the_user_not_their_name(env):
    item = env.admin.item("Stud finder")
    a = env.user("u-a", "Sam")
    b = env.user("u-b", "Sam")  # same display name, different person
    a.ok("post", f"/api/v1/items/{item['id']}/take")
    assert a.ok("get", "/api/v1/taken")["items"][0]["mine"] is True
    assert b.ok("get", "/api/v1/taken")["items"][0]["mine"] is False
    assert b.ok("get", "/api/v1/taken/route")["stops"] == []
    assert b.post(f"/api/v1/items/{item['id']}/take").status_code == 409
    # Renamed in Home Assistant: still theirs, shown by the new name.
    renamed = env.user("u-a", "Samantha")
    assert renamed.ok("get", "/api/v1/taken")["items"][0]["mine"] is True
    assert renamed.ok("get", "/api/v1/taken")["items"][0]["taken_by"] == "Samantha"
    # Put away: nobody has it any more.
    shelf = env.admin.location("Shelf")
    renamed.ok("post", f"/api/v1/items/{item['id']}/put-away", json={"location_id": shelf["id"], "mode": "move"})
    assert env.admin.ok("get", "/api/v1/taken")["items"] == []
    assert b.ok("post", f"/api/v1/items/{item['id']}/take")["mine"] is True


def test_migration_matches_old_taken_notes_to_users(tmp_path):
    url = f"sqlite:///{tmp_path / 'db.sqlite3'}"
    d = Database(url)
    cfg = alembic_config(url)
    with d.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0013")
    t = "2026-11-11 00:00:00"
    with d.engine.begin() as c:
        c.execute(text("INSERT INTO users (id,ha_user_id,username,display_name,ha_is_admin,first_seen,last_seen) VALUES "
                       "(1,'a','sam','Sam Smith',0,:t,:t),(2,'b','jo','Jo',0,:t,:t),(3,'c','jo2','Jo',0,:t,:t)"), {"t": t})
        c.execute(text("INSERT INTO products (id,name,identification_status,created_at,updated_at) VALUES (1,'Drill','manual',:t,:t)"), {"t": t})
        c.execute(text("INSERT INTO items (id,product_id,quantity,status,status_note,created_at,updated_at) VALUES "
                       "(1,1,1,'in_use','Taken by Sam Smith',:t,:t),(2,1,1,'in_use','Taken by Jo',:t,:t),"
                       "(3,1,1,'stored','Taken by Sam Smith',:t,:t),(4,1,1,'in_use','Taken by Nobody',:t,:t)"), {"t": t})
    with d.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    with d.engine.connect() as c:
        rows = dict(c.execute(text("SELECT id, taken_by_id FROM items ORDER BY id")).fetchall())
    # Unique name: matched.  Two users called Jo, or nobody: left unset (still taken, by nobody known).
    assert rows == {1: 1, 2: None, 3: None, 4: None}


# ---------------------------------------------------------------- batch put-away


def test_batch_put_away_reports_each_item(env):
    u = env.admin
    shed = u.location("Shed")
    bench = u.location("Bench")
    drill = u.item("Drill", location_id=shed["id"])
    saw = u.item("Saw", location_id=bench["id"])
    gone = u.item("Level", location_id=shed["id"])
    for it in (drill, saw, gone):
        u.ok("post", f"/api/v1/items/{it['id']}/take")
    u.ok("delete", f"/api/v1/items/{gone['id']}")
    r = u.ok("post", "/api/v1/put-away/batch", json={"item_ids": [drill["id"], saw["id"], gone["id"], 99999],
                                                     "location_id": shed["id"]})
    by = {x["item_id"]: x for x in r["results"]}
    assert by[drill["id"]]["status"] == "done" and by[drill["id"]]["result"] == "back"
    assert by[saw["id"]]["status"] == "choose"  # not its place: needs a decision, nothing changed
    assert by[gone["id"]]["status"] == "failed" and by[99999]["status"] == "failed"
    assert (r["done"], r["choose"], r["failed"]) == (1, 1, 2)
    assert u.ok("get", f"/api/v1/items/{drill['id']}")["status"] == "stored"
    assert u.ok("get", f"/api/v1/items/{saw['id']}")["status"] == "in_use"
    assert u.post("/api/v1/put-away/batch", json={"item_ids": [], "location_id": shed["id"]}).status_code == 422

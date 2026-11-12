"""Read-only users never see customer information or repair records (0.7.1).

Regression tests for the findings of the November 2026 authorization review:
attachment downloads, replenishment purchases into customer holdings, the
other read paths (item detail, lists, search, barcode, containers,
locations, trash, history, maintenance, relations, kits, tags, restock),
history/event wording, and the setup-code claim.
"""

from __future__ import annotations

import pytest

from tests.conftest import jpeg_bytes

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _viewer(env, ha_id="u-view", name="Val Viewer"):
    v = env.user(ha_id, name)
    v.get("/api/v1/me")
    uid = next(x["id"] for x in env.admin.ok("get", "/api/v1/users") if x["name"] == name)
    env.admin.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    return v


@pytest.fixture
def world(env):
    """A household item and a customer's item, each with photos/documents."""
    u = env.admin
    shed = u.location("Shed")
    box = u.container("Box 1", location_id=shed["id"])
    mine = u.item("Household drill", container_id=box["id"])
    cust = u.ok("post", "/api/v1/customers", json={"name": "Pat Neighbour", "phone": "555-0100", "email": "pat@example.com"})
    job = u.ok("post", "/api/v1/repair-jobs", json={
        "customer_id": cust["id"], "reported_problem": "Won't start",
        "new_equipment": {"name": "Pat's chainsaw", "serial_number": "CS-77", "container_id": box["id"]}})
    theirs = job["item"]["id"]
    files = {
        "household": u.upload(jpeg_bytes(), item_id=mine["id"]),
        "customer_item": u.upload(jpeg_bytes((10, 200, 10)), item_id=theirs),
        "job_photo": u.ok("post", "/api/v1/attachments", files={"file": ("dmg.jpg", jpeg_bytes((5, 5, 200)), "image/jpeg")},
                          data={"kind": "photo", "repair_job_id": str(job["id"])}),
        "job_document": u.ok("post", "/api/v1/attachments", files={"file": ("quote.pdf", PDF, "application/pdf")},
                             data={"kind": "receipt", "repair_job_id": str(job["id"])}),
    }
    return {"u": u, "box": box, "shed": shed, "mine": mine, "theirs": theirs, "job": job, "customer": cust, "files": files}


# ---------------------------------------------------------------- 1. attachments


@pytest.mark.parametrize("kind", ["file", "thumb"])
def test_read_only_can_fetch_household_attachments(env, world, kind):
    v = _viewer(env)
    r = v.get(f"/api/v1/attachments/{world['files']['household']['id']}/{kind}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")


@pytest.mark.parametrize("which", ["customer_item", "job_photo", "job_document"])
@pytest.mark.parametrize("kind", ["file", "thumb"])
def test_read_only_cannot_fetch_customer_or_repair_attachments(env, world, which, kind):
    v = _viewer(env)
    r = v.get(f"/api/v1/attachments/{world['files'][which]['id']}/{kind}")
    # Same answer as a missing attachment: nothing to tell it apart.
    assert r.status_code == 404 and r.json()["detail"] == v.get(f"/api/v1/attachments/999999/{kind}").json()["detail"]


@pytest.mark.parametrize("which", ["household", "customer_item", "job_photo", "job_document"])
@pytest.mark.parametrize("kind", ["file", "thumb"])
def test_members_and_admins_can_fetch_every_attachment(env, world, which, kind):
    member = env.user("u-sam", "Sam Member")
    member.get("/api/v1/me")
    for client in (env.admin, member):
        r = client.get(f"/api/v1/attachments/{world['files'][which]['id']}/{kind}")
        assert r.status_code == 200, (which, kind)


def test_household_attachment_responses_unchanged(env, world):
    v = _viewer(env)
    att = world["files"]["household"]["id"]
    a, b = env.admin.get(f"/api/v1/attachments/{att}/file"), v.get(f"/api/v1/attachments/{att}/file")
    assert a.content == b.content and b.headers["x-content-type-options"] == "nosniff"
    doc = env.admin.upload(PDF, kind="manual", filename="m.pdf", item_id=world["mine"]["id"])
    r = v.get(f"/api/v1/attachments/{doc['id']}/file")
    assert r.status_code == 200 and r.headers["content-security-policy"] == "sandbox"


# ---------------------------------------------------------------- 2. replenishment


@pytest.fixture
def oil(env):
    """Household oil (2 holdings) and a customer's holding of the same product."""
    u = env.admin
    garage = u.location("Garage")
    a = u.item("SAE 30 oil", quantity=1, min_quantity=2, location_id=garage["id"])
    b = u.ok("post", "/api/v1/items", json={"product_id": a["product_id"], "name": "SAE 30 oil", "quantity": 0,
                                             "location_id": garage["id"]})["items"][0]
    c = u.ok("post", "/api/v1/customers", json={"name": "Pat"})
    theirs = u.ok("post", "/api/v1/items", json={"product_id": a["product_id"], "name": "SAE 30 oil", "quantity": 2})["items"][0]
    u.ok("post", f"/api/v1/items/{theirs['id']}/owner", json={"customer_id": c["id"], "confirm": True})
    lst = u.ok("post", "/api/v1/shopping-list", json={"product_id": a["product_id"], "quantity": 3})
    entry = lst["groups"][0]["entries"][0]["id"]
    return {"u": u, "a": a, "b": b, "theirs": theirs, "entry": entry}


def _entry_status(u, entry_id):
    lst = u.ok("get", "/api/v1/shopping-list?include_closed=true")
    return next(e["status"] for g in lst["groups"] for e in g["entries"] if e["id"] == entry_id)


def test_purchase_into_chosen_household_holding(oil):
    u = oil["u"]
    u.ok("post", f"/api/v1/shopping-list/{oil['entry']}/purchased", json={"item_id": oil["b"]["id"]})
    assert u.ok("get", f"/api/v1/items/{oil['b']['id']}")["quantity"] == 3
    assert _entry_status(u, oil["entry"]) == "purchased"


def test_purchase_into_customer_holding_rejected_without_side_effects(oil):
    u = oil["u"]
    r = u.post(f"/api/v1/shopping-list/{oil['entry']}/purchased", json={"item_id": oil["theirs"]["id"], "unit_price": "9.99"})
    assert r.status_code == 422 and "customer" in r.json()["detail"]
    assert u.ok("get", f"/api/v1/items/{oil['theirs']['id']}")["quantity"] == 2  # customer holding untouched
    assert _entry_status(u, oil["entry"]) == "listed"  # not marked purchased
    restock = u.ok("get", f"/api/v1/products/{oil['a']['product_id']}/restock")
    assert restock["profile"]["last_price"] is None  # no partial update


def test_purchase_rejects_wrong_product_deleted_and_missing_ids(env, oil):
    u = oil["u"]
    other = u.item("Paper towels")
    gone = u.ok("post", "/api/v1/items", json={"product_id": oil["a"]["product_id"], "name": "SAE 30 oil"})["items"][0]
    u.ok("delete", f"/api/v1/items/{gone['id']}", expected=204)
    for bad in (other["id"], gone["id"], 999999):
        r = u.post(f"/api/v1/shopping-list/{oil['entry']}/purchased", json={"item_id": bad})
        assert r.status_code == 422, bad
    # no silent redirect to another holding
    assert u.ok("get", f"/api/v1/items/{oil['a']['id']}")["quantity"] == 1
    assert u.ok("get", f"/api/v1/items/{oil['b']['id']}")["quantity"] == 0
    assert _entry_status(u, oil["entry"]) == "listed"


def test_automatic_holding_skips_customer_inventory(oil):
    u = oil["u"]
    u.ok("post", f"/api/v1/shopping-list/{oil['entry']}/purchased", json={})
    assert u.ok("get", f"/api/v1/items/{oil['a']['id']}")["quantity"] == 4
    assert u.ok("get", f"/api/v1/items/{oil['theirs']['id']}")["quantity"] == 2


def test_parts_cannot_be_taken_from_customer_stock(env, world):
    u = world["u"]
    r = u.post(f"/api/v1/items/{world['mine']['id']}/service-events", json={
        "title": "Cleaned", "parts": [{"description": "chain", "item_id": world["theirs"], "quantity": "1", "deduct_stock": True}]})
    assert r.status_code == 422 and "household stock" in r.json()["detail"]


# ---------------------------------------------------------------- 3. other read paths


def test_customer_equipment_invisible_to_read_only_everywhere(env, world):
    u, v = world["u"], _viewer(env)
    theirs = world["theirs"]
    u.ok("patch", f"/api/v1/items/{theirs}", json={"barcode": "012345678905", "notes": "Pat's spare key in the drawer"})
    u.ok("post", f"/api/v1/items/{theirs}/meter-readings", json={"value": 12})
    u.ok("post", f"/api/v1/items/{theirs}/maintenance-tasks", json={"title": "Sharpen", "interval_days": 30, "first_due_on": "2020-01-01"})
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": theirs})
    u.ok("post", f"/api/v1/items/{world['mine']['id']}/relations", json={"other_item_id": theirs, "relation_type": "stored_with"})
    kit = u.ok("post", "/api/v1/kits", json={"name": "Bench kit", "item_ids": [world["mine"]["id"], theirs]})

    def leaks(payload) -> bool:
        text = str(payload)
        return any(s in text for s in ("Pat", "chainsaw", "CS-77", "555-0100", "pat@example.com", "Won't start"))

    # direct item endpoints: 404, as for a missing item
    for path in (f"/api/v1/items/{theirs}", f"/api/v1/items/{theirs}/maintenance", f"/api/v1/items/{theirs}/relations"):
        assert v.get(path).status_code == 404, path
    # lists, search, containers, locations, dashboards
    for path in ("/api/v1/items", "/api/v1/items?q=chainsaw", "/api/v1/search?q=chainsaw&ai=false", f"/api/v1/containers/{world['box']['id']}",
                 f"/api/v1/locations/{world['shed']['id']}", "/api/v1/dashboard", "/api/v1/maintenance/dashboard",
                 "/api/v1/replenishment", "/api/v1/shopping-list", "/api/v1/low-stock", "/api/v1/loans", "/api/v1/kits",
                 f"/api/v1/kits/{kit['id']}", f"/api/v1/items/{world['mine']['id']}/relations", "/api/v1/tags",
                 f"/api/v1/items/{world['mine']['id']}", "/api/v1/history", "/api/v1/history?limit=200"):
        r = v.get(path)
        assert r.status_code == 200, path
        body = r.json()
        if path.startswith("/api/v1/search"):
            # the response repeats the search words; what matters is what was found
            assert body["total_matches"] == 0 and body["results"] == [], body
            body = body["results"]
        assert not leaks(body), (path, body)
    assert [m["item"]["name"] for m in v.ok("get", f"/api/v1/kits/{kit['id']}")["members"]] == ["Household drill"]
    # (barcode lookup is not available to read-only users at all - unchanged)
    assert v.post("/api/v1/barcode/lookup", json={"code": "012345678905", "external": False}).status_code == 403
    # its NFC tag doesn't resolve, with the same answer as an unknown tag
    r = v.post("/api/v1/tags/resolve", json={"text": tag["url"]})
    assert r.status_code == 404 and r.json()["detail"] == "Tag not recognised"
    # history of the customer's item is empty
    assert v.ok("get", f"/api/v1/history?entity_type=item&entity_id={theirs}")["events"] == []
    # restock view of a customer-only product is not found
    product_id = u.ok("get", f"/api/v1/items/{theirs}")["product_id"]
    assert v.get(f"/api/v1/products/{product_id}/restock").status_code == 404
    # trash
    u.ok("delete", f"/api/v1/items/{theirs}", expected=204)
    assert not leaks(v.ok("get", "/api/v1/trash"))


def test_members_still_see_customer_equipment(env, world):
    member = env.user("u-sam", "Sam Member")
    member.get("/api/v1/me")
    assert member.ok("get", f"/api/v1/items/{world['theirs']}")["owner"]["name"] == "Pat Neighbour"
    assert any(r["id"] == world["theirs"] for r in member.ok("get", "/api/v1/search?q=chainsaw&ai=false")["results"])


def test_household_viewing_unchanged_for_read_only(env, world):
    v = _viewer(env)
    assert v.ok("get", f"/api/v1/items/{world['mine']['id']}")["name"] == "Household drill"
    names = [i["name"] for i in v.ok("get", "/api/v1/items")["items"]]
    assert names == ["Household drill"]
    assert [i["name"] for i in v.ok("get", f"/api/v1/containers/{world['box']['id']}")["items"]] == ["Household drill"]


def test_history_on_household_records_does_not_name_customer_equipment(env, world):
    u = world["u"]
    u.ok("post", f"/api/v1/items/{world['mine']['id']}/relations", json={"other_item_id": world["theirs"], "relation_type": "stored_with"})
    kit = u.ok("post", "/api/v1/kits", json={"name": "Bench kit", "item_ids": [world["theirs"]]})
    summaries = [e["summary"] for e in u.ok("get", "/api/v1/history?limit=200")["events"]
                 if e["action"] in ("related", "kit_member_added")]
    assert summaries and all("chainsaw" not in s for s in summaries), summaries
    assert any("customer equipment" in s for s in summaries)
    assert kit


def test_home_assistant_events_do_not_name_customer_equipment(env, world):
    u = world["u"]
    u.ok("post", f"/api/v1/customers/{world['customer']['id']}/equipment", json={"name": "Pat's hedge trimmer"})
    t = u.ok("post", f"/api/v1/items/{world['theirs']}/maintenance-tasks", json={"title": "Sharpen", "interval_days": 30})
    u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/complete", json={})
    events = [m for m in env.state.bus.recent if m["type"] in ("item_added", "maintenance_completed")]
    assert events and not any("Pat" in str(m) or "chainsaw" in str(m) for m in events), events
    assert any(m["data"].get("customer_equipment") for m in events)
    household = [m for m in events if m["type"] == "item_added" and m["data"].get("name") == "Household drill"]
    assert household  # household names are unchanged


# ---------------------------------------------------------------- 4. setup code


def _bare(tmp_path, **kw):
    from tests.conftest import Env

    return Env(tmp_path, configured_admin=False, **kw)


def test_database_read_only_user_cannot_claim(tmp_path):
    from home_inventory_server.models import Role, User

    e = _bare(tmp_path)
    kid = e.user("u-kid", "Kid")
    kid.get("/api/v1/me")
    with e.state.db.SessionLocal() as db:  # an administrator set this earlier, then left
        user = db.query(User).filter_by(ha_user_id="u-kid").one()
        user.role_override = Role.read_only
        db.commit()
    assert kid.ok("get", "/api/v1/me")["admin_setup"] is True
    r = kid.post("/api/v1/setup/claim-admin", json={"code": e.state.admin_bootstrap._code})
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert kid.ok("get", "/api/v1/me")["role"] == "read_only"
    # the code is still usable by a legitimate first administrator
    first = e.user("u-first", "First Person")
    first.ok("post", "/api/v1/setup/claim-admin", json={"code": e.state.admin_bootstrap._code})
    assert first.ok("get", "/api/v1/me")["role"] == "admin"


def test_configured_read_only_user_cannot_claim(tmp_path):
    e = _bare(tmp_path, read_only_users=["guest"])
    g = e.user("u-guest", "Guest")
    g.get("/api/v1/me")
    assert g.post("/api/v1/setup/claim-admin", json={"code": e.state.admin_bootstrap._code}).status_code == 403

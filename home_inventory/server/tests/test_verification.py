"""Inventory verification (checking a place) and the Needs Attention list."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_customers import _customer, _job, _viewer

TODAY = date.today()


def _shed(u):
    shed = u.location("Shed")
    shelf = u.location("Top shelf", parent_id=shed["id"])
    box = u.container("Fixings", "7", location_id=shelf["id"])
    screws = u.item("Wood screws", quantity=3, container_id=box["id"], barcode="012345678905")
    saw = u.item("Hand saw", location_id=shelf["id"])
    level = u.item("Spirit level", location_id=shed["id"])
    return shed, shelf, box, screws, saw, level


def test_checking_a_place(env):
    u = env.admin
    shed, shelf, box, screws, saw, level = _shed(u)
    garage = u.location("Garage")
    drill = u.item("Cordless drill", location_id=garage["id"])
    c = u.ok("post", "/api/v1/verifications", json={"location_id": shed["id"]}, expected=201)
    assert {i["name"] for i in c["items"]} == {"Wood screws", "Hand saw", "Spirit level"}  # whole tree, boxes too
    assert all(i["confidence"]["level"] == "never" for i in c["items"])
    sid = c["id"]
    u.ok("post", f"/api/v1/verifications/{sid}/items/{saw['id']}", json={"result": "present"})
    u.ok("post", f"/api/v1/verifications/{sid}/items/{screws['id']}", json={"result": "adjusted", "quantity": 5})
    u.ok("post", f"/api/v1/verifications/{sid}/items/{level['id']}", json={"result": "missing"})
    # Something found here that the records put in the garage.
    u.ok("post", f"/api/v1/verifications/{sid}/found-here", json={"item_id": drill["id"], "move": True})
    # Only expected things can be ticked; found things go through Found here.
    assert u.post(f"/api/v1/verifications/{sid}/items/{drill['id']}", json={"result": "present"}).status_code == 422
    done = u.ok("post", f"/api/v1/verifications/{sid}/finish")
    assert done["summary"] == {"present": 1, "adjusted": 1, "missing": 1, "found_here": 1, "not_checked": 0}
    assert u.ok("get", f"/api/v1/items/{screws['id']}")["quantity"] == 5
    assert u.ok("get", f"/api/v1/items/{level['id']}")["status"] == "missing"
    assert u.ok("get", f"/api/v1/items/{drill['id']}")["placement"]["location"]["id"] == shed["id"]
    conf = u.ok("get", f"/api/v1/items/{saw['id']}/verification")
    assert conf["confidence"]["level"] == "today" and conf["history"][0]["result"] == "present"
    # A finished check can't be changed; the next check compares with it.
    assert u.post(f"/api/v1/verifications/{sid}/items/{saw['id']}", json={"result": "missing"}).status_code == 422
    u.ok("post", f"/api/v1/items/{saw['id']}/adjust", json={"delta": 1})  # changed after the check
    again = u.ok("post", "/api/v1/verifications", json={"location_id": shed["id"]}, expected=201)
    assert again["previous"]["id"] == sid and again["previous"]["changed"] >= 1 and again["previous"]["new_here"] == 1
    saw_row = next(i for i in again["items"] if i["id"] == saw["id"])
    assert saw_row["confidence"]["changed_since"] and "changed since" in saw_row["confidence"]["label"]
    hist = u.ok("get", "/api/v1/verifications")["checks"]
    assert [h["id"] for h in hist[:2]] == [again["id"], sid]


def test_unchecked_things_are_never_changed(env):
    u = env.admin
    shed, shelf, box, screws, saw, level = _shed(u)
    c = u.ok("post", "/api/v1/verifications", json={"container_id": box["id"]}, expected=201)
    assert [i["name"] for i in c["items"]] == ["Wood screws"]
    done = u.ok("post", f"/api/v1/verifications/{c['id']}/finish")
    assert done["summary"]["not_checked"] == 1
    item = u.ok("get", f"/api/v1/items/{screws['id']}")
    assert item["status"] == "stored" and item["quantity"] == 3 and item["verified_at"] is None


def test_scanning_in_a_check(env):
    u = env.admin
    shed, shelf, box, screws, saw, level = _shed(u)
    elsewhere = u.item("Wood screws", quantity=1, location_id=u.location("Garage")["id"], barcode="012345678905")
    c = u.ok("post", "/api/v1/verifications", json={"location_id": shelf["id"]}, expected=201)
    hit = u.ok("post", f"/api/v1/verifications/{c['id']}/scan", json={"code": "012345678905"})
    assert [i["id"] for i in hit["expected"]] == [screws["id"]] and [i["id"] for i in hit["elsewhere"]] == [elsewhere["id"]]
    assert u.ok("post", f"/api/v1/verifications/{c['id']}/scan", json={"code": "4006381333931"})["unknown"]


def test_category_check_and_permissions(env):
    u = env.admin
    cat = u.ok("post", "/api/v1/categories", json={"name": "Hand tools"})
    saw = u.item("Hand saw", category="Hand tools")
    c = u.ok("post", "/api/v1/verifications", json={"category_id": cat["id"]}, expected=201)
    assert [i["id"] for i in c["items"]] == [saw["id"]]
    r = u.post(f"/api/v1/verifications/{c['id']}/found-here", json={"item_id": saw["id"], "move": True})
    assert r.status_code == 422  # a category has no place to move things to
    assert u.post("/api/v1/verifications", json={"location_id": 1, "container_id": 1}).status_code == 422
    viewer = _viewer(env)
    assert viewer.post("/api/v1/verifications", json={"category_id": cat["id"]}).status_code == 403
    assert viewer.get(f"/api/v1/verifications/{c['id']}").status_code == 200


def test_customer_equipment_is_never_in_a_check(env):
    u = env.admin
    shed = u.location("Workshop")
    job = _job(u, _customer(u))
    u.ok("post", f"/api/v1/items/{job['item']['id']}/move", json={"location_id": shed["id"]})
    mine = u.item("Bench vice", location_id=shed["id"])
    c = u.ok("post", "/api/v1/verifications", json={"location_id": shed["id"]}, expected=201)
    assert [i["id"] for i in c["items"]] == [mine["id"]]


def test_needs_attention(env):
    u = env.admin
    mower = u.item("Toro mower", manufacturer="Toro")
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower", "current_hours": 0})
    u.ok("post", f"/api/v1/items/{mower['id']}/maintenance-tasks",
         json={"title": "Sharpen blade", "interval_days": 30, "last_done_on": (TODAY - timedelta(days=40)).isoformat()})
    u.item("AA batteries", quantity=1, min_quantity=4)
    drill = u.item("Drill")
    u.ok("post", f"/api/v1/items/{drill['id']}/take")
    job = _job(u, _customer(u))
    att = u.ok("get", "/api/v1/attention")
    keys = [g["key"] for g in att["groups"]]
    assert keys[0] == "maintenance_overdue"  # urgent first
    groups = {g["key"]: g for g in att["groups"]}
    assert groups["maintenance_overdue"]["items"][0]["link"] == f"/equipment/{mower['id']}"
    assert groups["low_stock"]["items"][0]["title"] == "AA batteries"
    assert groups["taken"]["items"][0]["title"] == "Drill"
    assert groups["repairs"]["items"][0]["title"].startswith(job["number"])
    assert groups["equipment_plan"]["items"][0]["detail"] == "using a generic schedule"
    assert att["urgent"] >= 1 and att["total"] == sum(g["count"] for g in att["groups"])
    # Read-only users and requests without a user never see customer repairs here.
    assert "repairs" not in {g["key"] for g in _viewer(env).ok("get", "/api/v1/attention")["groups"]}
    assert "repairs" not in {g["key"] for g in env.integration().ok("get", "/api/v1/attention")["groups"]}


def test_attention_count_for_home_assistant(env):
    u = env.admin
    u.item("AA batteries", quantity=1, min_quantity=4)
    _job(u, _customer(u))  # customer repairs never count for requests without a user
    stats = env.integration().ok("get", "/api/v1/stats")
    assert stats["needs_attention"] == 1
    assert u.ok("get", "/api/v1/stats")["needs_attention"] == 2  # a member also sees the repair

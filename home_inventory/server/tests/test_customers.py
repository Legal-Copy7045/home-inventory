"""Customers, customer-owned equipment and repair jobs."""

from __future__ import annotations

from datetime import date


def _viewer(env):
    v = env.user("u-view", "Val Viewer")
    v.get("/api/v1/me")
    uid = next(x["id"] for x in env.admin.ok("get", "/api/v1/users") if x["name"] == "Val Viewer")
    env.admin.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    return v


def _customer(u, name="Pat Neighbour"):
    return u.ok("post", "/api/v1/customers", json={"name": name, "phone": "555-0100", "email": "pat@example.com"})


def _job(u, customer, **equipment):
    eq = {"name": "Toro Recycler mower", "manufacturer": "Toro", "model_number": "20333", "serial_number": "TR-9", **equipment}
    return u.ok("post", "/api/v1/repair-jobs", json={
        "customer_id": customer["id"], "new_equipment": eq,
        "condition_received": "Deck rusty, handle bent", "reported_problem": "Won't start"})


def test_customer_equipment_is_not_household_property(env):
    u = env.admin
    u.item("My own mower", purchase_price="300", warranty_expires=date.today().isoformat())
    before = u.ok("get", "/api/v1/stats")
    c = _customer(u)
    eq = u.ok("post", f"/api/v1/customers/{c['id']}/equipment", json={"name": "Pat's chainsaw", "serial_number": "CS-1"})
    assert eq["owner"] == {"kind": "customer", "customer_id": c["id"], "name": "Pat Neighbour"}
    after = u.ok("get", "/api/v1/stats")
    assert after["total_items"] == before["total_items"]
    assert after["total_value"] == before["total_value"]
    # still an ordinary, searchable item
    hits = u.ok("get", "/api/v1/search?q=chainsaw&ai=false")
    assert any(r["id"] == eq["id"] for r in hits["results"]), hits["results"]
    detail = u.ok("get", f"/api/v1/customers/{c['id']}")
    assert [e["name"] for e in detail["equipment"]] == ["Pat's chainsaw"]


def test_ordinary_edits_never_change_ownership(env):
    u = env.admin
    c = _customer(u)
    eq = u.ok("post", f"/api/v1/customers/{c['id']}/equipment", json={"name": "Blower", "location_id": u.location("Bench")["id"]})
    # owner is not an editable item field
    assert u.patch(f"/api/v1/items/{eq['id']}", json={"owner": None}).status_code == 422
    u.ok("patch", f"/api/v1/items/{eq['id']}", json={"name": "Leaf blower", "notes": "noisy"})
    assert u.ok("get", f"/api/v1/items/{eq['id']}")["owner"]["customer_id"] == c["id"]
    # moving it keeps the owner
    garage = u.location("Garage")
    u.ok("post", f"/api/v1/items/{eq['id']}/move", json={"location_id": garage["id"]})
    assert u.ok("get", f"/api/v1/items/{eq['id']}")["owner"]["customer_id"] == c["id"]
    # changing owner needs the explicit, confirmed endpoint
    assert u.post(f"/api/v1/items/{eq['id']}/owner", json={"customer_id": None}).status_code == 422
    u.ok("post", f"/api/v1/items/{eq['id']}/owner", json={"customer_id": None, "confirm": True})
    assert u.ok("get", f"/api/v1/items/{eq['id']}")["owner"] is None


def test_splitting_a_customer_holding_keeps_the_owner(env):
    u = env.admin
    c = _customer(u)
    shed = u.location("Shed")
    bench = u.location("Bench")
    tyres = u.ok("post", f"/api/v1/customers/{c['id']}/equipment", json={"name": "Trailer tyres", "location_id": shed["id"]})
    u.ok("post", f"/api/v1/items/{tyres['id']}/adjust", json={"delta": 3})  # 4 tyres
    u.ok("post", f"/api/v1/items/{tyres['id']}/move", json={"location_id": bench["id"], "quantity": 2})
    items = u.ok("get", "/api/v1/items?q=Trailer")["items"]
    assert len(items) == 2 and all(i["owner"] and i["owner"]["customer_id"] == c["id"] for i in items), items


def test_repair_job_lifecycle_parts_and_costs(env):
    u = env.admin
    c = _customer(u)
    job = _job(u, c)
    assert job["number"] == "RJ-0001" and job["status"] == "received"
    assert job["item"]["name"] == "Toro Recycler mower" and job["customer"]["phone"] == "555-0100"
    assert job["condition_received"] == "Deck rusty, handle bent"
    for status in ("awaiting_diagnosis", "awaiting_approval", "waiting_for_parts"):
        job = u.ok("post", f"/api/v1/repair-jobs/{job['id']}/status", json={"status": status})
    job = u.ok("post", f"/api/v1/repair-jobs/{job['id']}/service", json={
        "title": "Carburetor rebuilt", "work_performed": "Cleaned jets, new gasket",
        "parts": [{"description": "Carb kit", "part_number": "CK-1", "quantity": "1", "unit_cost": "18.50"},
                  {"description": "Spark plug", "quantity": "1", "unit_cost": "4.00"}]})
    assert job["status"] == "in_progress"  # recording work moves it on
    assert job["parts_charge"] == 22.5
    job = u.ok("patch", f"/api/v1/repair-jobs/{job['id']}", json={"labor_charge": "40", "diagnosis": "Varnished carburetor"})
    assert job["total"] == 62.5
    job = u.ok("post", f"/api/v1/repair-jobs/{job['id']}/status", json={"status": "completed"})
    assert job["awaiting_pickup"] and job["completed_on"] == date.today().isoformat()
    stats = u.ok("get", "/api/v1/stats")
    assert stats["repairs_awaiting_pickup"] == 1 and stats["repairs_in_progress"] == 0
    job = u.ok("post", f"/api/v1/repair-jobs/{job['id']}/status", json={"status": "returned", "note": "Collected by Pat"})
    assert job["returned_on"] == date.today().isoformat()
    assert [h["to"] for h in job["history"]] == ["received", "awaiting_diagnosis", "awaiting_approval", "waiting_for_parts",
                                                "in_progress", "completed", "returned"]
    # the work also appears in the equipment's own service history
    hist = u.ok("get", f"/api/v1/items/{job['item']['id']}/maintenance")["history"]
    assert hist[0]["title"] == "Carburetor rebuilt"


def test_multiple_visits_keep_persistent_history(env):
    u = env.admin
    c = _customer(u)
    first = _job(u, c)
    u.ok("post", f"/api/v1/repair-jobs/{first['id']}/service", json={"title": "Blade sharpened"})
    u.ok("post", f"/api/v1/repair-jobs/{first['id']}/status", json={"status": "returned"})
    second = u.ok("post", "/api/v1/repair-jobs", json={"customer_id": c["id"], "item_id": first["item"]["id"], "reported_problem": "Smokes"})
    assert second["number"] == "RJ-0002"
    u.ok("post", f"/api/v1/repair-jobs/{second['id']}/service", json={"title": "Oil change"})
    jobs = u.ok("get", f"/api/v1/items/{first['item']['id']}/repair-jobs")
    assert [j["number"] for j in jobs] == ["RJ-0002", "RJ-0001"]
    titles = [e["title"] for e in u.ok("get", f"/api/v1/items/{first['item']['id']}/maintenance")["history"]]
    assert set(titles) == {"Blade sharpened", "Oil change"}


def test_jobs_only_for_that_customers_equipment(env):
    u = env.admin
    pat = _customer(u)
    sam = _customer(u, "Sam Other")
    job = _job(u, pat)
    r = u.post("/api/v1/repair-jobs", json={"customer_id": sam["id"], "item_id": job["item"]["id"]})
    assert r.status_code == 422 and "different customer" in r.json()["detail"]
    mine = u.item("My generator")
    r = u.post("/api/v1/repair-jobs", json={"customer_id": pat["id"], "item_id": mine["id"]})
    assert r.status_code == 422 and "household" in r.json()["detail"]
    assert u.ok("get", f"/api/v1/items/{mine['id']}")["owner"] is None


def test_deleting_customer_keeps_equipment_customer_owned(env):
    u = env.admin
    c = _customer(u)
    job = _job(u, c)
    assert u.delete(f"/api/v1/customers/{c['id']}").status_code == 422  # open job
    u.ok("post", f"/api/v1/repair-jobs/{job['id']}/status", json={"status": "returned"})
    u.ok("delete", f"/api/v1/customers/{c['id']}", expected=204)
    item = u.ok("get", f"/api/v1/items/{job['item']['id']}")
    assert item["owner"]["customer_id"] == c["id"] and item["owner"]["name"].endswith("(deleted)")
    assert u.ok("get", "/api/v1/stats")["total_items"] == 0  # still not household
    # job keeps its customer details (snapshot)
    assert u.ok("get", f"/api/v1/repair-jobs/{job['id']}")["customer"]["name"] == "Pat Neighbour"


def test_read_only_users_cannot_see_customer_information(env):
    u = env.admin
    c = _customer(u)
    job = _job(u, c)
    viewer = _viewer(env)
    for path in ("/api/v1/customers", f"/api/v1/customers/{c['id']}", "/api/v1/repair-jobs", f"/api/v1/repair-jobs/{job['id']}",
                 f"/api/v1/items/{job['item']['id']}/repair-jobs", "/api/v1/maintenance/dashboard?owner=customer"):
        assert viewer.get(path).status_code == 403, path
    # Customer equipment is not visible to read-only users at all (0.7.1).
    assert viewer.get(f"/api/v1/items/{job['item']['id']}").status_code == 404
    listed = viewer.ok("get", "/api/v1/items")["items"]
    assert all(i["id"] != job["item"]["id"] and not i.get("owner") for i in listed)
    events = viewer.ok("get", "/api/v1/history")["events"]
    assert not any("Pat" in e["summary"] for e in events), [e["summary"] for e in events]
    # members can see them
    sam = env.user("u-sam", "Sam Member")
    assert sam.ok("get", f"/api/v1/customers/{c['id']}")["name"] == "Pat Neighbour"


def test_maintenance_dashboard_filters_household_and_customer(env):
    u = env.admin
    mine = u.item("My generator")
    u.ok("post", f"/api/v1/items/{mine['id']}/maintenance-tasks", json={"title": "Exercise", "interval_days": 30,
                                                                        "first_due_on": "2020-01-01"})
    c = _customer(u)
    job = _job(u, c)
    u.ok("post", f"/api/v1/items/{job['item']['id']}/maintenance-tasks", json={"title": "Check plug", "interval_days": 30,
                                                                               "first_due_on": "2020-01-01"})
    both = u.ok("get", "/api/v1/maintenance/dashboard")
    assert {t["title"] for t in both["overdue"]} == {"Exercise", "Check plug"}
    assert [j["number"] for j in both["repairs_in_progress"]] == ["RJ-0001"]
    home = u.ok("get", "/api/v1/maintenance/dashboard?owner=household")
    assert [t["title"] for t in home["overdue"]] == ["Exercise"] and "repairs_in_progress" not in home
    theirs = u.ok("get", "/api/v1/maintenance/dashboard?owner=customer")
    assert [t["title"] for t in theirs["overdue"]] == ["Check plug"]


def test_job_photos_and_contact_log(env):
    from tests.conftest import jpeg_bytes

    u = env.admin
    c = _customer(u)
    job = _job(u, c)
    att = u.ok("post", "/api/v1/attachments", files={"file": ("dmg.jpg", jpeg_bytes(), "image/jpeg")},
               data={"kind": "photo", "repair_job_id": str(job["id"]), "caption": "Damage on arrival"})
    detail = u.ok("get", f"/api/v1/repair-jobs/{job['id']}")
    assert [a["id"] for a in detail["attachments"]] == [att["id"]]
    cust = u.ok("post", f"/api/v1/customers/{c['id']}/contacts", json={"channel": "phone", "note": "Quoted $60, approved"})
    assert cust["contacts"][0]["note"] == "Quoted $60, approved"


def test_customer_search(env):
    u = env.admin
    _customer(u, "Alice Jones")
    _customer(u, "Bob Smith")
    assert [c["name"] for c in u.ok("get", "/api/v1/customers?q=smi")] == ["Bob Smith"]
    assert [c["name"] for c in u.ok("get", "/api/v1/customers?q=555")] == ["Alice Jones", "Bob Smith"]


def test_job_ticket_number_lookup_stock_part_cost_and_status_message_data(env):
    u = env.admin
    c = _customer(u)
    job = _job(u, c)
    # Ticket: a small PDF with the job's QR code; the QR's number opens the job
    r = u.get(f"/api/v1/repair-jobs/{job['id']}/ticket.pdf", params={"base": "https://ha.example"})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert u.ok("get", "/api/v1/repair-jobs/by-number/rj-0001")["id"] == job["id"]
    assert u.get("/api/v1/repair-jobs/by-number/RJ-9999").status_code == 404
    # A part taken from household stock is costed at what it cost
    plug = u.item("Spark plug RN9YC", quantity=4, purchase_price="3.25")
    job = u.ok("post", f"/api/v1/repair-jobs/{job['id']}/service", json={
        "title": "New plug", "parts": [{"description": "Spark plug", "item_id": plug["id"], "quantity": "1", "deduct_stock": True}]})
    assert job["parts_charge"] == 3.25
    assert u.ok("get", f"/api/v1/items/{plug['id']}")["quantity"] == 3
    # The status event says what happened, but never who the customer is
    u.ok("post", f"/api/v1/repair-jobs/{job['id']}/status", json={"status": "completed"})
    ev = [m for m in env.state.bus.recent if m["type"] == "repair_job_status"][-1]["data"]
    assert ev["status"] == "completed" and ev["status_label"].startswith("Completed") and ev["job_id"] == job["id"]
    assert not any(v in str(ev) for v in ("Pat", "555-0100", "pat@example.com"))


def test_business_details_for_invoices_are_settings(env):
    s = env.admin.ok("patch", "/api/v1/settings", json={"business_name": "Steve's Small Engines", "business_details": "1 High St\nVAT 123"})
    assert s["business_name"] == "Steve's Small Engines" and s["business_details"].startswith("1 High St")

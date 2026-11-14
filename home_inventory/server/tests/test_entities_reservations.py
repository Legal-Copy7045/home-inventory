"""Home Assistant entities on equipment, stock reserved for repairs, and
what to buy across everything."""

from __future__ import annotations

from datetime import date, timedelta

from tests.test_customers import _customer, _job
from tests.test_meter_sensors import _s, fake_ha  # noqa: F401 - fixture
from tests.test_parts import _set

TODAY = date.today()


def _gen(u):
    g = u.item("Honda EU2200i", manufacturer="Honda")
    u.ok("put", f"/api/v1/items/{g['id']}/equipment", json={"equipment_type": "Generator", "meter": "none"})
    return g


def test_entities_are_checked_then_confirmed(env, fake_ha):  # noqa: F811
    u = env.admin
    gen = _gen(u)
    fake_ha.states_by_id.update({
        "sensor.gen_battery": _s("sensor.gen_battery", 87, "%"),
        "sensor.gen_runtime": _s("sensor.gen_runtime", 120, "min"),
        "sensor.gen_power": _s("sensor.gen_power", 400, "W"),
        "sensor.porch_temp": _s("sensor.porch_temp", 12, "°C"),
    })
    base = f"/api/v1/equipment/{gen['id']}/entities"
    ok = u.ok("post", f"{base}/check", json={"entity_id": "sensor.gen_battery", "purpose": "battery"})
    assert ok["exists"] and ok["problems"] == [] and ok["state"]["value"] == "87"
    odd = u.ok("post", f"{base}/check", json={"entity_id": "sensor.porch_temp", "purpose": "battery"})
    assert any("unit" in p for p in odd["problems"])  # shown to the person, who decides
    missing = u.ok("post", f"{base}/check", json={"entity_id": "sensor.nope", "purpose": "battery"})
    assert not missing["exists"]
    # Saving needs the person's confirmation, and an entity Home Assistant knows.
    assert u.post(base, json={"entity_id": "sensor.gen_battery", "purpose": "battery"}).status_code == 422
    assert u.post(base, json={"entity_id": "sensor.nope", "purpose": "battery", "confirm": True}).status_code == 422
    u.ok("post", base, json={"entity_id": "sensor.gen_battery", "purpose": "battery", "confirm": True}, expected=201)
    # A meter must suit: the temperature sensor can't become the hour meter.
    assert u.post(base, json={"entity_id": "sensor.porch_temp", "purpose": "runtime", "confirm": True}).status_code == 422
    u.ok("post", base, json={"entity_id": "sensor.gen_runtime", "purpose": "runtime", "confirm": True}, expected=201)
    prof = u.ok("get", f"/api/v1/items/{gen['id']}/maintenance")["profile"]
    assert prof["hours_entity_id"] == "sensor.gen_runtime" and prof["meter"] == "hours"
    linked = {e["entity_id"]: e for e in u.ok("get", base)["entities"]}
    assert linked["sensor.gen_battery"]["state"]["value"] == "87" and linked["sensor.gen_runtime"]["meter"]
    # Gone from Home Assistant later: shown, never an error.
    del fake_ha.states_by_id["sensor.gen_battery"]
    gone = {e["entity_id"]: e for e in u.ok("get", f"/api/v1/equipment/{gen['id']}")["entities"]}
    assert gone["sensor.gen_battery"]["error"] == "Home Assistant no longer has this entity"
    # Removing a link
    u.ok("delete", f"{base}/sensor.gen_battery")
    u.ok("delete", f"{base}/sensor.gen_runtime")
    assert u.ok("get", base)["entities"] == []
    assert u.ok("get", f"/api/v1/items/{gen['id']}/maintenance")["profile"]["hours_entity_id"] is None
    # Never on ordinary items
    plain = u.item("Broom")
    assert u.get(f"/api/v1/equipment/{plain['id']}/entities").status_code == 404


def test_entities_without_home_assistant(env):
    gen = _gen(env.admin)
    r = env.admin.post(f"/api/v1/equipment/{gen['id']}/entities/check", json={"entity_id": "sensor.x", "purpose": "battery"})
    assert r.status_code == 422 and "can't reach Home Assistant" in r.json()["detail"]


def test_reserved_stock_stays_on_the_shelf_until_used(env):
    u = env.admin
    job = _job(u, _customer(u))
    plugs = u.item("Spark plug RJ19LM", quantity=3, purchase_price="4.00")
    base = f"/api/v1/repair-jobs/{job['id']}/reservations"
    r = u.ok("post", base, json={"item_id": plugs["id"], "quantity": 2}, expected=201)["reservations"][0]
    assert r["state"] == "reserved" and r["in_stock"] == 3 and r["available"] == 1
    assert u.ok("get", f"/api/v1/items/{plugs['id']}")["quantity"] == 3  # reserving never moves stock
    assert u.ok("get", f"/api/v1/items/{plugs['id']}")["reserved"] == 2
    assert u.post(base, json={"item_id": plugs["id"], "quantity": 2}).status_code == 422  # only 1 available
    assert u.post(base, json={"item_id": job["item"]["id"], "quantity": 1}).status_code == 422  # not customer equipment
    # Release: nothing else changes
    released = u.ok("post", f"/api/v1/reservations/{r['id']}/release")
    assert released["state"] == "released" and u.ok("get", f"/api/v1/items/{plugs['id']}")["quantity"] == 3
    # Use: the explicit step that takes it out of stock, recorded on the job
    r2 = u.ok("post", base, json={"item_id": plugs["id"], "quantity": 2}, expected=201)["reservations"][-1]
    used = u.ok("post", f"/api/v1/reservations/{r2['id']}/use")
    assert used["state"] == "used" and u.ok("get", f"/api/v1/items/{plugs['id']}")["quantity"] == 1
    full = u.ok("get", f"/api/v1/repair-jobs/{job['id']}")
    assert full["parts_charge"] == 8  # 2 x the plug's purchase price
    assert u.post(f"/api/v1/reservations/{r2['id']}/use").status_code == 422  # once only
    # Customer-only: a request without a user can't touch them.
    assert env.integration().get(base).status_code == 403


def test_reserved_stock_isnt_available_for_maintenance_and_demand_adds_up(env):
    u = env.admin
    mower = u.item("Mower")
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"meter": "none"})
    gen = _gen(u)
    plugs = u.item("Spark plug RJ19LM", quantity=2)
    old = (TODAY - timedelta(days=400)).isoformat()
    for machine in (mower, gen):  # the same plug for both machines: one line to buy
        t = u.ok("post", f"/api/v1/items/{machine['id']}/maintenance-tasks",
                 json={"title": "Replace plug", "interval_days": 365, "last_done_on": old})
        _set(u, t, [{"product_id": plugs["product_id"], "quantity": 1}])
    ready = u.ok("get", "/api/v1/parts/readiness")["lines"][0]
    assert ready["status"] == "in_stock" and ready["quantity"] == 2
    job = _job(u, _customer(u))
    u.ok("post", f"/api/v1/repair-jobs/{job['id']}/reservations", json={"item_id": plugs["id"], "quantity": 1}, expected=201)
    ready = u.ok("get", "/api/v1/parts/readiness")["lines"][0]
    assert ready["status"] == "partial" and ready["have"] == 1 and ready["reserved"] == 1
    u.item("AA batteries", quantity=1, min_quantity=4)
    d = u.ok("get", "/api/v1/replenishment/demand")
    lines = {x["name"]: x for g in d["groups"] for x in g["lines"]}
    plug = lines["Spark plug RJ19LM"]
    assert plug["buy_quantity"] == 1 and plug["in_stock"] == 2
    assert plug["reasons"][0]["kind"] == "maintenance" and "Honda EU2200i" in plug["reasons"][0]["text"]
    assert lines["AA batteries"]["reasons"][0]["kind"] == "minimum" and lines["AA batteries"]["buy_quantity"] >= 3
    assert d["count"] == 2

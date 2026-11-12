"""Operating hours read from Home Assistant sensors."""

from decimal import Decimal

import pytest

from home_inventory_server import ha_api
from home_inventory_server.models import MeterKind
from home_inventory_server.services import sensor_meters
from tests.test_privacy import _viewer


class FakeHA:
    def __init__(self, states):
        self.states_by_id = states

    def state(self, entity_id):
        return self.states_by_id.get(entity_id)

    def states(self):
        return list(self.states_by_id.values())


def _s(eid, state, unit=None, **attrs):
    a = dict(attrs)
    if unit:
        a["unit_of_measurement"] = unit
    return {"entity_id": eid, "state": str(state), "attributes": a}


@pytest.fixture
def fake_ha(monkeypatch):
    ha = FakeHA({})
    monkeypatch.setattr(ha_api.HAClient, "maybe", classmethod(lambda cls, settings: ha))
    return ha


def _mower(u):
    item = u.item("Lawn mower", location_id=u.location("Garage")["id"])
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json={"meter": "both", "hours_entity_id": "sensor.mower_runtime",
                                                                "cycles_entity_id": "counter.mower_starts"})
    return item


def test_to_value_converts_units_and_rejects_bad_states():
    assert sensor_meters.to_value(_s("sensor.x", 90, "min"), MeterKind.hours) == (Decimal("1.5"), None)
    assert sensor_meters.to_value(_s("sensor.x", 7200, "s"), MeterKind.hours) == (Decimal("2.0"), None)
    assert sensor_meters.to_value(_s("sensor.x", "12.34"), MeterKind.hours) == (Decimal("12.3"), None)  # default hours
    assert sensor_meters.to_value(_s("sensor.x", 2, "d"), MeterKind.hours)[0] == Decimal("48.0")
    assert sensor_meters.to_value(_s("sensor.x", 5, "kWh"), MeterKind.hours)[1] == "unit 'kwh' isn't a time unit"
    assert sensor_meters.to_value(_s("sensor.x", "unavailable"), MeterKind.hours)[0] is None
    assert sensor_meters.to_value(_s("sensor.x", "abc"), MeterKind.hours)[0] is None
    assert sensor_meters.to_value(None, MeterKind.hours)[1] == "entity not found"
    assert sensor_meters.to_value(_s("counter.c", 42), MeterKind.cycles) == (Decimal("42.0"), None)


def test_linked_sensors_record_readings_only_when_they_move_on(env, fake_ha):
    u = env.admin
    item = _mower(u)
    prof = u.ok("get", f"/api/v1/items/{item['id']}/maintenance")["profile"]
    assert prof["hours_entity_id"] == "sensor.mower_runtime" and prof["cycles_entity_id"] == "counter.mower_starts"
    fake_ha.states_by_id.update({"sensor.mower_runtime": _s("sensor.mower_runtime", 600, "min"),
                                 "counter.mower_starts": _s("counter.mower_starts", 12)})
    res = u.ok("post", f"/api/v1/items/{item['id']}/meter-sync")["results"]
    assert [(r["kind"], r["value"], r["recorded"]) for r in res] == [("hours", "10.0", True), ("cycles", "12.0", True)]
    # Unchanged: nothing new recorded
    res = u.ok("post", f"/api/v1/items/{item['id']}/meter-sync")["results"]
    assert not any(r["recorded"] for r in res)
    # Reset sensor: refused with a reason, not recorded
    fake_ha.states_by_id["sensor.mower_runtime"] = _s("sensor.mower_runtime", 30, "min")
    res = u.ok("post", f"/api/v1/items/{item['id']}/meter-sync")["results"]
    assert res[0]["recorded"] is False and "reset" in res[0]["problem"]
    m = u.ok("get", f"/api/v1/items/{item['id']}/maintenance")
    assert m["meters"]["hours"] == 10.0
    readings = m["readings"]
    assert any(r["source"] == "sensor" and "sensor.mower_runtime" in (r["note"] or "") for r in readings)


def test_background_sync_and_due_tasks_follow_sensor(env, fake_ha):
    u = env.admin
    item = _mower(u)
    u.ok("post", f"/api/v1/items/{item['id']}/maintenance-tasks", json={"title": "Oil change", "interval_hours": 25})
    fake_ha.states_by_id.update({"sensor.mower_runtime": _s("sensor.mower_runtime", 26, "h"),
                                 "counter.mower_starts": _s("counter.mower_starts", "unavailable")})
    db = env.state.db.SessionLocal()
    try:
        assert sensor_meters.sync_all(db, fake_ha) == 1
    finally:
        db.close()
    tasks = u.ok("get", f"/api/v1/items/{item['id']}/maintenance")["tasks"]
    assert tasks[0]["status"] == "overdue"


def test_meter_sensor_validation_and_access(env, fake_ha):
    u = env.admin
    item = u.item("Saw", location_id=u.location("Shed")["id"])
    assert u.put(f"/api/v1/items/{item['id']}/equipment", json={"hours_entity_id": "not an entity"}).status_code == 422
    assert u.post(f"/api/v1/items/{item['id']}/meter-sync").status_code == 422  # nothing linked
    fake_ha.states_by_id.update({"sensor.saw_runtime": _s("sensor.saw_runtime", 3, "h", friendly_name="Saw runtime"),
                                 "sensor.temp": _s("sensor.temp", 21, "°C"), "light.x": _s("light.x", "on")})
    cands = u.ok("get", "/api/v1/ha/meter-sensors")
    assert cands[0]["entity_id"] == "sensor.saw_runtime" and cands[0]["time"] is True
    assert "light.x" not in [c["entity_id"] for c in cands]
    # Clearing the link with ""
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json={"hours_entity_id": "sensor.saw_runtime"})
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json={"hours_entity_id": ""})
    assert u.ok("get", f"/api/v1/items/{item['id']}/maintenance")["profile"]["hours_entity_id"] is None
    viewer = _viewer(env)
    assert viewer.get("/api/v1/ha/meter-sensors").status_code == 403


def test_no_home_assistant_api(env, monkeypatch):
    monkeypatch.setattr(ha_api.HAClient, "maybe", classmethod(lambda cls, settings: None))
    assert env.admin.get("/api/v1/ha/meter-sensors").status_code == 503

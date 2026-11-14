"""Season mode, fuel & consumables, meter readings, service PDF, worth fixing."""

from datetime import UTC, date, datetime, timedelta

from home_inventory_server.services import equipment as eq


def _mower(u, name="Lawn mower", preset="lawn_mower"):
    item = u.item(name, category="Equipment")
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json={"preset": preset})
    return item


def test_which_season():
    assert eq.which_season(date(2026, 3, 1)) == "spring" and eq.which_season(date(2026, 12, 1)) == "spring"
    assert eq.which_season(date(2026, 9, 15)) == "autumn"
    assert eq.which_season(date(2026, 9, 15), "south") == "spring" and eq.which_season(date(2026, 4, 1), "south") == "autumn"


def test_season_jobs_and_consumables(env):
    u = env.admin
    _mower(u)
    _mower(u, "Chainsaw", "chainsaw")
    u.item("2 stroke oil", quantity=0)
    u.item("Bar and chain oil", quantity=2)
    spring = u.ok("get", "/api/v1/maintenance/season", params={"season": "spring"})
    mower = next(m for m in spring["machines"] if m["item"]["name"] == "Lawn mower")
    assert any("Pre-season" in j["title"] for j in mower["jobs"]) and any("oil" in j["title"].lower() for j in mower["jobs"])
    autumn = u.ok("get", "/api/v1/maintenance/season", params={"season": "autumn"})
    assert any("Winterize" in j["title"] for m in autumn["machines"] for j in m["jobs"])
    needs = {c["need"]: c for c in spring["consumables"]}
    assert needs["2-stroke oil"]["status"] == "none" and "Chainsaw" in needs["2-stroke oil"]["machines"]
    assert needs["Chain / bar oil"]["status"] == "ok" and needs["Chain / bar oil"]["have"][0]["name"] == "Bar and chain oil"
    assert "Engine oil" in needs and "Lawn mower" in needs["Engine oil"]["machines"]
    assert u.ok("post", "/api/v1/maintenance/season/shopping", params={"season": "spring"})["added"] == []  # no parts linked


def test_meter_readings_due_and_monthly_reminder(env):
    u = env.admin
    item = _mower(u)
    due = u.ok("get", "/api/v1/maintenance/readings-due")["machines"]
    assert [m["item_id"] for m in due] == [item["id"]] and due[0]["last_reading"] is None
    u.ok("post", f"/api/v1/items/{item['id']}/meter-readings", json={"kind": "hours", "value": 12})
    assert u.ok("get", "/api/v1/maintenance/readings-due")["machines"] == []
    with env.state.db.SessionLocal() as db:
        from home_inventory_server.models import MeterReading

        r = db.query(MeterReading).filter_by(item_id=item["id"]).one()
        r.recorded_at = datetime.now(UTC) - timedelta(days=45)
        db.commit()
        assert eq.monthly_reading_reminder(db) == 1
        db.commit()
        assert eq.monthly_reading_reminder(db) == 0  # once a month


def test_service_history_pdf(env):
    u = env.admin
    item = _mower(u)
    u.ok("post", f"/api/v1/items/{item['id']}/meter-readings", json={"kind": "hours", "value": 30})
    r = u.get(f"/api/v1/items/{item['id']}/service-history.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF" and "service-history-Lawn-mower" in r.headers["content-disposition"]


def test_worth_fixing(env):
    u = env.admin
    item = u.item("Leaf blower", purchase_price="200", purchase_date=str(date.today() - timedelta(days=400)))
    fix = u.ok("get", f"/api/v1/items/{item['id']}/worth-fixing", params={"repair_cost": "30"})
    assert fix["verdict"] == "fix" and "15%" in fix["reason"]
    assert u.ok("get", f"/api/v1/items/{item['id']}/worth-fixing", params={"repair_cost": "150"})["verdict"] == "replace"
    assert u.get(f"/api/v1/items/{item['id']}/worth-fixing", params={"repair_cost": "lots"}).status_code == 422
    bare = u.item("Mystery box")
    assert u.ok("get", f"/api/v1/items/{bare['id']}/worth-fixing", params={"repair_cost": "10"})["verdict"] == "unknown"

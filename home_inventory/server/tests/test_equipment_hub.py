"""The Equipment section: managed equipment over the shared item model,
its overview, checklist (with sources), parts status and reports."""

from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import Env, FakeProvider
from tests.test_parts import MANUAL, _pdf, _read, _set, _task

TODAY = date.today()


def _mower(u, **kw):
    mower = u.item("Toro Recycler 22-inch Personal Pace", manufacturer="Toro", model_number="20333",
                   serial_number="TR-1", **kw)
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower", "current_hours": 10})
    return mower


def test_equipment_is_a_managed_item_not_a_separate_inventory(env):
    u = env.admin
    mower = _mower(u)
    oil = u.item("Engine oil SAE 30", quantity=2)
    listed = u.ok("get", "/api/v1/equipment")["equipment"]
    assert [e["id"] for e in listed] == [mower["id"]]  # the oil is ordinary inventory
    assert listed[0]["plan"] == "generic"  # the preset is a typical schedule, said so
    # Same record everywhere: global search, the item itself.
    found = u.ok("get", "/api/v1/search", params={"q": "Toro"})["results"]
    assert any(r["id"] == mower["id"] for r in found)
    assert u.ok("get", f"/api/v1/items/{mower['id']}")["id"] == mower["id"]
    # Search, type and attention filters
    assert u.ok("get", "/api/v1/equipment", params={"q": "recycler"})["equipment"][0]["id"] == mower["id"]
    assert u.ok("get", "/api/v1/equipment", params={"q": "chainsaw"})["equipment"] == []
    assert u.ok("get", "/api/v1/equipment", params={"equipment_type": "Lawn mower"})["equipment"]
    # Using oil for the mower doesn't make the oil equipment.
    task = next(t for t in u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["tasks"] if "oil" in t["title"].lower())
    _set(u, task, [{"product_id": oil["product_id"], "quantity": 1}])
    assert [e["id"] for e in u.ok("get", "/api/v1/equipment")["equipment"]] == [mower["id"]]
    assert u.get(f"/api/v1/equipment/{oil['id']}").status_code == 404


def test_turning_equipment_back_into_an_item_keeps_everything(env):
    u = env.admin
    mower = _mower(u)
    tasks = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["tasks"]
    u.ok("post", f"/api/v1/maintenance-tasks/{tasks[0]['id']}/complete", json={"hours": 12, "notes": "Done"})
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"engine_model": "Briggs 140cc", "lifecycle_status": "storage"})
    u.ok("delete", f"/api/v1/items/{mower['id']}/equipment", expected=204)
    assert u.ok("get", "/api/v1/equipment")["equipment"] == []
    assert u.ok("get", f"/api/v1/items/{mower['id']}")["id"] == mower["id"]  # still an item
    m = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")
    assert m["history"] and m["profile"]["engine_model"] == "Briggs 140cc"
    # Managed again: details, tasks and history are all back, no duplicates.
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={})
    again = u.ok("get", f"/api/v1/equipment/{mower['id']}")
    assert again["profile"]["managed"] and again["profile"]["engine_model"] == "Briggs 140cc"
    assert again["lifecycle_label"] == "In storage"
    assert again["summary"]["tasks"] == len(tasks) and again["last_completed"]["notes"] == "Done"


def test_overview_shows_what_needs_attention_and_the_parts(env):
    u = env.admin
    shelf = u.location("Garage shelf")
    mower = _mower(u, purchase_price="350", warranty_expires=(TODAY + timedelta(days=200)).isoformat())
    plug = u.item("Spark plug RJ19LM", quantity=1, location_id=shelf["id"])
    filt = u.item("Air filter 36905", quantity=0)
    oil = _task(u, mower, "Change engine oil", interval_hours=5, last_done_hours=4)  # due at 9 h; meter at 10 h
    _set(u, oil, [{"product_id": plug["product_id"], "quantity": 1}, {"product_id": filt["product_id"], "quantity": 1}])
    ov = u.ok("get", f"/api/v1/equipment/{mower['id']}")
    assert ov["summary"]["attention"] == "overdue" and ov["summary"]["overdue"] >= 1
    assert any(t["title"] == "Change engine oil" for t in ov["upcoming"])
    parts = {p["product"]["name"]: p for p in ov["parts"]}
    assert parts["Spark plug RJ19LM"]["status"] == "in_stock" and parts["Spark plug RJ19LM"]["needed_soon"]
    where = parts["Spark plug RJ19LM"]["sources"][0]["items"][0]["where"]
    assert where == "Garage shelf"
    assert parts["Air filter 36905"]["status"] == "missing" and parts["Air filter 36905"]["buy"]
    assert ov["warranty"]["active"] and ov["meters"]["hours"] == 10
    assert ov["plan"]["state"] == "generic"


def test_checklist_groups_sources_and_pdf(env):
    u = env.admin
    mower = _mower(u)
    _task(u, mower, "Check oil level", schedule_group="before_use", task_kind="inspect")
    _task(u, mower, "Clean deck", schedule_group="after_use", task_kind="clean")
    cl = u.ok("get", f"/api/v1/equipment/{mower['id']}/checklist")
    groups = {g["key"]: g for g in cl["groups"]}
    assert [t["title"] for t in groups["before_use"]["tasks"]] == ["Check oil level"]
    assert groups["before_use"]["label"] == "Before each use"
    preset = next(t for g in cl["groups"] for t in g["tasks"] if t["source"] == "generic")
    assert "not from this model's manual" in preset["source_ref"]
    pdf = u.get(f"/api/v1/equipment/{mower['id']}/checklist.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_reports_across_equipment(env):
    u = env.admin
    mower = _mower(u)
    gen = u.item("Honda generator EU2200i")
    u.ok("put", f"/api/v1/items/{gen['id']}/equipment", json={"meter": "hours", "current_hours": 0})
    late = _task(u, gen, "Air filter inspection", interval_days=30, last_done_on=(TODAY - timedelta(days=45)).isoformat())
    soon = _task(u, gen, "Oil change", interval_days=30, last_done_on=(TODAY - timedelta(days=25)).isoformat())
    u.ok("post", f"/api/v1/maintenance-tasks/{soon['id']}/complete", json={"labor_cost": "10",
                                                                           "parts": [{"description": "Oil", "unit_cost": "8"}]})
    overdue = u.ok("get", "/api/v1/maintenance/reports/overdue")["tasks"]
    assert late["id"] in [t["id"] for t in overdue]
    upcoming = u.ok("get", "/api/v1/maintenance/reports/upcoming", params={"days": 400})["tasks"]
    assert soon["id"] in [t["id"] for t in upcoming] and all(t["status"] != "overdue" for t in upcoming)
    hist = u.ok("get", "/api/v1/maintenance/reports/history")
    assert hist["events"][0]["title"] == "Oil change" and hist["total_cost"] == 18
    cons = u.ok("get", "/api/v1/maintenance/reports/consolidated")["equipment"]
    assert {e["id"] for e in cons} == {mower["id"], gen["id"]} and cons[0]["attention"] == "overdue"
    assert u.get("/api/v1/maintenance/reports/everything").status_code == 422


def test_completing_maintenance_never_takes_stock_unless_asked(env):
    u = env.admin
    mower = _mower(u)
    oil = u.item("Oil bottle", quantity=3)
    task = _task(u, mower, "Oil", interval_hours=25)
    u.ok("post", f"/api/v1/maintenance-tasks/{task['id']}/complete",
         json={"hours": 30, "parts": [{"description": "Oil", "item_id": oil["id"], "quantity": 1}]})
    assert u.ok("get", f"/api/v1/items/{oil['id']}")["quantity"] == 3  # recorded, not taken
    u.ok("post", f"/api/v1/maintenance-tasks/{task['id']}/complete",
         json={"hours": 55, "parts": [{"description": "Oil", "item_id": oil["id"], "quantity": 1, "deduct_stock": True}]})
    assert u.ok("get", f"/api/v1/items/{oil['id']}")["quantity"] == 2  # only when confirmed
    # Changing the schedule later doesn't rewrite what was done.
    u.ok("patch", f"/api/v1/maintenance-tasks/{task['id']}", json={"interval_hours": 50, "title": "Oil change"})
    hist = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["history"]
    assert [h["title"] for h in hist[:2]] == ["Oil", "Oil"] and [h["hours"] for h in hist[:2]] == [55, 30]


def _manual_env(tmp_path, model="HRX217"):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = u.item("Honda mower", manufacturer="Honda", model_number=model)
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"meter": "hours", "current_hours": 0})
    pdf = u.upload(_pdf(["Maintenance schedule", "Every 50 hours: change oil"]), kind="manual",
                   filename="HRX217 owners manual.pdf", item_id=mower["id"], recognise=False)
    return fake, u, mower, pdf


def _services(res):
    return [{k: s[k] for k in ("title", "interval_hours", "interval_days", "first_at_hours", "instructions", "page",
                               "when", "kind", "section")} for s in res["services"]]


MANUAL_V2 = {**MANUAL, "equipment": {**MANUAL["equipment"], "models_covered": ["HRX217", "HRX217K5"],
                                     "schedule_varies_by_model": False},
             "services": [{**s, "when": "hours", "kind": "service", "section": "Maintenance Schedule"} for s in MANUAL["services"]]
             + [{"title": "Check engine oil level", "interval_hours": None, "interval_days": None, "interval_cycles": None,
                 "first_at_hours": None, "when": "before_use", "kind": "inspect", "section": "Pre-operation checks",
                 "instructions": None, "needs": [], "page": 8}]}


def test_manual_checklist_is_traceable_and_keeps_user_edits(tmp_path):
    fake, u, mower, pdf = _manual_env(tmp_path)
    fake.responses.append(MANUAL_V2)
    reading = _read(u, mower["id"], [pdf["id"]])
    assert reading["result"]["model_check"]["status"] == "match"
    u.ok("post", f"/api/v1/manual-readings/{reading['id']}/apply", json={"services": _services(reading["result"])})
    cl = u.ok("get", f"/api/v1/equipment/{mower['id']}/checklist")
    groups = {g["key"]: g["tasks"] for g in cl["groups"]}
    check = groups["before_use"][0]
    assert check["title"] == "Check engine oil level" and check["task_kind"] == "inspect"
    assert check["source"] == "manual" and check["source_ref"] == "HRX217 owners manual.pdf, Pre-operation checks, page 8"
    oil = next(t for t in groups["hours"] if t["title"] == "Change engine oil")
    assert oil["interval_hours"] == 50
    assert u.ok("get", f"/api/v1/equipment/{mower['id']}")["plan"]["state"] == "from_manual"
    # The person changes the oil interval and disables a task they don't need.
    u.ok("patch", f"/api/v1/maintenance-tasks/{oil['id']}", json={"interval_hours": 40})
    u.ok("post", f"/api/v1/maintenance-tasks/{check['id']}/not-applicable", json={"value": True})
    # A better manual: read again.  Their 40 h stays; the manual's new figure is recorded and flagged.
    changed = {**MANUAL_V2, "services": [{**s, "interval_hours": 60} if s["title"] == "Change engine oil" else s
                                         for s in MANUAL_V2["services"]]}
    fake.responses.append(changed)
    again = _read(u, mower["id"], [pdf["id"]])
    out = u.ok("post", f"/api/v1/manual-readings/{again['id']}/apply", json={"services": _services(again["result"])})
    assert out["summary"]["tasks_created"] == 0  # updated in place, never duplicated
    tasks = {t["title"]: t for t in u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["tasks"]}
    assert tasks["Change engine oil"]["interval_hours"] == 40 and tasks["Change engine oil"]["user_edited"]
    assert tasks["Change engine oil"]["manual_values"]["interval_hours"] == 60
    assert tasks["Change engine oil"]["review_status"] == "needs_review"
    assert tasks["Check engine oil level"]["not_applicable"]  # still disabled
    # Reviewed: approve keeps their value.
    u.ok("patch", f"/api/v1/maintenance-tasks/{tasks['Change engine oil']['id']}", json={"review_status": "approved"})
    assert u.ok("get", f"/api/v1/equipment/{mower['id']}")["plan"]["needs_review"] == 0


def test_uncertain_manual_model_needs_confirmation(tmp_path):
    fake, u, mower, pdf = _manual_env(tmp_path, model="HRN216")
    fake.responses.append(MANUAL_V2)
    reading = _read(u, mower["id"], [pdf["id"]])
    check = reading["result"]["model_check"]
    assert check["status"] == "mismatch" and check["confirm"] and "HRN216" in check["message"]
    r = u.post(f"/api/v1/manual-readings/{reading['id']}/apply", json={"services": _services(reading["result"])})
    assert r.status_code == 422 and "Confirm the manual applies" in r.json()["detail"]
    assert u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["tasks"] == []  # nothing applied
    u.ok("post", f"/api/v1/manual-readings/{reading['id']}/apply",
         json={"services": _services(reading["result"]), "confirm_model": True})
    assert u.ok("get", f"/api/v1/equipment/{mower['id']}")["plan"]["state"] == "from_manual_confirmed"


def test_manual_covering_variants_with_different_schedules_is_not_chosen_silently(tmp_path):
    fake, u, mower, pdf = _manual_env(tmp_path)
    fake.responses.append({**MANUAL_V2, "equipment": {**MANUAL_V2["equipment"], "schedule_varies_by_model": True}})
    reading = _read(u, mower["id"], [pdf["id"]])
    assert reading["result"]["model_check"]["status"] == "variants" and reading["result"]["model_check"]["confirm"]

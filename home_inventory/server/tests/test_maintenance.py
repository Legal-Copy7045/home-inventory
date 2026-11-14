"""Equipment maintenance: profiles, schedules, meters, history."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.conftest import jpeg_bytes

TODAY = date.today()


def _mower(u):
    item = u.item("Honda HRX217 lawn mower", manufacturer="Honda", model_number="HRX217", serial_number="MZCG-123")
    return item


def _summary(u, item_id):
    return u.ok("get", f"/api/v1/items/{item_id}/maintenance")


def _task(summary, title):
    return next(t for t in summary["tasks"] if t["title"] == title)


def test_profile_is_optional_and_preset_creates_editable_tasks(env):
    u = env.admin
    mower = _mower(u)
    s = _summary(u, mower["id"])
    assert s["profile"] is None and s["tasks"] == [] and s["history"] == []

    s = u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower", "engine_model": "GCV200"})
    assert s["profile"]["equipment_type"] == "Lawn mower" and s["profile"]["meter"] == "hours"
    assert s["profile"]["engine_model"] == "GCV200"
    assert {"Engine oil", "Spark plug", "Air filter"} <= {x["label"] for x in s["profile"]["specs"]}
    oil = _task(s, "Engine oil change")
    assert oil["interval_hours"] == 50 and oil["interval_days"] == 365
    assert oil["next_due_hours"] == 50 and oil["next_due_on"] == (TODAY + timedelta(days=365)).isoformat()
    # applying the preset again doesn't duplicate tasks
    s = u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower"})
    assert sum(1 for t in s["tasks"] if t["title"] == "Engine oil change") == 1
    # specs are free-form
    s = u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"specs": [
        {"label": "Engine oil", "value": "SAE 30", "kind": "spec"},
        {"label": "Spark plug", "value": "NGK", "kind": "part", "part_number": "BPR6ES"},
        {"label": "Deck belt", "value": "", "kind": "part", "part_number": "754-0467"}]})
    assert [x["part_number"] for x in s["profile"]["specs"]][1:] == ["BPR6ES", "754-0467"]


def test_used_equipment_schedules_start_from_current_hours(env):
    u = env.admin
    mower = u.item("Used mower")
    s = u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower", "current_hours": 212})
    oil = _task(s, "Engine oil change")
    assert s["meters"]["hours"] == 212
    assert oil["next_due_hours"] == 262 and oil["status"] == "ok"


def test_non_engine_equipment_and_custom_tasks(env):
    u = env.admin
    ladder = u.item("Extension ladder")
    s = u.ok("put", f"/api/v1/items/{ladder['id']}/equipment", json={"preset": "other", "equipment_type": "Ladder", "meter": "none"})
    assert s["profile"]["equipment_type"] == "Ladder" and s["tasks"] == []
    t = u.ok("post", f"/api/v1/items/{ladder['id']}/maintenance-tasks", json={"title": "Inspect rungs and feet", "interval_days": 180})
    assert t["status"] == "ok" and t["next_due_on"] == (TODAY + timedelta(days=180)).isoformat()
    manual = u.ok("post", f"/api/v1/items/{ladder['id']}/maintenance-tasks", json={"title": "Replace feet when worn"})
    assert manual["status"] == "manual"


def test_calendar_due_states(env):
    u = env.admin
    gen = u.item("Generator")
    first = (TODAY + timedelta(days=5)).isoformat()
    t = u.ok("post", f"/api/v1/items/{gen['id']}/maintenance-tasks", json={"title": "Exercise run", "interval_days": 30, "first_due_on": first})
    assert t["status"] == "due_soon" and t["days_left"] == 5
    t = u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_on": (TODAY - timedelta(days=2)).isoformat()})
    assert t["status"] == "overdue" and t["days_left"] == -2
    t = u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_on": (TODAY + timedelta(days=60)).isoformat()})
    assert t["status"] == "ok"


def test_operating_hours_completion_moves_next_threshold(env):
    u = env.admin
    mower = _mower(u)
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower"})
    u.ok("post", f"/api/v1/items/{mower['id']}/meter-readings", json={"value": 47})
    s = _summary(u, mower["id"])
    assert s["meters"]["hours"] == 47
    oil = _task(s, "Engine oil change")
    assert oil["status"] == "due_soon" and oil["hours_left"] == 3
    u.ok("post", f"/api/v1/items/{mower['id']}/meter-readings", json={"value": 51.5})
    oil = _task(_summary(u, mower["id"]), "Engine oil change")
    assert oil["status"] == "overdue"

    s = u.ok("post", f"/api/v1/maintenance-tasks/{oil['id']}/complete", json={
        "hours": 52, "work_performed": "Drained and refilled", "labor_cost": "0",
        "parts": [{"description": "SAE 30 oil", "quantity": "0.5", "unit_cost": "8.99"}]})
    oil = _task(s, "Engine oil change")
    assert oil["status"] == "ok"
    assert oil["last_done_hours"] == 52 and oil["next_due_hours"] == 102
    assert oil["next_due_on"] == (TODAY + timedelta(days=365)).isoformat()
    assert s["meters"]["hours"] == 52  # the service reading updated the meter
    ev = s["history"][0]
    assert ev["title"] == "Engine oil change" and ev["hours"] == 52 and ev["outcome"] == "completed"
    assert ev["parts"][0]["description"] == "SAE 30 oil" and ev["parts_cost"] == pytest.approx(4.5, abs=0.01)
    assert ev["performed_by"] == "Alex Admin" and ev["snapshot"]["serial_number"] == "MZCG-123"


def test_meter_cannot_go_backwards_without_correction(env):
    u = env.admin
    saw = u.item("Chainsaw")
    u.ok("post", f"/api/v1/items/{saw['id']}/meter-readings", json={"value": 30})
    r = u.post(f"/api/v1/items/{saw['id']}/meter-readings", json={"value": 12})
    assert r.status_code == 422 and "correction" in r.json()["detail"]
    s = u.ok("post", f"/api/v1/items/{saw['id']}/meter-readings", json={"value": 12, "correction": True, "note": "new hour meter"})
    assert s["meters"]["hours"] == 12 and s["readings"][0]["source"] == "correction"
    s = u.ok("post", f"/api/v1/items/{saw['id']}/meter-readings", json={"value": 14, "estimated": True})
    assert s["readings"][0]["source"] == "estimated"


def test_cycles_based_task(env):
    u = env.admin
    saw = u.item("Stihl MS 250")
    s = u.ok("put", f"/api/v1/items/{saw['id']}/equipment", json={"preset": "chainsaw"})
    sharpen = _task(s, "Chain sharpening")
    assert sharpen["interval_cycles"] == 5 and sharpen["next_due_cycles"] == 5
    u.ok("post", f"/api/v1/items/{saw['id']}/meter-readings", json={"kind": "cycles", "value": 4})
    assert _task(_summary(u, saw["id"]), "Chain sharpening")["status"] == "due_soon"
    s = u.ok("post", f"/api/v1/maintenance-tasks/{sharpen['id']}/complete", json={"cycles": 6})
    sharpen = _task(s, "Chain sharpening")
    assert sharpen["next_due_cycles"] == 11 and sharpen["status"] == "ok"


def test_skip_defer_and_not_applicable(env):
    u = env.admin
    gen = u.item("Generator")
    t = u.ok("post", f"/api/v1/items/{gen['id']}/maintenance-tasks", json={"title": "Exercise run", "interval_days": 30})
    due = t["next_due_on"]
    s = u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/skip", json={"note": "Away"})
    skipped = _task(s, "Exercise run")
    assert skipped["next_due_on"] == (date.fromisoformat(due) + timedelta(days=30)).isoformat()
    assert skipped["last_done_on"] is None  # skipping is not doing
    s = u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/defer", json={"until": (TODAY + timedelta(days=3)).isoformat()})
    assert _task(s, "Exercise run")["status"] == "deferred"
    assert u.post(f"/api/v1/maintenance-tasks/{t['id']}/defer", json={"until": TODAY.isoformat()}).status_code == 422
    s = u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/not-applicable", json={"value": True})
    assert _task(s, "Exercise run")["status"] == "not_applicable"
    assert [e["outcome"] for e in s["history"]] == ["not_applicable", "deferred", "skipped"]
    s = u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/not-applicable", json={"value": False})
    assert _task(s, "Exercise run")["status"] != "not_applicable"


def test_history_survives_task_and_profile_changes(env):
    u = env.admin
    mower = _mower(u)
    s = u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"preset": "lawn_mower"})
    oil = _task(s, "Engine oil change")
    u.ok("post", f"/api/v1/maintenance-tasks/{oil['id']}/complete", json={"hours": 42.5})
    # historical entry from before the item was added
    s = u.ok("post", f"/api/v1/items/{mower['id']}/service-events", json={
        "title": "Blade sharpened", "performed_on": "2025-06-10", "hours": 20, "performed_by": "Local shop", "labor_cost": "15"})
    assert [e["title"] for e in s["history"]] == ["Engine oil change", "Blade sharpened"]
    assert s["meters"]["hours"] == 42.5  # an older reading doesn't lower the meter
    # change the schedule, rename and delete the task, drop the profile
    u.ok("patch", f"/api/v1/maintenance-tasks/{oil['id']}", json={"interval_hours": 25, "title": "Oil"})
    u.ok("delete", f"/api/v1/maintenance-tasks/{oil['id']}", expected=204)
    u.ok("delete", f"/api/v1/items/{mower['id']}/equipment", expected=204)
    s = _summary(u, mower["id"])
    # Back to an ordinary item: nothing is deleted, it's just not managed (or scheduled).
    assert s["profile"]["managed"] is False
    assert all(t["title"] != "Oil" for t in s["tasks"])  # the deleted task stays deleted
    assert not [t for t in u.ok("get", "/api/v1/maintenance/dashboard", params={"days": 3650})["overdue"]
                if t["item"]["id"] == mower["id"]]
    titles = {e["title"]: e for e in s["history"]}
    assert titles["Engine oil change"]["task_title"] == "Engine oil change" and titles["Engine oil change"]["hours"] == 42.5
    assert titles["Blade sharpened"]["performed_on"] == "2025-06-10"


def test_future_service_date_rejected(env):
    u = env.admin
    item = u.item("Pressure washer")
    r = u.post(f"/api/v1/items/{item['id']}/service-events", json={"title": "x", "performed_on": (TODAY + timedelta(days=2)).isoformat()})
    assert r.status_code == 422


def test_parts_from_stock_and_photos(env):
    u = env.admin
    mower = _mower(u)
    filters = u.item("Mower air filter", quantity=3)
    photo = u.upload()
    s = u.ok("post", f"/api/v1/items/{mower['id']}/service-events", json={
        "title": "Air filter replaced", "attachment_ids": [photo["id"]],
        "parts": [{"description": "Air filter", "item_id": filters["id"], "quantity": "1", "deduct_stock": True}]})
    assert s["history"][0]["attachments"][0]["id"] == photo["id"]
    assert u.ok("get", f"/api/v1/items/{filters['id']}")["quantity"] == 2
    r = u.post(f"/api/v1/items/{mower['id']}/service-events", json={
        "title": "Too many", "parts": [{"description": "Air filter", "item_id": filters["id"], "quantity": "5", "deduct_stock": True}]})
    assert r.status_code == 422
    assert u.ok("get", f"/api/v1/items/{filters['id']}")["quantity"] == 2  # nothing taken on failure


def test_dashboard_lists_overdue_due_and_missing_records(env):
    u = env.admin
    gen = u.item("Generator")
    mower = _mower(u)
    blower = u.item("Leaf blower")
    u.ok("put", f"/api/v1/items/{blower['id']}/equipment", json={"preset": "leaf_blower"})
    t1 = u.ok("post", f"/api/v1/items/{gen['id']}/maintenance-tasks", json={"title": "Exercise run", "interval_days": 30})
    u.ok("patch", f"/api/v1/maintenance-tasks/{t1['id']}", json={"next_due_on": (TODAY - timedelta(days=1)).isoformat()})
    u.ok("post", f"/api/v1/items/{mower['id']}/maintenance-tasks",
         json={"title": "Pre-season inspection", "interval_days": 365, "first_due_on": (TODAY + timedelta(days=20)).isoformat()})
    d = u.ok("get", "/api/v1/maintenance/dashboard?within=30")
    assert [t["title"] for t in d["overdue"]] == ["Exercise run"]
    assert d["overdue"][0]["item"]["name"] == "Generator"
    assert "Pre-season inspection" in [t["title"] for t in d["due"]]
    assert "Pre-season inspection" not in [t["title"] for t in u.ok("get", "/api/v1/maintenance/dashboard?within=7")["due"]]
    assert [e["name"] for e in d["no_records"]] == ["Leaf blower"]
    stats = u.ok("get", "/api/v1/stats")
    assert stats["maintenance_overdue"] == 1


def test_read_only_users_can_view_but_not_record(env):
    u = env.admin
    gen = u.item("Generator")
    t = u.ok("post", f"/api/v1/items/{gen['id']}/maintenance-tasks", json={"title": "Exercise run", "interval_days": 30})
    viewer = env.user("u-v", "Val Viewer")
    viewer.get("/api/v1/me")
    uid = next(x["id"] for x in u.ok("get", "/api/v1/users") if x["name"] == "Val Viewer")
    u.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    assert viewer.get(f"/api/v1/items/{gen['id']}/maintenance").status_code == 200
    assert viewer.post(f"/api/v1/maintenance-tasks/{t['id']}/complete", json={}).status_code == 403
    assert viewer.post(f"/api/v1/items/{gen['id']}/meter-readings", json={"value": 1}).status_code == 403


def test_history_shown_in_item_audit(env):
    u = env.admin
    gen = u.item("Generator")
    u.ok("post", f"/api/v1/items/{gen['id']}/service-events", json={"title": "Carburetor cleaned"})
    hist = u.ok("get", f"/api/v1/history?entity_type=item&entity_id={gen['id']}")["events"]
    assert any("Carburetor cleaned" in h["summary"] for h in hist)


def test_upload_photo_for_service_then_attach(env):
    u = env.admin
    gen = u.item("Generator")
    att = u.ok("post", "/api/v1/attachments", files={"file": ("r.jpg", jpeg_bytes(), "image/jpeg")}, data={"kind": "photo"})
    s = u.ok("post", f"/api/v1/items/{gen['id']}/service-events", json={"title": "New plug", "attachment_ids": [att["id"]]})
    assert s["history"][0]["attachments"][0]["id"] == att["id"]

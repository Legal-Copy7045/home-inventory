"""Hour-meter photos: AI reads the meter, readings are dated, use over time
feeds the hours-based maintenance plan."""

from __future__ import annotations

from datetime import date, timedelta

from tests.conftest import jpeg_bytes

TODAY = date.today()


def _mower(u, hours=None):
    m = u.item("Zero-turn mower")
    body = {"meter": "hours"}
    if hours is not None:
        body["current_hours"] = hours
    u.ok("put", f"/api/v1/items/{m['id']}/equipment", json=body)
    return m


def test_photo_of_the_meter_is_read_and_saved_with_its_date(ai_env, fake_ai):
    u = ai_env.admin
    m = _mower(u, hours=100)
    photo = u.upload(jpeg_bytes(), item_id=m["id"])
    fake_ai.responses.append({"value": 123.45, "display_text": "00123.4", "confidence": 0.92})
    got = u.ok("post", f"/api/v1/items/{m['id']}/meter-photo", json={"attachment_id": photo["id"]})
    assert got == {"value": 123.5, "display_text": "00123.4", "confidence": 0.92, "last": 100.0, "lower_than_last": False}
    assert "100.0" in fake_ai.calls[-1]["prompt"] and fake_ai.calls[-1]["image"]
    # Nothing saved until confirmed; the reading carries the photo and the date it was taken
    s = u.ok("post", f"/api/v1/items/{m['id']}/meter-readings",
             json={"kind": "hours", "value": 123.4, "photo_id": photo["id"], "recorded_on": TODAY.isoformat()})
    assert s["meters"]["hours"] == 123.4
    r = s["readings"][0]
    assert r["source"] == "photo" and r["photo"]["id"] == photo["id"]
    # An unreadable meter gives no value rather than a guess
    fake_ai.responses.append({"value": None, "display_text": None, "confidence": 0.1})
    assert u.ok("post", f"/api/v1/items/{m['id']}/meter-photo", json={"attachment_id": photo["id"]})["value"] is None
    # Lower than the last reading is flagged
    fake_ai.responses.append({"value": 50, "display_text": "50", "confidence": 0.9})
    assert u.ok("post", f"/api/v1/items/{m['id']}/meter-photo", json={"attachment_id": photo["id"]})["lower_than_last"]
    # Someone else's photo can't be used
    other = u.item("Other")
    theirs = u.upload(jpeg_bytes(), item_id=other["id"])
    assert u.post(f"/api/v1/items/{m['id']}/meter-photo", json={"attachment_id": theirs["id"]}).status_code == 404


def test_backdated_readings_must_fit_between_their_neighbours(env):
    u = env.admin
    m = _mower(u)
    day = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
    u.ok("post", f"/api/v1/items/{m['id']}/meter-readings", json={"value": 10, "recorded_on": day(30)})
    u.ok("post", f"/api/v1/items/{m['id']}/meter-readings", json={"value": 30, "recorded_on": day(10)})
    # A photo from 20 days ago showing 20 h fits between them
    s = u.ok("post", f"/api/v1/items/{m['id']}/meter-readings", json={"value": 20, "recorded_on": day(20)})
    assert s["meters"]["hours"] == 30  # the latest reading is still the current one
    r = u.post(f"/api/v1/items/{m['id']}/meter-readings", json={"value": 35, "recorded_on": day(15)})
    assert r.status_code == 422 and "only 30" in r.json()["detail"]
    r = u.post(f"/api/v1/items/{m['id']}/meter-readings", json={"value": 5, "recorded_on": day(5)})
    assert r.status_code == 422 and "correction" in r.json()["detail"]
    r = u.post(f"/api/v1/items/{m['id']}/meter-readings", json={"value": 40, "recorded_on": (TODAY + timedelta(days=1)).isoformat()})
    assert r.status_code == 422


def test_use_over_time_estimates_when_hours_work_is_due(env):
    u = env.admin
    m = _mower(u)
    day = lambda n: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731
    for n, h in ((57, 100), (29, 114), (1, 128)):  # 14 h every 4 weeks = 0.5 h/day
        u.ok("post", f"/api/v1/items/{m['id']}/meter-readings", json={"value": h, "recorded_on": day(n)})
    task = u.ok("post", f"/api/v1/items/{m['id']}/maintenance-tasks", json={"title": "Oil change", "interval_hours": 50})
    s = u.ok("get", f"/api/v1/items/{m['id']}/maintenance")
    use = s["usage"]["hours"]
    assert use["per_day"] == 0.5 and use["per_week"] == 3.5 and [p["value"] for p in use["points"]] == [100, 114, 128]
    t = next(x for x in s["tasks"] if x["id"] == task["id"])
    assert t["hours_left"] == 50 and t["estimated_due_on"] == (TODAY + timedelta(days=100)).isoformat()
    # The Maintenance page lists it once the estimate falls inside the window
    assert task["id"] not in [x["id"] for x in u.ok("get", "/api/v1/maintenance/dashboard", params={"within": 90})["due"]]
    assert task["id"] in [x["id"] for x in u.ok("get", "/api/v1/maintenance/dashboard", params={"within": 120})["due"]]
    # Too little history: no estimate
    m2 = _mower(u, hours=5)
    t2 = u.ok("post", f"/api/v1/items/{m2['id']}/maintenance-tasks", json={"title": "Oil", "interval_hours": 50})
    assert t2["estimated_due_on"] is None

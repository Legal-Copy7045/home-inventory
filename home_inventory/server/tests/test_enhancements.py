"""0.16 enhancements: maintenance due notices, auto shopping list, and more."""

from __future__ import annotations

from home_inventory_server.services import maintenance_notices
from tests.test_parts import _equipment, _set, _task


def _check(env):
    db = env.state.db.SessionLocal()
    try:
        return maintenance_notices.check(db)
    finally:
        db.close()


def test_due_task_is_announced_once_with_parts_and_missing_parts_are_listed(env):
    events = []
    env.state.bus.publish = lambda t, d: events.append((t, d))
    u = env.admin
    mower = _equipment(u, "Mower")
    oil = u.item("Engine oil 1L", quantity=1)
    plug = u.item("Spark plug", quantity=1)
    u.ok("delete", f"/api/v1/items/{plug['id']}")  # none in stock
    t = _task(u, mower, "Service", interval_hours=50)
    u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_hours": 0})
    _set(u, t, [{"product_id": oil["product_id"], "quantity": 1}, {"product_id": plug["product_id"], "quantity": 1}])

    assert _check(env) == {"sent": 1, "added_to_shopping_list": 1}
    due = [d for k, d in events if k == "maintenance_due"]
    assert len(due) == 1
    d = due[0]
    assert d["item_name"] == "Mower" and d["title"] == "Service" and d["status"] == "overdue"
    assert d["parts_missing"] == ["Spark plug"] and d["parts_ready"] is False
    assert d["added_to_shopping_list"] == ["Spark plug"]
    assert "Mower: Service is overdue" in d["message"] and "Spark plug" in d["message"]
    shop = u.ok("get", "/api/v1/shopping-list")
    entry = next(e for g in shop["groups"] for e in g["entries"] if e["name"] == "Spark plug")
    assert "For Service on Mower" in entry["note"]

    # Not again for the same due point
    assert _check(env) == {"sent": 0, "added_to_shopping_list": 0}
    # Done, then due again later: announced again (the plug is already listed, so not added twice)
    u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/complete", json={})
    u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_hours": 0})
    assert _check(env)["sent"] == 1


def test_auto_shopping_can_be_turned_off(env):
    u = env.admin
    assert u.ok("get", "/api/v1/settings")["maintenance_auto_shopping"] is True
    u.ok("patch", "/api/v1/settings", json={"maintenance_auto_shopping": False})
    assert u.ok("get", "/api/v1/settings")["maintenance_auto_shopping"] is False
    mower = _equipment(u, "Mower")
    plug = u.item("Spark plug", quantity=1)
    u.ok("delete", f"/api/v1/items/{plug['id']}")
    t = _task(u, mower, "Plug", interval_hours=50)
    u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_hours": 0})
    _set(u, t, [{"product_id": plug["product_id"], "quantity": 1}])
    assert _check(env) == {"sent": 1, "added_to_shopping_list": 0}


def test_panorama_joins_photos_left_to_right(tmp_path):
    import io

    from PIL import Image

    from tests.conftest import Env, FakeProvider, jpeg_bytes

    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    wall = u.location("Garage wall")
    fake.responses.append({"items": [], "notes": None})
    scan = u.ok("post", f"/api/v1/locations/{wall['id']}/scans", files=[
        ("files", ("a.jpg", jpeg_bytes((255, 0, 0), (400, 300)))),
        ("files", ("b.jpg", jpeg_bytes((0, 0, 255), (200, 300)))),
    ])
    assert scan["status"] == "ready"
    img = fake.calls[-1]["image_bytes"]
    sent = Image.open(io.BytesIO(img[0] if isinstance(img, tuple) else img))
    assert sent.width == 600 and sent.height == 300  # side by side
    assert sent.getpixel((100, 150))[0] > 200 and sent.getpixel((500, 150))[2] > 200  # red left, blue right
    too_many = [("files", (f"{n}.jpg", jpeg_bytes())) for n in range(5)]
    assert u.post(f"/api/v1/locations/{wall['id']}/scans", files=too_many).status_code == 422
    env.state.db.dispose()


def test_warranty_claim_pack(env):
    import io
    import zipfile

    from tests.test_parts import _pdf

    u = env.admin
    item = u.item("Dishwasher", manufacturer="Bosch", model_number="SMS4", serial_number="FD9901",
                  purchase_date="2025-03-01", warranty_expires="2027-03-01", retailer="Currys")
    u.upload(_pdf(["RECEIPT"]), kind="receipt", filename="receipt.pdf", item_id=item["id"], recognise="false")
    u.upload(item_id=item["id"])  # a photo
    r = u.get(f"/api/v1/items/{item['id']}/claim-pack.zip")
    assert r.status_code == 200 and "warranty-claim-dishwasher.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "00 summary.pdf" in names and any(n.startswith("01 receipt") and n.endswith(".pdf") for n in names)
    assert any(n.startswith("photos/") for n in names)
    from pypdf import PdfReader

    text = PdfReader(io.BytesIO(z.read("00 summary.pdf"))).pages[0].extract_text()
    assert "FD9901" in text and "Currys" in text and "in warranty" in text


def test_seasonal_checklist_suggests_and_adds_yearly_jobs(env):
    from datetime import date

    from home_inventory_server.services import seasonal

    u = env.admin
    mower = u.item("Lawn mower")
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"equipment_type": "Lawn mower", "meter": "hours"})
    db = env.state.db.SessionLocal()
    try:
        c = seasonal.checklist(db, today=date(2026, 9, 15))  # Sep-Nov: autumn
        assert c["season"] == "Autumn" and c["months"] == ["September", "October", "November"]
        sug = [s for s in c["suggestions"] if s["item"]["id"] == mower["id"]]
        assert [(s["title"], s["this_season"]) for s in sug] == [
            ("Winterize (fuel stabilizer or run dry)", True), ("Pre-season inspection", False)]
        assert sug[0]["due_on"] == "2026-10-01" and sug[1]["due_on"] == "2027-03-01"
        # The southern hemisphere's October job is the northern April one
        env.admin.ok("patch", "/api/v1/settings", json={"hemisphere": "south"})
        db.expire_all()
        c = seasonal.checklist(db, today=date(2026, 9, 15))
        assert c["season"] == "Spring"
        assert [s["title"] for s in c["suggestions"] if s["this_season"]] == ["Pre-season inspection"]
    finally:
        db.close()
    u.ok("patch", "/api/v1/settings", json={"hemisphere": "north"})
    key = seasonal.SEASONAL.index(next(x for x in seasonal.SEASONAL if x[1].startswith("Winterize (fuel")))
    t = u.ok("post", "/api/v1/maintenance/seasonal", json={"item_id": mower["id"], "key": key})
    m = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")
    task = next(x for x in m["tasks"] if x["id"] == t["task_id"])
    assert task["interval_days"] == 365 and task["next_due_on"].endswith("-10-01")
    # Once added it's no longer suggested
    assert all(s["title"] != task["title"] for s in u.ok("get", "/api/v1/maintenance/seasonal")["suggestions"])


def test_ai_value_estimate_is_kept_with_the_item(tmp_path):
    from tests.conftest import Env, FakeProvider

    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    item = u.item("Cordless drill", manufacturer="DeWalt", model_number="DCD771")
    fake.responses.append({"replacement": 99.0, "used_low": 60, "used_high": 40, "basis": "Used, good condition.",
                           "confidence": 0.6})
    est = u.ok("post", f"/api/v1/items/{item['id']}/estimate-value")
    assert est["replacement"] == 99.0 and (est["used_low"], est["used_high"]) == (40.0, 60.0)
    assert "DeWalt" in fake.calls[-1]["prompt"] and "DCD771" in fake.calls[-1]["prompt"]
    got = u.ok("get", f"/api/v1/items/{item['id']}")
    assert got["ai_metadata"]["value_estimate"]["replacement"] == 99.0
    env.state.db.dispose()


def test_panorama_shrinks_big_photos_before_joining():
    import io

    from PIL import Image

    from home_inventory_server.services.media import stitch

    def big(color):
        buf = io.BytesIO()
        Image.new("RGB", (6000, 4500), color).save(buf, "JPEG", quality=70)
        return buf.getvalue()

    out = Image.open(io.BytesIO(stitch([big((200, 0, 0)), big((0, 0, 200))], height=1600)))
    assert out.height <= 1600 and out.width <= 8000


def test_places_inside_a_location_say_what_they_hold(env):
    u = env.admin
    rack = u.location("Rack")
    top = u.ok("post", "/api/v1/locations", json={"name": "Top shelf", "parent_id": rack["id"]})
    tray = u.ok("post", "/api/v1/locations", json={"name": "Tray", "parent_id": top["id"]})
    u.ok("post", "/api/v1/locations", json={"name": "Bottom shelf", "parent_id": rack["id"]})
    box = u.container("Lights", location_id=top["id"])
    u.item("Fairy lights", quantity=4, container_id=box["id"])
    u.item("Tape", location_id=tray["id"])
    kids = {c["name"]: c for c in u.ok("get", f"/api/v1/locations/{rack['id']}")["children"]}
    assert (kids["Top shelf"]["container_count"], kids["Top shelf"]["item_count"]) == (1, 5)
    assert (kids["Bottom shelf"]["container_count"], kids["Bottom shelf"]["item_count"]) == (0, 0)

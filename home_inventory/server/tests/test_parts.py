"""Parts: standards (generics), fitments, task parts, readiness, manuals."""

from __future__ import annotations

import io
import time

from reportlab.pdfgen import canvas

from tests.conftest import Env, FakeProvider, jpeg_bytes


def _equipment(u, name, **kw):
    item = u.item(name, **kw)
    u.ok("put", f"/api/v1/items/{item['id']}/equipment", json={"equipment_type": "Engine", "meter": "hours",
                                                                "current_hours": 0})
    return item


def _task(u, item, title, **kw):
    return u.ok("post", f"/api/v1/items/{item['id']}/maintenance-tasks", json={"title": title, **kw})


def _parts(u, product_id, **data):
    return u.ok("put", f"/api/v1/products/{product_id}/parts", json=data)


def _set(u, task, parts):
    return u.ok("put", f"/api/v1/maintenance-tasks/{task['id']}/parts", json={"parts": parts})


def _line(readiness, name):
    return next(x for x in readiness["lines"] if x["product"]["name"] == name)


def test_mower_and_generator_share_oil_and_plugs(env):
    """Acceptance: two machines, one brand-named oil and one generic oil,
    one shared spark plug; stock counted once."""
    u = env.admin
    mower = _equipment(u, "Honda HRX217 mower", manufacturer="Honda", model_number="HRX217")
    gen = _equipment(u, "Generator EU22i", manufacturer="Honda", model_number="EU22i")
    std = u.ok("post", "/api/v1/parts/standards", json={"name": "Engine oil SAE 10W-30", "specs": {"Viscosity": "10W-30"},
                                                         "unit": "litres"})
    assert std["is_generic"] and std["content_unit"] == "L"
    honda_oil = u.item("Honda 10W-30 engine oil", quantity=1)  # the brand the mower's manual names, none in stock
    u.ok("delete", f"/api/v1/items/{honda_oil['id']}")
    castrol = u.item("Castrol GTX 10W-30 5L", quantity=1)
    plug = u.item("NGK BPR6ES spark plug", model_number="BPR6ES", quantity=1)

    # Matching specs only *suggests* the Castrol meets the standard.
    det = _parts(u, castrol["product_id"], specs={"Viscosity": "10w30", "Standard": "API SN"}, content_quantity=5,
                 content_unit="L")
    link = next(x for x in det["links"] if x["kind"] == "meets")
    assert link["status"] == "suggested" and link["target"]["id"] == std["id"]
    hd = _parts(u, honda_oil["product_id"], specs={"Viscosity": "10W-30"}, content_quantity=1, content_unit="L")
    hlink = next(x for x in hd["links"] if x["kind"] == "meets")

    mower_oil = _task(u, mower, "Change oil", interval_hours=50)
    gen_oil = _task(u, gen, "Change oil", interval_hours=100)
    for t in (mower_oil, gen_oil):  # due now
        u.ok("patch", f"/api/v1/maintenance-tasks/{t['id']}", json={"next_due_hours": 0})
    _set(u, mower_oil, [{"product_id": honda_oil["product_id"], "quantity": 0.55, "unit": "L", "role": "Engine oil"},
                        {"product_id": plug["product_id"], "quantity": 1, "role": "Spark plug"}])
    _set(u, gen_oil, [{"product_id": std["id"], "quantity": 0.6, "unit": "L", "role": "Engine oil"},
                      {"product_id": plug["product_id"], "quantity": 1, "role": "Spark plug"}])

    # Brand doesn't matter for 10W-30: the Castrol counts by its designation
    # before any link is confirmed.
    r = u.ok("get", "/api/v1/parts/readiness")
    oil_lines = [x for x in r["lines"] if "oil" in x["product"]["name"].lower()]
    assert oil_lines and all(x["status"] == "in_stock" for x in oil_lines)
    assert any("same designation (10W-30)" == s["why"] for x in oil_lines for s in x["sources"])

    # Confirm both: the Honda oil and the Castrol meet the standard -> one pooled line
    u.ok("patch", f"/api/v1/product-links/{link['id']}", json={"status": "confirmed"})
    u.ok("patch", f"/api/v1/product-links/{hlink['id']}", json={"status": "confirmed"})
    r = u.ok("get", "/api/v1/parts/readiness")
    oil = _line(r, "Engine oil SAE 10W-30")
    assert oil["status"] == "in_stock" and oil["quantity"] == 1.15 and oil["unit"] == "L" and oil["have"] == 5
    assert {t["item"]["name"] for t in oil["tasks"]} == {"Honda HRX217 mower", "Generator EU22i"}
    assert oil["sources"][0]["product"]["name"] == "Castrol GTX 10W-30 5L"
    # One plug in stock, two needed: partial, buy one
    plugs = _line(r, "NGK BPR6ES spark plug")
    assert plugs["status"] == "partial" and plugs["quantity"] == 2 and plugs["have"] == 1 and plugs["short"] == 1
    assert plugs["buy"] == {"product_id": plug["product_id"], "name": "NGK BPR6ES spark plug", "quantity": 1}
    assert r["short_count"] == 1
    # The shortfall goes on the existing shopping list
    u.ok("post", "/api/v1/shopping-list", json={"product_id": plugs["buy"]["product_id"], "quantity": 1})

    # The models remember what they take
    takes = u.ok("get", f"/api/v1/products/{mower['product_id']}/parts")["takes"]
    assert {(f["role"], f["part"]["name"], f["status"]) for f in takes} == {
        ("Engine oil", "Honda 10W-30 engine oil", "confirmed"), ("Spark plug", "NGK BPR6ES spark plug", "confirmed")}
    # ... so does the item page, with each task's own readiness
    ip = u.ok("get", f"/api/v1/items/{mower['id']}/parts")
    rows = ip["tasks"][str(mower_oil["id"])]
    assert {x["product"]["name"]: x["status"] for x in rows} == {"Honda 10W-30 engine oil": "in_stock",
                                                                  "NGK BPR6ES spark plug": "in_stock"}


def test_brand_required_and_replacements(env):
    u = env.admin
    mower = _equipment(u, "Mower")
    std = u.ok("post", "/api/v1/parts/standards", json={"name": "10W-30", "specs": {"Viscosity": "10W-30"}})
    brand = u.item("Brand oil 1L", quantity=1)
    u.ok("delete", f"/api/v1/items/{brand['id']}")
    other = u.item("Other oil 1L", quantity=2)
    _parts(u, brand["product_id"], content_quantity=1, content_unit="L")
    _parts(u, other["product_id"], content_quantity=1, content_unit="L")
    for p in (brand, other):
        u.ok("post", f"/api/v1/products/{p['product_id']}/links", json={"target_id": std["id"], "kind": "meets"})
    t = _task(u, mower, "Oil")
    rows = _set(u, t, [{"product_id": brand["product_id"], "quantity": 1, "unit": "L", "allow_equivalent": False}])
    assert rows[0]["status"] == "missing"  # warranty: only that brand counts
    rows = _set(u, t, [{"product_id": brand["product_id"], "quantity": 1, "unit": "L", "allow_equivalent": True}])
    assert rows[0]["status"] == "in_stock" and rows[0]["have"] == 2

    # Superseded part numbers: one step only, and only when confirmed
    old = u.item("Air filter 17211-ZL8-023")
    u.ok("delete", f"/api/v1/items/{old['id']}")
    new = u.item("Air filter 17211-ZL8-033", quantity=1)
    newest = u.item("Air filter 17211-ZL8-099", quantity=5)
    t2 = _task(u, mower, "Air filter")
    link = u.ok("post", f"/api/v1/products/{old['product_id']}/links",
                json={"target_id": new["product_id"], "kind": "replaced_by", "status": "suggested"})
    assert _set(u, t2, [{"product_id": old["product_id"]}])[0]["status"] == "uncertain"
    u.ok("patch", f"/api/v1/product-links/{link['id']}", json={"status": "confirmed"})
    u.ok("post", f"/api/v1/products/{new['product_id']}/links", json={"target_id": newest["product_id"], "kind": "replaced_by"})
    row = u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(t2["id"])][0]
    assert row["status"] == "in_stock" and row["have"] == 1  # not 6: no chains
    # Validation
    r = u.post(f"/api/v1/products/{std['id']}/links", json={"target_id": std["id"], "kind": "meets"})
    assert r.status_code == 422
    r = u.post(f"/api/v1/products/{other['product_id']}/links", json={"target_id": new["product_id"], "kind": "meets"})
    assert r.status_code == 422 and "isn't a standard" in r.json()["detail"]


def test_units_that_cannot_be_compared_are_uncertain(env):
    u = env.admin
    mower = _equipment(u, "Mower")
    grease = u.item("Grease tub", quantity=1)  # no size recorded
    t = _task(u, mower, "Grease")
    row = _set(u, t, [{"product_id": grease["product_id"], "quantity": 50, "unit": "g"}])[0]
    assert row["status"] == "uncertain"
    _parts(u, grease["product_id"], content_quantity=0.4, content_unit="kg")
    row = u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(t["id"])][0]
    assert row["status"] == "in_stock" and row["have"] == 400


# --------------------------------------------------------------------------- manuals


def _read(u, item_id, attachment_ids):
    """Start reading a manual and wait for the background job."""
    r = u.ok("post", f"/api/v1/items/{item_id}/manual-readings", expected=202, json={"attachment_ids": attachment_ids})
    assert r["status"] in ("pending", "ready", "failed")
    for _ in range(200):
        if r["status"] != "pending":
            return r
        time.sleep(0.05)
        r = u.ok("get", f"/api/v1/manual-readings/{r['id']}")
    raise AssertionError("the reading never finished")


def _pdf(pages: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in pages:
        y = 800
        for line in text.split("\n"):
            c.drawString(40, y, line)
            y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


MANUAL = {
    "equipment": {"manufacturer": "Honda", "model": "HRX217", "engine": "GCV200"},
    "parts": [
        {"key": "engine_oil", "role": "Engine oil", "kind": "consumable", "name": "Engine oil SAE 10W-30",
         "manufacturer": None, "part_number": None, "alternatives": [], "specification": "SAE 10W-30, API SJ or later",
         "quantity": 0.55, "unit": "L", "page": 12},
        {"key": "spark_plug", "role": "Spark plug", "kind": "part", "name": "Spark plug", "manufacturer": "NGK",
         "part_number": "BPR6ES", "alternatives": ["W20EPR-U"], "specification": "gap 0.7-0.8 mm", "quantity": 1,
         "unit": None, "page": 13},
        {"key": "air_filter", "role": "Air filter", "kind": "part", "name": "Air filter element", "manufacturer": "Honda",
         "part_number": "17211-ZL8-023", "alternatives": [], "specification": None, "quantity": 1, "unit": None, "page": 13},
    ],
    "services": [
        {"title": "Change engine oil", "interval_hours": 50, "interval_days": 365, "interval_cycles": None,
         "first_at_hours": 5, "instructions": "Drain warm.", "needs": [{"key": "engine_oil", "quantity": 0.55, "unit": "L"}],
         "page": 12},
        {"title": "Replace spark plug", "interval_hours": 100, "interval_days": 730, "interval_cycles": None,
         "first_at_hours": None, "instructions": None, "needs": [{"key": "spark_plug", "quantity": 1, "unit": None}],
         "page": 13},
        {"title": "Replace air filter", "interval_hours": 100, "interval_days": None, "interval_cycles": None,
         "first_at_hours": None, "instructions": None, "needs": [{"key": "air_filter", "quantity": 1, "unit": None},
                                                                  {"key": "made_up", "quantity": 1, "unit": None}],
         "page": 13},
        {"title": "Check oil level", "interval_hours": None, "interval_days": 1, "interval_cycles": None,
         "first_at_hours": None, "instructions": "Before each use.", "needs": [], "page": 11},
    ],
    "specs": [{"label": "Oil capacity", "value": "0.55 L", "page": 12}],
}


def test_reading_a_manual_builds_the_plan(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = u.item("Lawn mower", manufacturer="Honda", model_number="HRX217")
    pdf = u.upload(_pdf(["Contents", "Maintenance schedule\nEvery 50 hours: change engine oil (SAE 10W-30, 0.55 L)",
                         "Spark plug: NGK BPR6ES"]), kind="manual", filename="manual.pdf", item_id=mower["id"], recognise=False)
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"meter": "hours", "current_hours": 2})
    u.item("Champion plug W20EPR-U", model_number="W20EPR-U", quantity=3)
    task = u.ok("post", f"/api/v1/items/{mower['id']}/maintenance-tasks", json={"title": "Replace spark plugs",
                                                                               "interval_hours": 200})

    fake.responses.append(MANUAL)
    reading = _read(u, mower["id"], [pdf["id"]])
    assert reading["status"] == "ready", reading
    prompt = fake.calls[0]["prompt"]
    assert "[page 2]" in prompt and "Every 50 hours" in prompt and "Honda" in prompt
    res = reading["result"]
    parts = {p["key"]: p for p in res["parts"]}
    assert parts["spark_plug"]["match"]["name"] == "Champion plug W20EPR-U"  # found by an alternative number
    assert parts["engine_oil"]["proposed_generic"] and parts["engine_oil"]["proposed_specs"] == {"Viscosity": "10W-30"}
    services = {s["title"]: s for s in res["services"]}
    assert services["Replace spark plug"]["task_match"] == {"id": task["id"], "title": "Replace spark plugs"}
    assert [n["key"] for n in services["Replace air filter"]["needs"]] == ["air_filter"]  # unknown key dropped

    choice = {
        "parts": [
            {"key": "engine_oil", "name": "Engine oil SAE 10W-30", "role": "Engine oil", "is_generic": True,
             "specs": {"Viscosity": "10W-30"}, "quantity": 0.55, "unit": "L", "page": 12},
            {"key": "spark_plug", "product_id": parts["spark_plug"]["match"]["id"], "role": "Spark plug", "quantity": 1,
             "page": 13},
            {"key": "air_filter", "name": "Air filter element", "manufacturer": "Honda", "part_number": "17211-ZL8-023",
             "role": "Air filter", "quantity": 1, "page": 13},
        ],
        "services": [
            {**{k: services["Change engine oil"][k] for k in ("title", "interval_hours", "interval_days", "first_at_hours",
                                                              "instructions", "needs", "page")}},
            {"title": "Replace spark plug", "task_id": task["id"], "interval_hours": 100, "interval_days": 730,
             "needs": [{"key": "spark_plug", "quantity": 1}], "page": 13},
            {"title": "Replace air filter", "interval_hours": 100, "needs": [{"key": "air_filter", "quantity": 1}]},
            {"title": "Check oil level", "include": False, "interval_days": 1},
        ],
        "specs": [{"label": "Oil capacity", "value": "0.55 L"}],
    }
    out = u.ok("post", f"/api/v1/manual-readings/{reading['id']}/apply", json=choice)
    assert out["summary"] == {"parts_created": 1, "standards_created": 1, "parts_linked": 3, "tasks_created": 2,
                              "tasks_updated": 1, "specs_added": 1}
    m = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")
    titles = {t["title"]: t for t in m["tasks"]}
    assert "Check oil level" not in titles
    oil = titles["Change engine oil"]
    assert oil["interval_hours"] == 50 and oil["next_due_hours"] == 5  # break-in service first
    assert titles["Replace spark plugs"]["interval_hours"] == 100  # existing task updated, kept its name
    assert {"label": "Oil capacity", "value": "0.55 L", "kind": "spec"} in m["profile"]["specs"]
    # Each service needs only its own parts
    ip = out["parts"]["tasks"]
    assert [r["product"]["name"] for r in ip[str(oil["id"])]] == ["Engine oil SAE 10W-30"]
    assert ip[str(oil["id"])][0]["quantity"] == 0.55 and ip[str(oil["id"])][0]["unit"] == "L"
    assert [(r["product"]["name"], r["status"]) for r in ip[str(task["id"])]] == [("Champion plug W20EPR-U", "in_stock")]
    assert [r["product"]["name"] for r in ip[str(titles["Replace air filter"]["id"])]] == ["Air filter element"]
    refs = {f["role"]: f["source_ref"] for f in out["parts"]["fitments"]}
    assert refs["Engine oil"] == "manual.pdf, page 12"  # traceable to the file and page
    assert res["model_check"]["status"] == "match"
    assert oil["source"] == "manual" and oil["source_ref"].startswith("manual.pdf") and oil["reading_id"] == reading["id"]
    assert oil["manual_values"]["interval_hours"] == 50
    # Applying twice isn't possible
    assert u.post(f"/api/v1/manual-readings/{reading['id']}/apply", json=choice).status_code == 422


def test_scanned_manuals_and_photos_of_pages(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    gen = _equipment(u, "Generator")
    # A scanned PDF (pages are pictures, no text) is read page by page as images
    scan = u.upload(_pdf(["", ""]), kind="manual", filename="scan.pdf", item_id=gen["id"], recognise=False)
    fake.responses += [{**MANUAL, "services": MANUAL["services"][:1], "parts": MANUAL["parts"][:1], "specs": []},
                       {**MANUAL, "services": MANUAL["services"][1:2], "parts": MANUAL["parts"][1:2], "specs": []}]
    r = _read(u, gen["id"], [scan["id"]])
    assert r["status"] == "ready" and len(fake.calls) == 2 and all(c["image"] for c in fake.calls)
    assert [s["title"] for s in r["result"]["services"]] == ["Change engine oil", "Replace spark plug"]
    assert "notes" not in r["result"]
    u.ok("post", f"/api/v1/manual-readings/{r['id']}/discard")
    fake.calls.clear()
    p1 = u.upload(jpeg_bytes(), item_id=gen["id"])
    p2 = u.upload(jpeg_bytes((10, 10, 10)), item_id=gen["id"])
    page1 = {**MANUAL, "services": MANUAL["services"][:1], "parts": MANUAL["parts"][:1], "specs": []}
    page2 = {**MANUAL, "services": MANUAL["services"][:2], "parts": MANUAL["parts"][:2], "specs": []}
    fake.responses += [page1, page2]
    r = _read(u, gen["id"], [p1["id"], p2["id"]])
    assert r["status"] == "ready" and all(c["image"] for c in fake.calls)
    assert [s["title"] for s in r["result"]["services"]] == ["Change engine oil", "Replace spark plug"]  # merged
    assert [p["key"] for p in r["result"]["parts"]] == ["engine_oil", "spark_plug"]
    # Someone else's attachment can't be used
    other = u.item("Other")
    att = u.upload(jpeg_bytes(), item_id=other["id"])
    assert u.post(f"/api/v1/items/{gen['id']}/manual-readings", json={"attachment_ids": [att["id"]]}).status_code == 404
    d = u.ok("post", f"/api/v1/manual-readings/{r['id']}/discard")
    assert d["status"] == "discarded"


def test_long_manuals_send_the_maintenance_pages():
    from home_inventory_server.services.manuals import select_text

    pages = [(n, "Safety warnings and legal text. " * 200) for n in range(1, 60)]
    pages.insert(30, (100, "Maintenance schedule: every 50 hours change the oil, replace the spark plug and air filter."))
    text = select_text(pages, limit=20000)
    assert "[page 100]" in text and len(text) <= 20000 + 500


def test_completing_a_service_takes_a_used_up_bottle_out_of_stock(env):
    u = env.admin
    mower = _equipment(u, "Mower")
    oil = u.item("Oil 1L", quantity=3)
    t = _task(u, mower, "Oil")
    u.ok("post", f"/api/v1/maintenance-tasks/{t['id']}/complete", json={"parts": [
        {"description": "Oil 1L", "item_id": oil["id"], "quantity": "0.55", "deduct_stock": True, "deduct_quantity": 1}]})
    assert u.ok("get", f"/api/v1/items/{oil['id']}")["quantity"] == 2
    h = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["history"][0]
    assert h["parts"][0]["quantity"] == 0.55


def test_adding_a_part_you_dont_have_yet(env):
    u = env.admin
    p = u.ok("post", "/api/v1/parts/products", json={"name": "Blade", "part_number": "72511-VH7-000"})
    again = u.ok("post", "/api/v1/parts/products", json={"name": "Mower blade", "part_number": "72511 VH7 000"})
    assert again["id"] == p["id"]  # same part number: the same part
    found = u.ok("get", "/api/v1/parts/search", params={"q": "72511"})
    assert [x["id"] for x in found] == [p["id"]]



def test_reading_runs_in_the_background_one_at_a_time(tmp_path):
    """Reading can take minutes: the request returns at once and the app polls."""
    import threading

    from home_inventory_server.models import ManualReading
    from home_inventory_server.services import manuals as man

    gate = threading.Event()

    class SlowAI(FakeProvider):
        def generate_json(self, prompt, schema, image, timeout):
            gate.wait(5)
            return super().generate_json(prompt, schema, image, timeout)

    fake = SlowAI()
    fake.responses.append(MANUAL)
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    gen = _equipment(u, "Generator")
    pdf = u.upload(_pdf(["Maintenance: oil every 50 hours"]), kind="manual", filename="m.pdf", item_id=gen["id"], recognise=False)
    first = u.ok("post", f"/api/v1/items/{gen['id']}/manual-readings", expected=202, json={"attachment_ids": [pdf["id"]]})
    assert first["status"] == "pending"
    # Asking again while it runs returns the same reading, not a second AI call
    again = u.ok("post", f"/api/v1/items/{gen['id']}/manual-readings", expected=202, json={"attachment_ids": [pdf["id"]]})
    assert again["id"] == first["id"]
    gate.set()
    for _ in range(200):
        r = u.ok("get", f"/api/v1/manual-readings/{first['id']}")
        if r["status"] != "pending":
            break
        time.sleep(0.05)
    assert r["status"] == "ready" and len(fake.calls) == 1
    assert [x["status"] for x in u.ok("get", f"/api/v1/items/{gen['id']}/manual-readings")] == ["ready"]

    # A reading left pending by a restart is reported as interrupted
    from datetime import UTC, datetime

    with env.state.db.SessionLocal() as db:
        stuck = ManualReading(item_id=gen["id"], attachment_ids=[pdf["id"]], status="pending",
                              created_at=datetime.now(UTC) - man.STALE_AFTER * 2)
        db.add(stuck)
        db.commit()
        stuck_id = stuck.id
    r = u.ok("get", f"/api/v1/manual-readings/{stuck_id}")
    assert r["status"] == "failed" and "interrupted" in r["error"]



def test_reading_a_manual_starts_tracking_maintenance(tmp_path):
    """A new mower with its manual attached: reading it turns it into equipment."""
    fake = FakeProvider()
    fake.responses.append(MANUAL)
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    toro = u.item("Toro TimeMaster 30", manufacturer="Toro")
    pdf = u.upload(_pdf(["Maintenance: oil every 50 hours"]), kind="manual", filename="toro.pdf", item_id=toro["id"],
                   recognise=False)
    assert u.ok("get", f"/api/v1/items/{toro['id']}/maintenance")["profile"] is None
    r = _read(u, toro["id"], [pdf["id"]])
    assert r["status"] == "ready"
    profile = u.ok("get", f"/api/v1/items/{toro['id']}/maintenance")["profile"]
    assert profile is not None and profile["meter"] == "hours"



def test_long_scanned_manuals_read_the_first_pages_or_the_chosen_ones(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = _equipment(u, "Toro mower")
    scan = u.upload(_pdf([""] * 15), kind="manual", filename="toro.pdf", item_id=mower["id"], recognise=False)
    fake.responses += [{**MANUAL, "services": [], "parts": MANUAL["parts"][:1], "specs": []}] * 12
    r = _read(u, mower["id"], [scan["id"]])
    assert len(fake.calls) == 12 and "pages 1-12 of 15" in r["result"]["notes"][0]
    u.ok("post", f"/api/v1/manual-readings/{r['id']}/discard")
    fake.calls.clear()
    fake.responses += [MANUAL, MANUAL]
    r2 = u.ok("post", f"/api/v1/items/{mower['id']}/manual-readings", expected=202,
              json={"attachment_ids": [scan["id"]], "pages": "14-15"})
    for _ in range(200):
        if r2["status"] != "pending":
            break
        time.sleep(0.05)
        r2 = u.ok("get", f"/api/v1/manual-readings/{r2['id']}")
    assert r2["status"] == "ready" and len(fake.calls) == 2 and "notes" not in r2["result"]
    bad = u.post(f"/api/v1/items/{mower['id']}/manual-readings", json={"attachment_ids": [scan["id"]], "pages": "abc"})
    assert bad.status_code == 422 and "isn't a page" in bad.json()["detail"]


def test_a_manual_part_counts_a_standard_you_keep_once_linked(env):
    """The manual names a specific plug; you keep a "Spark plug" standard.
    Not counted until you say the plug meets it, then it is."""
    u = env.admin
    blower = _equipment(u, "Ariens snow blower")
    std = u.ok("post", "/api/v1/parts/standards", json={"name": "Spark plug", "specs": {}})
    u.item("Spark plug", product_id=std["id"], quantity=3)
    named = u.ok("post", "/api/v1/parts/products", json={"name": "Champion spark plug", "part_number": "RN9YC"})
    task = _task(u, blower, "Replace spark plug", interval_days=365)
    _set(u, task, [{"product_id": named["id"], "quantity": 1}])
    def row():
        return u.ok("get", f"/api/v1/items/{blower['id']}/parts")["tasks"][str(task["id"])][0]

    assert row()["status"] == "missing"
    u.ok("post", f"/api/v1/products/{named['id']}/links", json={"target_id": std["id"], "kind": "meets", "status": "confirmed"})
    assert row()["status"] == "in_stock" and row()["have"] == 3


def _oil_manual(hours):
    return {"equipment": {"manufacturer": "Honda", "model": "HRX217"}, "parts": [], "specs": [],
            "services": [{"title": "Change engine oil", "interval_hours": hours, "interval_days": None,
                          "interval_cycles": None, "first_at_hours": None, "instructions": None, "needs": [], "page": 4}]}


def test_several_manuals_rank_and_the_higher_one_wins(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = u.item("Lawn mower", manufacturer="Honda", model_number="HRX217")
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"meter": "hours", "current_hours": 2})
    op = u.upload(_pdf(["Operator: change oil every 50 hours"]), kind="manual", filename="operator.pdf",
                  item_id=mower["id"], recognise=False)
    eng = u.upload(_pdf(["Engine: change oil every 100 hours"]), kind="manual", filename="engine.pdf",
                   item_id=mower["id"], recognise=False)
    # Rank: the engine manual wins.  Every manual listed once, roles checked.
    url = f"/api/v1/equipment/{mower['id']}/manuals"
    assert u.put(url, json={"order": [{"attachment_id": eng["id"]}]}).status_code == 422
    assert u.put(url, json={"order": [{"attachment_id": eng["id"], "role": "nope"},
                                      {"attachment_id": op["id"]}]}).status_code == 422
    out = u.ok("put", url, json={"order": [{"attachment_id": eng["id"], "role": "engine"},
                                           {"attachment_id": op["id"], "role": "operator"}]})
    assert [(m["name"], m["position"], m["role_label"]) for m in out["manuals"]] == [
        ("engine.pdf", 1, "Engine manual"), ("operator.pdf", 2, "Operator's manual")]
    plan = u.ok("get", f"/api/v1/equipment/{mower['id']}")["plan"]["manuals"]
    assert [m["name"] for m in plan] == ["engine.pdf", "operator.pdf"]

    # Read together: the engine manual is read first and its answer is kept;
    # the operator manual's different interval is noted.
    fake.responses.extend([_oil_manual(100), _oil_manual(50)])
    reading = _read(u, mower["id"], [op["id"], eng["id"]])
    assert "Engine: change oil" in fake.calls[0]["prompt"]
    [svc] = reading["result"]["services"]
    assert svc["interval_hours"] == 100 and svc["manual"] == "engine.pdf"
    assert svc["other_manuals"] == [{"manual": "operator.pdf", "interval_hours": 50, "interval_days": None,
                                     "interval_cycles": None}]

    # Read separately: a lower-priority manual never overrides the higher one.
    fake.responses.append(_oil_manual(100))
    r_eng = _read(u, mower["id"], [eng["id"]])
    u.ok("post", f"/api/v1/manual-readings/{r_eng['id']}/apply",
         json={"services": [{"title": "Change engine oil", "interval_hours": 100}]})
    fake.responses.append(_oil_manual(50))
    r_op = _read(u, mower["id"], [op["id"]])
    res = u.ok("post", f"/api/v1/manual-readings/{r_op['id']}/apply",
               json={"services": [{"title": "Change engine oil", "interval_hours": 50}]})
    assert res["summary"]["kept_from_higher_manual"] == ["Change engine oil"]
    tasks = u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["tasks"]
    assert [t["interval_hours"] for t in tasks if t["title"] == "Change engine oil"] == [100]


def test_a_standard_counts_the_oil_you_keep_once_linked_with_its_size(env):
    """The job needs 1.2 L of the "Engine oil" standard; you keep a 5 L
    bottle of Castrol.  Linked without a size it's uncertain; with the
    bottle's size it's in stock."""
    u = env.admin
    mower = _equipment(u, "Ride-on mower")
    std = u.ok("post", "/api/v1/parts/standards", json={"name": "Engine oil", "specs": {}, "unit": "L"})
    task = _task(u, mower, "Change engine oil", interval_hours=50)
    _set(u, task, [{"product_id": std["id"], "quantity": 1.2, "unit": "L"}])
    castrol = u.item("Castrol GTX 10W-30", quantity=1)

    def row():
        return u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(task["id"])][0]

    # Recognised as engine oil by its name (no link needed), but how much is in it?
    assert row()["status"] == "uncertain"
    u.ok("post", f"/api/v1/products/{castrol['product_id']}/links",
         json={"target_id": std["id"], "kind": "meets", "status": "confirmed"})
    assert row()["status"] == "uncertain"
    _parts(u, castrol["product_id"], content_quantity=5, content_unit="L")
    assert row()["status"] == "in_stock" and row()["have"] == 5

    # A part saved as specific can be turned into a standard first.
    named = u.ok("post", "/api/v1/parts/products", json={"name": "Engine oil (manual)"})
    assert _parts(u, named["id"], is_generic=True)["is_generic"] is True



def test_consumables_count_by_designation_not_brand(env):
    """10W-30 is 10W-30 from any maker but never 10W-40; a plain "Spark
    plug" standard takes any spark plug; filters only by part number."""
    u = env.admin
    mower = _equipment(u, "Zero-turn mower")
    oil30 = u.ok("post", "/api/v1/parts/standards", json={"name": "10W-30 Motor Oil", "specs": {}, "unit": "L"})
    plug_std = u.ok("post", "/api/v1/parts/standards", json={"name": "Spark plug", "specs": {}})
    filt_std = u.ok("post", "/api/v1/parts/standards", json={"name": "Oil filter", "specs": {}})
    kawa = u.ok("post", "/api/v1/parts/products", json={"name": "Kawasaki SAE 10W-40 Engine Oil", "part_number": "99969-6296"})
    t = _task(u, mower, "Service", interval_hours=100)
    _set(u, t, [{"product_id": oil30["id"], "quantity": 2, "unit": "L"}, {"product_id": plug_std["id"], "quantity": 2},
                {"product_id": filt_std["id"], "quantity": 1}, {"product_id": kawa["id"], "quantity": 1, "unit": "L"}])
    castrol = u.item("Castrol GTX 10W-30 5L", quantity=1)
    _parts(u, castrol["product_id"], content_quantity=5, content_unit="L")
    valvoline = u.item("Valvoline 10W-40 engine oil 1L", quantity=2)
    _parts(u, valvoline["product_id"], content_quantity=1, content_unit="L")
    u.item("NGK BPR6ES spark plug", quantity=2)
    u.item("Fram oil filter PH8170", quantity=1)

    rows = {x["product"]["name"]: x for x in u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(t["id"])]}
    assert rows["10W-30 Motor Oil"]["status"] == "in_stock"
    assert [s["product"]["name"] for s in rows["10W-30 Motor Oil"]["sources"]] == ["Castrol GTX 10W-30 5L"]  # not the 10W-40
    assert rows["Spark plug"]["status"] == "in_stock"
    assert rows["Oil filter"]["status"] == "missing"  # filters fit by part number, never by kind
    kw = rows["Kawasaki SAE 10W-40 Engine Oil"]
    assert kw["status"] == "in_stock" and kw["sources"][0]["product"]["name"] == "Valvoline 10W-40 engine oil 1L"

    # A person can say a brand doesn't meet a standard after all.
    u.ok("post", f"/api/v1/products/{castrol['product_id']}/links",
         json={"target_id": oil30["id"], "kind": "meets", "status": "rejected"})
    rows = {x["product"]["name"]: x for x in u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(t["id"])]}
    assert rows["10W-30 Motor Oil"]["status"] == "missing"
    # Only the named product counts when another brand isn't allowed.
    _set(u, t, [{"product_id": kawa["id"], "quantity": 1, "unit": "L", "allow_equivalent": False}])
    rows = u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(t["id"])]
    assert rows[0]["status"] == "missing"


def _plan(hours, oil_part, blade=False):
    parts = [{"key": "oil", "role": "Engine oil", "kind": "consumable", "name": oil_part, "manufacturer": None,
              "part_number": None, "alternatives": [], "specification": None, "quantity": 2, "unit": "L", "page": 3}]
    services = [{"title": "Change engine oil", "interval_hours": hours, "interval_days": None, "interval_cycles": None,
                 "first_at_hours": None, "instructions": None, "needs": [{"key": "oil", "quantity": 2, "unit": "L"}],
                 "page": 3}]
    if blade:
        services.append({"title": "Sharpen blades", "interval_hours": 25, "interval_days": None, "interval_cycles": None,
                         "first_at_hours": None, "instructions": None, "needs": [], "page": 9})
    return {"equipment": {"manufacturer": "Toro", "model": "Z"}, "parts": parts, "services": services, "specs": []}


def _two_manuals(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = u.item("Zero-turn mower", manufacturer="Toro", model_number="Z")
    u.ok("put", f"/api/v1/items/{mower['id']}/equipment", json={"meter": "hours", "current_hours": 2})
    op = u.upload(_pdf(["Operator manual"]), kind="manual", filename="operator.pdf", item_id=mower["id"], recognise=False)
    eng = u.upload(_pdf(["Engine manual"]), kind="manual", filename="engine.pdf", item_id=mower["id"], recognise=False)
    # The operator's manual first; the engine manual is marked as one.
    u.ok("put", f"/api/v1/equipment/{mower['id']}/manuals", json={"order": [
        {"attachment_id": op["id"], "role": "operator"}, {"attachment_id": eng["id"], "role": "engine"}]})
    return fake, u, mower, op, eng


def test_read_together_the_engine_manual_wins_engine_jobs(tmp_path):
    fake, u, mower, op, eng = _two_manuals(tmp_path)
    fake.responses.extend([_plan(50, "10W-30 Motor Oil", blade=True), _plan(100, "Kawasaki SAE 10W-40 Engine Oil")])
    r = _read(u, mower["id"], [op["id"], eng["id"]])
    services = {s["title"]: s for s in r["result"]["services"]}
    oil = services["Change engine oil"]
    assert oil["interval_hours"] == 100 and oil["manual"] == "engine.pdf"  # engine job: the engine manual, though ranked second
    assert oil["other_manuals"][0]["manual"] == "operator.pdf" and oil["other_manuals"][0]["interval_hours"] == 50
    assert services["Sharpen blades"]["manual"] == "operator.pdf"  # not an engine job
    assert [p["name"] for p in r["result"]["parts"]] == ["Kawasaki SAE 10W-40 Engine Oil"]  # one engine oil, not two


def test_read_separately_parts_for_a_role_replace_not_pile_up(tmp_path):
    fake, u, mower, op, eng = _two_manuals(tmp_path)

    def apply(att, plan, hours):
        fake.responses.append(plan)
        r = _read(u, mower["id"], [att["id"]])
        got = r["result"]["parts"][0]
        oil = {k: got[k] for k in ("key", "name", "role", "quantity", "unit", "page")}
        return u.ok("post", f"/api/v1/manual-readings/{r['id']}/apply", json={
            "parts": [oil], "services": [{"title": "Change engine oil", "interval_hours": hours,
                                          "needs": [{"key": "oil", "quantity": 2, "unit": "L"}]}]})

    apply(op, _plan(50, "10W-30 Motor Oil"), 50)
    out = apply(eng, _plan(100, "Kawasaki SAE 10W-40 Engine Oil"), 100)
    assert out["summary"].get("parts_replaced") == 1
    takes = u.ok("get", f"/api/v1/products/{mower['product_id']}/parts")["takes"]
    assert [(f["role"], f["part"]["name"]) for f in takes] == [("Engine oil", "Kawasaki SAE 10W-40 Engine Oil")]
    tasks = {t["title"]: t for t in u.ok("get", f"/api/v1/items/{mower['id']}/maintenance")["tasks"]}
    assert tasks["Change engine oil"]["interval_hours"] == 100
    rows = u.ok("get", f"/api/v1/items/{mower['id']}/parts")["tasks"][str(tasks["Change engine oil"]["id"])]
    assert [x["product"]["name"] for x in rows] == ["Kawasaki SAE 10W-40 Engine Oil"]
    # The operator's manual again: the engine manual's oil and interval stay.
    out = apply(op, _plan(50, "10W-30 Motor Oil"), 50)
    assert out["summary"]["kept_from_higher_manual"] == ["Change engine oil"]
    takes = u.ok("get", f"/api/v1/products/{mower['product_id']}/parts")["takes"]
    assert [f["part"]["name"] for f in takes] == ["Kawasaki SAE 10W-40 Engine Oil"]


def test_edit_what_the_model_takes(env):
    u = env.admin
    mower = _equipment(u, "Zero-turn mower")
    filt = u.ok("post", "/api/v1/parts/products", json={"name": "Oil filter", "part_number": "49065-0736"})
    other = u.ok("post", "/api/v1/parts/products", json={"name": "Oil filter (Toro)", "part_number": "063-8018-00"})
    f = u.ok("post", f"/api/v1/products/{mower['product_id']}/fitments",
             json={"part_product_id": filt["id"], "role": "Oil filter", "quantity": 1})
    g = u.ok("post", f"/api/v1/products/{mower['product_id']}/fitments", json={"part_product_id": other["id"], "role": "Oil filter"})
    u.ok("patch", f"/api/v1/part-fitments/{g['id']}", json={"status": "rejected"})  # remove the duplicate
    changed = u.ok("patch", f"/api/v1/part-fitments/{f['id']}", json={"role": "Engine oil filter", "quantity": 2})
    assert changed["role"] == "Engine oil filter" and changed["quantity"] == 2 and changed["source"] == "user"
    takes = u.ok("get", f"/api/v1/products/{mower['product_id']}/parts")["takes"]
    assert [(t["role"], t["part"]["name"]) for t in takes] == [("Engine oil filter", "Oil filter")]
    swapped = u.ok("patch", f"/api/v1/part-fitments/{f['id']}", json={"part_product_id": other["id"]})
    assert swapped["part"]["name"] == "Oil filter (Toro)"
    assert u.patch(f"/api/v1/part-fitments/{f['id']}", json={"part_product_id": mower["product_id"]}).status_code == 422

"""Insurance report and CSV / Excel export; AI receipt reading."""

import csv
import io

from openpyxl import load_workbook

from tests.conftest import jpeg_bytes
from tests.test_privacy import PDF, _viewer


def _world(u):
    house = u.location("House")
    lounge = u.ok("post", "/api/v1/locations", json={"name": "Lounge", "parent_id": house["id"]})
    garage = u.location("Garage")
    tv = u.item("Television", category="Electronics", manufacturer="Sony", model_number="KD-55", serial_number="SN123",
                purchase_price="899.00", currency="GBP", purchase_date="2024-03-01", quantity=1, location_id=lounge["id"])
    u.upload(jpeg_bytes(), item_id=tv["id"])
    u.upload(PDF, kind="receipt", filename="r.pdf", item_id=tv["id"])
    drills = u.item("Drill", purchase_price="50", quantity=2, location_id=garage["id"])
    sock = u.item("=HYPERLINK(\"http://x\")", location_id=garage["id"])  # formula injection attempt
    return house, garage, tv, drills, sock


def test_report_groups_by_room_with_values_and_paperwork(env):
    u = env.admin
    house, garage, tv, drills, sock = _world(u)
    rep = u.ok("get", "/api/v1/reports/inventory")
    assert rep["count"] == 3 and rep["total_value"] == "999.00" and rep["unvalued"] == 1
    rooms = {g["room"]: g for g in rep["groups"]}
    assert rooms["House › Lounge"]["value"] == "899.00" and rooms["Garage"]["value"] == "100.00" and rooms["Garage"]["count"] == 2
    row = next(r for r in rep["rows"] if r["item_id"] == tv["id"])
    assert (row["serial_number"], row["manufacturer"], row["model_number"]) == ("SN123", "Sony", "KD-55")
    assert row["photos"] == 1 and row["receipts"] == 1 and row["thumb_url"]
    assert row["place"] == "House › Lounge"
    only = u.ok("get", "/api/v1/reports/inventory", params={"location_id": garage["id"]})
    assert {r["item_id"] for r in only["rows"]} == {drills["id"], sock["id"]}


def test_csv_and_excel_exports(env):
    u = env.admin
    _house, _garage, tv, _drills, sock = _world(u)
    r = u.get("/api/v1/reports/inventory.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 3
    tv_row = next(x for x in rows if x["Item"] == "Television")
    assert tv_row["Serial number"] == "SN123" and tv_row["Value (price × quantity)"] == "899.00"
    assert any(x["Item"].startswith("'=HYPERLINK") for x in rows)  # neutralised
    x = u.get("/api/v1/reports/inventory.xlsx")
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb["Inventory"]
    assert ws["C1"].value == "Item" and ws.max_row == 4
    names = [c.value for c in ws["C"][1:]]
    assert "Television" in names and all(not str(n).startswith("=") for n in names)
    assert wb["By room"]["A2"].value in ("Garage", "House › Lounge")
    assert u.get("/api/v1/reports/inventory.pdf").status_code == 404


def test_report_leaves_out_customer_equipment(env):
    u = env.admin
    shed = u.location("Shed")
    u.item("My mower", location_id=shed["id"], purchase_price="300")
    cust = u.ok("post", "/api/v1/customers", json={"name": "Pat"})
    u.ok("post", "/api/v1/repair-jobs", json={"customer_id": cust["id"], "reported_problem": "x",
                                               "new_equipment": {"name": "Pat's mower", "location_id": shed["id"]}})
    names = [r["name"] for r in u.ok("get", "/api/v1/reports/inventory")["rows"]]
    assert names == ["My mower"]
    assert "Pat" not in u.get("/api/v1/reports/inventory.csv").content.decode()
    assert _viewer(env).ok("get", "/api/v1/reports/inventory")["count"] == 1


def test_read_receipt_suggests_but_saves_nothing(ai_env, fake_ai):
    u = ai_env.admin
    item = u.item("Cordless drill", location_id=u.location("Garage")["id"])
    receipt = u.upload(jpeg_bytes(), kind="receipt", item_id=item["id"], recognise=False)
    fake_ai.responses.append({"retailer": " Screwfix ", "purchase_date": "2025-01-31", "currency": "gbp", "total": 129.99,
                              "item_price": "119.99", "warranty_months": 13, "confidence": 0.9})
    res = u.ok("post", f"/api/v1/items/{item['id']}/read-receipt", json={"attachment_id": receipt["id"]})
    s = res["suggestion"]
    assert (s["retailer"], s["purchase_date"], s["currency"], s["item_price"], s["total"]) == ("Screwfix", "2025-01-31", "GBP", "119.99", "129.99")
    assert s["warranty_expires"] == "2026-02-28"  # 31 Jan + 13 months, clamped to month end
    assert "Cordless drill" in fake_ai.calls[-1]["prompt"]
    assert u.ok("get", f"/api/v1/items/{item['id']}")["purchase_price"] is None  # suggestion only
    # Nonsense from the AI is dropped, not stored
    fake_ai.responses.append({"retailer": None, "purchase_date": "31/01/2025", "currency": "pounds", "total": -5,
                              "item_price": None, "warranty_months": 999, "confidence": None})
    s = u.ok("post", f"/api/v1/items/{item['id']}/read-receipt", json={"attachment_id": receipt["id"]})["suggestion"]
    assert s["purchase_date"] is None and s["currency"] is None and s["total"] is None and s["warranty_months"] is None


def test_read_receipt_validation(ai_env, fake_ai):
    u = ai_env.admin
    shelf = u.location("Shelf")
    a, b = u.item("A", location_id=shelf["id"]), u.item("B", location_id=shelf["id"])
    on_b = u.upload(jpeg_bytes(), kind="receipt", item_id=b["id"], recognise=False)
    pdf = u.upload(PDF, kind="receipt", filename="r.pdf", item_id=a["id"], recognise=False)
    assert u.post(f"/api/v1/items/{a['id']}/read-receipt", json={"attachment_id": on_b["id"]}).status_code == 404
    assert u.post(f"/api/v1/items/{a['id']}/read-receipt", json={"attachment_id": pdf["id"]}).status_code == 422
    assert _viewer(ai_env).post(f"/api/v1/items/{a['id']}/read-receipt", json={"attachment_id": pdf["id"]}).status_code == 403

"""AI recognises documents attached to items and suggests field values."""

from __future__ import annotations

import time

from tests.conftest import Env, FakeProvider, jpeg_bytes
from tests.test_parts import _pdf

RECEIPT = {
    "document_type": "receipt", "title": "Lowe's receipt", "summary": "Purchase of a cordless drill.",
    "matches_item": True, "product_name": "DEWALT 20V drill", "manufacturer": "DEWALT", "model_number": "DCD771C2",
    "serial_number": None, "retailer": "Lowe's", "purchase_date": "2026-03-14", "currency": "USD",
    "item_price": 99.0, "total": 107.42, "warranty_months": 36, "warranty_expires": None, "confidence": 0.9,
}


def _wait(u, reading_id):
    for _ in range(200):
        r = u.ok("get", f"/api/v1/document-readings/{reading_id}")
        if r["status"] != "pending":
            return r
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_receipt_is_recognised_and_fills_empty_fields(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    drill = u.item("Cordless drill", manufacturer="Dewalt", model_number="DCD771")
    fake.responses.append(RECEIPT)
    att = u.upload(_pdf(["LOWE'S #1234", "DEWALT DCD771C2 20V DRILL 99.00", "TOTAL 107.42", "3 YEAR WARRANTY"]),
                   kind="document", filename="scan.pdf", item_id=drill["id"])
    assert att["reading_id"]
    r = _wait(u, att["reading_id"])
    assert r["status"] == "ready", r
    prompt = fake.calls[0]["prompt"]
    assert "DCD771C2" in prompt and "Cordless drill" in prompt and "[page 1]" in prompt
    s = r["suggestions"]
    assert s["kind"] == "receipt" and s["caption"] == "Lowe's receipt"
    by = {f["field"]: f for f in s["fields"]}
    # Empty fields are ticked; a different existing value is offered but not ticked
    assert by["retailer"] == {"field": "retailer", "label": "Bought from", "value": "Lowe's", "current": None,
                              "note": None, "selected": True}
    assert by["purchase_price"]["value"] == "99.00"
    assert by["warranty_expires"]["value"] == "2029-03-14" and "36 months" in by["warranty_expires"]["note"]
    assert by["warranty_start"]["value"] == "2026-03-14"
    assert by["model_number"]["current"] == "DCD771" and by["model_number"]["selected"] is False
    assert "manufacturer" not in by  # DEWALT == Dewalt: nothing to change

    # Apply what was chosen (a value can be corrected first)
    chosen = {f: by[f]["value"] for f in ("retailer", "purchase_date", "purchase_price", "currency",
                                          "warranty_start", "warranty_expires")}
    chosen["retailer"] = "Lowe's Home Improvement"
    out = u.ok("post", f"/api/v1/document-readings/{r['id']}/apply",
               json={"values": chosen, "kind": s["kind"], "caption": s["caption"]})
    assert out["status"] == "applied"
    item = u.ok("get", f"/api/v1/items/{drill['id']}")
    assert item["retailer"] == "Lowe's Home Improvement" and item["purchase_price"] == "99.00"
    assert item["warranty_expires"] == "2029-03-14" and item["model_number"] == "DCD771"  # not replaced
    doc = next(a for a in item["attachments"] if a["id"] == att["id"])
    assert doc["kind"] == "receipt" and doc["caption"] == "Lowe's receipt"
    # Once handled, it can't be applied again; fields outside the list are refused
    assert u.post(f"/api/v1/document-readings/{r['id']}/apply", json={"values": {}}).status_code == 422


def test_other_products_photos_and_failures(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = u.item("Lawn mower")
    # A photo of a warranty card for a different product: no field suggestions
    fake.responses.append({**RECEIPT, "document_type": "warranty", "title": "Toaster warranty", "matches_item": False})
    card = u.upload(jpeg_bytes(), kind="warranty", item_id=mower["id"])
    r = _wait(u, card["reading_id"])
    assert fake.calls[-1]["image"] and r["suggestions"]["fields"] == [] and r["suggestions"]["kind"] is None
    # Photos of the item itself aren't documents
    photo = u.upload(jpeg_bytes(), item_id=mower["id"])
    assert "reading_id" not in photo
    # A scanned PDF has no text: it says so, without calling the AI
    calls = len(fake.calls)
    scan = u.upload(_pdf([""]), kind="document", filename="scan.pdf", item_id=mower["id"])
    r = _wait(u, scan["reading_id"])
    assert r["status"] == "failed" and "photo of it" in r["error"] and len(fake.calls) == calls
    # Read again on request; dismissing hides the suggestions
    fake.responses.append({**RECEIPT, "document_type": "manual", "title": "Owner's manual", "item_price": None,
                           "total": None, "retailer": None, "purchase_date": None, "warranty_months": None})
    manual = u.upload(_pdf(["HRX217 owner's manual"]), kind="document", filename="m.pdf", item_id=mower["id"],
                      recognise=False)
    started = u.ok("post", f"/api/v1/attachments/{manual['id']}/recognise", expected=202)
    r = _wait(u, started["id"])
    assert r["suggestions"]["is_manual"] and r["suggestions"]["kind"] == "manual"
    assert {f["field"] for f in r["suggestions"]["fields"]} == {"manufacturer", "model_number"}
    listed = u.ok("get", f"/api/v1/items/{mower['id']}/document-readings")
    assert {x["attachment_id"] for x in listed} == {card["id"], scan["id"], manual["id"]}
    assert u.ok("post", f"/api/v1/document-readings/{r['id']}/dismiss")["status"] == "dismissed"


def test_without_ai_uploads_work_as_before(env):
    u = env.admin
    item = u.item("Fridge")
    att = u.upload(_pdf(["Receipt"]), kind="receipt", filename="r.pdf", item_id=item["id"])
    assert att["reading_id"] is None
    assert u.post(f"/api/v1/attachments/{att['id']}/recognise").status_code == 422


def test_files_added_as_manuals_are_recognised_too(tmp_path):
    """A receipt added through "Read the manual" by mistake is still identified."""
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    mower = u.item("Zero-turn mower")
    fake.responses.append(RECEIPT)
    att = u.upload(_pdf(["LOWE'S receipt"]), kind="manual", filename="manual.pdf", item_id=mower["id"])
    r = _wait(u, att["reading_id"])
    assert r["result"]["document_type"] == "receipt" and r["suggestions"]["kind"] == "receipt"

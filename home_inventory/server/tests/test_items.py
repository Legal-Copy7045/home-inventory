"""Items, products, quantities, duplicates, purchases/warranty, loans, history, trash."""

from datetime import date, timedelta


def test_create_item_manual_and_edit_history(env):
    u = env.admin
    item = u.item("Cordless drill", manufacturer="DeWalt", model_number="DCD791", category="Tools", purchase_price="129.99",
                  purchase_date="2024-05-01", retailer="Home Depot", warranty_expires="2027-05-01", serial_number="SN123")
    assert item["category"] == "Tools"
    assert item["product"]["identification_status"] == "manual"
    assert item["purchase_price"] == "129.99"
    upd = u.ok("patch", f"/api/v1/items/{item['id']}", json={"quantity": 2, "name": "Cordless drill driver"})
    assert upd["quantity"] == 2 and upd["name"] == "Cordless drill driver"
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})["events"]
    actions = [e["action"] for e in hist]
    assert "quantity_changed" in actions and "updated" in actions and "created" in actions
    qty = next(e for e in hist if e["action"] == "quantity_changed")
    assert qty["before"] == {"quantity": 1} and qty["after"] == {"quantity": 2}
    assert qty["user"] == "Alex Admin"


def test_name_required_and_validation(env):
    u = env.admin
    r = u.post("/api/v1/items", json={"quantity": 1})
    assert r.status_code == 422
    r = u.post("/api/v1/items", json={"name": "x", "quantity": -1})
    assert r.status_code == 422


def test_same_barcode_reuses_product_never_merges_by_name(env):
    u = env.admin
    a = u.item("Extension cord 10m", barcode="012345678905")
    b = u.item("Ext cord", barcode="0012345678905")  # same GTIN, EAN-13 form
    assert a["product_id"] == b["product_id"]
    c = u.item("Extension cord 10m")  # same name, no barcode -> separate product
    assert c["product_id"] != a["product_id"]
    sim = u.ok("get", "/api/v1/products/similar", params={"name": "extension cord"})
    assert {s["product"]["id"] for s in sim} >= {a["product_id"], c["product_id"]}


def test_track_individually_creates_instances(env):
    u = env.admin
    res = u.ok("post", "/api/v1/items", json={"name": "Battery 5Ah", "quantity": 3, "track_individually": True})
    assert len(res["items"]) == 3
    assert {i["quantity"] for i in res["items"]} == {1}
    assert len({i["product_id"] for i in res["items"]}) == 1


def test_photos_and_documents(env):
    u = env.admin
    photo = u.upload()
    assert photo["mime_type"] == "image/jpeg" and photo["thumb_url"]
    item = u.item("Kettle", photo_ids=[photo["id"]])
    assert item["photo"]["id"] == photo["id"]
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    rec = u.upload(pdf, kind="receipt", filename="receipt.pdf", item_id=item["id"])
    assert rec["mime_type"] == "application/pdf"
    detail = u.ok("get", f"/api/v1/items/{item['id']}")
    assert {a["kind"] for a in detail["attachments"]} == {"photo", "receipt"}
    f = u.get(rec["url"].replace("api/v1", "/api/v1"))
    assert f.status_code == 200 and f.headers["content-security-policy"] == "sandbox"
    # a PDF is not allowed as a photo; junk rejected
    r = u.post("/api/v1/attachments", files={"file": ("x.jpg", pdf)}, data={"kind": "photo"})
    assert r.status_code == 415
    r = u.post("/api/v1/attachments", files={"file": ("x.jpg", b"<script>alert(1)</script>")}, data={"kind": "document"})
    assert r.status_code == 415


def test_photo_exif_stripped(env):
    import io

    from PIL import Image

    img = Image.new("RGB", (40, 40), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    u = env.admin
    att = u.upload(buf.getvalue())
    data = u.get("/" + att["url"]).content
    assert b"SecretCam" not in data


def test_loans_and_overdue(env):
    u = env.admin
    item = u.item("Impact wrench", quantity=1)
    res = u.ok("post", f"/api/v1/items/{item['id']}/loan", json={"borrower": "Bob", "expected_return": (date.today() - timedelta(days=1)).isoformat()})
    assert res["item"]["status"] == "on_loan"
    assert res["loan"]["overdue"] is True
    assert u.post(f"/api/v1/items/{item['id']}/loan", json={"borrower": "Carol"}).status_code == 409
    loans = u.ok("get", "/api/v1/loans")
    assert loans[0]["borrower"] == "Bob"
    stats = u.ok("get", "/api/v1/stats")
    assert stats["loans_overdue"] == 1 and stats["on_loan"] == 1
    u.ok("post", f"/api/v1/loans/{res['loan']['id']}/return", json={})
    after = u.ok("get", f"/api/v1/items/{item['id']}")
    assert after["status"] == "stored" and after["loan"] is None
    assert u.ok("get", "/api/v1/stats")["loans_active"] == 0


def test_partial_loan_splits(env):
    u = env.admin
    item = u.item("Folding chair", quantity=6)
    res = u.ok("post", f"/api/v1/items/{item['id']}/loan", json={"borrower": "Neighbours", "quantity": 2})
    assert res["item"]["quantity"] == 2 and res["item"]["id"] != item["id"]
    assert u.ok("get", f"/api/v1/items/{item['id']}")["quantity"] == 4


def test_status_changes(env):
    u = env.admin
    item = u.item("Tent")
    r = u.ok("post", f"/api/v1/items/{item['id']}/status", json={"status": "missing", "note": "not in loft"})
    assert r["status"] == "missing"
    assert u.post(f"/api/v1/items/{item['id']}/status", json={"status": "on_loan"}).status_code == 409


def test_warranty_queries_and_value(env):
    u = env.admin
    today = date.today()
    u.item("TV", purchase_price="500", quantity=1, warranty_expires=(today + timedelta(days=10)).isoformat())
    u.item("Mixer", purchase_price="50.50", quantity=2, warranty_expires=(today + timedelta(days=200)).isoformat())
    u.item("Old radio", warranty_expires=(today - timedelta(days=5)).isoformat())
    soon = u.ok("get", "/api/v1/warranties", params={"days": 30})
    assert [i["name"] for i in soon] == ["TV"]
    with_expired = u.ok("get", "/api/v1/warranties", params={"days": 365, "include_expired": True})
    assert len(with_expired) == 3
    st = u.ok("get", "/api/v1/stats")
    assert st["total_value"] == 601.0
    assert st["warranties_expiring"] == 1


def test_delete_restore_purge(env):
    u = env.admin
    item = u.item("Broken lamp")
    u.ok("delete", f"/api/v1/items/{item['id']}", params={"reason": "binned"})
    assert u.ok("get", "/api/v1/items")["total"] == 0
    assert u.ok("get", "/api/v1/trash")["items"][0]["id"] == item["id"]
    u.ok("post", f"/api/v1/items/{item['id']}/restore")
    assert u.ok("get", "/api/v1/items")["total"] == 1
    u.ok("delete", f"/api/v1/items/{item['id']}")
    u.ok("delete", f"/api/v1/trash/items/{item['id']}")
    assert u.get(f"/api/v1/items/{item['id']}").status_code == 404
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})["events"]
    assert [e["action"] for e in hist][:3] == ["purged", "deleted", "restored"]


def test_items_filtering(env):
    u = env.admin
    shed = u.location("Shed")
    garage = u.location("Garage")
    box = u.container("Box", "1", location_id=shed["id"])
    u.item("Hammer", container_id=box["id"], category="Tools")
    u.item("Saw", location_id=shed["id"], category="Tools")
    u.item("Bike", location_id=garage["id"], category="Sports")
    assert u.ok("get", "/api/v1/items", params={"location_id": shed["id"]})["total"] == 2
    assert u.ok("get", "/api/v1/items", params={"category": "tools"})["total"] == 2
    assert u.ok("get", "/api/v1/items", params={"container_id": box["id"]})["total"] == 1
    assert u.ok("get", "/api/v1/items", params={"q": "bik"})["items"][0]["name"] == "Bike"

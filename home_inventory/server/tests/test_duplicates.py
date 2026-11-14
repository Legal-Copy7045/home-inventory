"""Possible duplicates and merging products."""

from tests.conftest import jpeg_bytes
from tests.test_privacy import _viewer

CODE = "5012345678900"


def _pairs(u):
    return [(p["products"][0]["name"], p["products"][1]["name"], p["reason"]) for p in u.ok("get", "/api/v1/products/duplicates")]


def test_finds_similar_names_same_barcode_and_model(env):
    u = env.admin
    shed = u.location("Shed")
    u.item("Cordless drill", location_id=shed["id"])
    u.item("Cordless drills", location_id=shed["id"])
    u.item("Hedge trimmer", location_id=shed["id"])
    u.item("WD-40 can", barcode=CODE, location_id=shed["id"])
    oil = u.item("Penetrating oil", location_id=shed["id"])
    u.ok("patch", f"/api/v1/items/{oil['id']}", json={"barcode": CODE})  # barcode added later by hand
    u.item("Mower", manufacturer="Honda", model_number="HRX217", location_id=shed["id"])
    u.item("Lawnmower", manufacturer="honda", model_number="hrx-217".replace("-", ""), location_id=shed["id"])
    u.item("Battery 18V", model_number="DCB184", location_id=shed["id"])
    u.item("Battery 18V", model_number="DCB182", location_id=shed["id"])  # different models: not duplicates
    pairs = _pairs(u)
    assert ("WD-40 can", "Penetrating oil", "Same barcode") in pairs
    assert ("Mower", "Lawnmower", "Same maker and model") in pairs
    assert ("Cordless drill", "Cordless drills", "Similar name") in pairs
    assert not any("Battery" in a for a, _b, _r in pairs)
    assert not any("Hedge" in a or "Hedge" in b for a, b, _r in pairs)


def test_merge_moves_everything_and_fills_blanks(env):
    u = env.admin
    garage, shed = u.location("Garage"), u.location("Shed")
    keep = u.item("Cordless drill", location_id=garage["id"])
    other = u.item("Cordless drills", manufacturer="DeWalt", barcode=CODE, category="Tools", location_id=shed["id"], quantity=2)
    u.upload(jpeg_bytes(), product_id=other["product"]["id"] if "product" in other else u.ok("get", f"/api/v1/items/{other['id']}")["product"]["id"])
    keep_pid = u.ok("get", f"/api/v1/items/{keep['id']}")["product"]["id"]
    other_pid = u.ok("get", f"/api/v1/items/{other['id']}")["product"]["id"]
    u.ok("put", f"/api/v1/products/{other_pid}/restock", json={"enabled": True, "target_stock": 3})
    res = u.ok("post", f"/api/v1/products/{keep_pid}/merge", json={"other_id": other_pid})
    assert res["items_moved"] == [other["id"]] and {"manufacturer", "barcode", "category_id"} <= set(res["filled"])
    moved = u.ok("get", f"/api/v1/items/{other['id']}")
    assert moved["product"]["id"] == keep_pid and moved["manufacturer"] == "DeWalt" and moved["quantity"] == 2
    assert moved["placement"]["location"]["id"] == shed["id"]  # holdings stay where they are
    assert u.ok("get", f"/api/v1/products/{keep_pid}/restock")["profile"]["target_stock"] == 3
    assert u.ok("get", "/api/v1/products/duplicates") == []
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "product", "entity_id": keep_pid})["events"]
    assert any(e["action"] == "products_merged" for e in hist)
    assert u.post(f"/api/v1/products/{keep_pid}/merge", json={"other_id": other_pid}).status_code == 409  # already merged
    assert u.post(f"/api/v1/products/{keep_pid}/merge", json={"other_id": keep_pid}).status_code == 409
    assert u.post(f"/api/v1/products/{keep_pid}/merge", json={"other_id": 999999}).status_code == 404


def test_dismissed_pairs_are_not_suggested_again(env):
    u = env.admin
    shed = u.location("Shed")
    a = u.item("Garden hose", location_id=shed["id"])
    b = u.item("Garden hoses", location_id=shed["id"])
    pa, pb = (u.ok("get", f"/api/v1/items/{i['id']}")["product"]["id"] for i in (a, b))
    assert len(u.ok("get", "/api/v1/products/duplicates")) == 1
    u.ok("post", "/api/v1/products/duplicates/dismiss", json={"product_ids": [pb, pa]})
    assert u.ok("get", "/api/v1/products/duplicates") == []
    viewer = _viewer(env)
    assert viewer.get("/api/v1/products/duplicates").status_code == 403
    assert viewer.post(f"/api/v1/products/{pa}/merge", json={"other_id": pb}).status_code == 403


def test_products_whose_photos_look_the_same(env):
    import io

    from PIL import Image, ImageDraw

    def photo(shift=0, colour=(200, 40, 40)):
        img = Image.new("RGB", (320, 240), (240, 240, 240))
        d = ImageDraw.Draw(img)
        d.rectangle((60 + shift, 50, 260 + shift, 190), fill=colour)
        d.ellipse((100 + shift, 80, 160 + shift, 140), fill=(20, 20, 20))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70 + shift)
        return buf.getvalue()

    u = env.admin
    case = u.item("Black hard case")
    dewalt = u.item("DeWalt carry box")
    other = u.item("Garden hose")
    u.upload(photo(0), item_id=case["id"])
    u.upload(photo(2), item_id=dewalt["id"])  # the same thing, photographed again
    blank = Image.new("RGB", (320, 240), (240, 240, 240))
    ImageDraw.Draw(blank).line((0, 0, 320, 240), fill=(0, 120, 0), width=30)
    buf = io.BytesIO()
    blank.save(buf, "JPEG")
    u.upload(buf.getvalue(), item_id=other["id"])
    pairs = u.ok("get", "/api/v1/products/duplicates")
    by_reason = [(sorted(x["name"] for x in p["products"]), p["reason"]) for p in pairs]
    assert (["Black hard case", "DeWalt carry box"], "Photos look the same") in by_reason
    assert not any("Garden hose" in names for names, _ in by_reason)
    # A second look uses the stored fingerprints
    assert (env.settings.data_dir / "photo_hashes.json").exists()
    assert u.ok("get", "/api/v1/products/duplicates") == pairs

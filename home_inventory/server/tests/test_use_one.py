"""Barcode "take one out" for consumables."""

from tests.test_privacy import _viewer

CODE = "012345678905"


def _post(u, **body):
    return u.post("/api/v1/barcode/use-one", json={"code": CODE, **body})


def test_use_one_picks_the_only_holding_and_flags_low_stock(env):
    u = env.admin
    shelf = u.location("Shelf")
    item = u.item("AA batteries", quantity=3, min_quantity=3, barcode=CODE, location_id=shelf["id"])
    r = u.ok("post", "/api/v1/barcode/use-one", json={"code": CODE})
    assert r["used"]["item_id"] == item["id"] and r["used"]["remaining"] == 2 and r["used"]["low_stock"] is True
    assert u.ok("get", f"/api/v1/items/{item['id']}")["quantity"] == 2
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": item["id"]})["events"]
    assert any("took one out" in e["summary"] for e in hist)


def test_use_one_asks_when_ambiguous_and_prefers_scan_mode_container(env):
    u = env.admin
    garage, box = u.location("Garage"), u.container("Box")
    a = u.item("AA batteries", quantity=4, barcode=CODE, location_id=garage["id"])
    b = u.item("AA batteries", quantity=2, barcode=CODE, container_id=box["id"])
    r = u.ok("post", "/api/v1/barcode/use-one", json={"code": CODE})
    assert r["used"] is None and {c["item_id"] for c in r["choices"]} == {a["id"], b["id"]}
    assert u.ok("get", f"/api/v1/items/{a['id']}")["quantity"] == 4  # nothing changed
    r = u.ok("post", "/api/v1/barcode/use-one", json={"code": CODE, "container_id": box["id"]})
    assert r["used"]["item_id"] == b["id"] and r["used"]["remaining"] == 1
    r = u.ok("post", "/api/v1/barcode/use-one", json={"code": CODE, "item_id": a["id"]})
    assert r["used"]["item_id"] == a["id"] and r["used"]["remaining"] == 3


def test_use_one_errors_and_roles(env):
    u = env.admin
    assert _post(u).status_code == 404  # unknown barcode
    item = u.item("AA batteries", quantity=1, barcode=CODE, location_id=u.location("Shelf")["id"])
    u.ok("post", "/api/v1/barcode/use-one", json={"code": CODE})
    assert _post(u).status_code == 409  # none left
    assert u.ok("get", f"/api/v1/items/{item['id']}")["quantity"] == 0
    assert _post(u, item_id=item["id"]).status_code == 409
    assert _viewer(env).post("/api/v1/barcode/use-one", json={"code": CODE}).status_code == 403

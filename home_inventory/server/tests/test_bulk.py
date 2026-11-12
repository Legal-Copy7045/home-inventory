"""Multi-select on the Items page: bulk move / edit / delete, sort by location."""

from tests.test_privacy import _viewer


def _ids(res):
    return [i["id"] for i in res["items"]]


def test_sort_by_location_groups_items_by_place(env):
    u = env.admin
    garage, shed = u.location("Garage"), u.location("Shed")
    bin_ = u.container("Bin", location_id=shed["id"])
    a = u.item("Zip ties", location_id=garage["id"])
    b = u.item("Axe", location_id=shed["id"])
    c = u.item("Bolts", container_id=bin_["id"])
    d = u.item("Anvil", location_id=garage["id"])
    nowhere = u.ok("post", "/api/v1/items", json={"name": "Aardvark statue"})["items"][0]
    res = u.ok("get", "/api/v1/items", params={"sort": "location"})
    paths = [i["placement"]["path"] for i in res["items"]]
    assert _ids(res) == [d["id"], a["id"], b["id"], c["id"], nowhere["id"]], paths
    assert paths[-1] == "Unassigned"
    # Still filterable and pageable
    page = u.ok("get", "/api/v1/items", params={"sort": "location", "limit": 2, "offset": 2})
    assert page["total"] == 5 and _ids(page) == [b["id"], c["id"]]
    assert _ids(u.ok("get", "/api/v1/items", params={"sort": "location", "location_id": shed["id"]})) == [b["id"], c["id"]]


def test_bulk_move_edit_delete(env):
    u = env.admin
    garage = u.location("Garage")
    box = u.container("Box 7")
    items = [u.item(n, location_id=garage["id"]) for n in ("Hammer", "Saw", "Chisel")]
    ids = [i["id"] for i in items]

    moved = u.ok("post", "/api/v1/items/bulk-move", json={"item_ids": ids[:2], "container_id": box["id"]})
    assert all(i["placement"]["container"]["id"] == box["id"] for i in moved["items"])
    assert len(u.ok("get", f"/api/v1/containers/{box['id']}")["items"]) == 2
    hist = u.ok("get", "/api/v1/history", params={"entity_type": "item", "entity_id": ids[0]})["events"]
    assert any(e["action"] == "moved" for e in hist)

    edited = u.ok("post", "/api/v1/items/bulk-edit", json={"item_ids": ids, "category": "Hand tools", "manufacturer": "Stanley",
                                                           "status": "in_use", "retailer": "B&Q"})
    for i in edited["items"]:
        assert (i["category"], i["manufacturer"], i["status"]) == ("Hand tools", "Stanley", "in_use")
    assert u.ok("get", f"/api/v1/items/{ids[2]}")["retailer"] == "B&Q"
    # Only the given fields change
    u.ok("post", "/api/v1/items/bulk-edit", json={"item_ids": ids[:1], "status": "stored"})
    one = u.ok("get", f"/api/v1/items/{ids[0]}")
    assert one["status"] == "stored" and one["category"] == "Hand tools" and one["manufacturer"] == "Stanley"

    res = u.ok("post", "/api/v1/items/bulk-delete", json={"item_ids": ids[1:], "reason": "Sold"})
    assert res["deleted"] == ids[1:]
    assert u.ok("get", f"/api/v1/items/{ids[1]}")["deleted_at"]
    assert _ids(u.ok("get", "/api/v1/items")) == [ids[0]]
    u.ok("post", f"/api/v1/items/{ids[1]}/restore")  # still restorable one by one
    assert u.ok("get", f"/api/v1/items/{ids[1]}")["deleted_at"] is None


def test_bulk_actions_are_all_or_nothing_and_validated(env):
    u = env.admin
    shelf = u.location("Shelf")
    a = u.item("Tape", location_id=shelf["id"])
    assert u.post("/api/v1/items/bulk-edit", json={"item_ids": [a["id"]]}).status_code == 422  # nothing to change
    assert u.post("/api/v1/items/bulk-edit", json={"item_ids": [], "category": "X"}).status_code == 422
    assert u.post("/api/v1/items/bulk-edit", json={"item_ids": [a["id"]], "status": "on_loan"}).status_code == 409
    assert u.post("/api/v1/items/bulk-edit", json={"item_ids": [a["id"], 999999], "category": "Changed"}).status_code == 404
    assert u.ok("get", f"/api/v1/items/{a['id']}")["category"] is None
    assert u.post("/api/v1/items/bulk-delete", json={"item_ids": [a["id"], 999999]}).status_code == 404
    assert u.ok("get", f"/api/v1/items/{a['id']}")["deleted_at"] is None
    assert u.post("/api/v1/items/bulk-move", json={"item_ids": [a["id"]], "location_id": 999999}).status_code == 409
    assert u.post("/api/v1/items/bulk-edit", json={"item_ids": list(range(1, 502)), "category": "X"}).status_code == 422


def test_bulk_actions_need_member(env):
    u = env.admin
    a = u.item("Tape", location_id=u.location("Shelf")["id"])
    viewer = _viewer(env)
    for path, body in (("move", {"location_id": None}), ("edit", {"category": "X"}), ("delete", {})):
        assert viewer.post(f"/api/v1/items/bulk-{path}", json={"item_ids": [a["id"]], **body}).status_code == 403

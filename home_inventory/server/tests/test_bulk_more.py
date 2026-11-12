"""Paging + select-all-matching, undo/restore, bulk lend and add-to-kit."""

from tests.test_privacy import _viewer


def test_items_paging_is_done_in_sql_and_ids_cover_all_matches(env):
    u = env.admin
    garage, shed = u.location("Garage"), u.location("Shed")
    bin_ = u.container("Bin", location_id=shed["id"])
    made = [u.item(f"Thing {n:03d}", location_id=garage["id"]) for n in range(12)]
    in_bin = u.item("Thing in bin", container_id=bin_["id"])
    p1 = u.ok("get", "/api/v1/items", params={"limit": 5, "offset": 0})
    p3 = u.ok("get", "/api/v1/items", params={"limit": 5, "offset": 10})
    assert p1["total"] == 13 and len(p1["items"]) == 5 and len(p3["items"]) == 3
    seen = [i["id"] for o in (0, 5, 10) for i in u.ok("get", "/api/v1/items", params={"limit": 5, "offset": o})["items"]]
    assert sorted(seen) == sorted([m["id"] for m in made] + [in_bin["id"]])  # no gaps or repeats
    # Location filter is now SQL: includes items in containers in that place
    shed_items = u.ok("get", "/api/v1/items", params={"location_id": shed["id"]})
    assert shed_items["total"] == 1 and shed_items["items"][0]["id"] == in_bin["id"]
    ids = u.ok("get", "/api/v1/items/ids", params={"q": "Thing 00"})
    assert ids["total"] == 10 and len(ids["ids"]) == 10
    by_loc = u.ok("get", "/api/v1/items/ids", params={"sort": "location"})["ids"]
    assert by_loc[0] == made[0]["id"] and by_loc[-1] == in_bin["id"]  # Garage before Shed


def test_bulk_delete_can_be_undone(env):
    u = env.admin
    shelf = u.location("Shelf")
    a, b, c = (u.item(n, location_id=shelf["id"]) for n in ("A", "B", "C"))
    u.ok("post", "/api/v1/items/bulk-delete", json={"item_ids": [a["id"], b["id"]]})
    deleted = [i["id"] for i in u.ok("get", "/api/v1/deleted-items")["items"]]
    assert set(deleted) == {a["id"], b["id"]}
    res = u.ok("post", "/api/v1/items/bulk-restore", json={"item_ids": [a["id"], b["id"], c["id"]]})
    assert set(res["restored"]) == {a["id"], b["id"]}  # c was never deleted
    assert u.ok("get", "/api/v1/items")["total"] == 3
    assert u.ok("get", "/api/v1/deleted-items")["items"] == []
    assert u.post("/api/v1/items/bulk-restore", json={"item_ids": [999999]}).status_code == 404


def test_bulk_lend_and_status_guard(env):
    u = env.admin
    shelf = u.location("Shelf")
    a, b = u.item("Ladder", location_id=shelf["id"]), u.item("Drill", location_id=shelf["id"])
    res = u.ok("post", "/api/v1/items/bulk-lend", json={"item_ids": [a["id"], b["id"]], "borrower": "Sam", "expected_return": "2030-01-01"})
    assert all(i["status"] == "on_loan" and i["loan"]["borrower"] == "Sam" for i in res["items"])
    # Lending again fails as a whole
    c = u.item("Saw", location_id=shelf["id"])
    assert u.post("/api/v1/items/bulk-lend", json={"item_ids": [c["id"], a["id"]], "borrower": "Jo"}).status_code == 409
    assert u.ok("get", f"/api/v1/items/{c['id']}")["status"] == "stored"
    # Setting a status by hand can't leave a loan open (single or bulk)
    assert u.post(f"/api/v1/items/{a['id']}/status", json={"status": "stored"}).status_code == 409
    assert u.post("/api/v1/items/bulk-edit", json={"item_ids": [b["id"]], "status": "in_use"}).status_code == 409
    assert u.ok("get", f"/api/v1/items/{b['id']}")["status"] == "on_loan"


def test_bulk_add_to_kit(env):
    u = env.admin
    shelf = u.location("Shelf")
    items = [u.item(n, location_id=shelf["id"]) for n in ("Saw", "Blade", "Guide")]
    kit = u.ok("post", "/api/v1/kits", json={"name": "Circular saw kit"})
    u.ok("post", f"/api/v1/kits/{kit['id']}/members", json={"item_id": items[0]["id"]})
    res = u.ok("post", f"/api/v1/kits/{kit['id']}/members/bulk", json={"item_ids": [i["id"] for i in items]})
    assert res["added"] == [items[1]["id"], items[2]["id"]]
    assert len(res["kit"]["members"]) == 3
    assert u.post(f"/api/v1/kits/{kit['id']}/members/bulk", json={"item_ids": [999999]}).status_code == 404


def test_new_bulk_actions_need_member(env):
    u = env.admin
    a = u.item("Tape", location_id=u.location("Shelf")["id"])
    kit = u.ok("post", "/api/v1/kits", json={"name": "K"})
    viewer = _viewer(env)
    assert viewer.post("/api/v1/items/bulk-lend", json={"item_ids": [a["id"]], "borrower": "x"}).status_code == 403
    assert viewer.post("/api/v1/items/bulk-restore", json={"item_ids": [a["id"]]}).status_code == 403
    assert viewer.post(f"/api/v1/kits/{kit['id']}/members/bulk", json={"item_ids": [a["id"]]}).status_code == 403
    assert viewer.ok("get", "/api/v1/items/ids")["total"] == 1

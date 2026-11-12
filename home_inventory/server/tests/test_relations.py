"""Item relationships and kits."""

from __future__ import annotations


def _compressor_setup(u):
    garage = u.location("Garage")
    shelf = u.location("Shelf", parent_id=garage["id"])
    compressor = u.item("Air compressor", location_id=garage["id"])
    hose = u.item("Air hose 50 ft", location_id=shelf["id"])
    inflator = u.item("Tire inflator", location_id=shelf["id"])
    return garage, shelf, compressor, hose, inflator


def test_relations_visible_from_both_sides(env):
    u = env.admin
    _, _, compressor, hose, inflator = _compressor_setup(u)
    u.ok("post", f"/api/v1/items/{hose['id']}/relations", json={"other_item_id": compressor["id"], "relation_type": "accessory_of"})
    # added from the parent's side, with the direction reversed
    u.ok("post", f"/api/v1/items/{compressor['id']}/relations",
         json={"other_item_id": inflator["id"], "relation_type": "accessory_of", "direction": "other_to_this"})
    parent = u.ok("get", f"/api/v1/items/{compressor['id']}/relations")["relations"]
    assert {(r["label"], r["item"]["name"]) for r in parent} == {("Accessory", "Air hose 50 ft"), ("Accessory", "Tire inflator")}
    child = u.ok("get", f"/api/v1/items/{hose['id']}/relations")["relations"]
    assert [(r["label"], r["item"]["name"]) for r in child] == [("Accessory of", "Air compressor")]


def test_items_stay_independent(env):
    u = env.admin
    garage, shelf, compressor, hose, _ = _compressor_setup(u)
    rel = u.ok("post", f"/api/v1/items/{hose['id']}/relations", json={"other_item_id": compressor["id"], "relation_type": "accessory_of"})
    # moving the accessory does not move the parent
    bench = u.location("Bench")
    u.ok("post", f"/api/v1/items/{hose['id']}/move", json={"location_id": bench["id"]})
    assert u.ok("get", f"/api/v1/items/{compressor['id']}")["placement"]["path"].endswith("Garage")
    assert u.ok("get", f"/api/v1/items/{hose['id']}")["placement"]["path"].endswith("Bench")
    # each remains independently searchable
    names = [r["name"] for r in u.ok("get", "/api/v1/search?q=hose&ai=false")["results"]]
    assert names == ["Air hose 50 ft"]
    # deleting the relationship deletes neither item
    rid = rel["relations"][0]["id"]
    u.ok("delete", f"/api/v1/relations/{rid}", expected=204)
    assert u.get(f"/api/v1/items/{hose['id']}").status_code == 200
    assert u.get(f"/api/v1/items/{compressor['id']}").status_code == 200
    assert u.ok("get", f"/api/v1/items/{hose['id']}/relations")["relations"] == []


def test_search_shows_related_equipment(env):
    u = env.admin
    _, _, compressor, hose, inflator = _compressor_setup(u)
    for it in (hose, inflator):
        u.ok("post", f"/api/v1/items/{it['id']}/relations", json={"other_item_id": compressor["id"], "relation_type": "accessory_of"})
    res = u.ok("get", "/api/v1/search?q=air compressor&ai=false")["results"]
    top = next(r for r in res if r["name"] == "Air compressor")
    assert {x["item"]["name"] for x in top["related"]} == {"Air hose 50 ft", "Tire inflator"}


def test_relation_validation(env):
    u = env.admin
    _, _, compressor, hose, _ = _compressor_setup(u)
    base = f"/api/v1/items/{hose['id']}/relations"
    assert u.post(base, json={"other_item_id": hose["id"], "relation_type": "accessory_of"}).status_code == 422
    u.ok("post", base, json={"other_item_id": compressor["id"], "relation_type": "accessory_of"})
    assert u.post(base, json={"other_item_id": compressor["id"], "relation_type": "accessory_of"}).status_code == 422
    # symmetric types are de-duplicated in either direction
    u.ok("post", base, json={"other_item_id": compressor["id"], "relation_type": "stored_with"})
    r = u.post(f"/api/v1/items/{compressor['id']}/relations", json={"other_item_id": hose["id"], "relation_type": "stored_with"})
    assert r.status_code == 422
    assert u.post(base, json={"other_item_id": compressor["id"], "relation_type": "custom"}).status_code == 422
    custom = u.ok("post", base, json={"other_item_id": compressor["id"], "relation_type": "custom", "custom_label": "spare for"})
    assert any(x["label"] == "spare for" for x in custom["relations"])
    assert u.post(base, json={"other_item_id": 99999, "relation_type": "accessory_of"}).status_code == 422


def test_trashed_items_hidden_from_relations(env):
    u = env.admin
    _, _, compressor, hose, _ = _compressor_setup(u)
    u.ok("post", f"/api/v1/items/{hose['id']}/relations", json={"other_item_id": compressor["id"], "relation_type": "accessory_of"})
    u.ok("delete", f"/api/v1/items/{hose['id']}", expected=204)
    assert u.ok("get", f"/api/v1/items/{compressor['id']}/relations")["relations"] == []
    u.ok("post", f"/api/v1/items/{hose['id']}/restore")
    assert len(u.ok("get", f"/api/v1/items/{compressor['id']}/relations")["relations"]) == 1


def test_kit_members_in_different_locations(env):
    u = env.admin
    garage, shelf, compressor, hose, inflator = _compressor_setup(u)
    tote = u.container("Compressor tote", location_id=garage["id"])
    regulator = u.item("Regulator", container_id=tote["id"])
    kit = u.ok("post", "/api/v1/kits", json={"name": "Air compressor kit", "container_id": tote["id"],
                                             "item_ids": [regulator["id"], hose["id"], inflator["id"]]})
    states = {m["item"]["name"]: m["state"] for m in kit["members"]}
    assert states == {"Regulator": "present", "Air hose 50 ft": "elsewhere", "Tire inflator": "elsewhere"}
    assert kit["complete"] is True and kit["counts"]["elsewhere"] == 2
    assert u.ok("get", f"/api/v1/items/{hose['id']}/relations")["kits"] == [{"id": kit["id"], "name": "Air compressor kit"}]
    # putting the hose in the tote makes it present; the compressor didn't move with it
    u.ok("post", f"/api/v1/items/{hose['id']}/move", json={"container_id": tote["id"]})
    kit = u.ok("get", f"/api/v1/kits/{kit['id']}")
    assert {m["item"]["name"]: m["state"] for m in kit["members"]}["Air hose 50 ft"] == "present"
    assert u.ok("get", f"/api/v1/items/{compressor['id']}")["placement"]["path"].endswith("Garage")


def test_kit_missing_loaned_and_short(env):
    u = env.admin
    garage, _, compressor, hose, inflator = _compressor_setup(u)
    fittings = u.item("Quick-connect fittings", quantity=2, location_id=garage["id"])
    kit = u.ok("post", "/api/v1/kits", json={"name": "Air kit", "location_id": garage["id"]})
    for it, qty in ((hose, 1), (inflator, 1), (fittings, 4)):
        kit = u.ok("post", f"/api/v1/kits/{kit['id']}/members", json={"item_id": it["id"], "quantity": qty})
    u.ok("post", f"/api/v1/items/{inflator['id']}/loan", json={"borrower": "Neighbour"})
    u.ok("post", f"/api/v1/items/{hose['id']}/status", json={"status": "missing"})
    kit = u.ok("get", f"/api/v1/kits/{kit['id']}")
    states = {m["item"]["name"]: m["state"] for m in kit["members"]}
    assert states == {"Air hose 50 ft": "missing", "Tire inflator": "on_loan", "Quick-connect fittings": "short"}
    assert kit["complete"] is False
    # an optional member doesn't make the kit incomplete
    for m in kit["members"]:
        if m["state"] in ("missing", "short"):
            u.ok("patch", f"/api/v1/kits/{kit['id']}/members/{m['id']}", json={"optional": True})
    assert u.ok("get", f"/api/v1/kits/{kit['id']}")["complete"] is True
    # removing a member or the kit leaves the items alone
    member = kit["members"][0]
    u.ok("delete", f"/api/v1/kits/{kit['id']}/members/{member['id']}", expected=204)
    u.ok("delete", f"/api/v1/kits/{kit['id']}", expected=204)
    assert u.get(f"/api/v1/items/{hose['id']}").status_code == 200
    assert u.ok("get", "/api/v1/kits") == []


def test_kit_duplicate_member_rejected(env):
    u = env.admin
    _, _, compressor, _, _ = _compressor_setup(u)
    kit = u.ok("post", "/api/v1/kits", json={"name": "K", "item_ids": [compressor["id"]]})
    assert u.post(f"/api/v1/kits/{kit['id']}/members", json={"item_id": compressor["id"]}).status_code == 422
    assert u.post("/api/v1/kits", json={"name": "Both", "container_id": 1, "location_id": 1}).status_code == 422

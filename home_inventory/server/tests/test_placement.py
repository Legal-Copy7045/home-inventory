"""Locations, containers, nested containers, paths, cycles, movement."""


def build_shed(u):
    prop = u.location("Property")
    shed = u.location("Shed", prop["id"], kind="building")
    right = u.location("Right side", shed["id"])
    ground = u.location("Ground level", right["id"])
    house = u.location("House", prop["id"])
    garage = u.location("Garage", house["id"])
    return prop, shed, right, ground, house, garage


def test_hierarchical_paths(env):
    u = env.admin
    prop, shed, right, ground, *_ = build_shed(u)
    box = u.container("Box", "15", location_id=ground["id"])
    assert box["path"] == "Property › Shed › Right side › Ground level › Box #15"
    loc = u.ok("get", f"/api/v1/locations/{ground['id']}")
    assert [p["name"] for p in loc["path_parts"]] == ["Property", "Shed", "Right side", "Ground level"]
    assert loc["containers"][0]["id"] == box["id"]
    tree = u.ok("get", "/api/v1/locations/tree")
    assert tree[0]["name"] == "Property"
    assert {c["name"] for c in tree[0]["children"]} == {"Shed", "House"}


def test_location_cycle_prevented(env):
    u = env.admin
    prop, shed, right, ground, *_ = build_shed(u)
    r = u.patch(f"/api/v1/locations/{prop['id']}", json={"parent_id": ground["id"]})
    assert r.status_code == 409
    assert "own sub-locations" in r.json()["detail"]
    r = u.patch(f"/api/v1/locations/{shed['id']}", json={"parent_id": shed["id"]})
    assert r.status_code == 409


def test_nested_containers_and_cycle_prevention(env):
    u = env.admin
    _, _, _, ground, _, garage = build_shed(u)
    tote = u.container("Tote", "1", location_id=ground["id"])
    tin = u.container("Screw tin", parent_container_id=tote["id"])
    bag = u.container("Bag", parent_container_id=tin["id"])
    assert bag["path"].endswith("Tote #1 › Screw tin › Bag")
    # cannot put tote into its own descendant
    r = u.post(f"/api/v1/containers/{tote['id']}/move", json={"parent_container_id": bag["id"]})
    assert r.status_code == 409
    r = u.post(f"/api/v1/containers/{tote['id']}/move", json={"parent_container_id": tote["id"]})
    assert r.status_code == 409
    # both placements rejected
    r = u.post(f"/api/v1/containers/{tote['id']}/move", json={"parent_container_id": bag["id"], "location_id": garage["id"]})
    assert r.status_code == 422


def test_moving_container_moves_contents(env):
    u = env.admin
    _, _, _, ground, _, garage = build_shed(u)
    box = u.container("Box", "15", location_id=ground["id"])
    inner = u.container("Pouch", parent_container_id=box["id"])
    item = u.item("Extension cord", quantity=2, container_id=box["id"])
    nested_item = u.item("Fuse", container_id=inner["id"])
    assert "Shed" in item["placement"]["path"]
    u.ok("post", f"/api/v1/containers/{box['id']}/move", json={"location_id": garage["id"]})
    moved = u.ok("get", f"/api/v1/items/{item['id']}")
    assert moved["placement"]["path"] == "Property › House › Garage › Box #15"
    moved2 = u.ok("get", f"/api/v1/items/{nested_item['id']}")
    assert moved2["placement"]["path"] == "Property › House › Garage › Box #15 › Pouch"
    hist = u.ok("get", "/api/v1/history", params={"container_id": box["id"]})["events"]
    assert any(e["action"] == "moved" and e["entity_type"] == "container" for e in hist)


def test_item_move_split_and_loose(env):
    u = env.admin
    _, _, _, ground, _, garage = build_shed(u)
    box = u.container("Box", "15", location_id=ground["id"])
    item = u.item("Extension cord", quantity=3, container_id=box["id"])
    moved = u.ok("post", f"/api/v1/items/{item['id']}/move", json={"location_id": garage["id"], "quantity": 1})
    assert moved["id"] != item["id"]
    assert moved["quantity"] == 1
    assert moved["placement"]["container"] is None
    assert moved["placement"]["path"] == "Property › House › Garage"
    orig = u.ok("get", f"/api/v1/items/{item['id']}")
    assert orig["quantity"] == 2
    assert {s["quantity"] for s in orig["siblings"]} == {1}
    # loose items listed
    loose = u.ok("get", "/api/v1/items", params={"loose": True})
    assert [i["id"] for i in loose["items"]] == [moved["id"]]
    # cannot move more than available
    r = u.post(f"/api/v1/items/{item['id']}/move", json={"location_id": garage["id"], "quantity": 5})
    assert r.status_code == 409


def test_temporary_move_and_return(env):
    u = env.admin
    _, _, _, ground, _, garage = build_shed(u)
    box = u.container("Box", "15", location_id=ground["id"])
    item = u.item("Ladder", location_id=ground["id"])
    t = u.ok("post", f"/api/v1/items/{item['id']}/move", json={"location_id": garage["id"], "temporary": True})
    assert t["status"] == "temporarily_relocated"
    assert t["home_placement"]["path"].endswith("Ground level")
    assert t["placement"]["path"].endswith("Garage")
    temp = u.ok("get", "/api/v1/temporary")
    assert [i["id"] for i in temp["items"]] == [item["id"]]
    back = u.ok("post", f"/api/v1/items/{item['id']}/return")
    assert back["status"] == "stored"
    assert back["home_placement"] is None
    assert back["placement"]["path"].endswith("Ground level")
    # container temporary move
    c = u.ok("post", f"/api/v1/containers/{box['id']}/move", json={"location_id": garage["id"], "temporary": True})
    assert c["temporarily_moved"] and c["home_placement"]["path"].endswith("Ground level")
    c = u.ok("post", f"/api/v1/containers/{box['id']}/return")
    assert not c["temporarily_moved"]
    assert c["path"].endswith("Ground level › Box #15")


def test_delete_container_moves_contents_out_and_restore(env):
    u = env.admin
    _, _, _, ground, _, _ = build_shed(u)
    box = u.container("Box", "15", location_id=ground["id"])
    item = u.item("Tape", container_id=box["id"])
    u.ok("delete", f"/api/v1/containers/{box['id']}")
    it = u.ok("get", f"/api/v1/items/{item['id']}")
    assert it["placement"]["path"].endswith("Ground level")
    trash = u.ok("get", "/api/v1/trash")
    assert trash["containers"][0]["id"] == box["id"]
    u.ok("post", f"/api/v1/containers/{box['id']}/restore")
    assert u.ok("get", f"/api/v1/containers/{box['id']}")["deleted_at"] is None


def test_location_delete_requires_empty(env):
    u = env.admin
    prop, shed, *_ = build_shed(u)
    assert u.delete(f"/api/v1/locations/{shed['id']}").status_code == 409


def test_container_lookup_by_number_and_qr(env):
    u = env.admin
    box = u.container("Christmas box", "15")
    assert u.ok("get", "/api/v1/containers/lookup", params={"ref": "#15"})["id"] == box["id"]
    assert u.ok("get", "/api/v1/containers/lookup", params={"ref": f"HINV:C:{box['qr_code']}"})["id"] == box["id"]
    assert u.ok("post", "/api/v1/qr/resolve", json={"text": f"https://ha.example/x?c={box['qr_code']}"})["container_id"] == box["id"]
    assert u.post("/api/v1/qr/resolve", json={"text": "hello"}).status_code == 404

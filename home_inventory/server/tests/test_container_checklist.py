"""Printed box checklists: print, read the marks back, review and apply."""

from __future__ import annotations

from tests.conftest import Env, FakeProvider, jpeg_bytes


def _box(u):
    garage = u.location("Garage")
    shed = u.location("Shed")
    box = u.container("Blue tote", number="7", location_id=garage["id"])
    inner = u.container("Small tin", parent_container_id=box["id"])
    items = {
        "drill": u.item("Cordless drill", container_id=box["id"]),
        "screws": u.item("Wood screws", container_id=box["id"], quantity=40),
        "tape": u.item("Duct tape", container_id=box["id"]),
        "glue": u.item("Old glue", container_id=box["id"]),
        "fuses": u.item("Fuses 13A", container_id=inner["id"], quantity=6),
    }
    return garage, shed, box, items


def test_print_lists_the_box_and_boxes_inside_it(env):
    u = env.admin
    _g, _s, box, items = _box(u)
    rows = u.ok("get", f"/api/v1/containers/{box['id']}/checklist")["rows"]
    assert [r["row"] for r in rows] == [1, 2, 3, 4, 5]
    assert rows[-1]["name"] == "Fuses 13A" and rows[-1]["inside"] == "Small tin"  # inner box last
    r = u.get(f"/api/v1/containers/{box['id']}/checklist.pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert u.get("/api/v1/containers/999999/checklist.pdf").status_code == 404


def test_read_a_completed_page_then_apply_what_was_confirmed(tmp_path):
    fake = FakeProvider()
    env = Env(tmp_path, ai_provider=fake)
    u = env.admin
    _garage, shed, box, items = _box(u)
    rows = {r["name"]: r for r in u.ok("get", f"/api/v1/containers/{box['id']}/checklist")["rows"]}
    fake.responses.append({"rows": [
        {"row": rows["Cordless drill"]["row"], "item_id": items["drill"]["id"], "action": "back", "move_to": None,
         "quantity": None, "note": None, "confidence": 0.95},
        {"row": rows["Wood screws"]["row"], "item_id": items["screws"]["id"], "action": "back", "move_to": None,
         "quantity": 25, "note": None, "confidence": 0.9},
        {"row": rows["Duct tape"]["row"], "item_id": None, "action": "move", "move_to": "the shed", "quantity": None,
         "note": "on the shelf", "confidence": 0.8},  # record number unreadable: matched by row
        {"row": rows["Old glue"]["row"], "item_id": items["glue"]["id"], "action": "dispose", "move_to": None,
         "quantity": None, "note": None, "confidence": 0.9},
        {"row": 99, "item_id": 424242, "action": "back", "move_to": None, "quantity": None, "note": None, "confidence": 0.5},
    ], "extras": ["Torch, red"]})
    read = u.ok("post", f"/api/v1/containers/{box['id']}/checklist/read",
                files={"file": ("page1.jpg", jpeg_bytes(), "image/jpeg")})
    assert read["pages"] == 1 and fake.calls[0]["image"] and "record " + str(items["drill"]["id"]) in fake.calls[0]["prompt"]
    got = {r["name"]: r for r in read["rows"]}
    assert got["Wood screws"]["quantity_now"] == 25
    assert got["Duct tape"]["action"] == "move" and got["Duct tape"]["place"]["id"] == shed["id"]
    assert read["unmatched"][0]["item_id"] == 424242 and read["extras"] == ["Torch, red"]
    # Nothing has changed yet.
    assert u.ok("get", f"/api/v1/items/{items['glue']['id']}")["quantity"] == 1

    # Only things in this box; everything checked before anything changes.
    other = u.item("Not in the box")
    bad = [{"item_id": other["id"], "action": "dispose"}, {"item_id": items["glue"]["id"], "action": "dispose"}]
    assert u.post(f"/api/v1/containers/{box['id']}/checklist/apply", json={"actions": bad}).status_code == 422
    assert u.ok("get", f"/api/v1/items/{items['glue']['id']}")["quantity"] == 1
    assert u.post(f"/api/v1/containers/{box['id']}/checklist/apply",
                  json={"actions": [{"item_id": items["tape"]["id"], "action": "move"}]}).status_code == 422

    out = u.ok("post", f"/api/v1/containers/{box['id']}/checklist/apply", json={"actions": [
        {"item_id": items["drill"]["id"], "action": "back"},
        {"item_id": items["screws"]["id"], "action": "back", "quantity": 25},
        {"item_id": items["tape"]["id"], "action": "move", "location_id": shed["id"]},
        {"item_id": items["glue"]["id"], "action": "dispose"},
        {"item_id": items["fuses"]["id"], "action": "missing"},
    ]})
    assert {k: out[k] for k in ("back", "move", "dispose", "missing", "counted")} == {
        "back": 2, "move": 1, "dispose": 1, "missing": 1, "counted": 1}
    assert u.ok("get", f"/api/v1/items/{items['screws']['id']}")["quantity"] == 25
    assert u.ok("get", f"/api/v1/items/{items['tape']['id']}")["placement"]["location"]["id"] == shed["id"]
    assert items["glue"]["id"] in [i["id"] for i in u.ok("get", "/api/v1/deleted-items")["items"]]
    assert u.ok("get", f"/api/v1/items/{items['fuses']['id']}")["status"] == "missing"
    # Recorded as a check of the box.
    checks = u.ok("get", "/api/v1/verifications", params={"scope_type": "container", "scope_id": box["id"]})
    assert checks["checks"][0]["id"] == out["check_id"]


def test_reading_needs_ai_and_a_photo_or_pdf(env):
    u = env.admin
    _g, _s, box, _items = _box(u)
    r = u.post(f"/api/v1/containers/{box['id']}/checklist/read", files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
    assert r.status_code == 503
    viewer_like = u.post(f"/api/v1/containers/{box['id']}/checklist/apply", json={"actions": []})
    assert viewer_like.status_code == 422

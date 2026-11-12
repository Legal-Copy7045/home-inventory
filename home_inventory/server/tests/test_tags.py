"""NFC tags: assignment, management and resolution."""

from __future__ import annotations


def _setup(u):
    garage = u.location("Garage")
    compressor = u.item("Air compressor", location_id=garage["id"])
    return garage, compressor


def test_assign_and_resolve_with_opaque_link(env):
    u = env.admin
    u.ok("patch", "/api/v1/settings", json={"qr_base_url": "https://ha.example.com/abc_home_inventory"})
    _, compressor = _setup(u)
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"], "default_action": "meter",
                                             "hardware_id": "04:a2:3b:1c:9d:80:00"})
    assert tag["url"] == f"https://ha.example.com/abc_home_inventory/n/{tag['ref']}"
    # opaque: random, not derived from ids, different for every tag
    other = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]})
    assert len(tag["ref"]) >= 20 and tag["ref"] != other["ref"] and not tag["ref"].isdigit()
    assert tag["hardware_id"] == "04A23B1C9D8000"
    hit = u.ok("post", "/api/v1/tags/resolve", json={"text": tag["url"]})
    assert (hit["target_type"], hit["target_id"], hit["default_action"]) == ("item", compressor["id"], "meter")
    assert hit["scan_count"] == 1
    # bare ref, the in-app code form, and the scanner's generic resolver all work
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": tag["ref"]})["target_id"] == compressor["id"]
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": f"HINV:N:{tag['ref']}"})["target_id"] == compressor["id"]
    assert u.ok("post", "/api/v1/qr/resolve", json={"text": tag["url"]})["id"] == compressor["id"]
    # Android can also identify the chip itself
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": "", "hardware_id": "04A23B1C9D8000"})["id"] == tag["id"]


def test_link_survives_moves(env):
    u = env.admin
    _, compressor = _setup(u)
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]})
    shed = u.location("Shed")
    u.ok("post", f"/api/v1/items/{compressor['id']}/move", json={"location_id": shed["id"]})
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": tag["ref"]})["target_id"] == compressor["id"]


def test_duplicate_chip_detected(env):
    u = env.admin
    garage, compressor = _setup(u)
    u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"], "hardware_id": "04aa"})
    r = u.post("/api/v1/tags", json={"target_type": "location", "target_id": garage["id"], "hardware_id": "04:AA"})
    assert r.status_code == 422 and "already assigned" in r.json()["detail"]
    dup = u.ok("get", "/api/v1/tags/check?hardware_id=04-aa")["duplicate"]
    assert dup["target_name"] == "Air compressor"
    assert u.ok("get", "/api/v1/tags/check?hardware_id=ffff")["duplicate"] is None


def test_inactive_and_lost_tags_do_not_resolve(env):
    u = env.admin
    _, compressor = _setup(u)
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]})
    u.ok("delete", f"/api/v1/tags/{tag['id']}", expected=204)
    r = u.post("/api/v1/tags/resolve", json={"text": tag["url"]})
    unknown = u.post("/api/v1/tags/resolve", json={"text": "/n/" + "x" * 22})
    assert r.status_code == unknown.status_code == 404
    assert r.json()["detail"] == unknown.json()["detail"] == "Tag not recognised"  # no difference to probe
    u.ok("patch", f"/api/v1/tags/{tag['id']}", json={"status": "active"})
    assert u.post("/api/v1/tags/resolve", json={"text": tag["url"]}).status_code == 200


def test_replace_lost_tag(env):
    u = env.admin
    _, compressor = _setup(u)
    old = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"], "label": "Handle",
                                             "default_action": "service"})
    new = u.ok("post", f"/api/v1/tags/{old['id']}/replace", json={})
    assert new["ref"] != old["ref"] and new["replaces_tag_id"] == old["id"]
    assert (new["label"], new["default_action"], new["target_id"]) == ("Handle", "service", compressor["id"])
    assert u.post("/api/v1/tags/resolve", json={"text": old["ref"]}).status_code == 404
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": new["ref"]})["id"] == new["id"]
    statuses = {t["id"]: t["status"] for t in u.ok("get", f"/api/v1/tags?target_type=item&target_id={compressor['id']}")}
    assert statuses == {old["id"]: "lost", new["id"]: "active"}


def test_reassign_tag(env):
    u = env.admin
    garage, compressor = _setup(u)
    box = u.container("Tote", location_id=garage["id"])
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"], "default_action": "meter"})
    moved = u.ok("patch", f"/api/v1/tags/{tag['id']}", json={"target_type": "container", "target_id": box["id"]})
    assert moved["target_name"] == "Tote" and moved["default_action"] == "view"  # meter isn't valid for containers
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": tag["ref"]})["target_type"] == "container"
    assert u.patch(f"/api/v1/tags/{tag['id']}", json={"default_action": "meter"}).status_code == 422


def test_multiple_tags_and_kits(env):
    u = env.admin
    _, compressor = _setup(u)
    kit = u.ok("post", "/api/v1/kits", json={"name": "Air kit", "item_ids": [compressor["id"]]})
    a = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"], "label": "Tank"})
    b = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"], "label": "Hose reel"})
    k = u.ok("post", "/api/v1/tags", json={"target_type": "kit", "target_id": kit["id"]})
    assert len(u.ok("get", f"/api/v1/tags?target_type=item&target_id={compressor['id']}")) == 2
    assert {a["ref"], b["ref"], k["ref"]} and len({a["ref"], b["ref"], k["ref"]}) == 3
    assert u.ok("post", "/api/v1/tags/resolve", json={"text": k["ref"]})["target_type"] == "kit"


def test_validation(env):
    u = env.admin
    assert u.post("/api/v1/tags", json={"target_type": "item", "target_id": 999}).status_code == 422
    assert u.post("/api/v1/tags", json={"target_type": "user", "target_id": 1}).status_code == 422


def test_scanning_requires_normal_authentication(env):
    u = env.admin
    _, compressor = _setup(u)
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]})
    # no Home Assistant identity -> refused, even with a valid tag
    assert env.raw_ingress().post("/api/v1/tags/resolve", json={"text": tag["url"]}).status_code == 401
    # an untrusted peer can't use the tag either
    env.settings.trusted_proxies[:] = ["172.30.32.2"]
    try:
        r = env.raw_ingress().post("/api/v1/tags/resolve", json={"text": tag["url"]}, headers={"X-Remote-User-Id": "u-admin"})
        assert r.status_code == 403
    finally:
        env.settings.trusted_proxies[:] = ["testclient", "172.30.32.2"]
    # read-only users can look up (view) but not manage tags
    v = env.user("u-v", "Val Viewer")
    v.get("/api/v1/me")
    uid = next(x["id"] for x in u.ok("get", "/api/v1/users") if x["name"] == "Val Viewer")
    u.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    assert v.post("/api/v1/tags/resolve", json={"text": tag["url"]}).status_code == 200
    assert v.post("/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]}).status_code == 403


def test_qr_fallback_svg(env):
    u = env.admin
    _, compressor = _setup(u)
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]})
    r = u.get(f"/api/v1/tags/{tag['id']}/qr.svg?base=https://ha.example.com/abc_home_inventory")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"<svg" in r.content


def test_tag_scanned_event_has_no_secrets(env):
    u = env.admin
    _, compressor = _setup(u)
    tag = u.ok("post", "/api/v1/tags", json={"target_type": "item", "target_id": compressor["id"]})
    u.ok("post", "/api/v1/tags/resolve", json={"text": tag["url"]})
    scanned = [m for m in env.state.bus.recent if m["type"] == "tag_scanned"]
    assert scanned and scanned[-1]["data"]["target_id"] == compressor["id"]
    assert tag["ref"] not in str(scanned[-1])  # the reference is never broadcast


def test_read_only_users_can_scan_labels(env):
    u = env.admin
    box = u.container("Box")
    v = env.user("u-v2", "Vi Viewer")
    v.get("/api/v1/me")
    uid = next(x["id"] for x in u.ok("get", "/api/v1/users") if x["name"] == "Vi Viewer")
    u.ok("patch", f"/api/v1/users/{uid}", json={"role": "read_only"})
    code = u.ok("get", f"/api/v1/containers/{box['id']}")["qr_code"]
    assert v.ok("post", "/api/v1/qr/resolve", json={"text": f"HINV:C:{code}"})["id"] == box["id"]

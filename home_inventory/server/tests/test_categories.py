"""Categories are a fixed list kept by administrators; items choose from it."""

from tests.test_privacy import _viewer


def _strict(env):
    u = env.admin
    u.auto_categories = False
    return u


def test_items_only_take_existing_categories(env):
    u = _strict(env)
    kitchen = u.ok("post", "/api/v1/categories", json={"name": "Kitchen & Dining"})
    r = u.post("/api/v1/items", json={"name": "Whisk", "category": "Kitchenware"})
    assert r.status_code == 409 and "isn't one of your categories" in r.json()["detail"]
    # Other spellings of an existing one are that one
    it = u.item("Whisk", category="kitchen and dining")
    assert it["category"] == "Kitchen & Dining"
    assert u.item("Pan", category="KITCHEN & DINING")["category"] == "Kitchen & Dining"
    # Clearing is fine; Equipment is always available (maintenance needs it)
    assert u.ok("patch", f"/api/v1/items/{it['id']}", json={"category": ""})["category"] is None
    assert u.item("Mower", category="Equipment")["category"] == "Equipment"
    assert kitchen["item_count"] == 0


def test_admins_add_rename_merge_and_delete(env):
    u = _strict(env)
    a = u.ok("post", "/api/v1/categories", json={"name": "Kitchenware"})
    b = u.ok("post", "/api/v1/categories", json={"name": "Kitchen and dining"})
    assert u.post("/api/v1/categories", json={"name": "kitchen & dining"}).status_code == 409  # same thing
    whisk = u.item("Whisk", category="Kitchenware")
    u.item("Plates", category="Kitchen and dining")
    # Rename, but not onto another category's name
    assert u.patch(f"/api/v1/categories/{a['id']}", json={"name": "Kitchen & dining"}).status_code == 409
    u.ok("patch", f"/api/v1/categories/{b['id']}", json={"name": "Kitchen & Dining"})
    # Can't delete one that's in use; merge instead
    assert u.delete(f"/api/v1/categories/{a['id']}").status_code == 409
    res = u.ok("post", f"/api/v1/categories/{a['id']}/merge", json={"into_id": b["id"]})
    assert res["moved"] == 1 and "Kitchenware" in res["into"]["synonyms"]
    assert u.ok("get", f"/api/v1/items/{whisk['id']}")["category"] == "Kitchen & Dining"
    cats = {c["name"]: c for c in u.ok("get", "/api/v1/categories")}
    assert "Kitchenware" not in cats and cats["Kitchen & Dining"]["item_count"] == 2
    # The old name now means the merged category (AI suggestions, barcodes)
    assert u.item("Ladle", category="Kitchenware")["category"] == "Kitchen & Dining"
    # Empty categories can be deleted; Equipment can't
    spare = u.ok("post", "/api/v1/categories", json={"name": "Spare"})
    u.ok("delete", f"/api/v1/categories/{spare['id']}")
    u.item("Mower", category="Equipment")
    eq = next(c for c in u.ok("get", "/api/v1/categories") if c["name"] == "Equipment")
    assert u.post(f"/api/v1/categories/{eq['id']}/merge", json={"into_id": b["id"]}).status_code == 409
    acts = [e["summary"] for e in u.ok("get", "/api/v1/history", params={"limit": 50})["events"]]
    assert any("Merged category Kitchenware into Kitchen & Dining" in a for a in acts)


def test_only_admins_manage_categories(env):
    cat = env.admin.ok("post", "/api/v1/categories", json={"name": "Tools"})
    member = env.user("u-mem", "Mo Member")
    member.auto_categories = False
    member.get("/api/v1/me")
    assert member.post("/api/v1/categories", json={"name": "Junk"}).status_code == 403
    assert member.patch(f"/api/v1/categories/{cat['id']}", json={"name": "Junk"}).status_code == 403
    assert member.delete(f"/api/v1/categories/{cat['id']}").status_code == 403
    viewer = _viewer(env)
    assert viewer.post("/api/v1/categories", json={"name": "Junk"}).status_code == 403

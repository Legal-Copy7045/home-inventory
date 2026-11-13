"""Take it / put away, and finding an item from a photo."""

from tests.conftest import jpeg_bytes
from tests.test_privacy import _viewer


def test_take_then_put_back_in_its_own_place(env):
    u = env.admin
    shelf = u.location("Shed shelf")
    drill = u.item("Cordless drill", location_id=shelf["id"])
    taken = u.ok("post", f"/api/v1/items/{drill['id']}/take")
    assert taken["status"] == "in_use" and taken["status_note"].startswith("Taken by ")
    assert taken["placement"]["location"]["id"] == shelf["id"]  # it still lives there
    listed = u.ok("get", "/api/v1/taken")["items"]
    assert [i["id"] for i in listed] == [drill["id"]] and listed[0]["mine"] is True
    r = u.ok("post", f"/api/v1/items/{drill['id']}/put-away", json={"location_id": shelf["id"]})
    assert r["result"] == "back" and r["item"]["status"] == "stored"
    assert u.ok("get", "/api/v1/taken")["items"] == []


def test_put_away_somewhere_new_asks_then_moves_or_borrows_the_place(env):
    u = env.admin
    shed = u.location("Shed")
    bench = u.location("Workbench")
    box = u.container("Drill case", location_id=shed["id"])
    drill = u.item("Cordless drill", container_id=box["id"])
    saw = u.item("Jigsaw", container_id=box["id"])
    for it in (drill, saw):
        u.ok("post", f"/api/v1/items/{it['id']}/take")

    ask = u.ok("post", f"/api/v1/items/{drill['id']}/put-away", json={"location_id": bench["id"]})
    assert ask["result"] == "choose" and ask["item"]["status"] == "in_use"  # nothing changed yet

    once = u.ok("post", f"/api/v1/items/{drill['id']}/put-away", json={"location_id": bench["id"], "mode": "temporary"})
    assert once["result"] == "temporary" and once["item"]["status"] == "temporarily_relocated"
    assert once["item"]["home_placement"]["container"]["id"] == box["id"]
    # Taken again later and put back in its case: it's home.
    u.ok("post", f"/api/v1/items/{drill['id']}/take")
    home = u.ok("post", f"/api/v1/items/{drill['id']}/put-away", json={"container_id": box["id"]})
    assert home["result"] == "home" and home["item"]["status"] == "stored"
    assert home["item"]["placement"]["container"]["id"] == box["id"]

    moved = u.ok("post", f"/api/v1/items/{saw['id']}/put-away", json={"location_id": bench["id"], "mode": "move"})
    assert moved["result"] == "moved" and moved["item"]["status"] == "stored"
    assert moved["item"]["placement"]["location"]["id"] == bench["id"] and moved["item"]["home_placement"] is None


def test_take_and_put_away_rules(env):
    u = env.admin
    shelf = u.location("Shelf")
    drill = u.item("Drill", location_id=shelf["id"])
    assert u.post(f"/api/v1/items/{drill['id']}/put-away", json={}).status_code == 409
    assert u.post(f"/api/v1/items/{drill['id']}/put-away", json={"location_id": 99999}).status_code in (404, 409, 422)
    viewer = _viewer(env)
    assert viewer.post(f"/api/v1/items/{drill['id']}/take").status_code == 403
    assert viewer.post(f"/api/v1/items/{drill['id']}/put-away", json={"location_id": shelf["id"]}).status_code == 403
    # Taking twice is harmless.
    u.ok("post", f"/api/v1/items/{drill['id']}/take")
    assert u.ok("post", f"/api/v1/items/{drill['id']}/take")["status"] == "in_use"


def test_identify_from_photo_lists_matching_items(ai_env, fake_ai):
    u = ai_env.admin
    shelf = u.location("Shed")
    drill = u.item("Cordless drill", location_id=shelf["id"])
    u.item("Garden hose", location_id=shelf["id"])
    fake_ai.responses.append({"name": "Cordless drill", "category": "Tools", "confidence": 0.9})
    r = u.ok("post", "/api/v1/ai/identify-to-find", files={"file": ("p.jpg", jpeg_bytes())})
    assert r["suggestion"]["name"] == "Cordless drill"
    assert [m["id"] for m in r["matches"]][:1] == [drill["id"]]
    assert all(m["display_name"] != "Garden hose" for m in r["matches"])
    # Not a photo, and no AI configured.
    assert u.post("/api/v1/ai/identify-to-find", files={"file": ("x.jpg", b"not an image")}).status_code == 415


def test_identify_from_photo_needs_ai(env):
    assert env.admin.post("/api/v1/ai/identify-to-find", files={"file": ("p.jpg", jpeg_bytes())}).status_code == 503


def test_places_resolve_spoken_names_and_box_numbers(env):
    u = env.admin
    shed = u.location("Shed")
    sem = u.location("Small Engine Repair", parent_id=shed["id"])
    u.location("Small parts drawer", parent_id=shed["id"])
    garage = u.location("Garage")
    top_a = u.location("Top shelf", parent_id=shed["id"])
    top_b = u.location("Top shelf", parent_id=garage["id"])
    five = u.container("Fittings", number="5", location_id=shed["id"])
    r = lambda q: u.ok("get", "/api/v1/places/resolve", params={"q": q})  # noqa: E731

    res = r("small engine repair")
    assert res["clear"] and res["candidates"][0]["id"] == sem["id"] and res["candidates"][0]["type"] == "location"
    assert r("the small engine repair")["candidates"][0]["id"] == sem["id"]
    res = r("container number 5")
    assert res["clear"] and (res["candidates"][0]["type"], res["candidates"][0]["id"]) == ("container", five["id"])
    assert r("box five")["candidates"][0]["id"] == five["id"]
    assert "number 5" in r("box 5")["candidates"][0]["spoken"]
    # Same name in two places: not clear, both offered with where they are
    res = r("top shelf")
    assert not res["clear"] and {c["id"] for c in res["candidates"][:2]} == {top_a["id"], top_b["id"]}
    assert {c["where"] for c in res["candidates"][:2]} == {"Shed", "Garage"}
    # Misheard a little: found, but not clear enough to act on alone
    res = r("small engine pair")
    assert res["candidates"][0]["id"] == sem["id"]
    assert r("unicorn stable")["candidates"] == []

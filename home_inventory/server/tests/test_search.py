"""Rule-based natural-language search accuracy (no AI)."""

from datetime import date, timedelta


def seed(u):
    prop = u.location("Property")
    shed = u.location("Shed", prop["id"])
    garage = u.location("Garage", prop["id"])
    loft = u.location("Loft", prop["id"])
    box15 = u.container("Box", "15", location_id=shed["id"])
    xmas = u.container("Christmas tub", "4", location_id=loft["id"])
    u.item("Axle stand", quantity=2, location_id=garage["id"], category="Automotive")
    u.item("Xmas tree lights", quantity=3, container_id=xmas["id"], category="Holiday decorations")
    u.item("Bauble set", container_id=xmas["id"], category="Holiday decorations", tags="christmas")
    u.item("USB-C charger 65W", quantity=2, location_id=garage["id"], category="Electronics")
    u.item("USB-C charger 20W", quantity=1, container_id=box15["id"], category="Electronics")
    u.item("Lightning cable", container_id=box15["id"], category="Electronics")
    u.item("20V battery 5Ah", manufacturer="DeWalt", quantity=3, container_id=box15["id"], category="Tools")
    u.item("Battery 2Ah", manufacturer="Ryobi", quantity=1, location_id=garage["id"], category="Tools")
    wrench = u.item("Impact wrench", manufacturer="Milwaukee", model_number="2767-20", location_id=garage["id"])
    u.ok("post", f"/api/v1/items/{wrench['id']}/loan", json={"borrower": "Dave", "expected_return": (date.today() + timedelta(days=3)).isoformat()})
    return locals()


def q(u, text):
    return u.ok("get", "/api/v1/search", params={"q": text})


def test_where_are_axle_stands(env):
    seed(env.admin)
    r = q(env.admin, "Where are my axle stands?")
    assert [x["name"] for x in r["results"]] == ["Axle stand"]
    assert "Garage" in r["answer"] and "2" in r["answer"]
    assert r["results"][0]["placement"]["path"] == "Property › Garage"


def test_christmas_synonyms(env):
    seed(env.admin)
    r = q(env.admin, "Find my Christmas decorations")
    names = {x["name"] for x in r["results"]}
    assert names == {"Xmas tree lights", "Bauble set"}


def test_how_many_usb_c_chargers(env):
    seed(env.admin)
    r = q(env.admin, "How many USB-C chargers do I own?")
    assert r["interpretation"]["intent"] == "count"
    assert r["total_quantity"] == 3
    assert r["answer"].startswith("You own 3")
    assert "Lightning" not in str([x["name"] for x in r["results"]])


def test_dewalt_batteries(env):
    seed(env.admin)
    r = q(env.admin, "Where are all my DeWalt batteries?")
    assert [x["name"] for x in r["results"]] == ["20V battery 5Ah"]
    assert r["interpretation"]["manufacturer"] == "DeWalt"
    assert "Box #15" in r["answer"]


def test_did_i_lend_impact_wrench(env):
    seed(env.admin)
    r = q(env.admin, "Did I lend anyone my impact wrench?")
    assert r["interpretation"]["intent"] == "loans"
    assert r["answer"].startswith("Yes — you lent Impact wrench to Dave")
    r2 = q(env.admin, "Did I lend anyone my axle stands?")
    assert r2["answer"].startswith("No — none of your")


def test_model_number_partial_and_typo(env):
    seed(env.admin)
    assert q(env.admin, "2767-20")["results"][0]["name"] == "Impact wrench"
    assert q(env.admin, "axel stand")["results"][0]["name"] == "Axle stand"
    assert q(env.admin, "charg")["total_matches"] == 2


def test_barcode_search_and_no_invention(env):
    u = env.admin
    seed(u)
    u.item("Duct tape", barcode="012345678905")
    assert q(u, "012345678905")["results"][0]["name"] == "Duct tape"
    r = q(u, "where is my snowmobile")
    assert r["results"] == [] and "couldn't find" in r["answer"]


def test_results_include_maps_and_status(env):
    u = env.admin
    s = seed(u)
    photo = u.upload()
    m = u.ok("post", "/api/v1/maps", json={"title": "Garage wall", "location_id": s["garage"]["id"], "photo_id": photo["id"]})
    r = q(u, "impact wrench")
    assert r["results"][0]["status"] == "on_loan"
    assert r["results"][0]["maps"][0]["id"] == m["id"]

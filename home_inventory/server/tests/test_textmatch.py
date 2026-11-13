"""Word search across a location's whole path."""

from home_inventory_server.services.textmatch import matches_words

PATH = "Shed › Front Right Shelving › Right Bottom Shelf"


def test_words_in_any_order_across_the_hierarchy():
    assert matches_words("shed right front shelf", PATH)
    assert matches_words("shed right front shelf", "Shed › Front Right Shelving")
    assert matches_words("front shelves shed", PATH)
    assert matches_words("rig sh", PATH)
    assert matches_words("bottom", PATH)
    assert matches_words("shelving", "Basement › Server Shelf Left")


def test_no_false_matches():
    assert not matches_words("garage shelf", PATH)
    assert not matches_words("frog", PATH)
    assert not matches_words("basement", "Shed")


def test_container_search_uses_words_and_place(env):
    u = env.admin
    shed = u.location("Shed")
    rack = u.ok("post", "/api/v1/locations", json={"name": "Front Right Shelving", "parent_id": shed["id"]})
    u.container("Towing / Axle Stands", number="6", location_id=rack["id"])
    u.container("Christmas lights", number="7")
    found = u.ok("get", "/api/v1/containers", params={"q": "axle shed right"})
    assert [c["name"] for c in found] == ["Towing / Axle Stands"]
    assert [c["name"] for c in u.ok("get", "/api/v1/containers", params={"q": "#7"})] == ["Christmas lights"]


def test_same_cases_as_the_web_app():
    import json
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[2] / "frontend" / "src" / "match_cases.json").read_text())
    for q, text in cases["match"]:
        assert matches_words(q, text), (q, text)
    for q, text in cases["no_match"]:
        assert not matches_words(q, text), (q, text)

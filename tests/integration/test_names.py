"""Voice name normalization (custom_components/home_inventory/names.py)."""

from __future__ import annotations

import pytest

from custom_components.home_inventory.intent import group_by_name, pick, spoken_answer
from custom_components.home_inventory.names import normalize, same_thing


@pytest.mark.parametrize(("a", "b"), [
    ("Wood screws", "wood screw"), ("Hoses", "hose"), ("Batteries", "battery"), ("Boxes", "box"),
    ("Brushes", "brush"), ("Benches", "bench"), ("Knives", "knife"), ("Shelves", "shelf"),
    ("Axle stands", "axle stand"), ("Movies", "movie"), ("Sizes", "size"), ("Compasses", "compass"),
    ("Glasses", "safety glasses"[7:]), ("SD cards", "SD card"), ("Cherries", "cherry"),
])
def test_singular_and_plural_are_the_same(a, b):
    assert same_thing(a, b), (normalize(a), normalize(b))


@pytest.mark.parametrize("word", ["glass", "compass", "class", "status", "cactus", "radius", "bus", "gas",
                                  "lens", "chassis", "analysis", "series", "physics", "grass", "brass"])
def test_singular_words_ending_in_s_are_kept(word):
    assert normalize(word.title()) == word


@pytest.mark.parametrize("name", ["Makita", "Stihl", "Bosch", "Ryobi", "Dewalt", "Hilti", "Wiss", "Brabus"])
def test_brand_names_are_left_alone(name):
    assert normalize(name) == name.lower()


def test_brand_names_that_look_plural_still_match_themselves():
    # "Lucas" looks like a plural ("cameras"); both sides go through the same
    # rules, so the name still matches itself and nothing else.
    assert same_thing("Lucas battery charger", "LUCAS battery charger")
    assert spoken_answer("lucas charger", [item("Lucas charger"), item("Lucas battery")])[0] == "Lucas charger is in Shed."


def test_always_plural_words_stay_distinct_from_their_singular():
    assert normalize("Safety glasses") == "safety glasses"
    assert not same_thing("Safety glasses", "Safety glass")
    assert normalize("Pliers") == "pliers" and normalize("Scissors") == "scissors"


def test_model_numbers():
    assert same_thing("Honda HRX-217", "honda hrx 217")
    assert same_thing("Honda HRX217", "Honda HRX-217")
    assert same_thing("WD-40", "WD 40")
    assert not same_thing("Honda HRX217", "Honda HRX218")
    assert normalize("Bosch GSR 18V-55") == "bosch gsr 18v55"  # digits never singularised
    assert normalize("RTX 3080s") == "rtx 3080s"


def test_measurements():
    assert same_thing("Water hose 50ft", "water hose 50 ft")
    assert same_thing("Water hose 50ft", "water hose 50 feet")
    assert same_thing("Water hose 50ft", "Water hose 50-foot")
    assert same_thing("Water hose 50ft", "Water hose 50'")
    assert same_thing("18V drill", "18 volt drill")
    assert same_thing("2.5mm drill bits", "2.5 mm drill bit")
    assert not same_thing("Water hose 50ft", "Water hose 25ft")
    assert not same_thing("2.5mm drill bits", "25mm drill bits")
    assert normalize("Box 3 in shed") == "box 3 in shed"  # "in" after a space is a word, not inches


def test_hyphens_apostrophes_and_punctuation():
    assert same_thing("Self-propelled mower", "self propelled mower")
    assert same_thing("Pat's bike", "Pat’s bike")
    assert same_thing("Black & Decker sander", "Black and Decker sander")
    assert same_thing("The hedge trimmer", "hedge trimmer")
    assert same_thing("  Hedge   Trimmer!! ", "hedge trimmer")
    assert normalize("Café table") == "cafe table"


@pytest.mark.parametrize(("a", "b"), [
    ("Drill", "Drill bits"), ("Glass", "Glasses"), ("Class", "Clas"), ("Hose", "House"),
    ("Spark plug", "Spark plug socket"), ("Status light", "Statu light"), ("Saw", "Saws all"),
])
def test_similar_but_different_names_stay_different(a, b):
    assert not same_thing(a, b)


def item(name, path="Shed"):
    return {"display_name": name, "status": "stored", "placement": {"path": path}}


def test_grouping_uses_the_normalized_name():
    groups = group_by_name([item("Wood screws", "A"), item("Wood screw", "B"), item("Glass", "C"), item("Glasses", "D")])
    assert [len(g["items"]) for g in groups] == [2, 1, 1]


def test_glass_and_status_answer_directly():
    # Used to break: "glass" -> "glas", "status" -> "statu"
    assert spoken_answer("glass", [item("Glass"), item("Glass cutter")])[0] == "Glass is in Shed."
    assert spoken_answer("compass", [item("Compass"), item("Compass case")])[0] == "Compass is in Shed."
    assert spoken_answer("status light", [item("Status light"), item("Status board")])[0] == "Status light is in Shed."
    assert spoken_answer("water hose 50 feet", [item("Water hose 50ft"), item("Water hose 25ft")])[0] == "Water hose 50ft is in Shed."


def test_several_candidates_stay_ambiguous():
    text, choices = spoken_answer("glass", [item("Glass cutter"), item("Glass jar"), item("Safety glasses")])
    assert choices is not None and len(choices) == 3 and text.endswith("Which one did you mean?")
    text, choices = spoken_answer("hose", [item("Water hose 50ft"), item("Water hose 25ft")])
    assert choices is not None and len(choices) == 2
    assert pick("the 25 foot one", choices)["name"] == "Water hose 25ft"
    assert pick("the 50ft one", choices)["name"] == "Water hose 50ft"
    assert pick("the second one", choices)["name"] == "Water hose 25ft"
    sizes = [item("Drill bit 2.5mm"), item("Drill bit 25mm")]
    assert spoken_answer("drill bits", sizes)[1] is not None


def test_ordinals_in_names_are_not_positions():
    choices = spoken_answer("tote", [item("Tote 2"), item("Tote 1")])[1]
    assert pick("tote 1", choices)["name"] == "Tote 1"

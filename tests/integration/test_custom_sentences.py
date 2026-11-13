"""The Assist sentences file shipped in deploy/ matches the integration's intents."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

hassil = pytest.importorskip("hassil")

FILE = Path(__file__).parents[2] / "deploy" / "assist" / "custom_sentences" / "en" / "home_inventory.yaml"


def _intents():
    from hassil import Intents

    with FILE.open(encoding="utf-8") as f:
        return Intents.from_dict(yaml.safe_load(f))


def test_every_intent_is_one_the_integration_handles():
    from custom_components.home_inventory import intent as hi

    names = set(_intents().intents)
    assert names == {hi.INTENT_WHERE_IS, hi.INTENT_GOES, hi.INTENT_MOVE, hi.INTENT_CHOOSE, hi.INTENT_MAINTENANCE, hi.INTENT_LOW_STOCK, hi.INTENT_ON_LOAN}


@pytest.mark.parametrize(("text", "intent", "slots"), [
    ("where is the hedge trimmer", "HomeInventoryWhereIs", {"item": "hedge trimmer"}),
    ("where did I put my drill", "HomeInventoryWhereIs", {"item": "drill"}),
    ("do I have any cable ties", "HomeInventoryWhereIs", {"item": "cable ties"}),
    ("where do I keep the ladder", "HomeInventoryWhereIs", {"item": "ladder"}),
    ("where does the hammer go", "HomeInventoryWhereDoesItGo", {"item": "hammer"}),
    ("where do the axle stands go back", "HomeInventoryWhereDoesItGo", {"item": "axle stands"}),
    ("tell me where to put the engine oil", "HomeInventoryWhereDoesItGo", {"item": "engine oil"}),
    ("where should I put my drill back", "HomeInventoryWhereDoesItGo", {"item": "drill"}),
    ("where do I put away the hedge trimmer", "HomeInventoryWhereDoesItGo", {"item": "hedge trimmer"}),
    ("where does the jigsaw belong", "HomeInventoryWhereDoesItGo", {"item": "jigsaw"}),
    ("I've moved the engine oil to small engine repair", "HomeInventoryMove", {"item": "engine oil", "place": "small engine repair"}),
    ("that I have moved my engine oil to container number 5", "HomeInventoryMove", {"item": "engine oil", "place": "container number 5"}),
    ("I put the drill in the garage", "HomeInventoryMove", {"item": "drill", "place": "garage"}),
    ("the hedge trimmer is now in the shed", "HomeInventoryMove", {"item": "hedge trimmer", "place": "shed"}),
    ("yes", "HomeInventoryChoose", {"choice": "yes"}),
    ("none of them", "HomeInventoryChoose", {"choice": "no"}),
    ("the second one", "HomeInventoryChoose", {"choice": "second"}),
    ("what maintenance is due", "HomeInventoryMaintenanceDue", {}),
    ("is anything due for a service", "HomeInventoryMaintenanceDue", {}),
    ("what's running low", "HomeInventoryRunningLow", {}),
    ("what do I need to buy", "HomeInventoryRunningLow", {}),
    ("what's lent out", "HomeInventoryOnLoan", {}),
    ("who has borrowed my stuff", "HomeInventoryOnLoan", {}),
])
def test_phrases_are_recognised(text, intent, slots):
    from hassil import recognize

    result = recognize(text, _intents())
    assert result is not None, text
    assert result.intent.name == intent
    from custom_components.home_inventory.intent import goes_query

    got = {k: str(v.value).strip() for k, v in result.entities.items()}
    if intent == "HomeInventoryWhereDoesItGo":
        got = {k: goes_query(v) for k, v in got.items()}  # what the handler searches for
    assert got == slots

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
    assert names == {hi.INTENT_WHERE_IS, hi.INTENT_CHOOSE, hi.INTENT_MAINTENANCE, hi.INTENT_LOW_STOCK, hi.INTENT_ON_LOAN}


@pytest.mark.parametrize(("text", "intent", "slots"), [
    ("where is the hedge trimmer", "HomeInventoryWhereIs", {"item": "hedge trimmer"}),
    ("where did I put my drill", "HomeInventoryWhereIs", {"item": "drill"}),
    ("do I have any cable ties", "HomeInventoryWhereIs", {"item": "cable ties"}),
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
    assert {k: str(v.value).strip() for k, v in result.entities.items()} == slots

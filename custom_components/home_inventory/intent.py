"""Voice: "where is the hedge trimmer?" - with a follow-up when it's unclear.

Two intents:

* ``HomeInventoryWhereIs`` (slot ``item``) answers where something is.
  Several records with the same name are one answer ("Drill is in A, and
  in B").  When the words match *different* things, it asks instead:
  "I found 3 things for hose: A, B and C. Which one did you mean?" and
  remembers the choices for two minutes.
* ``HomeInventoryChoose`` (slot ``choice``) is the reply: "the kink control
  one", "the second one", "the siphon pump".  It answers for that thing.

The question ends with "?" and carries a reprompt, which keeps the
conversation open: Assist voice devices listen again, and Home Assistant's
Alexa integration keeps the Alexa session open.

Any voice front end that hands Home Assistant an intent can use these:
Assist (with the custom sentences in ``docs/voice.md``), Siri through a
Shortcut that passes text to Assist, and an Alexa custom skill.
"""

from __future__ import annotations

import re
import time
from difflib import SequenceMatcher
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .api import InventoryError
from .const import DOMAIN

INTENT_WHERE_IS = "HomeInventoryWhereIs"
INTENT_CHOOSE = "HomeInventoryChoose"
PENDING_KEY = f"{DOMAIN}_voice_pending"
PENDING_SECONDS = 120
MAX_CHOICES = 6
ORDINALS = {
    "first": 0, "1st": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1, "other": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
    "fourth": 3, "4th": 3, "four": 3, "4": 3,
    "fifth": 4, "5th": 4, "five": 4, "5": 4,
    "sixth": 5, "6th": 5, "six": 5, "6": 5,
}


def _place(item: dict[str, Any]) -> str:
    return (item.get("placement") or {}).get("path", "").replace(" › ", ", ") or "somewhere without a place yet"


def _where(item: dict[str, Any]) -> str:
    """Where one record is, as a phrase ("in Shed, Box 3", "lent to Sam")."""
    if item.get("loan"):
        return f"lent to {item['loan'].get('borrower') or 'someone'}"
    where = _place(item)
    if item.get("status") == "missing":
        return f"marked missing, last in {where}"
    if item.get("home_placement") and item.get("status") == "temporarily_relocated":
        return f"in {where} for now, normally {_place({'placement': item['home_placement']})}"
    return f"in {where}"


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in s.split())


def group_by_name(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records of the same thing (same name) together, best match first."""
    groups: dict[str, dict[str, Any]] = {}
    for it in results:
        name = it.get("display_name") or it.get("name") or "That"
        g = groups.setdefault(_norm(name), {"name": name, "items": []})
        g["items"].append(it)
    return list(groups.values())


def answer_for(group: dict[str, Any]) -> str:
    places = [_where(it) for it in group["items"][:3]]
    more = len(group["items"]) - 3
    tail = f", and {more} more place{'s' if more != 1 else ''}" if more > 0 else ""
    return f"{group['name']} is " + ", and ".join(places) + tail + "."


def _list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def spoken_answer(query: str, results: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]] | None]:
    """(what to say, the choices to remember if it's a question)."""
    if not results:
        return f"I couldn't find {query} in your inventory.", None
    groups = group_by_name(results)
    exact = [g for g in groups if _norm(g["name"]) == _norm(query)]
    if len(groups) == 1 or len(exact) == 1:
        return answer_for(exact[0] if exact else groups[0]), None
    shown = groups[:MAX_CHOICES]
    names = [g["name"] for g in shown]
    if len(groups) > MAX_CHOICES:
        return (f"I found {len(groups)} things for {query}, including {_list(names[:4])}. "
                "Which one did you mean?"), shown
    return f"I found {len(groups)} things for {query}: {_list(names)}. Which one did you mean?", shown


def pick(choice: str, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The group a reply refers to: by position ("the second one") or name."""
    words = _norm(choice).split()
    for w in words:
        if w in ORDINALS and ORDINALS[w] < len(groups) and not any(w in _norm(g["name"]).split() for g in groups):
            return groups[ORDINALS[w]]
    if "last" in words:
        return groups[-1]
    target = " ".join(w for w in words if w not in {"the", "one", "a", "an", "i", "meant", "it", "is", "please", "called"})
    if not target:
        return None
    best, best_score = None, 0.0
    for g in groups:
        name = _norm(g["name"])
        name_words = set(name.split())
        overlap = len(set(target.split()) & name_words) / max(1, len(set(target.split())))
        score = max(SequenceMatcher(None, target, name).ratio(), overlap)
        if target in name:
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = g, score
    return best if best_score >= 0.5 else None


def _pending(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(PENDING_KEY, {})


def _key(intent_obj: intent.Intent) -> str:
    # One open question per voice device (Alexa requests carry no device id).
    return intent_obj.device_id or intent_obj.satellite_id or intent_obj.platform or "default"


async def _search(intent_obj: intent.Intent, query: str) -> list[dict[str, Any]] | None:
    hass = intent_obj.hass
    entries = hass.data.get(DOMAIN) or {}
    if not entries:
        return None
    client = next(iter(entries.values())).client
    user = await hass.auth.async_get_user(intent_obj.context.user_id) if intent_obj.context.user_id else None
    r = await client.request("GET", "search", params={"q": query, "limit": 10, "ai": "false"}, user=user)
    return r.get("results") or []


def _ask(response: intent.IntentResponse, speech: str, reprompt: str) -> None:
    response.async_set_speech(speech)
    response.async_set_reprompt(reprompt)  # keeps an Alexa session open


class WhereIsHandler(intent.IntentHandler):
    intent_type = INTENT_WHERE_IS
    description = "Finds where something is kept, using Home Inventory"
    slot_schema = {vol.Required("item"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        query = str(slots["item"]["value"]).strip().strip("?.!")
        response = intent_obj.create_response()
        try:
            results = await _search(intent_obj, query)
        except InventoryError:
            response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
            return response
        if results is None:
            response.async_set_speech("Home Inventory isn't set up.")
            return response
        speech, choices = spoken_answer(query, results)
        pending = _pending(intent_obj.hass)
        if choices:
            pending[_key(intent_obj)] = {"groups": choices, "until": time.monotonic() + PENDING_SECONDS}
            _ask(response, speech, f"Which one: {_list([g['name'] for g in choices[:4]])}?")
        else:
            pending.pop(_key(intent_obj), None)
            response.async_set_speech(speech)
        return response


class ChooseHandler(intent.IntentHandler):
    intent_type = INTENT_CHOOSE
    description = "Answers Home Inventory's 'which one did you mean?'"
    slot_schema = {vol.Required("choice"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        choice = str(slots["choice"]["value"]).strip().strip("?.!")
        response = intent_obj.create_response()
        pending = _pending(intent_obj.hass)
        key = _key(intent_obj)
        open_q = pending.get(key)
        if not open_q or open_q["until"] < time.monotonic():
            pending.pop(key, None)
            response.async_set_speech("I'm not sure what you mean. Ask me where something is, for example: where is the drill.")
            return response
        group = pick(choice, open_q["groups"])
        if group is None:
            names = [g["name"] for g in open_q["groups"]]
            _ask(response, f"Sorry, which one: {_list(names[:4])}?", "Which one did you mean?")
            return response
        pending.pop(key, None)
        response.async_set_speech(answer_for(group))
        return response


def async_setup_intents(hass: HomeAssistant) -> None:
    intent.async_register(hass, WhereIsHandler())
    intent.async_register(hass, ChooseHandler())


def async_unload_intents(hass: HomeAssistant) -> None:
    remove = getattr(intent, "async_remove", None)
    if remove is not None:
        remove(hass, INTENT_WHERE_IS)
        remove(hass, INTENT_CHOOSE)
    hass.data.pop(PENDING_KEY, None)

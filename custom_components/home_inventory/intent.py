"""Voice: "where is the hedge trimmer?" - with a follow-up when it's unclear.

Three intents:

* ``HomeInventoryWhereIs`` (slot ``item``) answers where something is.
  The verb follows the name ("Wood screws are in…", "Drill is in…").
  Several records with the same name are one answer ("Drill is in A, and
  in B").  When the words match *different* things, it asks instead:
  "I found 3 things for hose: A, B and C. Which one did you mean?" and
  remembers the choices for two minutes.
* ``HomeInventoryWhereDoesItGo`` (slot ``item``) answers where something
  *belongs* - its usual place - for putting it back: "Hammer goes in
  Garage, Tool wall." If it's out (taken, lent, here for now), that's
  added: "It's in the kitchen for now."
* ``HomeInventoryChoose`` (slot ``choice``) is the reply: "the kink control
  one", "the second one", "the siphon pump".  It answers for that thing.

The question ends with "?" and carries a reprompt, which keeps the
conversation open: Assist voice devices listen again, and Home Assistant's
Alexa integration keeps the Alexa session open.

Three questions need no slots: ``HomeInventoryMaintenanceDue`` ("what
maintenance is due?"), ``HomeInventoryRunningLow`` ("what's running low?")
and ``HomeInventoryOnLoan`` ("what's lent out?").

Final answers can also go to a phone as a notification (the integration's
options: off, Alexa only, or all voice assistants).

Any voice front end that hands Home Assistant an intent can use these:
Assist (with the custom sentences in ``docs/voice.md``), Siri through a
Shortcut that passes text to Assist, and an Alexa custom skill.
"""

from __future__ import annotations

import logging
import re
import time
from contextvars import ContextVar
from difflib import SequenceMatcher
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .api import InventoryApiError, InventoryError
from .const import CONF_NOTIFY_SERVICE, CONF_VOICE_NOTIFY, DOMAIN, NAME, VOICE_NOTIFY_ALEXA, VOICE_NOTIFY_ALL
from .names import key as name_key
from .names import normalize

_LOGGER = logging.getLogger(__name__)

INTENT_WHERE_IS = "HomeInventoryWhereIs"
INTENT_CHOOSE = "HomeInventoryChoose"
INTENT_GOES = "HomeInventoryWhereDoesItGo"
INTENT_MOVE = "HomeInventoryMove"
INTENT_WHATS_IN = "HomeInventoryWhatsIn"
YES = {"yes", "yeah", "yep", "yup", "correct", "right", "that right", "thats right", "sure", "ok", "okay", "do it", "yes please"}
NO = {"no", "nope", "no thanks", "neither", "none", "none of them", "wrong", "cancel", "stop"}
INTENT_MAINTENANCE = "HomeInventoryMaintenanceDue"
INTENT_MAINT_PARTS = "HomeInventoryMaintenanceParts"
INTENT_HAVE_PARTS = "HomeInventoryHaveParts"
INTENT_LAST_SERVICE = "HomeInventoryLastService"
INTENT_ATTENTION = "HomeInventoryNeedsAttention"
INTENT_RECENT_MAINT = "HomeInventoryRecentMaintenance"
MAINT_RANGES = ("overdue", "today", "week", "month", "upcoming", "all")
MOVED_RE = re.compile(r"(?i)^(?:the |my )?(.+?) (?:is|are) now (?:in|on|at) (?:the )?(.+)$")
INTENT_LOW_STOCK = "HomeInventoryRunningLow"
INTENT_ON_LOAN = "HomeInventoryOnLoan"
SPOKEN_MAX = 5  # read out at most this many things, then "and N more"
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


def _box(c: dict[str, Any]) -> str:
    """A container as it's spoken: "Plastic tote number 1, labelled Hoses"."""
    name, kind, number = c.get("name") or "a box", (c.get("type") or "").strip(), c.get("number")
    num = f" number {number}" if number else ""
    if kind and kind.lower() not in name.lower():
        return f"{kind[:1].upper()}{kind[1:]}{num}, labelled {name}"
    return f"{name}{num}"


def _place(item: dict[str, Any]) -> str:
    """Where a record is: the box first (innermost first), then where the box is."""
    p = item.get("placement") or {}
    boxes = [_box(c) for c in reversed(p.get("container_path") or [])]
    rooms = ", ".join(loc["name"] for loc in p.get("location_path") or [])
    if boxes:
        text = ", inside ".join(boxes)
        return f"{text}, located in {rooms}" if rooms else text
    return rooms or (p.get("path") or "").replace(" › ", ", ") or "somewhere without a place yet"


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


def group_by_name(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records of the same thing (same name) together, best match first."""
    groups: dict[str, dict[str, Any]] = {}
    for it in results:
        name = it.get("display_name") or it.get("name") or "That"
        g = groups.setdefault(name_key(name), {"name": name, "items": []})
        g["items"].append(it)
    return list(groups.values())


# Words that are plural without ending in "s" (or always plural), and
# singular words that do end in "s".
_PLURAL = {"people", "children", "men", "women", "feet", "teeth", "mice", "geese", "dice", "cattle",
           "scissors", "pliers", "tongs", "tweezers", "shears", "clippers", "loppers", "secateurs",
           "binoculars", "goggles", "headphones", "earphones", "jeans", "trousers", "shorts", "pants",
           "glasses", "spectacles", "overalls", "pyjamas", "pajamas", "snips", "nippers", "cutters"}
_SINGULAR = {"series", "species", "news", "gas", "bus", "lens", "chassis", "corps", "means", "kit", "set",
             "pack", "box", "bag", "case", "tin", "jar", "pair", "bundle", "roll", "reel", "tub", "bucket"}
_UNITS = {"ft", "in", "mm", "cm", "m", "km", "v", "w", "kw", "a", "ah", "mah", "kg", "g", "lb", "lbs", "oz",
          "ml", "l", "pk", "pc", "pcs", "gal", "psi", "nm", "hp", "cc", "x"}


def verb_for(name: str) -> str:
    """ "is" or "are" for an item name ("Wood screws are", "Siphon pump with hose is").

    The noun that decides is the main one: before "of"/"with"/"for" ("Box of
    screws is", "Set of drill bits is"), the last real word otherwise (sizes
    such as "50ft" are skipped).  Two things joined by "and" are plural.
    """
    head = re.split(r"\b(?:of|with|for|from|in|on)\b", name.lower(), maxsplit=1)[0]
    if re.search(r"\band\b|&", head):
        return "are"
    words = [w for w in re.findall(r"[a-z]+(?:'[a-z]+)?", head) if w not in _UNITS]
    if not words:
        return "is"
    w = words[-1]
    if w in _PLURAL:
        return "are"
    if w in _SINGULAR or w.endswith(("ss", "us", "is", "'s")) or len(w) <= 2:
        return "is"
    return "are" if w.endswith("s") else "is"


def answer_for(group: dict[str, Any]) -> str:
    places = [_where(it) for it in group["items"][:3]]
    more = len(group["items"]) - 3
    tail = f", and {more} more place{'s' if more != 1 else ''}" if more > 0 else ""
    return f"{group['name']} {verb_for(group['name'])} " + ", and ".join(places) + tail + "."


def goes_query(text: str) -> str:
    """The name in "put away the drill back": the sentence wildcard can keep
    "away the" / "back" around it."""
    return re.sub(r"^(?:(?:away|back|the|my)\s+)+|(?:\s+(?:back|away))+$", "", text.strip(), flags=re.I) or text


def _home(item: dict[str, Any]) -> str:
    """Where a record belongs: its usual place when it's away, else where it is."""
    home = item.get("home_placement") or item.get("placement") or {}
    return _place({"placement": home})


def _away(item: dict[str, Any]) -> str | None:
    """Why it isn't there now, as a short sentence (or None)."""
    status = item.get("status")
    if item.get("loan"):
        return f"It's lent to {item['loan'].get('borrower') or 'someone'} at the moment."
    if status == "in_use":
        note = item.get("status_note") or ""
        who = note[len("Taken by "):] if note.startswith("Taken by ") else ""
        return f"{who} has it at the moment." if who else "It's in use at the moment."
    if status == "temporarily_relocated" and item.get("home_placement"):
        return f"It's in {_place(item)} for now."
    if status == "missing":
        return "It's marked missing."
    return None


def goes_answer_for(group: dict[str, Any]) -> str:
    """ "Hammer goes in Garage, Tool wall." - where to put it back."""
    items = group["items"]
    places: list[str] = []
    for it in items:
        p = _home(it)
        if p not in places:
            places.append(p)
    verb = "go" if verb_for(group["name"]) == "are" else "goes"
    shown = places[:3]
    more = len(places) - 3
    tail = f", or {more} other place{'s' if more != 1 else ''}" if more > 0 else ""
    text = f"{group['name']} {verb} in " + ", or in ".join(shown) + tail + "."
    if len(items) == 1 and (away := _away(items[0])):
        text += f" {away}"
    return text


def _list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def spoken_answer(query: str, results: list[dict[str, Any]], goes: bool = False) -> tuple[str, list[dict[str, Any]] | None]:
    """(what to say, the choices to remember if it's a question).
    ``goes``: answer where it belongs rather than where it is."""
    if not results:
        return f"I couldn't find {query} in your inventory.", None
    groups = group_by_name(results)
    exact = [g for g in groups if name_key(g["name"]) == name_key(query)]
    if len(groups) == 1 or len(exact) == 1:
        group = exact[0] if exact else groups[0]
        return (goes_answer_for(group) if goes else answer_for(group)), None
    shown = groups[:MAX_CHOICES]
    names = [g["name"] for g in shown]
    if len(groups) > MAX_CHOICES:
        return (f"I found {len(groups)} things for {query}, including {_list(names[:4])}. "
                "Which one did you mean?"), shown
    return f"I found {len(groups)} things for {query}: {_list(names)}. Which one did you mean?", shown


def pick(choice: str, groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The group a reply refers to: by position ("the second one") or name."""
    words = normalize(choice).split()
    for w in words:
        if w in ORDINALS and ORDINALS[w] < len(groups) and not any(w in normalize(g["name"]).split() for g in groups):
            return groups[ORDINALS[w]]
    if "last" in words:
        return groups[-1]
    target = " ".join(w for w in words if w not in {"the", "one", "a", "an", "i", "meant", "it", "is", "please", "called"})
    if not target:
        return None
    best, best_score = None, 0.0
    for g in groups:
        name = normalize(g["name"])
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


# True while answering an open question: that conversation may update or
# close its own pending state.
_CONTINUING: ContextVar[bool] = ContextVar("home_inventory_continuing", default=False)


def _key_info(intent_obj: intent.Intent) -> tuple[str, bool]:
    """Where a conversation's open question is kept, and whether that key
    reliably separates conversations.  A voice device or satellite does;
    without one (Alexa through /api/alexa, the Assist dialog) all requests
    from the same platform and user share a key, so it is not reliable."""
    if intent_obj.device_id or intent_obj.satellite_id:
        return str(intent_obj.device_id or intent_obj.satellite_id), True
    return f"{intent_obj.platform or 'default'}:{intent_obj.context.user_id or '-'}", False


def _key(intent_obj: intent.Intent) -> str:
    return _key_info(intent_obj)[0]


def _open(intent_obj: intent.Intent, state: dict[str, Any]) -> bool:
    """Store an open question.  Under a shared key a new question never
    replaces another conversation's open one: returns False instead, and the
    caller answers without asking (fail safe rather than cross the answers)."""
    key, reliable = _key_info(intent_obj)
    pending = _pending(intent_obj.hass)
    current = pending.get(key)
    if not reliable and not _CONTINUING.get() and current and current["until"] >= time.monotonic():
        return False
    pending[key] = {**state, "until": time.monotonic() + PENDING_SECONDS}
    return True


def _close(intent_obj: intent.Intent) -> None:
    """Drop this conversation's open question (never another's)."""
    key, reliable = _key_info(intent_obj)
    if reliable or _CONTINUING.get():
        _pending(intent_obj.hass).pop(key, None)


def _no_question(response: intent.IntentResponse, names: list[str]) -> intent.IntentResponse:
    response.async_set_speech(f"I found several: {_list(names[:4])}. Please ask again with the full name.")
    return response


async def _search(intent_obj: intent.Intent, query: str) -> list[dict[str, Any]] | None:
    hass = intent_obj.hass
    entries = hass.data.get(DOMAIN) or {}
    if not entries:
        return None
    client = next(iter(entries.values())).client
    user = await hass.auth.async_get_user(intent_obj.context.user_id) if intent_obj.context.user_id else None
    r = await client.request("GET", "search", params={"q": query, "limit": 10, "ai": "false"}, user=user)
    return r.get("results") or []


def _photo_of(items: list[dict[str, Any]] | None) -> int | None:
    """The photo to show with an answer: the first record with one."""
    for it in items or []:
        photo = it.get("photo") or {}
        if photo.get("id"):
            return int(photo["id"])
    return None


async def _notify(intent_obj: intent.Intent, answer: str, items: list[dict[str, Any]] | None = None) -> None:
    """Also send a final answer as a notification, if that's turned on.

    Only answers that found something are sent (not questions, not "I
    couldn't find…").  A notification that fails never spoils the spoken answer.
    """
    hass = intent_obj.hass
    entries = hass.config_entries.async_entries(DOMAIN)
    opts = entries[0].options if entries else {}
    mode, service = opts.get(CONF_VOICE_NOTIFY), opts.get(CONF_NOTIFY_SERVICE)
    if not service or mode not in (VOICE_NOTIFY_ALEXA, VOICE_NOTIFY_ALL):
        return
    if mode == VOICE_NOTIFY_ALEXA and intent_obj.platform != "alexa":
        return
    try:
        msg: dict[str, Any] = {"title": NAME, "message": answer}
        photo = _photo_of(items)
        if photo is not None:
            # "Show me": the companion app shows the photo (it signs in to
            # Home Assistant to fetch a relative /api/ image).
            from .photo import PHOTO_URL

            msg["data"] = {"image": f"{PHOTO_URL}/{photo}", "attachment": {"url": f"{PHOTO_URL}/{photo}", "content-type": "jpeg"}}
        await hass.services.async_call("notify", service, msg, blocking=False, context=intent_obj.context)
    except Exception as err:  # noqa: BLE001 - never break the voice answer
        _LOGGER.warning("Couldn't send the voice answer to notify.%s: %s", service, err)


def _ask(response: intent.IntentResponse, speech: str, reprompt: str) -> None:
    response.async_set_speech(speech)
    response.async_set_reprompt(reprompt)  # keeps an Alexa session open


class WhereIsHandler(intent.IntentHandler):
    intent_type = INTENT_WHERE_IS
    description = "Finds where something is kept, using Home Inventory"
    slot_schema = {vol.Required("item"): cv.string}
    goes = False

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        query = str(slots["item"]["value"]).strip().strip("?.!")
        if self.goes:
            query = goes_query(query)
        response = intent_obj.create_response()
        try:
            results = await _search(intent_obj, query)
        except InventoryError:
            response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
            return response
        if results is None:
            response.async_set_speech("Home Inventory isn't set up.")
            return response
        speech, choices = spoken_answer(query, results, goes=self.goes)
        if choices:
            if not _open(intent_obj, {"groups": choices, "goes": self.goes}):
                return _no_question(response, [g["name"] for g in choices])
            _ask(response, speech, f"Which one: {_list([g['name'] for g in choices[:4]])}?")
        else:
            _close(intent_obj)
            response.async_set_speech(speech)
            if results:
                groups = group_by_name(results)
                exact = [g for g in groups if name_key(g["name"]) == name_key(query)]
                await _notify(intent_obj, speech, (exact[0] if exact else groups[0])["items"])
        return response


# ---------------------------------------------------------------------------
# "I've moved the engine oil to small engine repair"
# ---------------------------------------------------------------------------
#
# 1. Find the item (asking which one if the words fit several things, or
#    which record if the same thing is kept in several places).
# 2. Find the place (App ``places/resolve``): a clear match moves at once;
#    one doubtful match asks "Did you mean…?" (yes/no); several ask which.
# 3. Move it (App ``put-away`` in "move" mode: the new place becomes its
#    home; its usual place counts as putting it back) and say so.


def _place_label(c: dict[str, Any]) -> str:
    return f"{c['spoken']}, in {c['where']}" if c.get("where") else c["spoken"]


def _record_label(item: dict[str, Any]) -> str:
    return f"the one in {_place(item)}"


async def _client_user(intent_obj: intent.Intent):
    hass = intent_obj.hass
    entries = hass.data.get(DOMAIN) or {}
    if not entries:
        return None, None
    client = next(iter(entries.values())).client
    user = await hass.auth.async_get_user(intent_obj.context.user_id) if intent_obj.context.user_id else None
    return client, user


def _remember(intent_obj: intent.Intent, state: dict[str, Any]) -> bool:
    return _open(intent_obj, {**state, "kind": "move"})


async def _move_find_place(intent_obj: intent.Intent, response: intent.IntentResponse, item: dict[str, Any],
                           place_text: str) -> intent.IntentResponse:
    client, user = await _client_user(intent_obj)
    res = await client.request("GET", "places/resolve", params={"q": place_text}, user=user)
    cands = res.get("candidates") or []
    name = item.get("display_name") or item.get("name") or "it"
    if not cands:
        _close(intent_obj)
        response.async_set_speech(f"I couldn't find a place called {place_text}, so I haven't moved {name}.")
        return response
    if res.get("clear"):
        return await _move_do(intent_obj, response, item, cands[0])
    if len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= 0.2:
        if not _remember(intent_obj, {"stage": "confirm", "item": item, "place": cands[0]}):
            return _no_question(response, [_place_label(c) for c in cands])
        _ask(response, f"Did you mean {_place_label(cands[0])}?", "Yes or no?")
        return response
    shown = cands[:4]
    if not _remember(intent_obj, {"stage": "place", "item": item, "groups": [{"name": _place_label(c), "place": c} for c in shown]}):
        return _no_question(response, [_place_label(c) for c in shown])
    _ask(response, f"Which place: {_list([_place_label(c) for c in shown])}?", "Which place did you mean?")
    return response


async def _move_do(intent_obj: intent.Intent, response: intent.IntentResponse, item: dict[str, Any],
                   place: dict[str, Any]) -> intent.IntentResponse:
    _close(intent_obj)
    client, user = await _client_user(intent_obj)
    body = {"container_id": place["id"] if place["type"] == "container" else None,
            "location_id": place["id"] if place["type"] == "location" else None, "mode": "move"}
    name = item.get("display_name") or item.get("name") or "it"
    try:
        r = await client.request("POST", f"items/{item['id']}/put-away", json_body=body, user=user)
    except InventoryApiError as err:
        if err.status == 403:
            response.async_set_speech("Sorry, your Home Inventory account can't move things.")
        else:
            response.async_set_speech(f"Sorry, I couldn't move {name}: {err}.")
        return response
    where = _place_label(place)
    speech = {
        "back": f"{name} {verb_for(name)} already in {where}.",
        "home": f"Done. {name} {verb_for(name)} back in {where}.",
    }.get(r.get("result"), f"Done. I've moved {name} to {where}.")
    response.async_set_speech(speech)
    await _notify(intent_obj, speech, [r.get("item") or item])
    return response


async def _move_with_item(intent_obj: intent.Intent, response: intent.IntentResponse, group: dict[str, Any],
                          place_text: str) -> intent.IntentResponse:
    """One thing chosen: if it's kept in several places, ask which record."""
    records = group["items"]
    if len(records) > 1:
        shown = records[:MAX_CHOICES]
        if not _remember(intent_obj, {"stage": "record", "place_text": place_text,
                                      "groups": [{"name": _record_label(it), "item": it} for it in shown]}):
            return _no_question(response, [_record_label(it) for it in shown])
        _ask(response, f"Which {group['name']}: {_list([_record_label(it) for it in shown[:4]])}?",
             f"Which {group['name']}?")
        return response
    return await _move_find_place(intent_obj, response, records[0], place_text)


class MoveHandler(intent.IntentHandler):
    intent_type = INTENT_MOVE
    description = "Records that something has been moved to another place, using Home Inventory"
    slot_schema = {vol.Required("item"): cv.string, vol.Required("place"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        query = goes_query(str(slots["item"]["value"]).strip().strip("?.!"))
        place_text = str(slots["place"]["value"]).strip().strip("?.!")
        response = intent_obj.create_response()
        try:
            results = await _search(intent_obj, query)
            if results is None:
                response.async_set_speech("Home Inventory isn't set up.")
                return response
            if not results:
                response.async_set_speech(f"I couldn't find {query} in your inventory, so I haven't moved anything.")
                return response
            groups = group_by_name(results)
            exact = [g for g in groups if name_key(g["name"]) == name_key(query)]
            if len(groups) == 1 or len(exact) == 1:
                return await _move_with_item(intent_obj, response, exact[0] if exact else groups[0], place_text)
            shown = groups[:MAX_CHOICES]
            if not _remember(intent_obj, {"stage": "item", "place_text": place_text, "groups": shown}):
                return _no_question(response, [g["name"] for g in shown])
            _ask(response, f"Which one did you move: {_list([g['name'] for g in shown[:4]])}?", "Which one did you move?")
            return response
        except InventoryError:
            response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
            return response


async def _move_reply(intent_obj: intent.Intent, response: intent.IntentResponse, open_q: dict[str, Any],
                      choice: str) -> intent.IntentResponse:
    """The answer to one of the move questions."""
    said = normalize(choice)
    if said in NO:
        _close(intent_obj)
        response.async_set_speech("Okay, I haven't moved anything.")
        return response
    try:
        if open_q["stage"] == "confirm":
            if said in YES:
                return await _move_do(intent_obj, response, open_q["item"], open_q["place"])
            # They said a place instead: look that up.
            return await _move_find_place(intent_obj, response, open_q["item"], choice)
        group = pick(choice, open_q["groups"])
        if group is None:
            names = [g["name"] for g in open_q["groups"]]
            _ask(response, f"Sorry, which one: {_list(names[:4])}?", "Which one did you mean? Or say no.")
            return response
        if open_q["stage"] == "item":
            return await _move_with_item(intent_obj, response, group, open_q["place_text"])
        if open_q["stage"] == "record":
            return await _move_find_place(intent_obj, response, group["item"], open_q["place_text"])
        return await _move_do(intent_obj, response, open_q["item"], group["place"])
    except InventoryError:
        response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
        return response


# ---------------------------------------------------------------------------
# "What's in box 5?" / "What's on the top shelf?"
# ---------------------------------------------------------------------------


def _count_name(it: dict[str, Any]) -> str:
    name = it.get("display_name") or it.get("name") or "something"
    q = it.get("quantity") or 1
    return f"{q} {name}" if q != 1 else name


def contents_answer(place: dict[str, Any], data: dict[str, Any]) -> str:
    """ "Box number 5, Fittings, has: 3 washers, a drill and 1 more." """
    label = _place_label(place)
    things = [_count_name(i) for i in data.get("items") or []]
    boxes = [(_box(c) if c.get("number") or c.get("type") else c.get("name") or "a box") for c in data.get("containers") or []]
    places = [c.get("name") for c in data.get("children") or [] if c.get("name")]
    parts = []
    if things:
        parts.append(_spoken_list(things))
    if boxes:
        parts.append(f"{_plural(len(boxes), 'box', 'boxes')}: {_spoken_list(boxes)}")
    if places:
        parts.append(f"{_plural(len(places), 'place', 'places')} inside it: {_spoken_list(places)}")
    if not parts:
        return f"Nothing is recorded in {label}."
    return f"In {label}: " + "; ".join(parts) + "."


async def _say_contents(intent_obj: intent.Intent, response: intent.IntentResponse, place: dict[str, Any]) -> intent.IntentResponse:
    _close(intent_obj)
    client, user = await _client_user(intent_obj)
    path = f"containers/{place['id']}" if place["type"] == "container" else f"locations/{place['id']}"
    data = await client.request("GET", path, user=user)
    speech = contents_answer(place, data or {})
    response.async_set_speech(speech)
    await _notify(intent_obj, speech)
    return response


class WhatsInHandler(intent.IntentHandler):
    intent_type = INTENT_WHATS_IN
    description = "Says what is in a box or on a shelf, using Home Inventory"
    slot_schema = {vol.Required("place"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = self.async_validate_slots(intent_obj.slots)
        place_text = str(slots["place"]["value"]).strip().strip("?.!")
        response = intent_obj.create_response()
        try:
            client, user = await _client_user(intent_obj)
            if client is None:
                response.async_set_speech("Home Inventory isn't set up.")
                return response
            res = await client.request("GET", "places/resolve", params={"q": place_text}, user=user)
            cands = res.get("candidates") or []
            if not cands:
                response.async_set_speech(f"I couldn't find a place called {place_text}.")
                return response
            if res.get("clear") or len(cands) == 1:
                return await _say_contents(intent_obj, response, cands[0])
            shown = cands[:4]
            if not _open(intent_obj, {"kind": "whatsin", "groups": [{"name": _place_label(c), "place": c} for c in shown]}):
                return _no_question(response, [_place_label(c) for c in shown])
            _ask(response, f"Which one: {_list([_place_label(c) for c in shown])}?", "Which place did you mean?")
            return response
        except InventoryError:
            response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
            return response


class WhereDoesItGoHandler(WhereIsHandler):
    intent_type = INTENT_GOES
    description = "Says where something belongs, so it can be put back, using Home Inventory"
    goes = True


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
            if normalize(choice) in YES | NO:
                response.async_set_speech("There's nothing to confirm right now.")
                return response
            # Assist ranks "the {choice}" (one wildcard) above "the {item} is now in
            # the {place}" (two), so a move said that way arrives here: do the move.
            moved = MOVED_RE.match(choice)
            if moved:
                return await MoveHandler().async_handle(
                    intent.Intent(intent_obj.hass, intent_obj.platform, INTENT_MOVE,
                                  {"item": {"value": moved.group(1)}, "place": {"value": moved.group(2)}},
                                  intent_obj.text_input, intent_obj.context, intent_obj.language,
                                  assistant=intent_obj.assistant, device_id=intent_obj.device_id,
                                  satellite_id=intent_obj.satellite_id))
            # No question open: a bare name ("the toro lawn mower") is a question itself.
            return await WhereIsHandler().async_handle(
                intent.Intent(intent_obj.hass, intent_obj.platform, INTENT_WHERE_IS,
                              {"item": {"value": choice}}, intent_obj.text_input, intent_obj.context,
                              intent_obj.language, assistant=intent_obj.assistant, device_id=intent_obj.device_id,
                              satellite_id=intent_obj.satellite_id))
        token = _CONTINUING.set(True)
        try:
            return await self._answer(intent_obj, response, pending, key, open_q, choice)
        finally:
            _CONTINUING.reset(token)

    async def _answer(self, intent_obj: intent.Intent, response: intent.IntentResponse, pending: dict[str, Any],
                      key: str, open_q: dict[str, Any], choice: str) -> intent.IntentResponse:
        if open_q.get("kind") == "move":
            return await _move_reply(intent_obj, response, open_q, choice)
        if open_q.get("kind") == "maint" and normalize(choice) not in NO:
            chosen = pick(choice, open_q["groups"])
            if chosen is not None:
                return await _maintenance(intent_obj, {**open_q["params"], "equipment": None, "equipment_id": chosen["id"]})
        if open_q.get("kind") == "whatsin" and normalize(choice) not in NO:
            chosen = pick(choice, open_q["groups"])
            if chosen is not None:
                try:
                    return await _say_contents(intent_obj, response, chosen["place"])
                except InventoryError:
                    response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
                    return response
        if normalize(choice) in NO:
            pending.pop(key, None)
            response.async_set_speech("Okay.")
            return response
        group = pick(choice, open_q["groups"])
        if group is None:
            names = [g["name"] for g in open_q["groups"]]
            _ask(response, f"Sorry, which one: {_list(names[:4])}?", "Which one did you mean?")
            return response
        pending.pop(key, None)
        speech = goes_answer_for(group) if open_q.get("goes") else answer_for(group)
        response.async_set_speech(speech)
        await _notify(intent_obj, speech, group["items"])
        return response


async def _get(intent_obj: intent.Intent, path: str, params: dict[str, Any] | None = None) -> Any:
    hass = intent_obj.hass
    entries = hass.data.get(DOMAIN) or {}
    if not entries:
        return None
    client = next(iter(entries.values())).client
    user = await hass.auth.async_get_user(intent_obj.context.user_id) if intent_obj.context.user_id else None
    return await client.request("GET", path, params=params, user=user)


def _spoken_list(names: list[str]) -> str:
    if len(names) <= SPOKEN_MAX:
        return _list(names)
    return ", ".join(names[:SPOKEN_MAX]) + f" and {len(names) - SPOKEN_MAX} more"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def maintenance_answer(dash: dict[str, Any]) -> str:
    overdue = [f"{t['item']['name']} {t['title'].lower()}" for t in dash.get("overdue", [])]
    soon = [f"{t['item']['name']} {t['title'].lower()}" for t in dash.get("due", [])]
    if not overdue and not soon:
        return "Nothing is due for maintenance in the next two weeks."
    parts = []
    if overdue:
        parts.append(f"{_plural(len(overdue), 'job is', 'jobs are')} overdue: {_spoken_list(overdue)}")
    if soon:
        parts.append(f"{_plural(len(soon), 'job is', 'jobs are')} due in the next two weeks: {_spoken_list(soon)}")
    return ". ".join(parts) + "."


def low_stock_answer(rows: list[dict[str, Any]]) -> str:
    low = [r for r in rows if r.get("min_quantity") is not None and r["quantity"] < r["min_quantity"]]
    if not low:
        return "Nothing is running low."
    names = [f"{r['name']} ({r['quantity']} left)" for r in low]
    return f"You're running low on {_spoken_list(names)}."


def loans_answer(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Nothing is lent out."
    names = [f"{r['item']['name']} to {r['borrower']}" for r in rows]
    return f"{_plural(len(rows), 'thing is', 'things are')} lent out: {_spoken_list(names)}."


class _QuestionHandler(intent.IntentHandler):
    """A question with no slots: ask the App, say the answer."""

    path = ""
    params: dict[str, Any] | None = None

    def answer(self, data: Any) -> str:
        raise NotImplementedError

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        response = intent_obj.create_response()
        try:
            data = await _get(intent_obj, self.path, self.params)
        except InventoryError:
            response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
            return response
        if data is None:
            response.async_set_speech("Home Inventory isn't set up.")
            return response
        speech = self.answer(data)
        response.async_set_speech(speech)
        await _notify(intent_obj, speech)
        return response


# ---------------------------------------------------------------------------
# Maintenance questions.  The App words the answer from the real schedules;
# here we only pass the question on, and ask "which one?" when the
# equipment named could be more than one machine.
# ---------------------------------------------------------------------------


def _slot(intent_obj: intent.Intent, name: str) -> str | None:
    v = (intent_obj.slots or {}).get(name, {}).get("value")
    v = str(v).strip().strip("?.!") if v is not None else ""
    return v or None


async def _maintenance(intent_obj: intent.Intent, params: dict[str, Any]) -> intent.IntentResponse:
    response = intent_obj.create_response()
    try:
        data = await _get(intent_obj, "voice/maintenance", params)
    except InventoryError:
        response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
        return response
    if data is None:
        response.async_set_speech("Home Inventory isn't set up.")
        return response
    choose = data.get("choose")
    if choose:
        groups = [{"name": c["name"], "id": c["id"]} for c in choose]
        if not _open(intent_obj, {"kind": "maint", "groups": groups, "params": params}):
            return _no_question(response, [g["name"] for g in groups])
        _ask(response, data.get("speech") or f"Which one: {_list([g['name'] for g in groups])}?", "Which one did you mean?")
        return response
    _close(intent_obj)
    response.async_set_speech(data.get("speech") or "")
    await _notify(intent_obj, data.get("speech") or "")
    return response


class MaintenanceDueHandler(intent.IntentHandler):
    """ "What upcoming maintenance do I need to do?", "...on the Toro mower?",
    "What maintenance is overdue / due this week?" """

    intent_type = INTENT_MAINTENANCE
    description = "Says which equipment maintenance is due (overdue, this week, upcoming...), optionally for one machine"
    slot_schema = {vol.Optional("equipment"): cv.string, vol.Optional("range"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        rng = (_slot(intent_obj, "range") or "upcoming").lower()
        return await _maintenance(intent_obj, {"kind": "due", "range": rng if rng in MAINT_RANGES else "upcoming",
                                               "equipment": _slot(intent_obj, "equipment")})


class MaintenancePartsHandler(intent.IntentHandler):
    """ "What parts do I need for the next mower service?" """

    intent_type = INTENT_MAINT_PARTS
    description = "Says which parts the next service on a machine needs, and whether they're in stock"
    slot_schema = {vol.Required("equipment"): cv.string}
    kind = "parts"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        return await _maintenance(intent_obj, {"kind": self.kind, "equipment": _slot(intent_obj, "equipment")})


class HavePartsHandler(MaintenancePartsHandler):
    """ "Do I have the parts I need to service the snowblower?" """

    intent_type = INTENT_HAVE_PARTS
    description = "Says whether everything the next service on a machine needs is in stock"
    kind = "have_parts"


class LastServiceHandler(intent.IntentHandler):
    """ "When did I last change the oil in the mower?" """

    intent_type = INTENT_LAST_SERVICE
    description = "Says when a maintenance job was last done, from the service history"
    slot_schema = {vol.Required("task"): cv.string, vol.Optional("equipment"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        return await _maintenance(intent_obj, {"kind": "last_done", "task": _slot(intent_obj, "task"),
                                               "equipment": _slot(intent_obj, "equipment")})


class NeedsAttentionHandler(intent.IntentHandler):
    intent_type = INTENT_ATTENTION
    description = "Says which equipment has overdue or due-soon maintenance"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        return await _maintenance(intent_obj, {"kind": "attention"})


class RecentMaintenanceHandler(intent.IntentHandler):
    intent_type = INTENT_RECENT_MAINT
    description = "Says what maintenance was completed in the last month"

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        return await _maintenance(intent_obj, {"kind": "recent"})


MAINTENANCE_HANDLERS = (MaintenanceDueHandler, MaintenancePartsHandler, HavePartsHandler, LastServiceHandler, NeedsAttentionHandler,
                        RecentMaintenanceHandler)


class RunningLowHandler(_QuestionHandler):
    intent_type = INTENT_LOW_STOCK
    description = "Says which supplies are below their minimum, using Home Inventory"
    path = "low-stock"

    def answer(self, data: Any) -> str:
        return low_stock_answer(data)


class OnLoanHandler(_QuestionHandler):
    intent_type = INTENT_ON_LOAN
    description = "Says what is lent out and to whom, using Home Inventory"
    path = "loans"

    def answer(self, data: Any) -> str:
        return loans_answer(data)


QUESTION_HANDLERS = (*MAINTENANCE_HANDLERS, RunningLowHandler, OnLoanHandler)


async def _log(intent_obj: intent.Intent, response: intent.IntentResponse) -> None:
    """Record the question and answer in the App's Voice history (Admin).
    Never affects the answer: any problem is only logged here."""
    try:
        client, user = await _client_user(intent_obj)
        if client is None:
            return
        speech = ((response.speech or {}).get("plain") or {}).get("speech")
        slots = {k: str(v.get("value"))[:200] for k, v in (intent_obj.slots or {}).items() if isinstance(v, dict) and v.get("value") is not None}
        await client.request("POST", "voice/history", user=user, timeout=10, json_body={
            "source": intent_obj.platform, "device": intent_obj.device_id or intent_obj.satellite_id,
            "intent": intent_obj.intent_type, "slots": slots, "text": intent_obj.text_input,
            "answer": speech, "question": bool(response.reprompt),
        })
    except Exception as err:  # noqa: BLE001 - history is a nice-to-have
        _LOGGER.debug("Couldn't record voice history: %s", err)


def _logged(hass: HomeAssistant, handler: intent.IntentHandler) -> intent.IntentHandler:
    inner = handler.async_handle

    async def handle(intent_obj: intent.Intent) -> intent.IntentResponse:
        response = await inner(intent_obj)
        create = getattr(hass, "async_create_background_task", None)
        if create is not None:
            create(_log(intent_obj, response), "home_inventory voice history")
        else:
            hass.async_create_task(_log(intent_obj, response))
        return response

    handler.async_handle = handle  # type: ignore[method-assign]
    return handler


REGISTERED_KEY = f"{DOMAIN}_intents_registered"


def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the voice intents once, however many Home Inventory entries
    are set up (a second entry or a reload would otherwise log "Intent … is
    being overwritten" for each one)."""
    if hass.data.get(REGISTERED_KEY):
        return
    hass.data[REGISTERED_KEY] = True
    intent.async_register(hass, _logged(hass, WhereIsHandler()))
    intent.async_register(hass, _logged(hass, WhereDoesItGoHandler()))
    intent.async_register(hass, _logged(hass, MoveHandler()))
    intent.async_register(hass, _logged(hass, WhatsInHandler()))
    intent.async_register(hass, _logged(hass, ChooseHandler()))
    for handler in QUESTION_HANDLERS:
        intent.async_register(hass, _logged(hass, handler()))


def async_unload_intents(hass: HomeAssistant) -> None:
    remove = getattr(intent, "async_remove", None)
    if remove is not None:
        for name in (INTENT_WHERE_IS, INTENT_GOES, INTENT_MOVE, INTENT_WHATS_IN, INTENT_CHOOSE, INTENT_MAINTENANCE,
                     INTENT_MAINT_PARTS, INTENT_HAVE_PARTS, INTENT_LAST_SERVICE, INTENT_ATTENTION, INTENT_RECENT_MAINT, INTENT_LOW_STOCK,
                     INTENT_ON_LOAN):
            remove(hass, name)
    hass.data.pop(PENDING_KEY, None)
    hass.data.pop(REGISTERED_KEY, None)

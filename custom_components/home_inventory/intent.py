"""Voice: "where is the hedge trimmer?".

Registers the ``HomeInventoryWhereIs`` intent (slot ``item``).  Any voice
front end that hands Home Assistant an intent can use it:

* **Assist** (Home Assistant's own voice, the companion apps, Wear OS,
  satellites) with the custom sentences in ``docs/voice.md``;
* **Siri** through an Apple Shortcut that passes your question to Assist;
* an **Alexa custom skill** ("Alexa, ask Home Inventory where the hedge
  trimmer is"): Home Assistant's ``alexa`` integration forwards Alexa
  intents to the intent of the same name.

The answer is spoken, so it is kept short: the best one to three places.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent

from .api import InventoryError
from .const import DOMAIN

INTENT_WHERE_IS = "HomeInventoryWhereIs"


def _place(item: dict[str, Any]) -> str:
    return (item.get("placement") or {}).get("path", "").replace(" › ", ", ") or "somewhere without a place yet"


def spoken_answer(query: str, results: list[dict[str, Any]]) -> str:
    """A short sentence for a voice assistant."""
    if not results:
        return f"I couldn't find {query} in your inventory."
    lines = []
    for it in results[:3]:
        name = it.get("display_name") or query
        if it.get("loan"):
            who = it["loan"].get("borrower") or "someone"
            lines.append(f"{name} is lent to {who}")
            continue
        where = _place(it)
        if it.get("status") == "missing":
            lines.append(f"{name} is marked missing; it was last in {where}")
        elif it.get("home_placement") and it.get("status") == "temporarily_relocated":
            lines.append(f"{name} is in {where} for now, normally {_place({'placement': it['home_placement']})}")
        else:
            lines.append(f"{name} is in {where}")
    more = len(results) - 3
    tail = f", and {more} more place{'s' if more != 1 else ''}" if more > 0 else ""
    return "; ".join(lines) + tail + "."


class WhereIsHandler(intent.IntentHandler):
    intent_type = INTENT_WHERE_IS
    description = "Finds where something is kept, using Home Inventory"
    slot_schema = {vol.Required("item"): cv.string}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        query = str(slots["item"]["value"]).strip().strip("?.!")
        response = intent_obj.create_response()
        entries = hass.data.get(DOMAIN) or {}
        if not entries:
            response.async_set_speech("Home Inventory isn't set up.")
            return response
        client = next(iter(entries.values())).client
        user = await hass.auth.async_get_user(intent_obj.context.user_id) if intent_obj.context.user_id else None
        try:
            r = await client.request("GET", "search", params={"q": query, "limit": 5, "ai": "false"}, user=user)
        except InventoryError:
            response.async_set_speech("Sorry, I couldn't reach Home Inventory.")
            return response
        response.async_set_speech(spoken_answer(query, r.get("results") or []))
        return response


def async_setup_intents(hass: HomeAssistant) -> None:
    intent.async_register(hass, WhereIsHandler())


def async_unload_intents(hass: HomeAssistant) -> None:
    remove = getattr(intent, "async_remove", None)
    if remove is not None:
        remove(hass, INTENT_WHERE_IS)

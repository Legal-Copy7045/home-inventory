"""Alexa custom skill -> Home Assistant forwarder for Home Inventory.

Paste this file as ``lambda_function.py`` into an Alexa-hosted skill or an
AWS Lambda function (Python 3.12+, standard library only).

Why it exists: Home Assistant's ``/api/alexa`` endpoint (enabled by
``alexa:`` in configuration.yaml) needs a Home Assistant access token, which
Alexa itself can't send.  This function adds it and passes the question on;
Home Assistant runs the ``HomeInventoryWhereIs`` intent from the Home
Inventory integration and returns the spoken answer.

Settings (environment variables on AWS Lambda, or edit the defaults below on
an Alexa-hosted skill, which has no environment variables):

  HA_URL     https://your-home-assistant.example  (no trailing /api)
  HA_TOKEN   a Home Assistant long-lived access token
  SKILL_ID   amzn1.ask.skill....  (only this skill may use the function)

Keep the token private: anyone with it can use your Home Assistant as that
user.  A dedicated, non-administrator Home Assistant user is a good idea.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

HA_URL = os.environ.get("HA_URL", "")      # e.g. "https://example.ui.nabu.casa"
HA_TOKEN = os.environ.get("HA_TOKEN", "")  # long-lived access token
SKILL_ID = os.environ.get("SKILL_ID", "")  # amzn1.ask.skill.xxxxxxxx

TIMEOUT = 6  # seconds; Alexa gives up after about 8
# Intents answered by Home Assistant, and the slot each one needs.
FORWARDED = {"HomeInventoryWhereIs": "item", "HomeInventoryWhereDoesItGo": "item", "HomeInventoryChoose": "choice",
             "HomeInventoryMaintenanceDue": None, "HomeInventoryRunningLow": None, "HomeInventoryOnLoan": None}


def _say(text: str, end: bool = True, reprompt: str | None = None) -> dict:
    out = {
        "version": "1.0",
        "response": {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end},
    }
    if reprompt:
        out["response"]["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    return out


def _skill_id(event: dict) -> str | None:
    return ((event.get("session") or {}).get("application") or {}).get("applicationId") or (
        ((event.get("context") or {}).get("System") or {}).get("application") or {}
    ).get("applicationId")


ASKING = "home_inventory_asking"  # session attribute: a "which one?" question is open


def _forward(event: dict) -> dict:
    req = urllib.request.Request(
        HA_URL.rstrip("/") + "/api/alexa",
        data=json.dumps(event).encode(),
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            return _say("Home Assistant refused the request. Check the access token in the Home Inventory skill.")
        if err.code == 404:
            return _say("Home Assistant's Alexa support isn't turned on. Add alexa to configuration.yaml and restart.")
        return _say("Sorry, Home Assistant had a problem answering that.")
    except (urllib.error.URLError, TimeoutError, OSError):
        return _say("Sorry, I couldn't reach Home Assistant.")
    try:
        out = json.loads(body) if body else _say("Sorry, Home Assistant didn't answer.")
    except ValueError:
        return _say("Sorry, Home Assistant sent an answer I couldn't read.")
    # Remember whether Home Assistant asked "which one?", so a reply Alexa
    # can't place is answered sensibly instead of closing the question.
    asking = (out.get("response") or {}).get("shouldEndSession") is False
    out["sessionAttributes"] = {**(out.get("sessionAttributes") or {}), ASKING: asking}
    return out


def lambda_handler(event: dict, context=None) -> dict:
    if SKILL_ID and _skill_id(event) != SKILL_ID:
        raise ValueError("Request is not from the configured skill")  # Alexa shows a generic error
    if not HA_URL or not HA_TOKEN:
        return _say("The Home Inventory skill isn't set up yet: its Home Assistant address or token is missing.")

    request = event.get("request") or {}
    kind = request.get("type")
    if kind == "LaunchRequest":
        return _say("What are you looking for? For example, say: where is the hedge trimmer.", end=False,
                    reprompt="Ask me where something is.")
    if kind == "SessionEndedRequest":
        return {"version": "1.0", "response": {}}
    if kind != "IntentRequest":
        return _say("Sorry, I can't do that.")

    name = (request.get("intent") or {}).get("name")
    asking = bool(((event.get("session") or {}).get("attributes") or {}).get(ASKING))
    if name == "HomeInventoryAnswer":
        # A bare reply ("zero turn"): Home Assistant reads it as the answer to
        # "which one?", or as a new question when none is open.
        value = ((((request.get("intent") or {}).get("slots") or {}).get("answer") or {}).get("value"))
        if not value:
            return _say("Sorry, which one?", end=False, reprompt="Say its name, or for example: the first one.") if asking \
                else _say("Ask where something is, for example: where is the hedge trimmer.")
        request["intent"] = {"name": "HomeInventoryChoose", "confirmationStatus": "NONE",
                             "slots": {"choice": {"name": "choice", "value": value}}}
        name = "HomeInventoryChoose"
    if name in FORWARDED:
        needed = FORWARDED[name]
        slot = (((request.get("intent") or {}).get("slots") or {}).get(needed) or {}) if needed else {"value": True}
        if not slot.get("value"):
            return _say("What should I look for?", end=False, reprompt="Say, for example: where is the drill.")
        return _forward(event)
    if name == "AMAZON.HelpIntent":
        return _say("Ask where something is kept, for example: where is the hedge trimmer, or where something goes "
                    "back, for example: where does the hammer go. You can also ask what maintenance is due, "
                    "what's running low, or what's lent out.", end=False,
                    reprompt="What are you looking for?")
    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
        return _say("Okay.")
    if asking:
        return _say("Sorry, I didn't catch which one. Say its name, or for example: the first one.", end=False,
                    reprompt="Which one did you mean?")
    # End the session, so the next "Alexa, ask Home Inventory..." starts afresh
    # (inside an open session that phrase isn't understood).
    return _say("Sorry, I didn't catch that. Try: where is the hedge trimmer, or: where does the hammer go.")

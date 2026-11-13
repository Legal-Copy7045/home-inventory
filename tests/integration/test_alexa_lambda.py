"""The Alexa skill forwarder in deploy/alexa (standard library only)."""

from __future__ import annotations

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SKILL = "amzn1.ask.skill.test"


def _load(monkeypatch, url, token="tok", skill=SKILL):
    monkeypatch.setenv("HA_URL", url)
    monkeypatch.setenv("HA_TOKEN", token)
    monkeypatch.setenv("SKILL_ID", skill)
    path = Path(__file__).parents[2] / "deploy" / "alexa" / "lambda_function.py"
    spec = importlib.util.spec_from_file_location("alexa_lambda", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_ha():
    seen = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("Authorization"), body))
            if self.headers.get("Authorization") != "Bearer tok":
                self.send_response(401)
                self.end_headers()
                return
            out = json.dumps({"version": "1.0", "response": {"outputSpeech": {"type": "PlainText", "text": "Hedge trimmer is in Shed."}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", seen
    srv.shutdown()


def _event(kind="IntentRequest", intent=None, item=None, skill=SKILL):
    req = {"type": kind}
    if intent:
        req["intent"] = {"name": intent, "slots": {"item": {"name": "item", **({"value": item} if item else {})}}}
    return {"version": "1.0", "session": {"application": {"applicationId": skill}}, "request": req}


def _text(res):
    return res["response"]["outputSpeech"]["text"]


def test_where_is_is_forwarded_with_the_token(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url + "/")
    res = mod.lambda_handler(_event(intent="HomeInventoryWhereIs", item="hedge trimmer"))
    assert _text(res) == "Hedge trimmer is in Shed."
    path, auth, body = seen[0]
    assert path == "/api/alexa" and auth == "Bearer tok"
    assert body["request"]["intent"]["slots"]["item"]["value"] == "hedge trimmer"


def test_built_in_requests_are_answered_without_home_assistant(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    assert mod.lambda_handler(_event("LaunchRequest"))["response"]["shouldEndSession"] is False
    assert "where is the hedge trimmer" in _text(mod.lambda_handler(_event(intent="AMAZON.HelpIntent")))
    assert _text(mod.lambda_handler(_event(intent="AMAZON.StopIntent"))) == "Okay."
    assert mod.lambda_handler(_event("SessionEndedRequest")) == {"version": "1.0", "response": {}}
    assert _text(mod.lambda_handler(_event(intent="HomeInventoryWhereIs"))) == "What should I look for?"  # empty slot
    assert seen == []


def test_other_skills_are_refused(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    with pytest.raises(ValueError):
        mod.lambda_handler(_event(intent="HomeInventoryWhereIs", item="x", skill="amzn1.ask.skill.someone-else"))
    assert seen == []


def test_problems_are_spoken_plainly(monkeypatch, fake_ha):
    url, _seen = fake_ha
    bad_token = _load(monkeypatch, url, token="wrong")
    assert "access token" in _text(bad_token.lambda_handler(_event(intent="HomeInventoryWhereIs", item="x")))
    unreachable = _load(monkeypatch, "http://127.0.0.1:9")
    assert _text(unreachable.lambda_handler(_event(intent="HomeInventoryWhereIs", item="x"))) == "Sorry, I couldn't reach Home Assistant."
    unset = _load(monkeypatch, "", token="")
    assert "isn't set up yet" in _text(unset.lambda_handler(_event(intent="HomeInventoryWhereIs", item="x")))


def test_questions_without_a_slot_are_forwarded(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    for name in ("HomeInventoryMaintenanceDue", "HomeInventoryRunningLow", "HomeInventoryOnLoan"):
        ev = _event(intent=name)
        ev["request"]["intent"]["slots"] = {}
        mod.lambda_handler(ev)
    assert [b["request"]["intent"]["name"] for _p, _a, b in seen] == [
        "HomeInventoryMaintenanceDue", "HomeInventoryRunningLow", "HomeInventoryOnLoan"]


def test_bare_answers_are_forwarded_as_the_reply(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    ev = _event(intent="HomeInventoryAnswer")
    ev["request"]["intent"]["slots"] = {"answer": {"name": "answer", "value": "zero turn"}}
    res = mod.lambda_handler(ev)
    body = seen[-1][2]["request"]["intent"]
    assert body["name"] == "HomeInventoryChoose" and body["slots"]["choice"]["value"] == "zero turn"
    assert res["sessionAttributes"][mod.ASKING] is False  # Home Assistant gave a final answer


def test_fallback_closes_the_session_unless_a_question_is_open(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    plain = mod.lambda_handler(_event(intent="AMAZON.FallbackIntent"))
    assert plain["response"]["shouldEndSession"] is True
    ev = _event(intent="AMAZON.FallbackIntent")
    ev["session"]["attributes"] = {mod.ASKING: True}
    open_q = mod.lambda_handler(ev)
    assert open_q["response"]["shouldEndSession"] is False and "which one" in _text(open_q)
    assert seen == []


def test_where_does_it_go_is_forwarded(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    mod.lambda_handler(_event(intent="HomeInventoryWhereDoesItGo", item="hammer"))
    intent_body = seen[-1][2]["request"]["intent"]
    assert intent_body["name"] == "HomeInventoryWhereDoesItGo" and intent_body["slots"]["item"]["value"] == "hammer"


def test_interaction_model_matches_the_forwarder(monkeypatch, fake_ha):
    url, _seen = fake_ha
    mod = _load(monkeypatch, url)
    path = Path(__file__).parents[2] / "deploy" / "alexa" / "interaction-model.json"
    model = json.loads(path.read_text(encoding="utf-8"))["interactionModel"]["languageModel"]
    ours = {i["name"] for i in model["intents"] if i["name"].startswith("HomeInventory")}
    assert ours == set(mod.FORWARDED) | {"HomeInventoryAnswer"}
    seen_samples: dict[str, str] = {}
    for i in model["intents"]:
        for sample in i.get("samples", []):
            key = sample.lower()
            assert key not in seen_samples, f"{sample!r} is in {i['name']} and {seen_samples[key]}"
            seen_samples[key] = i["name"]
    goes = next(i for i in model["intents"] if i["name"] == "HomeInventoryWhereDoesItGo")
    assert "where does the {item} go" in goes["samples"] and "to tell me where to put the {item}" in goes["samples"]


def test_move_and_yes_no_are_forwarded(monkeypatch, fake_ha):
    url, seen = fake_ha
    mod = _load(monkeypatch, url)
    ev = _event(intent="HomeInventoryMove")
    ev["request"]["intent"]["slots"] = {"item": {"name": "item", "value": "engine oil"}, "place": {"name": "place", "value": "garage"}}
    mod.lambda_handler(ev)
    sent = seen[-1][2]["request"]["intent"]
    assert sent["name"] == "HomeInventoryMove" and sent["slots"]["place"]["value"] == "garage"
    missing = _event(intent="HomeInventoryMove")
    missing["request"]["intent"]["slots"] = {"item": {"name": "item", "value": "engine oil"}, "place": {"name": "place"}}
    assert _text(mod.lambda_handler(missing)).startswith("What did you move, and where to?")
    # "Did you mean...?" answered yes: forwarded as the reply
    yes = _event(intent="AMAZON.YesIntent")
    yes["session"]["attributes"] = {mod.ASKING: True}
    mod.lambda_handler(yes)
    sent = seen[-1][2]["request"]["intent"]
    assert sent["name"] == "HomeInventoryChoose" and sent["slots"]["choice"]["value"] == "yes"
    before = len(seen)
    assert _text(mod.lambda_handler(_event(intent="AMAZON.NoIntent"))) == "Okay."  # nothing was asked
    assert len(seen) == before

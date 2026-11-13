"""The AI rate gate keeps calls inside the provider's limits."""

from __future__ import annotations

import pytest

from home_inventory_server.ai import AIBusyError, AIService, ratelimit
from home_inventory_server.ai.providers import AIProviderError
from tests.conftest import make_settings


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.slept = 0.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.slept += s


def test_requests_per_minute_queue_instead_of_going_over(tmp_path):
    c = Clock()
    g = ratelimit.AIRateGate(15, 0, 0, None, clock=c.time, sleep=c.sleep)
    for _ in range(15):
        g.acquire(100, max_wait=None)
    assert c.slept == 0
    g.acquire(100, max_wait=None)  # the 16th waits for the first to age out
    assert 59 <= c.slept <= 61


def test_tokens_per_minute(tmp_path):
    c = Clock()
    g = ratelimit.AIRateGate(0, 10_000, 0, None, clock=c.time, sleep=c.sleep)
    g.acquire(6_000, None)
    g.acquire(3_000, None)
    assert c.slept == 0
    g.acquire(3_000, None)
    assert c.slept >= 59


def test_daily_limit_survives_a_restart_and_waiting_callers_give_up(tmp_path):
    c = Clock()
    store = tmp_path / "ai_usage.json"
    g = ratelimit.AIRateGate(0, 0, 3, store, clock=c.time, sleep=c.sleep)
    for _ in range(3):
        g.acquire(10, None)
    again = ratelimit.AIRateGate(0, 0, 3, store, clock=c.time, sleep=c.sleep)  # App restarted
    with pytest.raises(ratelimit.AIBusy) as err:
        again.acquire(10, max_wait=30)
    assert "a day" in str(err.value) and err.value.retry_after > 3600
    assert again.status()["requests_last_24h"] == 3
    again.acquire(10, max_wait=None)  # background work waits for the day to roll on
    assert c.slept >= 24 * 3600 - 60


def test_rate_limited_provider_pauses_then_retries(tmp_path, monkeypatch):
    c = Clock()
    s = make_settings(tmp_path)
    s.ai_retry_cooldown = 60

    class Flaky:
        name, model = "fake", "m"
        calls = 0

        def generate_json(self, prompt, schema, image, timeout):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise AIProviderError("Gemini error 429", rate_limited=True)
            return {"ok": True}

    ai = AIService(s, provider=Flaky())
    ai.gate = ratelimit.AIRateGate(0, 0, 0, None, cooldown=60, clock=c.time, sleep=c.sleep)
    ratelimit.set_background(True)
    try:
        assert ai._call("hi", {"type": "object"}, None).data == {"ok": True}
    finally:
        ratelimit.set_background(False)
    assert Flaky.calls == 2 and c.slept >= 60  # waited out the cool-down


def test_interactive_call_gives_up_on_a_long_cooldown(tmp_path):
    c = Clock()
    s = make_settings(tmp_path)
    s.ai_max_wait_seconds = 30

    class Limited:
        name, model = "fake", "m"

        def generate_json(self, prompt, schema, image, timeout):
            raise AIProviderError("429 RESOURCE_EXHAUSTED", rate_limited=True)

    ai = AIService(s, provider=Limited())
    ai.gate = ratelimit.AIRateGate(0, 0, 0, None, cooldown=60, clock=c.time, sleep=c.sleep)
    with pytest.raises(AIBusyError) as err:
        ai._call("hi", {"type": "object"}, None)
    assert "Try again in about" in str(err.value)


def test_home_assistant_passes_on_gemini_quota_errors():
    from home_inventory_server.ai.providers import looks_rate_limited

    assert looks_rate_limited('{"message":"Error generating content: 429 RESOURCE_EXHAUSTED quota"}')
    assert not looks_rate_limited("Invalid API key")


def test_default_gemini_model_follows_google():
    from home_inventory_server.ai.providers import GeminiProvider

    assert GeminiProvider.DEFAULT_MODEL == "gemini-flash-lite-latest"

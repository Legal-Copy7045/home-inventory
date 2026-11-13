"""Keep AI calls inside the provider's limits.

Free tiers limit requests per minute, tokens per minute and requests per day
(Gemini's free tier, for example: 15 a minute, 250,000 tokens a minute and
500 a day).  Going over gets requests refused and, repeated, can get an
account flagged, so every AI call goes through one gate:

* a call waits for its turn instead of going over a limit;
* background work (reading manuals and documents, bulk value estimates)
  waits as long as it takes, even into the next day, so it completes over
  time;
* a call someone is waiting for gives up after ``ai_max_wait_seconds`` and
  says when to try again, rather than going over;
* when the provider says "too many requests", every call pauses for
  ``ai_retry_cooldown`` seconds before trying again.

The day's count is kept in ``/data/ai_usage.json`` so a restart doesn't
reset it.  It is a rolling 24 hours, which can never exceed a daily limit
however the provider counts its day.  A limit of 0 means no limit.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

DAY = 24 * 3600
MINUTE = 60.0

_local = threading.local()


def background() -> bool:
    """True while running a background job (see services/jobs.py)."""
    return bool(getattr(_local, "background", False))


def set_background(value: bool) -> None:
    _local.background = value


class AIBusy(RuntimeError):
    """The limits don't allow another AI call soon enough."""

    def __init__(self, message: str, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def estimate_tokens(prompt: str, schema: Any, image_size: tuple[int, int] | None) -> int:
    """A generous estimate of a request's tokens (text + image + answer).

    Text is about 4 characters a token.  Images are counted as Gemini does:
    258 tokens, or 258 per 768 px tile for larger images.  The answer is
    allowed 1,000 tokens.
    """
    text = len(prompt) + len(json.dumps(schema)) if schema is not None else len(prompt)
    tokens = math.ceil(text / 4) + 1000
    if image_size:
        w, h = image_size
        tokens += 258 if max(w, h) <= 384 else 258 * math.ceil(w / 768) * math.ceil(h / 768)
    return tokens


class AIRateGate:
    def __init__(self, per_minute: int, tokens_per_minute: int, per_day: int, store: Path | None,
                 cooldown: float = 60.0, clock=time.time, sleep=time.sleep) -> None:
        self.per_minute = max(0, int(per_minute))
        self.tokens_per_minute = max(0, int(tokens_per_minute))
        self.per_day = max(0, int(per_day))
        self.cooldown = max(1.0, float(cooldown))
        self.store = store
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._minute: deque[tuple[float, int]] = deque()  # (time, tokens)
        self._day: deque[float] = deque(self._load())
        self._paused_until = 0.0

    # ---------------------------------------------------------------- persistence

    def _load(self) -> list[float]:
        if self.store is None:
            return []
        try:
            data = json.loads(self.store.read_text())
            now = self._clock()
            return sorted(t for t in data.get("calls", []) if isinstance(t, (int, float)) and now - t < DAY)
        except (OSError, ValueError, AttributeError):
            return []

    def _save(self) -> None:
        if self.store is None:
            return
        try:
            tmp = self.store.with_suffix(".tmp")
            tmp.write_text(json.dumps({"calls": list(self._day)}))
            tmp.replace(self.store)
        except OSError as err:  # never let bookkeeping break a call
            _LOGGER.debug("Couldn't save AI usage: %s", err)

    # ---------------------------------------------------------------- the gate

    def _wait_needed(self, now: float, tokens: int) -> tuple[float, str]:
        while self._minute and now - self._minute[0][0] >= MINUTE:
            self._minute.popleft()
        while self._day and now - self._day[0] >= DAY:
            self._day.popleft()
        waits = [(max(0.0, self._paused_until - now), "the AI provider asked to slow down")]
        if self.per_minute and len(self._minute) >= self.per_minute:
            waits.append((self._minute[-self.per_minute][0] + MINUTE - now, f"the limit of {self.per_minute} AI requests a minute"))
        if self.tokens_per_minute:
            used = sum(t for _, t in self._minute)
            if used + tokens > self.tokens_per_minute and self._minute:
                # Wait until enough of the last minute's tokens have aged out.
                freed, at = 0, now
                for ts, t in self._minute:
                    freed += t
                    at = ts + MINUTE
                    if used - freed + tokens <= self.tokens_per_minute:
                        break
                waits.append((at - now, f"the limit of {self.tokens_per_minute:,} AI tokens a minute"))
        if self.per_day and len(self._day) >= self.per_day:
            waits.append((self._day[-self.per_day] + DAY - now, f"the limit of {self.per_day} AI requests a day"))
        return max(waits, key=lambda w: w[0])

    def acquire(self, tokens: int, max_wait: float | None) -> None:
        """Wait for a turn, then count this call.  ``max_wait=None`` waits as
        long as needed (background work); otherwise raises :class:`AIBusy`."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                wait, why = self._wait_needed(now, tokens)
                if wait <= 0:
                    self._minute.append((now, tokens))
                    self._day.append(now)
                    self._save()
                    return
            if max_wait is not None and waited + wait > max_wait:
                raise AIBusy(f"AI is paused for {why}. Try again in about {_human(wait)}.", wait)
            step = min(wait, 30.0)
            if waited == 0:
                _LOGGER.info("AI request waiting %s for %s", _human(wait), why)
            self._sleep(step)
            waited += step

    def pause(self, seconds: float | None = None) -> float:
        """The provider refused a request for its rate: hold every call."""
        seconds = self.cooldown if seconds is None else seconds
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + seconds)
        return seconds

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            self._wait_needed(now, 0)
            return {
                "requests_last_minute": len(self._minute),
                "tokens_last_minute": sum(t for _, t in self._minute),
                "requests_last_24h": len(self._day),
                "limits": {"per_minute": self.per_minute, "tokens_per_minute": self.tokens_per_minute,
                           "per_day": self.per_day},
                "paused_for": max(0, round(self._paused_until - now)),
            }


def _human(seconds: float) -> str:
    seconds = max(1, math.ceil(seconds))
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 5400:
        return f"{round(seconds / 60)} minutes"
    return f"{round(seconds / 3600, 1)} hours"

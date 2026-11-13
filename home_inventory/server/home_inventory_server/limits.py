"""Request size limits and per-user rate limits.

``BodySizeLimit`` is plain ASGI middleware so the limit applies *before*
Starlette's multipart parser spools an upload to disk: a declared
``Content-Length`` over the limit is refused immediately, and a streamed
body is cut off as soon as it passes the limit.

``RateLimiter`` keeps an in-memory sliding window per (user, bucket).  It
protects the operations that cost money or reach out to the internet (AI
calls, web-link import, online barcode lookups).  The App runs as a single
process, so in-memory state is sufficient.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException

MB = 1024 * 1024


class BodySizeLimit:
    def __init__(self, app, limit_for: Callable[[str], int]) -> None:
        self.app = app
        self.limit_for = limit_for

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        limit = self.limit_for(scope["path"])
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    return await _too_large(send, limit)
                break

        received = 0
        responded = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message):
            nonlocal responded
            if message["type"] == "http.response.start":
                responded = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not responded:
                await _too_large(send, limit)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send, limit: int) -> None:
    body = json.dumps({"detail": f"Upload is larger than the {limit // MB} MB limit"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class RateLimiter:
    """Sliding-window limits, e.g. ``{"ai": (20, 60)}`` = 20 calls per 60 s."""

    def __init__(self, limits: dict[str, tuple[int, float]]) -> None:
        self.limits = limits
        self._hits: dict[tuple[int, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, user_id: int, bucket: str) -> None:
        count, window = self.limits[bucket]
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault((user_id, bucket), deque())
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= count:
                retry = max(1, int(window - (now - hits[0])) + 1)
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests for this feature; please wait a moment and try again",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)


DEFAULT_RATE_LIMITS = {
    "ai": (20, 60.0),
    "weblink": (10, 60.0),
    "barcode": (30, 60.0),
    "voice_log": (60, 60.0),
}

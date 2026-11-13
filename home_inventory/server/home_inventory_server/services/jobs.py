"""Background AI jobs (reading manuals and documents).

They run on a small shared pool rather than a thread each, so a burst of
uploads can't start dozens of AI calls at once: extra jobs wait their turn.
AI calls made by a job wait for the rate limits as long as needed (see
ai/ratelimit.py).  A job still pending when its turn comes is run; one that's been marked
failed meanwhile (stale) is skipped by the job itself.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

_LOGGER = logging.getLogger(__name__)

MAX_WORKERS = 3

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def submit(fn: Callable[[], None], name: str) -> Future:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ai-job")
        pool = _pool

    def run() -> None:
        from ..ai import ratelimit

        # AI calls from background work wait for their turn as long as needed.
        ratelimit.set_background(True)
        try:
            fn()
        except Exception:  # noqa: BLE001 - the job records its own failures; this is a last resort
            _LOGGER.exception("Background job %s failed", name)

    return pool.submit(run)

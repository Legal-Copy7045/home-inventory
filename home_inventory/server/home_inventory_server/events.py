"""In-process event bus.

Domain operations queue events on the SQLAlchemy session; they are published
only after the transaction commits, so subscribers (the companion Home
Assistant integration via the SSE stream) never see events for changes that
were rolled back.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session, sessionmaker

_LOGGER = logging.getLogger(__name__)

# Event types published to Home Assistant (prefixed with "home_inventory_").
EVENT_TYPES = {
    "item_added",
    "item_removed",
    "item_moved",
    "container_moved",
    "container_inventory_updated",
    "item_loaned",
    "item_returned",
    "warranty_expiring",
    "item_expiring",
    "low_stock",
    "maintenance_completed",
    "maintenance_due",
    "repair_job_status",
    "tag_scanned",
    "shopping_list_updated",
    "checkup_due",
}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.recent: deque[dict[str, Any]] = deque(maxlen=100)
        self._seq = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        self._seq += 1
        message = {
            "id": self._seq,
            "type": event_type,
            "ts": datetime.now(UTC).isoformat(),
            "data": data,
        }
        self.recent.append(message)
        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._fanout(message)
        else:
            self._loop.call_soon_threadsafe(self._fanout, message)

    def _fanout(self, message: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                _LOGGER.warning("Dropping event for slow subscriber")


def _without_customer_names(db: Session, data: dict[str, Any]) -> dict[str, Any]:
    """Home Assistant's event bus is visible to every Home Assistant user, so
    events about customer equipment never carry its name."""
    ids = list(data.get("item_ids") or []) + ([data["item_id"]] if data.get("item_id") else [])
    if not ids or "name" not in data:
        return data
    from .models import ItemOwner

    db.flush()
    if any(db.get(ItemOwner, i) is not None for i in ids):
        return {**data, "name": "Customer equipment", "customer_equipment": True}
    return data


def queue_event(db: Session, event_type: str, data: dict[str, Any]) -> None:
    if event_type not in EVENT_TYPES:  # programming error, not user input
        raise ValueError(f"Unknown event type {event_type}")
    db.info.setdefault("pending_events", []).append((event_type, data))


def install_session_hooks(factory: sessionmaker, bus: EventBus) -> None:
    @sa_event.listens_for(factory, "before_commit")
    def _before_commit(session: Session) -> None:
        # Ownership is final now (customer equipment gets its owner after the
        # "item added" event was queued), so check names here.
        pending = session.info.get("pending_events")
        if pending:
            session.info["pending_events"] = [(t, _without_customer_names(session, d)) for t, d in pending]

    @sa_event.listens_for(factory, "after_commit")
    def _after_commit(session: Session) -> None:
        pending = session.info.pop("pending_events", [])
        for event_type, data in pending:
            try:
                bus.publish(event_type, json.loads(json.dumps(data, default=str)))
            except Exception:  # noqa: BLE001  pragma: no cover
                _LOGGER.exception("Failed to publish %s", event_type)

    @sa_event.listens_for(factory, "after_rollback")
    def _after_rollback(session: Session) -> None:
        session.info.pop("pending_events", None)

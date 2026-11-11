"""Quick capture: photograph many items in a row, add details later.

Each photo immediately becomes an item in the chosen container/location,
flagged as needing details (product identification status ``pending``), so
the container's contents are right straight away.  When AI is configured, a
background worker asks it what each photo shows and stores the answer as a
*suggestion* only; nothing is filled in until the user confirms it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..events import queue_event
from ..models import Attachment, IdentificationStatus, Item, Product
from .placement import Resolver, validate_item_target

_LOGGER = logging.getLogger(__name__)
PLACEHOLDER_NAME = "Item to identify"


def capture(db: Session, actor: Actor, photo: Attachment, container_id: int | None, location_id: int | None, ai_enabled: bool) -> Item:
    validate_item_target(db, container_id, location_id)
    product = Product(
        name=PLACEHOLDER_NAME,
        identification_status=IdentificationStatus.pending,
        ai_metadata={"capture": {"status": "queued" if ai_enabled else "none", "at": datetime.now(UTC).isoformat()}},
    )
    db.add(product)
    db.flush()
    item = Item(product_id=product.id, quantity=1, container_id=container_id, location_id=location_id)
    db.add(item)
    db.flush()
    photo.item_id = item.id
    item.primary_photo_id = photo.id
    where = Resolver.load(db).describe_item(item)["path"]
    audit.record(db, actor, "captured", "item", item.id, f"Quick photo added to {where} (details to follow)",
                 related_containers=[container_id])
    queue_event(db, "item_added", {"item_ids": [item.id], "product_id": product.id, "name": PLACEHOLDER_NAME,
                                   "quantity": 1, "location": where, "user": actor.name, "needs_details": True})
    if container_id:
        queue_event(db, "container_inventory_updated", {"container_id": container_id, "user": actor.name})
    return item


def pending_query():
    return (
        select(Item)
        .join(Product)
        .where(Item.deleted_at.is_(None), Product.deleted_at.is_(None),
               Product.identification_status == IdentificationStatus.pending)
        .order_by(Item.id)
    )


def suggestion(product: Product) -> dict[str, Any] | None:
    meta = product.ai_metadata or {}
    cap = meta.get("capture") or {}
    if cap.get("status") == "ready":
        return meta.get("suggestion")
    return None


# ---------------------------------------------------------------------------
# Background AI suggestions
# ---------------------------------------------------------------------------


def _suggest_one(state, item_id: int) -> None:
    db = state.db.SessionLocal()
    try:
        item = db.get(Item, item_id)
        if item is None or item.product.identification_status is not IdentificationStatus.pending:
            return
        photo = db.get(Attachment, item.primary_photo_id) if item.primary_photo_id else None
        meta = dict(item.product.ai_metadata or {})
        cap = dict(meta.get("capture") or {})
        if photo is None:
            cap["status"] = "none"
        else:
            try:
                res = state.ai.identify_item(state.media.read(photo.stored_path))
                meta["suggestion"] = res.data
                meta["provider"], meta["model"] = res.provider, res.model
                cap["status"] = "ready"
            except Exception as err:  # noqa: BLE001 - never lose the capture over AI
                _LOGGER.info("AI suggestion for item %s failed: %s", item_id, err)
                cap["status"] = "failed"
        meta["capture"] = cap
        item.product.ai_metadata = meta
        db.commit()
    finally:
        db.close()


async def worker(state) -> None:
    """Process queued captures one at a time (also picks up any left from
    before a restart)."""
    queue: asyncio.Queue[int] = state.capture_queue
    db = state.db.SessionLocal()
    try:
        for item in db.scalars(pending_query()):
            if ((item.product.ai_metadata or {}).get("capture") or {}).get("status") == "queued":
                queue.put_nowait(item.id)
    finally:
        db.close()
    while True:
        item_id = await queue.get()
        try:
            if state.ai.enabled:
                await asyncio.to_thread(_suggest_one, state, item_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Capture worker error")
        finally:
            queue.task_done()

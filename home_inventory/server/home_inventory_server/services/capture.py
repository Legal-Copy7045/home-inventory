"""Quick capture: photograph many items in a row, add details later.

Each photo immediately becomes an item flagged as needing details (product
identification status ``pending``).  Photos can go straight into a container
or location, or be taken with no place at all - e.g. photograph everything on
several shelves, grouped shelf by shelf, then say which shelf each group is
on afterwards (:func:`unsorted`, :func:`place`).  When AI is configured, a
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


def capture(
    db: Session,
    actor: Actor,
    photo: Attachment,
    container_id: int | None,
    location_id: int | None,
    ai_enabled: bool,
    batch: str | None = None,
    group: int | None = None,
) -> Item:
    validate_item_target(db, container_id, location_id)
    placed = container_id is not None or location_id is not None
    product = Product(
        name=PLACEHOLDER_NAME,
        identification_status=IdentificationStatus.pending,
        ai_metadata={"capture": {"status": "queued" if ai_enabled else "none", "at": datetime.now(UTC).isoformat()}},
    )
    db.add(product)
    db.flush()
    now = datetime.now(UTC).isoformat()
    item = Item(
        product_id=product.id, quantity=1, container_id=container_id, location_id=location_id,
        # Which photo session / shelf group it was taken in, for sorting later.
        ai_metadata={"capture": {"batch": batch, "group": group, "at": now}},
    )
    db.add(item)
    db.flush()
    photo.item_id = item.id
    item.primary_photo_id = photo.id
    where = Resolver.load(db).describe_item(item)["path"]
    summary = f"Quick photo added to {where} (details to follow)" if placed else "Quick photo taken (place and details to follow)"
    audit.record(db, actor, "captured", "item", item.id, summary, related_containers=[container_id])
    queue_event(db, "item_added", {"item_ids": [item.id], "product_id": product.id, "name": PLACEHOLDER_NAME,
                                   "quantity": 1, "location": where, "user": actor.name, "needs_details": True})
    if container_id:
        queue_event(db, "container_inventory_updated", {"container_id": container_id, "user": actor.name})
    return item


# ---------------------------------------------------------------------------
# Sorting photos taken without a place
# ---------------------------------------------------------------------------


def _capture_meta(item: Item) -> dict[str, Any] | None:
    meta = item.ai_metadata or {}
    cap = meta.get("capture") if isinstance(meta, dict) else None
    return cap if isinstance(cap, dict) else None


def unsorted_items(db: Session) -> list[Item]:
    """Quick-photo items that have no place yet."""
    items = db.scalars(
        select(Item).where(Item.deleted_at.is_(None), Item.container_id.is_(None), Item.location_id.is_(None)).order_by(Item.id)
    ).all()
    return [i for i in items if _capture_meta(i) is not None]


def unsorted_groups(items: list[Item]) -> list[dict[str, Any]]:
    """Group unsorted items by photo session and shelf group, newest session
    first and groups in the order they were taken."""
    groups: dict[tuple[str, int], dict[str, Any]] = {}
    for i in items:
        cap = _capture_meta(i) or {}
        batch = str(cap.get("batch") or f"item-{i.id}")
        g = cap.get("group")
        group = g if isinstance(g, int) else 1
        entry = groups.setdefault((batch, group), {"batch": batch, "group": group, "taken_at": cap.get("at"), "items": []})
        entry["items"].append(i)
    first_id = {k: min(x.id for x in v["items"]) for k, v in groups.items()}
    batch_first = {}
    for (b, _g), fid in first_id.items():
        batch_first[b] = min(fid, batch_first.get(b, fid))
    order = sorted(groups, key=lambda k: (-batch_first[k[0]], k[1]))
    return [groups[k] for k in order]


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

"""Stock reserved for repair jobs: available, reserved and used, kept apart.

* **Reserve**: set some of a holding aside for a job.  The quantity on the
  shelf doesn't change; the reserved part just stops counting as available
  (for maintenance readiness and other jobs).
* **Release**: give it back (nothing else changes).
* **Use**: the explicit step that takes it out of stock, recorded as a part
  used in the job's service history (costed at what it cost you).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import Item, ItemOwner, RepairJob, StockReservation


class ReservationError(ValueError):
    pass


def reserved(db: Session, item_id: int) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(StockReservation.quantity), 0)).where(
        StockReservation.item_id == item_id, StockReservation.released_at.is_(None),
        StockReservation.consumed_at.is_(None))) or 0)


def reserved_by_item(db: Session, item_ids: list[int]) -> dict[int, int]:
    if not item_ids:
        return {}
    return {iid: int(n) for iid, n in db.execute(
        select(StockReservation.item_id, func.sum(StockReservation.quantity))
        .where(StockReservation.item_id.in_(item_ids), StockReservation.released_at.is_(None),
               StockReservation.consumed_at.is_(None)).group_by(StockReservation.item_id))}


def available(db: Session, item: Item) -> int:
    return max(0, item.quantity - reserved(db, item.id))


def reserve(db: Session, actor: Actor, job: RepairJob, item: Item, quantity: int, note: str | None = None) -> StockReservation:
    if item.deleted_at is not None:
        raise ReservationError("That item has been deleted")
    if db.get(ItemOwner, item.id) is not None:
        raise ReservationError("Reserve household stock, not customer equipment")
    if job.status in ("returned", "cancelled"):
        raise ReservationError("This job is closed")
    free = available(db, item)
    if quantity > free:
        raise ReservationError(f"Only {free} of {item.product.name} available to reserve")
    r = StockReservation(item_id=item.id, quantity=quantity, repair_job_id=job.id, note=note, created_by_id=actor.user_id)
    db.add(r)
    db.flush()
    # The household item's own history: it doesn't name the customer.
    audit.record(db, actor, "stock_reserved", "item", item.id, f"{item.product.name}: {quantity} reserved for repair {job.number}")
    return r


def release(db: Session, actor: Actor, r: StockReservation) -> StockReservation:
    if r.released_at or r.consumed_at:
        raise ReservationError("This reservation is already closed")
    r.released_at = datetime.now(UTC)
    item = db.get(Item, r.item_id)
    audit.record(db, actor, "stock_released", "item", r.item_id, f"{item.product.name}: {r.quantity} no longer reserved")
    return r


def use(db: Session, actor: Actor, r: StockReservation, currency: str | None) -> StockReservation:
    """Take the reserved parts out of stock, as a part used on the job."""
    from . import maintenance as mnt

    if r.released_at or r.consumed_at:
        raise ReservationError("This reservation is already closed")
    job = db.get(RepairJob, r.repair_job_id) if r.repair_job_id else None
    equipment = db.get(Item, job.item_id) if job and job.item_id else None
    if equipment is None or equipment.deleted_at is not None:
        raise ReservationError("The job's equipment no longer exists")
    stock = db.get(Item, r.item_id)
    if stock is None or stock.deleted_at is not None:
        raise ReservationError("The reserved stock no longer exists")
    if stock.quantity < r.quantity:
        raise ReservationError(f"Only {stock.quantity} of {stock.product.name} left in stock")
    event = mnt.record_service(db, actor, equipment, {
        "title": f"Parts used: {stock.product.name}",
        "parts": [{"description": stock.product.name, "part_number": stock.product.model_number, "quantity": r.quantity,
                   "item_id": stock.id, "deduct_stock": True}],
    }, currency=currency)
    event.repair_job_id = job.id
    r.consumed_at = datetime.now(UTC)
    return r


def out(db: Session, r: StockReservation) -> dict[str, Any]:
    item = db.get(Item, r.item_id)
    job = db.get(RepairJob, r.repair_job_id) if r.repair_job_id else None
    return {"id": r.id, "item_id": r.item_id, "name": item.product.name if item else None, "quantity": r.quantity,
            "job_id": r.repair_job_id, "job_number": job.number if job else None, "note": r.note,
            "state": "used" if r.consumed_at else "released" if r.released_at else "reserved",
            "in_stock": item.quantity if item else 0, "available": available(db, item) if item else 0,
            "created_at": r.created_at.isoformat() if r.created_at else None}


def for_job(db: Session, job: RepairJob) -> list[dict[str, Any]]:
    return [out(db, r) for r in db.scalars(select(StockReservation).where(StockReservation.repair_job_id == job.id)
                                           .order_by(StockReservation.id))]

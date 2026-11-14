"""Customers, customer-owned equipment and repair jobs.

Ownership rules
---------------
* An item is household property unless an ``ItemOwner`` row says otherwise.
* Ownership is changed only by :func:`set_owner` (audited, explicit) or set
  at creation by :func:`add_customer_equipment`; ordinary item edits never
  touch it.  Splitting a holding copies its owner (see ``split_item``).
* Customer equipment is left out of household totals, value, warranty and
  expiry alerts and low-stock calculations (:func:`household_only`).
* Customer details are only shown to members and administrators; read-only
  users see "Customer equipment" without the name.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import (
    REPAIR_STATUSES,
    Attachment,
    Customer,
    CustomerContact,
    Item,
    ItemOwner,
    RepairJob,
    RepairJobEvent,
    ServiceEvent,
)

OPEN_STATUSES = ("received", "awaiting_diagnosis", "awaiting_approval", "waiting_for_parts", "in_progress")
STATUS_LABELS = {
    "received": "Received",
    "awaiting_diagnosis": "Awaiting diagnosis",
    "awaiting_approval": "Awaiting approval",
    "waiting_for_parts": "Waiting for parts",
    "in_progress": "In progress",
    "completed": "Completed – awaiting pickup",
    "returned": "Returned",
    "cancelled": "Cancelled",
}


class CustomerError(ValueError):
    """User-facing validation problem."""


def household_only():
    """SQL condition: the ``Item`` row is household property."""
    return ~exists().where(ItemOwner.item_id == Item.id)


def customer_owned():
    return exists().where(ItemOwner.item_id == Item.id)


def owner_of(db: Session, item_id: int) -> Customer | None:
    row = db.get(ItemOwner, item_id)
    return db.get(Customer, row.customer_id) if row else None


def owner_out(db: Session, item_id: int) -> dict[str, Any] | None:
    """Owner block for item responses; hides the name from read-only users."""
    row = db.get(ItemOwner, item_id)
    if row is None:
        return None
    if not db.info.get("can_view_customers", False):
        return {"kind": "customer", "customer_id": None, "name": None}
    c = db.get(Customer, row.customer_id)
    return {"kind": "customer", "customer_id": c.id, "name": c.name + (" (deleted)" if c.deleted_at else "")}


def audit_name(db: Session, item: Item | None) -> str:
    """Item name for history summaries recorded against *other* records.

    History entries are filtered by the record they belong to; when one
    mentions a second item that is customer equipment, it says so instead of
    naming it, so read-only users never see customer equipment names.
    """
    if item is None:
        return "an item"
    return "customer equipment" if db.get(ItemOwner, item.id) is not None else item.product.name


def copy_owner(db: Session, source: Item, clone: Item) -> None:
    row = db.get(ItemOwner, source.id)
    if row is not None:
        db.add(ItemOwner(item_id=clone.id, customer_id=row.customer_id, since=row.since))


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


def _get_customer(db: Session, customer_id: int) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None or c.deleted_at is not None:
        raise CustomerError("Customer not found")
    return c


def customer_out(c: Customer, db: Session | None = None, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "address": c.address,
        "notes": c.notes,
        "deleted": c.deleted_at is not None,
    }
    if db is not None:
        out["equipment_count"] = db.scalar(select(func.count()).select_from(ItemOwner).join(Item, Item.id == ItemOwner.item_id)
                                           .where(ItemOwner.customer_id == c.id, Item.deleted_at.is_(None))) or 0
        out["open_jobs"] = db.scalar(select(func.count()).select_from(RepairJob).where(
            RepairJob.customer_id == c.id, RepairJob.deleted_at.is_(None), RepairJob.status.in_(OPEN_STATUSES + ("completed",)))) or 0
    if detail and db is not None:
        items = db.scalars(select(Item).join(ItemOwner, ItemOwner.item_id == Item.id)
                           .where(ItemOwner.customer_id == c.id, Item.deleted_at.is_(None)).order_by(Item.id)).all()
        out["equipment"] = [{"id": i.id, "name": i.product.name, "manufacturer": i.product.manufacturer,
                             "model_number": i.product.model_number, "serial_number": i.serial_number} for i in items]
        jobs = db.scalars(select(RepairJob).where(RepairJob.customer_id == c.id, RepairJob.deleted_at.is_(None))
                          .order_by(RepairJob.received_on.desc(), RepairJob.id.desc())).all()
        out["jobs"] = [job_out(db, j) for j in jobs]
        contacts = db.scalars(select(CustomerContact).where(CustomerContact.customer_id == c.id)
                              .order_by(CustomerContact.contacted_at.desc()).limit(50)).all()
        out["contacts"] = [{"id": x.id, "contacted_at": x.contacted_at.isoformat(), "channel": x.channel, "note": x.note} for x in contacts]
    return out


def search_customers(db: Session, q: str | None) -> list[Customer]:
    stmt = select(Customer).where(Customer.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Customer.name.ilike(like), Customer.phone.ilike(like), Customer.email.ilike(like)))
    return list(db.scalars(stmt.order_by(Customer.name).limit(200)))


def create_customer(db: Session, actor: Actor, data: dict[str, Any]) -> Customer:
    c = Customer(**data)
    db.add(c)
    db.flush()
    audit.record(db, actor, "customer_added", "customer", c.id, f"Added customer {c.name}")
    return c


def update_customer(db: Session, actor: Actor, c: Customer, data: dict[str, Any]) -> Customer:
    for k, v in data.items():
        setattr(c, k, v)
    audit.record(db, actor, "customer_changed", "customer", c.id, f"Updated customer {c.name}")
    return c


def delete_customer(db: Session, actor: Actor, c: Customer) -> None:
    """Soft delete. Their equipment stays customer-owned (never becomes household)."""
    open_jobs = db.scalar(select(func.count()).select_from(RepairJob).where(
        RepairJob.customer_id == c.id, RepairJob.deleted_at.is_(None), RepairJob.status.in_(OPEN_STATUSES + ("completed",))))
    if open_jobs:
        raise CustomerError("This customer has open repair jobs; finish or cancel them first")
    c.deleted_at = datetime.now(UTC)
    audit.record(db, actor, "customer_deleted", "customer", c.id, f"Deleted customer {c.name}")


def add_contact(db: Session, actor: Actor, c: Customer, channel: str | None, note: str) -> CustomerContact:
    contact = CustomerContact(customer_id=c.id, channel=channel, note=note, created_by_id=actor.user_id)
    db.add(contact)
    return contact


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def add_customer_equipment(db: Session, actor: Actor, c: Customer, data: dict[str, Any]) -> Item:
    """Create an item that belongs to ``c`` from the start (one transaction)."""
    from ..schemas import ItemCreate
    from .inventory import create_items

    items = create_items(db, actor, ItemCreate(**data))
    item = items[0]
    db.add(ItemOwner(item_id=item.id, customer_id=c.id, since=date.today()))
    db.flush()
    audit.record(db, actor, "owner_set", "item", item.id, f"{item.product.name} belongs to customer {c.name}")
    return item


def set_owner(db: Session, actor: Actor, item: Item, customer_id: int | None) -> None:
    """Explicitly change ownership. ``None`` returns the item to the household."""
    row = db.get(ItemOwner, item.id)
    if customer_id is None:
        if row is None:
            return
        old = db.get(Customer, row.customer_id)
        db.delete(row)
        audit.record(db, actor, "owner_removed", "item", item.id, f"{item.product.name} now belongs to the household (was {old.name})")
        return
    c = _get_customer(db, customer_id)
    if row is None:
        db.add(ItemOwner(item_id=item.id, customer_id=c.id, since=date.today()))
    else:
        row.customer_id = c.id
        row.since = date.today()
    audit.record(db, actor, "owner_set", "item", item.id, f"{item.product.name} belongs to customer {c.name}")


# ---------------------------------------------------------------------------
# Repair jobs
# ---------------------------------------------------------------------------


def _next_number(db: Session) -> str:
    n = (db.scalar(select(func.count()).select_from(RepairJob)) or 0) + 1
    while db.scalar(select(RepairJob.id).where(RepairJob.number == f"RJ-{n:04d}")):
        n += 1
    return f"RJ-{n:04d}"


def _job_snapshot(c: Customer, item: Item | None) -> dict[str, Any]:
    snap: dict[str, Any] = {"customer_name": c.name, "customer_phone": c.phone, "customer_email": c.email}
    if item is not None:
        snap.update({"equipment": item.product.name, "manufacturer": item.product.manufacturer,
                     "model_number": item.product.model_number, "serial_number": item.serial_number})
    return snap


def open_job(db: Session, actor: Actor, data: dict[str, Any], currency: str) -> RepairJob:
    c = _get_customer(db, data.pop("customer_id"))
    new_equipment = data.pop("new_equipment", None)
    item_id = data.pop("item_id", None)
    if new_equipment:
        item = add_customer_equipment(db, actor, c, new_equipment)
    elif item_id:
        item = db.get(Item, item_id)
        if item is None or item.deleted_at is not None:
            raise CustomerError("Equipment not found")
        row = db.get(ItemOwner, item.id)
        if row is None:
            raise CustomerError("That item belongs to the household. Assign it to the customer first, or add it as new equipment.")
        if row.customer_id != c.id:
            raise CustomerError("That equipment belongs to a different customer")
    else:
        raise CustomerError("Choose the equipment, or add new equipment for this customer")
    job = RepairJob(number=_next_number(db), customer_id=c.id, item_id=item.id, status="received", currency=currency,
                    snapshot=_job_snapshot(c, item), created_by_id=actor.user_id, **data)
    if job.received_on is None:
        job.received_on = date.today()
    db.add(job)
    db.flush()
    db.add(RepairJobEvent(job_id=job.id, status_from=None, status_to="received", note="Job opened", user_name=actor.name))
    audit.record(db, actor, "repair_opened", "item", item.id, f"{job.number}: {item.product.name} received from {c.name}")
    from ..events import queue_event

    queue_event(db, "repair_job_status", {"job_id": job.id, "number": job.number, "status": "received", "user": actor.name})
    return job


def update_job(db: Session, actor: Actor, job: RepairJob, data: dict[str, Any]) -> RepairJob:
    for k, v in data.items():
        setattr(job, k, v)
    audit.record(db, actor, "repair_changed", "item", job.item_id or 0, f"{job.number}: details updated")
    return job


def change_status(db: Session, actor: Actor, job: RepairJob, status: str, note: str | None) -> RepairJob:
    if status not in REPAIR_STATUSES:
        raise CustomerError("Unknown status")
    if status == job.status:
        return job
    before = job.status
    job.status = status
    today = date.today()
    if status == "completed" and job.completed_on is None:
        job.completed_on = today
    if status == "returned":
        job.completed_on = job.completed_on or today
        job.returned_on = job.returned_on or today
    if status in OPEN_STATUSES:
        job.returned_on = None
        if status != "completed":
            job.completed_on = None
    db.add(RepairJobEvent(job_id=job.id, status_from=before, status_to=status, note=note, user_name=actor.name))
    audit.record(db, actor, "repair_status", "item", job.item_id or 0,
                 f"{job.number}: {STATUS_LABELS[before]} → {STATUS_LABELS[status]}" + (f" ({note})" if note else ""))
    from ..events import queue_event

    # No customer details in Home Assistant events: the integration fetches
    # the contact itself when it sends the customer a message.
    queue_event(db, "repair_job_status", {"job_id": job.id, "number": job.number, "status": status,
                                          "status_label": STATUS_LABELS[status], "previous": before, "user": actor.name})
    return job


def _money(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def job_out(db: Session, job: RepairJob, detail: bool = False) -> dict[str, Any]:
    events = db.scalars(select(ServiceEvent).where(ServiceEvent.repair_job_id == job.id, ServiceEvent.deleted_at.is_(None))
                        .order_by(ServiceEvent.performed_on, ServiceEvent.id)).all()
    parts_cost = sum((p.unit_cost * p.quantity for e in events for p in e.parts if p.unit_cost is not None), Decimal(0))
    service_labor = sum((e.labor_cost for e in events if e.labor_cost is not None), Decimal(0))
    parts = job.parts_charge if job.parts_charge is not None else parts_cost
    labor = job.labor_charge if job.labor_charge is not None else service_labor
    item = db.get(Item, job.item_id) if job.item_id else None
    snap = job.snapshot or {}
    out: dict[str, Any] = {
        "id": job.id,
        "number": job.number,
        "status": job.status,
        "status_label": STATUS_LABELS[job.status],
        "open": job.status in OPEN_STATUSES,
        "awaiting_pickup": job.status == "completed",
        "customer": {"id": job.customer_id, "name": (db.get(Customer, job.customer_id).name if db.get(Customer, job.customer_id) else snap.get("customer_name"))},
        "item": {"id": item.id if item and item.deleted_at is None else None,
                 "name": item.product.name if item else snap.get("equipment"),
                 "serial_number": item.serial_number if item else snap.get("serial_number")},
        "received_on": job.received_on.isoformat(),
        "completed_on": job.completed_on.isoformat() if job.completed_on else None,
        "returned_on": job.returned_on.isoformat() if job.returned_on else None,
        "reported_problem": job.reported_problem,
        "estimate": _money(job.estimate),
        "labor_charge": _money(labor),
        "parts_charge": _money(parts),
        "parts_charge_overridden": job.parts_charge is not None,
        "total": float(labor + parts),
        "currency": job.currency,
    }
    if detail:
        from .inventory import attachment_out
        from .maintenance import event_out

        atts = db.scalars(select(Attachment).where(Attachment.repair_job_id == job.id).order_by(Attachment.id)).all()
        history = db.scalars(select(RepairJobEvent).where(RepairJobEvent.job_id == job.id).order_by(RepairJobEvent.id)).all()
        c = db.get(Customer, job.customer_id)
        out.update({
            "customer": {**out["customer"], "phone": c.phone if c else snap.get("customer_phone"),
                         "email": c.email if c else snap.get("customer_email"), "address": c.address if c else None},
            "item": {**out["item"], "manufacturer": item.product.manufacturer if item else snap.get("manufacturer"),
                     "model_number": item.product.model_number if item else snap.get("model_number")},
            "condition_received": job.condition_received,
            "diagnosis": job.diagnosis,
            "approval_notes": job.approval_notes,
            "notes": job.notes,
            "labor_charge_set": _money(job.labor_charge),
            "parts_charge_set": _money(job.parts_charge),
            "service": [event_out(e) for e in events],
            "attachments": [attachment_out(a) for a in atts],
            "history": [{"ts": h.ts.isoformat(), "from": h.status_from, "to": h.status_to,
                         "to_label": STATUS_LABELS[h.status_to], "note": h.note, "user": h.user_name} for h in history],
            "snapshot": snap,
        })
    return out


def list_jobs(db: Session, status: str | None, customer_id: int | None, item_id: int | None) -> list[RepairJob]:
    stmt = select(RepairJob).where(RepairJob.deleted_at.is_(None))
    if status == "open":
        stmt = stmt.where(RepairJob.status.in_(OPEN_STATUSES))
    elif status == "pickup":
        stmt = stmt.where(RepairJob.status == "completed")
    elif status == "active":
        stmt = stmt.where(RepairJob.status.in_(OPEN_STATUSES + ("completed",)))
    elif status:
        stmt = stmt.where(RepairJob.status == status)
    if customer_id:
        stmt = stmt.where(RepairJob.customer_id == customer_id)
    if item_id:
        stmt = stmt.where(RepairJob.item_id == item_id)
    return list(db.scalars(stmt.order_by(RepairJob.received_on.desc(), RepairJob.id.desc()).limit(500)))


def counts(db: Session) -> dict[str, int]:
    base = select(func.count()).select_from(RepairJob).where(RepairJob.deleted_at.is_(None))
    return {
        "repairs_in_progress": db.scalar(base.where(RepairJob.status.in_(OPEN_STATUSES))) or 0,
        "repairs_awaiting_pickup": db.scalar(base.where(RepairJob.status == "completed")) or 0,
    }

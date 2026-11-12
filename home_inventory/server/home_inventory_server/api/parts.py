"""Parts API: part details on products, standards, links, task parts,
readiness, and reading equipment manuals."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import Attachment, Item, ItemOwner, MaintenanceTask, ManualReading, Product, ProductLink, Role
from ..schemas import _In
from ..services import manuals as man
from ..services import parts as parts_svc

router = APIRouter(prefix="/api/v1")

# --------------------------------------------------------------------------- inputs


class PartDetailsIn(_In):
    is_generic: bool | None = None
    specs: dict[str, str] | None = None
    content_quantity: Decimal | None = Field(default=None, gt=0, le=1_000_000)
    content_unit: str | None = Field(default=None, max_length=10)


class StandardIn(_In):
    name: str = Field(min_length=1, max_length=300)
    specs: dict[str, str] | None = None
    unit: str | None = Field(default=None, max_length=10)


class LinkIn(_In):
    target_id: int
    kind: Literal["meets", "replaced_by"]
    status: Literal["suggested", "confirmed", "rejected"] = "confirmed"
    note: str | None = Field(default=None, max_length=300)


class LinkPatch(_In):
    status: Literal["suggested", "confirmed", "rejected"]


class TaskPartIn(_In):
    product_id: int
    quantity: Decimal = Field(default=Decimal(1), gt=0, le=100000)
    unit: str | None = Field(default=None, max_length=10)
    allow_equivalent: bool = True
    note: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, max_length=80)


class TaskPartsIn(_In):
    parts: list[TaskPartIn] = Field(default_factory=list, max_length=50)


class FitmentPatch(_In):
    status: Literal["suggested", "confirmed", "rejected"]


class ManualReadIn(_In):
    attachment_ids: list[int] = Field(min_length=1, max_length=man.MAX_PHOTOS + 1)


class ManualPartChoice(_In):
    key: str = Field(max_length=40)
    include: bool = True
    product_id: int | None = None
    name: str | None = Field(default=None, max_length=300)
    role: str | None = Field(default=None, max_length=80)
    manufacturer: str | None = Field(default=None, max_length=200)
    part_number: str | None = Field(default=None, max_length=120)
    specification: str | None = Field(default=None, max_length=300)
    is_generic: bool = False
    specs: dict[str, str] | None = None
    quantity: Decimal | None = Field(default=None, gt=0, le=100000)
    unit: str | None = Field(default=None, max_length=10)
    allow_equivalent: bool = True
    page: int | None = Field(default=None, ge=0, le=5000)


class ManualNeed(_In):
    key: str = Field(max_length=40)
    quantity: Decimal | None = Field(default=None, gt=0, le=100000)
    unit: str | None = Field(default=None, max_length=10)


class ManualServiceChoice(_In):
    include: bool = True
    title: str = Field(min_length=1, max_length=200)
    task_id: int | None = None
    interval_hours: Decimal | None = Field(default=None, gt=0, le=100000)
    interval_days: int | None = Field(default=None, gt=0, le=36500)
    interval_cycles: int | None = Field(default=None, gt=0, le=10_000_000)
    first_at_hours: Decimal | None = Field(default=None, gt=0, le=100000)
    instructions: str | None = Field(default=None, max_length=5000)
    needs: list[ManualNeed] = Field(default_factory=list, max_length=60)
    page: int | None = Field(default=None, ge=0, le=5000)


class ManualSpecChoice(_In):
    include: bool = True
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(default="", max_length=200)


class ManualApplyIn(_In):
    parts: list[ManualPartChoice] = Field(default_factory=list, max_length=60)
    services: list[ManualServiceChoice] = Field(default_factory=list, max_length=40)
    specs: list[ManualSpecChoice] = Field(default_factory=list, max_length=40)


# --------------------------------------------------------------------------- helpers


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (parts_svc.PartsError, man.ManualError) as err:
        raise HTTPException(422, str(err)) from err


def _product(db: Session, product_id: int) -> Product:
    p = db.get(Product, product_id)
    if p is None or p.deleted_at is not None:
        raise HTTPException(404, "Product not found")
    return p


def _item(db: Session, actor: Actor, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, "Item not found")
    if not actor.can(Role.member) and db.get(ItemOwner, item.id) is not None:
        raise HTTPException(404, "Item not found")  # customer equipment is for members only
    return item


def _task(db: Session, actor: Actor, task_id: int) -> MaintenanceTask:
    task = db.get(MaintenanceTask, task_id)
    if task is None or task.deleted_at is not None:
        raise HTTPException(404, "Maintenance task not found")
    _item(db, actor, task.item_id)
    return task


# --------------------------------------------------------------------------- products and standards


@router.get("/products/{product_id}/parts")
def product_parts(product_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return parts_svc.details_out(db, _product(db, product_id))


@router.put("/products/{product_id}/parts")
def save_product_parts(product_id: int, data: PartDetailsIn, actor: Actor = Depends(require_member),
                       db: Session = Depends(get_db)):
    product = _product(db, product_id)
    _run(parts_svc.save_details, db, actor, product, data.model_dump(exclude_unset=True))
    db.commit()
    return parts_svc.details_out(db, product)


@router.get("/parts/standards")
def list_standards(q: str | None = Query(None, max_length=100), actor: Actor = Depends(current_actor),
                   db: Session = Depends(get_db)):
    return parts_svc.standards(db, q)


@router.post("/parts/standards", status_code=201)
def add_standard(data: StandardIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    p = _run(parts_svc.create_standard, db, actor, data.name, data.specs, data.unit)
    db.commit()
    return parts_svc.details_out(db, p)


@router.get("/parts/search")
def search_parts(q: str = Query(..., min_length=1, max_length=100), actor: Actor = Depends(current_actor),
                 db: Session = Depends(get_db)):
    """Products to pick as a task part: standards first, then by name or part number."""
    term = f"%{q.strip()}%"
    rows = db.scalars(select(Product).where(Product.deleted_at.is_(None),
                                            Product.name.ilike(term) | Product.model_number.ilike(term) | Product.sku.ilike(term))
                      .order_by(Product.is_generic.desc(), Product.name).limit(20))
    return [parts_svc._brief(p) for p in rows]


@router.post("/products/{product_id}/links", status_code=201)
def add_link(product_id: int, data: LinkIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    product, target = _product(db, product_id), _product(db, data.target_id)
    link = _run(parts_svc.set_link, db, actor, product, target, data.kind, data.status, note=data.note)
    db.commit()
    return parts_svc.link_out(link, db)


@router.patch("/product-links/{link_id}")
def change_link(link_id: int, data: LinkPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    link = db.get(ProductLink, link_id)
    if link is None:
        raise HTTPException(404, "Link not found")
    link = _run(parts_svc.set_link, db, actor, _product(db, link.product_id), _product(db, link.target_id), link.kind,
                data.status)
    db.commit()
    return parts_svc.link_out(link, db)


@router.delete("/product-links/{link_id}", status_code=204)
def remove_link(link_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    link = db.get(ProductLink, link_id)
    if link is None:
        raise HTTPException(404, "Link not found")
    db.delete(link)
    db.commit()


@router.patch("/part-fitments/{fitment_id}")
def change_fitment(fitment_id: int, data: FitmentPatch, actor: Actor = Depends(require_member),
                   db: Session = Depends(get_db)):
    from ..models import PartFitment

    f = db.get(PartFitment, fitment_id)
    if f is None:
        raise HTTPException(404, "Not found")
    f.status = data.status
    db.commit()
    return parts_svc.fitment_out(db, f)


# --------------------------------------------------------------------------- tasks and readiness


@router.get("/items/{item_id}/parts")
def item_parts(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return parts_svc.item_parts(db, _item(db, actor, item_id))


@router.put("/maintenance-tasks/{task_id}/parts")
def set_task_parts(task_id: int, data: TaskPartsIn, actor: Actor = Depends(require_member),
                   db: Session = Depends(get_db)):
    task = _task(db, actor, task_id)
    _run(parts_svc.set_task_parts, db, actor, task, [p.model_dump() for p in data.parts])
    db.commit()
    return parts_svc.task_readiness(db, task)


@router.get("/parts/readiness")
def parts_readiness(within: int = Query(30, ge=0, le=365), actor: Actor = Depends(current_actor),
                    db: Session = Depends(get_db)):
    from ..services import customers as cust

    # Household stock for household equipment; members also see customer jobs.
    item_filter = None if actor.can(Role.member) else cust.household_only()
    return parts_svc.readiness(db, within_days=within, item_filter=item_filter)


# --------------------------------------------------------------------------- manuals


def _reading(db: Session, actor: Actor, reading_id: int) -> ManualReading:
    r = db.get(ManualReading, reading_id)
    if r is None:
        raise HTTPException(404, "Manual reading not found")
    _item(db, actor, r.item_id)
    return r


@router.get("/items/{item_id}/manual-readings")
def list_readings(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    _item(db, actor, item_id)
    rows = db.scalars(select(ManualReading).where(ManualReading.item_id == item_id)
                      .order_by(ManualReading.id.desc()).limit(10))
    return [man.reading_out(r) for r in rows]


@router.post("/items/{item_id}/manual-readings", status_code=201)
async def read_manual(item_id: int, data: ManualReadIn, request: Request, actor: Actor = Depends(require_member),
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    st = request.app.state
    item = _item(db, actor, item_id)
    _run(man.equipment_item, db, item)
    if not st.ai.enabled:
        raise HTTPException(422, "AI is not configured. Choose a provider in the App configuration.")
    for att_id in data.attachment_ids:
        att = db.get(Attachment, att_id)
        if att is None or att.item_id != item.id:
            raise HTTPException(404, "Choose a manual or photo attached to this item")
    st.rate_limiter.check(actor.user_id, "ai")
    reading = ManualReading(item_id=item.id, attachment_ids=list(dict.fromkeys(data.attachment_ids)),
                            created_by_id=actor.user_id)
    db.add(reading)
    db.commit()
    await asyncio.to_thread(man.analyse, db, reading, st.ai, st.media, item)
    return man.reading_out(reading)


@router.get("/manual-readings/{reading_id}")
def get_reading(reading_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return man.reading_out(_reading(db, actor, reading_id))


@router.post("/manual-readings/{reading_id}/apply")
def apply_reading(reading_id: int, data: ManualApplyIn, actor: Actor = Depends(require_member),
                  db: Session = Depends(get_db)):
    reading = _reading(db, actor, reading_id)
    item = _item(db, actor, reading.item_id)
    summary = _run(man.apply, db, actor, item, reading, data.model_dump())
    db.commit()
    return {"reading": man.reading_out(reading), "summary": summary, "parts": parts_svc.item_parts(db, item)}


@router.post("/manual-readings/{reading_id}/discard")
def discard_reading(reading_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    reading = _reading(db, actor, reading_id)
    if reading.status == "applied":
        raise HTTPException(409, "This reading has already been applied")
    reading.status = "discarded"
    db.commit()
    return man.reading_out(reading)

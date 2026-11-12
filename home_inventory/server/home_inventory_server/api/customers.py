"""Customers, customer-owned equipment and repair jobs.

Every route here needs the member role, including reads: customer names and
contact details are not shown to read-only users.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor, get_db, require_member
from ..models import Customer, Item, ItemOwner, RepairJob
from ..schemas import (
    ContactIn,
    CustomerEquipmentIn,
    CustomerIn,
    CustomerPatch,
    OwnerIn,
    RepairJobFields,
    RepairJobIn,
    RepairStatusIn,
    ServiceIn,
)
from ..services import customers as cust
from ..services import inventory as inv
from ..services import maintenance as mnt
from ..services import stats as stats_svc
from ..services.placement import PlacementError, Resolver

router = APIRouter(prefix="/api/v1")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (cust.CustomerError, mnt.MaintenanceError) as err:
        raise HTTPException(422, str(err)) from err
    except inv.NotFound as err:
        raise HTTPException(404, str(err)) from err
    except (inv.Conflict, PlacementError) as err:
        raise HTTPException(409, str(err)) from err


def _customer(db: Session, customer_id: int) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None or c.deleted_at is not None:
        raise HTTPException(404, "Customer not found")
    return c


def _job(db: Session, job_id: int) -> RepairJob:
    j = db.get(RepairJob, job_id)
    if j is None or j.deleted_at is not None:
        raise HTTPException(404, "Repair job not found")
    return j


# ---------------------------------------------------------------- customers


@router.get("/customers")
def list_customers(q: str | None = Query(None, max_length=200), actor: Actor = Depends(require_member),
                   db: Session = Depends(get_db)):
    return [cust.customer_out(c, db) for c in cust.search_customers(db, q)]


@router.post("/customers", status_code=201)
def add_customer(data: CustomerIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = cust.create_customer(db, actor, data.model_dump())
    db.commit()
    return cust.customer_out(c, db, detail=True)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    return cust.customer_out(_customer(db, customer_id), db, detail=True)


@router.patch("/customers/{customer_id}")
def change_customer(customer_id: int, data: CustomerPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _customer(db, customer_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("name") is None:
        fields.pop("name", None)
    cust.update_customer(db, actor, c, fields)
    db.commit()
    return cust.customer_out(c, db, detail=True)


@router.delete("/customers/{customer_id}", status_code=204)
def remove_customer(customer_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    _run(cust.delete_customer, db, actor, _customer(db, customer_id))
    db.commit()


@router.post("/customers/{customer_id}/contacts", status_code=201)
def add_contact(customer_id: int, data: ContactIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _customer(db, customer_id)
    cust.add_contact(db, actor, c, data.channel, data.note)
    db.commit()
    return cust.customer_out(c, db, detail=True)


@router.post("/customers/{customer_id}/equipment", status_code=201)
def add_equipment(customer_id: int, data: CustomerEquipmentIn, actor: Actor = Depends(require_member),
                  db: Session = Depends(get_db)):
    c = _customer(db, customer_id)
    item = _run(cust.add_customer_equipment, db, actor, c, data.model_dump(exclude_none=True))
    db.commit()
    return inv.item_out(item, Resolver.load(db), db, detail=True)


@router.post("/items/{item_id}/owner")
def change_owner(item_id: int, data: OwnerIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """The only way to change who owns an item (audited; needs ``confirm``)."""
    if not data.confirm:
        raise HTTPException(422, "Confirm the change of owner")
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, "Item not found")
    if data.customer_id is None:
        open_jobs = [j for j in cust.list_jobs(db, "active", None, item.id)]
        if open_jobs:
            raise HTTPException(409, "This equipment has an open repair job; return or cancel it first")
    _run(cust.set_owner, db, actor, item, data.customer_id)
    db.commit()
    return inv.item_out(item, Resolver.load(db), db, detail=True)


# ---------------------------------------------------------------- repair jobs


@router.get("/repair-jobs")
def list_repair_jobs(status: str | None = Query(None, max_length=30), customer_id: int | None = None,
                     item_id: int | None = None, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    return [cust.job_out(db, j) for j in cust.list_jobs(db, status, customer_id, item_id)]


@router.post("/repair-jobs", status_code=201)
def open_repair_job(data: RepairJobIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    payload = data.model_dump(exclude_unset=True)
    if data.new_equipment is not None:
        payload["new_equipment"] = data.new_equipment.model_dump(exclude_none=True)
    job = _run(cust.open_job, db, actor, payload, stats_svc.currency(db))
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.get("/repair-jobs/{job_id}")
def get_repair_job(job_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    return cust.job_out(db, _job(db, job_id), detail=True)


@router.patch("/repair-jobs/{job_id}")
def change_repair_job(job_id: int, data: RepairJobFields, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    cust.update_job(db, actor, job, data.model_dump(exclude_unset=True))
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.post("/repair-jobs/{job_id}/status")
def set_repair_status(job_id: int, data: RepairStatusIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    _run(cust.change_status, db, actor, job, data.status, data.note)
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.post("/repair-jobs/{job_id}/service", status_code=201)
def add_job_service(job_id: int, data: ServiceIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Record work and parts for this job; it also appears in the equipment's service history."""
    job = _job(db, job_id)
    item = db.get(Item, job.item_id) if job.item_id else None
    if item is None or item.deleted_at is not None:
        raise HTTPException(409, "The equipment for this job no longer exists")
    payload = data.model_dump()
    payload["parts"] = [p.model_dump() for p in data.parts]
    if not payload.get("title"):
        raise HTTPException(422, "Describe the work (title)")
    event = _run(mnt.record_service, db, actor, item, payload, currency=job.currency or stats_svc.currency(db))
    event.repair_job_id = job.id
    if job.status in ("received", "awaiting_diagnosis", "awaiting_approval", "waiting_for_parts"):
        cust.change_status(db, actor, job, "in_progress", "Work recorded")
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.delete("/repair-jobs/{job_id}", status_code=204)
def remove_repair_job(job_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    job.deleted_at = datetime.now(UTC)
    audit.record(db, actor, "repair_deleted", "item", job.item_id or 0, f"{job.number}: deleted")
    db.commit()


@router.get("/items/{item_id}/repair-jobs")
def item_jobs(item_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    if db.get(ItemOwner, item_id) is None:
        return []
    return [cust.job_out(db, j) for j in cust.list_jobs(db, None, None, item_id)]

"""Customers, customer-owned equipment and repair jobs.

Every route here needs the member role, including reads: customer names and
contact details are not shown to read-only users.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor, get_db, require_customer_access
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
def list_customers(q: str | None = Query(None, max_length=200), actor: Actor = Depends(require_customer_access),
                   db: Session = Depends(get_db)):
    return [cust.customer_out(c, db) for c in cust.search_customers(db, q)]


@router.post("/customers", status_code=201)
def add_customer(data: CustomerIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    c = cust.create_customer(db, actor, data.model_dump())
    db.commit()
    return cust.customer_out(c, db, detail=True)


@router.get("/customers/{customer_id}")
def get_customer(customer_id: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    return cust.customer_out(_customer(db, customer_id), db, detail=True)


@router.patch("/customers/{customer_id}")
def change_customer(customer_id: int, data: CustomerPatch, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    c = _customer(db, customer_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("name") is None:
        fields.pop("name", None)
    cust.update_customer(db, actor, c, fields)
    db.commit()
    return cust.customer_out(c, db, detail=True)


@router.delete("/customers/{customer_id}", status_code=204)
def remove_customer(customer_id: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    _run(cust.delete_customer, db, actor, _customer(db, customer_id))
    db.commit()


@router.post("/customers/{customer_id}/contacts", status_code=201)
def add_contact(customer_id: int, data: ContactIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    c = _customer(db, customer_id)
    cust.add_contact(db, actor, c, data.channel, data.note)
    db.commit()
    return cust.customer_out(c, db, detail=True)


@router.post("/customers/{customer_id}/equipment", status_code=201)
def add_equipment(customer_id: int, data: CustomerEquipmentIn, actor: Actor = Depends(require_customer_access),
                  db: Session = Depends(get_db)):
    c = _customer(db, customer_id)
    item = _run(cust.add_customer_equipment, db, actor, c, data.model_dump(exclude_none=True))
    db.commit()
    return inv.item_out(item, Resolver.load(db), db, detail=True)


@router.post("/items/{item_id}/owner")
def change_owner(item_id: int, data: OwnerIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
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
                     item_id: int | None = None, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    return [cust.job_out(db, j) for j in cust.list_jobs(db, status, customer_id, item_id)]


@router.post("/repair-jobs", status_code=201)
def open_repair_job(data: RepairJobIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    payload = data.model_dump(exclude_unset=True)
    if data.new_equipment is not None:
        payload["new_equipment"] = data.new_equipment.model_dump(exclude_none=True)
    job = _run(cust.open_job, db, actor, payload, stats_svc.currency(db))
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.get("/repair-jobs/{job_id}")
def get_repair_job(job_id: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    return cust.job_out(db, _job(db, job_id), detail=True)


@router.patch("/repair-jobs/{job_id}")
def change_repair_job(job_id: int, data: RepairJobFields, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    cust.update_job(db, actor, job, data.model_dump(exclude_unset=True))
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.post("/repair-jobs/{job_id}/status")
def set_repair_status(job_id: int, data: RepairStatusIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    _run(cust.change_status, db, actor, job, data.status, data.note)
    db.commit()
    return cust.job_out(db, job, detail=True)


@router.post("/repair-jobs/{job_id}/service", status_code=201)
def add_job_service(job_id: int, data: ServiceIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
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
def remove_repair_job(job_id: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    job = _job(db, job_id)
    job.deleted_at = datetime.now(UTC)
    audit.record(db, actor, "repair_deleted", "item", job.item_id or 0, f"{job.number}: deleted")
    db.commit()


@router.get("/items/{item_id}/repair-jobs")
def item_jobs(item_id: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    if db.get(ItemOwner, item_id) is None:
        return []
    return [cust.job_out(db, j) for j in cust.list_jobs(db, None, None, item_id)]


@router.get("/repair-jobs/by-number/{number}")
def job_by_number(number: str, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    """For a job ticket's QR code: the job with this number."""
    job = db.scalar(select(RepairJob).where(func.upper(RepairJob.number) == number.strip().upper(), RepairJob.deleted_at.is_(None)))
    if job is None:
        raise HTTPException(404, "Repair job not found")
    return {"id": job.id, "number": job.number}


@router.get("/repair-jobs/{job_id}/ticket.pdf")
def job_ticket(job_id: int, request: Request, base: str | None = None, actor: Actor = Depends(require_customer_access),
               db: Session = Depends(get_db)):
    """A tag for the machine: job number, customer, problem and a QR code
    that opens the job."""
    import io
    from datetime import date as _date

    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    from ..services import labels as labels_svc
    from .routes import _qr_base, _state

    job = _job(db, job_id)
    out = cust.job_out(db, job, detail=True)
    root = labels_svc.clean_base_url(_qr_base(db, _state(request), base))
    payload = f"{root}/r/{job.number}" if root else f"HINV:R:{job.number}"
    w, h = 100 * mm, 62 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(f"Job ticket {job.number}")
    qr = 40 * mm
    c.drawImage(ImageReader(io.BytesIO(labels_svc.qr_png(payload, scale=8))), w - qr - 4 * mm, h - qr - 4 * mm, qr, qr)
    x, y = 5 * mm, h - 10 * mm
    c.setFont("Helvetica-Bold", 18)
    c.drawString(x, y, job.number)
    lines = [("Helvetica-Bold", 10, out["customer"]["name"] or ""),
             ("Helvetica", 9, out["customer"].get("phone") or ""),
             ("Helvetica", 9, out["item"]["name"] or ""),
             ("Helvetica", 8, f"In: {out['received_on']}")]
    y -= 7 * mm
    for font, size, text in lines:
        if text:
            c.setFont(font, size)
            c.drawString(x, y, str(text)[:34])
            y -= size + 3
    problem = (out.get("reported_problem") or "").replace("\n", " ")
    if problem:
        c.setFont("Helvetica-Oblique", 8)
        words, line, yy = problem.split(), "", 14 * mm
        for wd in words:
            if c.stringWidth(f"{line} {wd}".strip(), "Helvetica-Oblique", 8) > w - 10 * mm:
                c.drawString(x, yy, line)
                yy -= 10
                line = wd
                if yy < 4 * mm:
                    break
            else:
                line = f"{line} {wd}".strip()
        if yy >= 4 * mm and line:
            c.drawString(x, yy, line)
    c.setFont("Helvetica", 6)
    c.drawRightString(w - 4 * mm, 3 * mm, f"Printed {_date.today().isoformat()}")
    c.showPage()
    c.save()
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="ticket-{job.number}.pdf"'})


# ---------------------------------------------------------------------------
# Stock reserved for a job
# ---------------------------------------------------------------------------


class ReserveIn(BaseModel):
    item_id: int
    quantity: int = Field(ge=1, le=100000)
    note: str | None = Field(default=None, max_length=300)


def _res(fn, *args, **kwargs):
    from ..services import reservations as res

    try:
        return fn(*args, **kwargs)
    except res.ReservationError as err:
        raise HTTPException(422, str(err)) from err
    except mnt.MaintenanceError as err:
        raise HTTPException(422, str(err)) from err


def _reservation(db: Session, rid: int):
    from ..models import StockReservation

    r = db.get(StockReservation, rid)
    if r is None or r.repair_job_id is None:
        raise HTTPException(404, "Reservation not found")
    _job(db, r.repair_job_id)  # visible to this user?
    return r


@router.get("/repair-jobs/{job_id}/reservations")
def job_reservations(job_id: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    from ..services import reservations as res

    return {"reservations": res.for_job(db, _job(db, job_id))}


@router.post("/repair-jobs/{job_id}/reservations", status_code=201)
def reserve_for_job(job_id: int, data: ReserveIn, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    """Set stock aside for this job.  The quantity on the shelf doesn't change."""
    from ..services import reservations as res

    job = _job(db, job_id)
    item = db.get(Item, data.item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    _res(res.reserve, db, actor, job, item, data.quantity, data.note)
    db.commit()
    return {"reservations": res.for_job(db, job)}


@router.post("/reservations/{rid}/release")
def release_reservation(rid: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    from ..services import reservations as res

    r = _reservation(db, rid)
    _res(res.release, db, actor, r)
    db.commit()
    return res.out(db, r)


@router.post("/reservations/{rid}/use")
def use_reservation(rid: int, actor: Actor = Depends(require_customer_access), db: Session = Depends(get_db)):
    """Take the reserved parts out of stock, as parts used on the job."""
    from ..services import reservations as res

    r = _reservation(db, rid)
    job = _job(db, r.repair_job_id)
    _res(res.use, db, actor, r, job.currency or stats_svc.currency(db))
    db.commit()
    return res.out(db, r)

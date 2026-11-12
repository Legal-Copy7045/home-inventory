"""Equipment maintenance API: profiles, tasks, meter readings, service history."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import Attachment, EquipmentProfile, Item, MaintenanceTask, MeterKind, Role, ServiceEvent
from ..schemas import DeferIn, EquipmentIn, MeterReadingIn, NotApplicableIn, ServiceIn, SkipIn, TaskFields, TaskIn
from ..services import inventory as inv
from ..services import maintenance as mnt
from ..services import stats as stats_svc

router = APIRouter(prefix="/api/v1")


def _item(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, "Item not found")
    return item


def _task(db: Session, task_id: int) -> MaintenanceTask:
    task = db.get(MaintenanceTask, task_id)
    if task is None or task.deleted_at is not None:
        raise HTTPException(404, "Maintenance task not found")
    _item(db, task.item_id)
    return task


def _event(db: Session, event_id: int) -> ServiceEvent:
    ev = db.get(ServiceEvent, event_id)
    if ev is None or ev.deleted_at is not None:
        raise HTTPException(404, "Service entry not found")
    return ev


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except mnt.MaintenanceError as err:
        raise HTTPException(422, str(err)) from err


def _attachments_for(db: Session):
    def fetch(event_id: int):
        rows = db.scalars(select(Attachment).where(Attachment.service_event_id == event_id).order_by(Attachment.id)).all()
        return [inv.attachment_out(a) for a in rows]

    return fetch


def _summary(db: Session, item: Item) -> dict:
    return mnt.item_maintenance(db, item, _attachments_for(db))


@router.get("/maintenance/presets")
def presets(actor: Actor = Depends(current_actor)):
    return mnt.presets_out()


@router.get("/maintenance/dashboard")
def maintenance_dashboard(within: int = Query(30, ge=0, le=365),
                          owner: Literal["all", "household", "customer"] = "all",
                          actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from ..services import customers as cust

    if owner == "customer" and not actor.can(Role.member):
        raise HTTPException(403, "Customer equipment is visible to members only")
    item_filter = {"household": cust.household_only(), "customer": cust.customer_owned()}.get(owner)
    if item_filter is None and not actor.can(Role.member):
        item_filter = cust.household_only()
    out = mnt.dashboard(db, within_days=within, item_filter=item_filter)
    if actor.can(Role.member) and owner != "household":
        out["repairs_in_progress"] = [cust.job_out(db, j) for j in cust.list_jobs(db, "open", None, None)]
        out["awaiting_pickup"] = [cust.job_out(db, j) for j in cust.list_jobs(db, "pickup", None, None)]
    return out


@router.get("/items/{item_id}/maintenance")
def item_maintenance(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return _summary(db, _item(db, item_id))


@router.put("/items/{item_id}/equipment")
def save_equipment(item_id: int, data: EquipmentIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _item(db, item_id)
    fields = data.model_dump(exclude_unset=True, exclude={"preset", "current_hours", "current_cycles"})
    for kind, value in ((MeterKind.hours, data.current_hours), (MeterKind.cycles, data.current_cycles)):
        if value is not None:
            _run(mnt.add_reading, db, actor, item, kind, Decimal(value), source="manual", note="Reading when tracking started")
    if data.specs is not None:
        fields["specs"] = [spec.model_dump() for spec in data.specs]
    _run(mnt.save_profile, db, actor, item, fields, data.preset)
    db.commit()
    return _summary(db, item)


@router.delete("/items/{item_id}/equipment", status_code=204)
def delete_equipment(item_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Stop tracking: removes the profile and open tasks; service history stays."""
    from datetime import UTC, datetime

    from .. import audit

    item = _item(db, item_id)
    profile = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    if profile is None:
        raise HTTPException(404, "This item has no equipment details")
    db.delete(profile)
    for task in db.scalars(select(MaintenanceTask).where(MaintenanceTask.item_id == item.id, MaintenanceTask.deleted_at.is_(None))):
        task.deleted_at = datetime.now(UTC)
    audit.record(db, actor, "equipment_profile_removed", "item", item.id, f"{item.product.name}: stopped tracking maintenance")
    db.commit()


@router.post("/items/{item_id}/maintenance-tasks", status_code=201)
def add_task(item_id: int, data: TaskIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _item(db, item_id)
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    task = _run(mnt.create_task, db, actor, item, fields)
    db.commit()
    return mnt.task_out(task, mnt.meters_for(db, item.id))


@router.patch("/maintenance-tasks/{task_id}")
def change_task(task_id: int, data: TaskFields, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    task = _task(db, task_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("title") is None:
        fields.pop("title", None)
    _run(mnt.update_task, db, actor, task, fields)
    db.commit()
    return mnt.task_out(task, mnt.meters_for(db, task.item_id))


@router.delete("/maintenance-tasks/{task_id}", status_code=204)
def remove_task(task_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    mnt.delete_task(db, actor, _task(db, task_id))
    db.commit()


@router.post("/maintenance-tasks/{task_id}/complete", status_code=201)
def complete_task(task_id: int, data: ServiceIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    task = _task(db, task_id)
    payload = data.model_dump()
    payload["parts"] = [p.model_dump() for p in data.parts]
    _run(mnt.complete_task, db, actor, task, payload, stats_svc.currency(db))
    db.commit()
    return _summary(db, _item(db, task.item_id))


@router.post("/maintenance-tasks/{task_id}/skip")
def skip_task(task_id: int, data: SkipIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    task = _task(db, task_id)
    _run(mnt.skip_task, db, actor, task, data.note)
    db.commit()
    return _summary(db, _item(db, task.item_id))


@router.post("/maintenance-tasks/{task_id}/defer")
def defer_task(task_id: int, data: DeferIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    task = _task(db, task_id)
    _run(mnt.defer_task, db, actor, task, data.until, data.note)
    db.commit()
    return _summary(db, _item(db, task.item_id))


@router.post("/maintenance-tasks/{task_id}/not-applicable")
def not_applicable(task_id: int, data: NotApplicableIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    task = _task(db, task_id)
    _run(mnt.set_not_applicable, db, actor, task, data.value, data.note)
    db.commit()
    return _summary(db, _item(db, task.item_id))


@router.post("/items/{item_id}/meter-readings", status_code=201)
def add_reading(item_id: int, data: MeterReadingIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _item(db, item_id)
    _run(mnt.add_reading, db, actor, item, MeterKind(data.kind), Decimal(data.value),
         source="estimated" if data.estimated else "manual", note=data.note, correction=data.correction)
    db.commit()
    return _summary(db, item)


@router.post("/items/{item_id}/service-events", status_code=201)
def add_service(item_id: int, data: ServiceIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Record service that wasn't scheduled, including history from before the item was added."""
    item = _item(db, item_id)
    payload = data.model_dump()
    payload["parts"] = [p.model_dump() for p in data.parts]
    if not payload.get("title"):
        raise HTTPException(422, "Describe the service (title)")
    _run(mnt.record_service, db, actor, item, payload, currency=stats_svc.currency(db))
    db.commit()
    return _summary(db, item)


@router.delete("/service-events/{event_id}", status_code=204)
def remove_service(event_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    from datetime import UTC, datetime

    from .. import audit

    ev = _event(db, event_id)
    ev.deleted_at = datetime.now(UTC)
    name = (ev.snapshot or {}).get("item_name", "Item")
    audit.record(db, actor, "service_removed", "item", ev.item_id or 0, f"{name}: removed service entry “{ev.title}” ({ev.performed_on.isoformat()})")
    db.commit()


@router.get("/maintenance/today")
def today(actor: Actor = Depends(current_actor)):
    """Server date, so the UI agrees with the backend on what is overdue."""
    return {"date": date.today().isoformat()}


# ---------------------------------------------------------------------------
# Meters read from Home Assistant sensors
# ---------------------------------------------------------------------------


def _ha(request: Request):
    from ..ha_api import HAClient

    client = HAClient.maybe(request.app.state.settings)
    if client is None:
        raise HTTPException(503, "Home Assistant API access is not available to the App")
    return client


@router.get("/ha/meter-sensors")
def meter_sensors(request: Request, actor: Actor = Depends(require_member)):
    """Home Assistant entities that could report operating hours or cycles."""
    from ..ha_api import HAUnavailable
    from ..services import sensor_meters

    try:
        return sensor_meters.candidates(_ha(request))
    except HAUnavailable as err:
        raise HTTPException(502, str(err)) from err


@router.post("/items/{item_id}/meter-sync")
def meter_sync(item_id: int, request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Read the linked sensors now (also happens every 15 minutes)."""
    from ..services import sensor_meters

    item = _item(db, item_id)
    profile = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    if profile is None or not (profile.hours_entity_id or profile.cycles_entity_id):
        raise HTTPException(422, "No Home Assistant sensor is linked to this item")
    results = sensor_meters.sync_profile(db, _ha(request), profile)
    db.commit()
    return {"results": results}

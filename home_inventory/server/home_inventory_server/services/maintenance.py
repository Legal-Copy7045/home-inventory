"""Equipment maintenance: profiles, schedules, meter readings, service history.

Due state is computed, never stored:

* ``not_applicable`` - the task was marked as not applying to this item;
* ``deferred``       - postponed until ``deferred_until`` (still listed);
* ``overdue``        - any threshold (date, hours, cycles) has passed;
* ``due_soon``       - any threshold is within its "due soon" window;
* ``ok``             - thresholds exist and none is near;
* ``manual``         - no threshold at all (do it when needed).

Completing a task records a service event and moves the thresholds on from
the *completion* point (date + interval, hours at completion + interval).
Skipping moves them on by one interval from the current due point, so the
cadence is kept.  Service history never depends on the task still existing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import (
    Attachment,
    EquipmentProfile,
    Item,
    MaintenanceTask,
    MeterKind,
    MeterReading,
    ServiceEvent,
    ServiceOutcome,
    ServicePart,
)


class MaintenanceError(ValueError):
    """User-facing validation problem."""


# ---------------------------------------------------------------------------
# Presets: editable starting points, copied into tasks when chosen
# ---------------------------------------------------------------------------

_SMALL_ENGINE_SPECS = [
    {"label": "Engine oil", "kind": "spec", "value": ""},
    {"label": "Oil capacity", "kind": "spec", "value": ""},
    {"label": "Spark plug", "kind": "part", "value": "", "part_number": ""},
    {"label": "Air filter", "kind": "part", "value": "", "part_number": ""},
    {"label": "Fuel filter", "kind": "part", "value": "", "part_number": ""},
]


def _t(title: str, days: int | None = None, hours: float | None = None, cycles: int | None = None, note: str | None = None):
    return {"title": title, "interval_days": days, "interval_hours": hours, "interval_cycles": cycles, "instructions": note}


PRESETS: dict[str, dict[str, Any]] = {
    "lawn_mower": {
        "name": "Lawn mower",
        "meter": "hours",
        "fuel_type": "Gasoline",
        "specs": _SMALL_ENGINE_SPECS + [{"label": "Blade", "kind": "part", "value": "", "part_number": ""}],
        "tasks": [
            _t("Engine oil change", days=365, hours=50),
            _t("Air filter clean / inspect", hours=25),
            _t("Air filter replace", days=365, hours=100),
            _t("Spark plug replace", days=365, hours=100),
            _t("Blade sharpen / balance", hours=25),
            _t("Pre-season inspection", days=365),
            _t("Winterize (fuel stabilizer or run dry)", days=365),
        ],
    },
    "string_trimmer": {
        "name": "String trimmer",
        "meter": "hours",
        "fuel_type": "2-stroke mix",
        "specs": _SMALL_ENGINE_SPECS[2:] + [{"label": "Fuel mix ratio", "kind": "spec", "value": ""},
                                           {"label": "Trimmer line", "kind": "part", "value": "", "part_number": ""}],
        "tasks": [
            _t("Air filter inspect / clean", hours=25),
            _t("Spark plug replace", days=365, hours=100),
            _t("Fuel filter replace", days=365),
            _t("Winterize (run dry)", days=365),
        ],
    },
    "chainsaw": {
        "name": "Chainsaw",
        "meter": "cycles",
        "fuel_type": "2-stroke mix",
        "specs": _SMALL_ENGINE_SPECS[2:] + [{"label": "Chain", "kind": "part", "value": "", "part_number": ""},
                                           {"label": "Bar", "kind": "part", "value": "", "part_number": ""},
                                           {"label": "Bar & chain oil", "kind": "spec", "value": ""}],
        "tasks": [
            _t("Chain sharpening", cycles=5, note="One cycle = one tank of fuel or one working session."),
            _t("Bar dress / flip and clean", cycles=10),
            _t("Air filter clean", cycles=5),
            _t("Spark plug replace", days=365),
        ],
    },
    "leaf_blower": {
        "name": "Leaf blower",
        "meter": "hours",
        "fuel_type": "2-stroke mix",
        "specs": _SMALL_ENGINE_SPECS[2:],
        "tasks": [_t("Air filter clean", hours=25), _t("Spark plug replace", days=365, hours=100)],
    },
    "generator": {
        "name": "Generator",
        "meter": "hours",
        "fuel_type": "Gasoline",
        "specs": _SMALL_ENGINE_SPECS,
        "tasks": [
            _t("Exercise run under load", days=30, note="Run 15–30 minutes with a load connected."),
            _t("Engine oil change", days=365, hours=100),
            _t("Air filter inspect", hours=50),
            _t("Spark plug replace", days=365, hours=100),
            _t("Fuel stabilizer / fresh fuel", days=90),
        ],
    },
    "pressure_washer": {
        "name": "Pressure washer",
        "meter": "hours",
        "fuel_type": "Gasoline",
        "specs": _SMALL_ENGINE_SPECS + [{"label": "Pump oil", "kind": "spec", "value": ""}],
        "tasks": [
            _t("Engine oil change", days=365, hours=50),
            _t("Pump oil change", days=365, hours=50),
            _t("Winterize pump (pump saver)", days=365),
        ],
    },
    "snow_blower": {
        "name": "Snow blower",
        "meter": "hours",
        "fuel_type": "Gasoline",
        "specs": _SMALL_ENGINE_SPECS + [{"label": "Shear pins", "kind": "part", "value": "", "part_number": ""}],
        "tasks": [
            _t("Pre-season inspection", days=365),
            _t("Engine oil change", days=365, hours=50),
            _t("Spark plug replace", days=365, hours=100),
            _t("Check shear pins and auger", days=365),
        ],
    },
    "other": {"name": "Other equipment", "meter": "none", "specs": [], "tasks": []},
}


def presets_out() -> list[dict[str, Any]]:
    return [{"key": k, **v} for k, v in PRESETS.items()]


# ---------------------------------------------------------------------------
# Meters
# ---------------------------------------------------------------------------


def current_meter(db: Session, item_id: int, kind: MeterKind) -> Decimal | None:
    return db.scalar(
        select(MeterReading.value)
        .where(MeterReading.item_id == item_id, MeterReading.kind == kind)
        .order_by(MeterReading.recorded_at.desc(), MeterReading.id.desc())
        .limit(1)
    )


def add_reading(
    db: Session,
    actor: Actor,
    item: Item,
    kind: MeterKind,
    value: Decimal,
    source: str = "manual",
    note: str | None = None,
    recorded_at: datetime | None = None,
    correction: bool = False,
    service_event_id: int | None = None,
) -> MeterReading:
    if value < 0:
        raise MaintenanceError("A meter reading can't be negative")
    latest = current_meter(db, item.id, kind)
    if latest is not None and value < latest and not correction:
        raise MaintenanceError(
            f"The meter last read {latest}. Mark the new reading as a correction if the meter was reset or mis-read."
        )
    reading = MeterReading(
        item_id=item.id,
        kind=kind,
        value=value,
        source="correction" if correction else source,
        note=note,
        recorded_at=recorded_at or datetime.now(UTC),
        created_by_id=actor.user_id,
        service_event_id=service_event_id,
    )
    db.add(reading)
    db.flush()
    unit = "h" if kind is MeterKind.hours else " cycles"
    audit.record(db, actor, "meter_reading", "item", item.id, f"{item.product.name}: meter {value}{unit} ({reading.source})")
    return reading


def reading_out(r: MeterReading) -> dict[str, Any]:
    return {"id": r.id, "kind": r.kind.value, "value": float(r.value), "recorded_at": r.recorded_at.isoformat(),
            "source": r.source, "note": r.note, "service_event_id": r.service_event_id}


# ---------------------------------------------------------------------------
# Tasks and due state
# ---------------------------------------------------------------------------


@dataclass
class Meters:
    hours: Decimal | None
    cycles: Decimal | None


def meters_for(db: Session, item_id: int) -> Meters:
    return Meters(current_meter(db, item_id, MeterKind.hours), current_meter(db, item_id, MeterKind.cycles))


def task_state(task: MaintenanceTask, meters: Meters, today: date | None = None) -> dict[str, Any]:
    """Due state plus how far away each threshold is."""
    today = today or date.today()
    days_left = (task.next_due_on - today).days if task.next_due_on else None
    hours_left = (
        float(task.next_due_hours - meters.hours) if task.next_due_hours is not None and meters.hours is not None else None
    )
    if task.next_due_hours is not None and meters.hours is None:
        hours_left = float(task.next_due_hours)  # no reading yet: assume 0 h used
    cycles_left = (
        float(task.next_due_cycles - meters.cycles)
        if task.next_due_cycles is not None and meters.cycles is not None
        else (float(task.next_due_cycles) if task.next_due_cycles is not None else None)
    )
    if task.not_applicable:
        status = "not_applicable"
    elif task.deferred_until and task.deferred_until > today:
        status = "deferred"
    elif all(x is None for x in (days_left, hours_left, cycles_left)):
        status = "manual"
    elif (days_left is not None and days_left < 0) or (hours_left is not None and hours_left <= 0) or (
        cycles_left is not None and cycles_left <= 0
    ):
        status = "overdue"
    elif (
        (days_left is not None and days_left <= task.due_soon_days)
        or (hours_left is not None and hours_left <= float(task.due_soon_hours))
        or (cycles_left is not None and cycles_left <= task.due_soon_cycles)
    ):
        status = "due_soon"
    else:
        status = "ok"
    return {"status": status, "days_left": days_left, "hours_left": hours_left, "cycles_left": cycles_left}


def _num(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


def task_out(task: MaintenanceTask, meters: Meters, today: date | None = None) -> dict[str, Any]:
    return {
        "id": task.id,
        "item_id": task.item_id,
        "title": task.title,
        "instructions": task.instructions,
        "interval_days": task.interval_days,
        "interval_hours": _num(task.interval_hours),
        "interval_cycles": task.interval_cycles,
        "due_soon_days": task.due_soon_days,
        "due_soon_hours": _num(task.due_soon_hours),
        "due_soon_cycles": task.due_soon_cycles,
        "last_done_on": task.last_done_on.isoformat() if task.last_done_on else None,
        "last_done_hours": _num(task.last_done_hours),
        "last_done_cycles": task.last_done_cycles,
        "next_due_on": task.next_due_on.isoformat() if task.next_due_on else None,
        "next_due_hours": _num(task.next_due_hours),
        "next_due_cycles": task.next_due_cycles,
        "deferred_until": task.deferred_until.isoformat() if task.deferred_until else None,
        "not_applicable": task.not_applicable,
        **task_state(task, meters, today),
    }


def _schedule_from(task: MaintenanceTask, on: date, hours: Decimal | None, cycles: Decimal | None) -> None:
    """Set the next thresholds one interval after the given point."""
    # A task without intervals is one-off: once done it has no next due date.
    task.next_due_on = on + timedelta(days=task.interval_days) if task.interval_days else None
    task.next_due_hours = (hours or Decimal(0)) + task.interval_hours if task.interval_hours else None
    task.next_due_cycles = int((cycles or 0) + task.interval_cycles) if task.interval_cycles else None


def create_task(db: Session, actor: Actor, item: Item, data: dict[str, Any]) -> MaintenanceTask:
    task = MaintenanceTask(item_id=item.id, **{k: v for k, v in data.items() if k not in ("first_due_on",)})
    meters = meters_for(db, item.id)
    if task.last_done_on or task.last_done_hours is not None or task.last_done_cycles is not None:
        _schedule_from(task, task.last_done_on or date.today(), task.last_done_hours, task.last_done_cycles)
    else:
        _schedule_from(task, date.today(), meters.hours, meters.cycles)
    if data.get("first_due_on"):
        task.next_due_on = data["first_due_on"]
    if data.get("next_due_on") and not task.interval_days:
        task.next_due_on = data["next_due_on"]
    db.add(task)
    db.flush()
    audit.record(db, actor, "maintenance_task_added", "item", item.id, f"{item.product.name}: added maintenance task “{task.title}”")
    return task


def update_task(db: Session, actor: Actor, task: MaintenanceTask, data: dict[str, Any]) -> MaintenanceTask:
    intervals_changed = any(k in data and data[k] != getattr(task, k) for k in ("interval_days", "interval_hours", "interval_cycles"))
    for k, v in data.items():
        setattr(task, k, v)
    if intervals_changed and not any(k in data for k in ("next_due_on", "next_due_hours", "next_due_cycles")):
        meters = meters_for(db, task.item_id)
        _schedule_from(
            task,
            task.last_done_on or date.today(),
            task.last_done_hours if task.last_done_hours is not None else meters.hours,
            task.last_done_cycles if task.last_done_cycles is not None else meters.cycles,
        )
    db.flush()
    item = db.get(Item, task.item_id)
    audit.record(db, actor, "maintenance_task_changed", "item", task.item_id, f"{item.product.name}: changed maintenance task “{task.title}”")
    return task


def delete_task(db: Session, actor: Actor, task: MaintenanceTask) -> None:
    """Soft delete; service history keeps its own copy of the task title."""
    task.deleted_at = datetime.now(UTC)
    item = db.get(Item, task.item_id)
    audit.record(db, actor, "maintenance_task_removed", "item", task.item_id, f"{item.product.name}: removed maintenance task “{task.title}”")


# ---------------------------------------------------------------------------
# Service events
# ---------------------------------------------------------------------------


def _snapshot(item: Item) -> dict[str, Any]:
    p = item.product
    return {
        "item_name": p.name,
        "manufacturer": p.manufacturer,
        "model_number": p.model_number,
        "serial_number": item.serial_number,
        "label": item.label,
    }


def record_service(
    db: Session,
    actor: Actor,
    item: Item,
    data: dict[str, Any],
    task: MaintenanceTask | None = None,
    outcome: ServiceOutcome = ServiceOutcome.completed,
    currency: str | None = None,
) -> ServiceEvent:
    """Add a service-history entry (also used for backdated history)."""
    parts = data.pop("parts", None) or []
    performed_on: date = data.get("performed_on") or date.today()
    if performed_on > date.today():
        raise MaintenanceError("The service date can't be in the future")
    event = ServiceEvent(
        item_id=item.id,
        task_id=task.id if task else None,
        task_title=task.title if task else None,
        outcome=outcome,
        performed_on=performed_on,
        hours=data.get("hours"),
        cycles=data.get("cycles"),
        title=data.get("title") or (task.title if task else "Service"),
        work_performed=data.get("work_performed"),
        notes=data.get("notes"),
        performed_by=data.get("performed_by") or actor.name,
        labor_cost=data.get("labor_cost"),
        currency=currency if data.get("labor_cost") is not None or parts else None,
        snapshot=_snapshot(item),
        created_by_id=actor.user_id,
    )
    for part in parts:
        deduct = part.pop("deduct_stock", False)
        if part.get("item_id") is not None:
            stock = db.get(Item, part["item_id"])
            if stock is None or stock.deleted_at is not None:
                raise MaintenanceError("The inventory item for a part was not found")
            if deduct:
                qty = Decimal(str(part.get("quantity") or 1))
                if qty != qty.to_integral_value():
                    raise MaintenanceError("Only whole quantities can be taken from stock")
                from .inventory import Conflict, adjust_quantity

                try:
                    adjust_quantity(db, actor, stock, -int(qty), f"Used for service of {item.product.name}")
                except Conflict as err:
                    raise MaintenanceError(f"{stock.product.name}: {err}") from err
        event.parts.append(ServicePart(**part))
    db.add(event)
    db.flush()
    # A reading taken during service updates the meter (never backwards).
    for kind, value in ((MeterKind.hours, data.get("hours")), (MeterKind.cycles, data.get("cycles"))):
        if value is None:
            continue
        latest = current_meter(db, item.id, kind)
        if latest is None or Decimal(value) > latest:
            add_reading(db, actor, item, kind, Decimal(value), source="service", service_event_id=event.id,
                        recorded_at=datetime.combine(performed_on, datetime.now(UTC).time(), tzinfo=UTC))
    for att_id in data.get("attachment_ids") or []:
        att = db.get(Attachment, att_id)
        if att is None:
            raise MaintenanceError("Photo or document not found")
        att.service_event_id = event.id
        if att.item_id is None and att.container_id is None and att.location_id is None:
            att.item_id = item.id
    verb = {"completed": "Service", "skipped": "Skipped", "deferred": "Deferred", "not_applicable": "Not applicable"}[outcome.value]
    audit.record(db, actor, f"service_{outcome.value}", "item", item.id, f"{item.product.name}: {verb} – {event.title}")
    return event


def complete_task(db: Session, actor: Actor, task: MaintenanceTask, data: dict[str, Any], currency: str | None) -> ServiceEvent:
    item = db.get(Item, task.item_id)
    meters = meters_for(db, item.id)
    hours = Decimal(str(data["hours"])) if data.get("hours") is not None else meters.hours
    cycles = Decimal(str(data["cycles"])) if data.get("cycles") is not None else meters.cycles
    event = record_service(db, actor, item, data, task=task, currency=currency)
    if event.performed_on >= (task.last_done_on or date.min):
        task.last_done_on = event.performed_on
        task.last_done_hours = hours
        task.last_done_cycles = int(cycles) if cycles is not None else None
        _schedule_from(task, event.performed_on, hours, cycles)
    task.deferred_until = None
    task.not_applicable = False
    from ..events import queue_event

    queue_event(db, "maintenance_completed", {
        "item_id": item.id, "name": item.product.name, "task": task.title, "performed_on": event.performed_on.isoformat(),
        "hours": float(event.hours) if event.hours is not None else None,
        "next_due_on": task.next_due_on.isoformat() if task.next_due_on else None,
        "next_due_hours": float(task.next_due_hours) if task.next_due_hours is not None else None,
        "user": actor.name,
    })
    return event


def skip_task(db: Session, actor: Actor, task: MaintenanceTask, note: str | None) -> ServiceEvent:
    """Skip this occurrence: the schedule moves on one interval, keeping its cadence."""
    item = db.get(Item, task.item_id)
    event = record_service(db, actor, item, {"title": task.title, "notes": note}, task=task, outcome=ServiceOutcome.skipped)
    if task.interval_days and task.next_due_on:
        task.next_due_on += timedelta(days=task.interval_days)
    elif not (task.interval_hours or task.interval_cycles):
        task.next_due_on = None
    if task.interval_hours and task.next_due_hours is not None:
        task.next_due_hours += task.interval_hours
    if task.interval_cycles and task.next_due_cycles is not None:
        task.next_due_cycles += task.interval_cycles
    task.deferred_until = None
    return event


def defer_task(db: Session, actor: Actor, task: MaintenanceTask, until: date, note: str | None) -> ServiceEvent:
    if until <= date.today():
        raise MaintenanceError("Choose a date in the future")
    item = db.get(Item, task.item_id)
    task.deferred_until = until
    return record_service(
        db, actor, item, {"title": task.title, "notes": note or f"Deferred until {until.isoformat()}"},
        task=task, outcome=ServiceOutcome.deferred,
    )


def set_not_applicable(db: Session, actor: Actor, task: MaintenanceTask, value: bool, note: str | None) -> ServiceEvent | None:
    task.not_applicable = value
    if not value:
        return None
    item = db.get(Item, task.item_id)
    return record_service(db, actor, item, {"title": task.title, "notes": note}, task=task, outcome=ServiceOutcome.not_applicable)


def event_out(e: ServiceEvent, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    parts = [
        {"id": p.id, "description": p.description, "part_number": p.part_number, "quantity": float(p.quantity),
         "unit_cost": _num(p.unit_cost), "item_id": p.item_id}
        for p in e.parts
    ]
    parts_cost = sum((Decimal(str(p["unit_cost"])) * Decimal(str(p["quantity"])) for p in parts if p["unit_cost"] is not None), Decimal(0))
    return {
        "id": e.id,
        "item_id": e.item_id,
        "task_id": e.task_id,
        "task_title": e.task_title,
        "outcome": e.outcome.value,
        "performed_on": e.performed_on.isoformat(),
        "hours": _num(e.hours),
        "cycles": e.cycles,
        "title": e.title,
        "work_performed": e.work_performed,
        "notes": e.notes,
        "performed_by": e.performed_by,
        "labor_cost": _num(e.labor_cost),
        "parts_cost": float(parts_cost) if parts else None,
        "currency": e.currency,
        "parts": parts,
        "snapshot": e.snapshot,
        "attachments": attachments or [],
    }


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def profile_out(p: EquipmentProfile | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "id": p.id,
        "item_id": p.item_id,
        "equipment_type": p.equipment_type,
        "engine_manufacturer": p.engine_manufacturer,
        "engine_model": p.engine_model,
        "engine_serial": p.engine_serial,
        "fuel_type": p.fuel_type,
        "meter": p.meter,
        "specs": p.specs or [],
        "notes": p.notes,
    }


def save_profile(db: Session, actor: Actor, item: Item, data: dict[str, Any], preset: str | None) -> EquipmentProfile:
    profile = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    created = profile is None
    if created:
        profile = EquipmentProfile(item_id=item.id, meter="hours")
        db.add(profile)
    if preset:
        if preset not in PRESETS:
            raise MaintenanceError("Unknown equipment type")
        pre = PRESETS[preset]
        profile.equipment_type = profile.equipment_type or pre["name"]
        profile.meter = pre["meter"] if created else profile.meter
        profile.fuel_type = profile.fuel_type or pre.get("fuel_type")
        if not profile.specs:
            profile.specs = [dict(s) for s in pre["specs"]]
    for k, v in data.items():
        setattr(profile, k, v)
    db.flush()
    if preset:
        existing = {t.title.lower() for t in db.scalars(select(MaintenanceTask).where(
            MaintenanceTask.item_id == item.id, MaintenanceTask.deleted_at.is_(None)))}
        for spec in PRESETS[preset]["tasks"]:
            if spec["title"].lower() not in existing:
                data_t = {k: (Decimal(str(v)) if k == "interval_hours" and v is not None else v)
                          for k, v in spec.items()}
                create_task(db, actor, item, data_t)
    audit.record(db, actor, "equipment_profile_" + ("added" if created else "changed"), "item", item.id,
                 f"{item.product.name}: {'started tracking maintenance' if created else 'updated equipment details'}")
    return profile


def item_maintenance(db: Session, item: Item, attachments_for, today: date | None = None) -> dict[str, Any]:
    """Everything the item page shows about maintenance, in one call."""
    profile = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    meters = meters_for(db, item.id)
    tasks = db.scalars(select(MaintenanceTask).where(MaintenanceTask.item_id == item.id, MaintenanceTask.deleted_at.is_(None))
                       .order_by(MaintenanceTask.sort_order, MaintenanceTask.id)).all()
    events = db.scalars(select(ServiceEvent).where(ServiceEvent.item_id == item.id, ServiceEvent.deleted_at.is_(None))
                        .order_by(ServiceEvent.performed_on.desc(), ServiceEvent.id.desc())).all()
    readings = db.scalars(select(MeterReading).where(MeterReading.item_id == item.id)
                          .order_by(MeterReading.recorded_at.desc(), MeterReading.id.desc()).limit(20)).all()
    order = {"overdue": 0, "due_soon": 1, "deferred": 2, "ok": 3, "manual": 4, "not_applicable": 5}
    task_rows = sorted((task_out(t, meters, today) for t in tasks), key=lambda t: (order[t["status"]], t["days_left"] if t["days_left"] is not None else 99999))
    return {
        "profile": profile_out(profile),
        "meters": {"hours": _num(meters.hours), "cycles": _num(meters.cycles)},
        "readings": [reading_out(r) for r in readings],
        "tasks": task_rows,
        "history": [event_out(e, attachments_for(e.id)) for e in events],
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def dashboard(db: Session, within_days: int = 30, today: date | None = None, item_filter=None) -> dict[str, Any]:
    today = today or date.today()
    stmt = (select(MaintenanceTask, Item).join(Item, Item.id == MaintenanceTask.item_id)
            .where(MaintenanceTask.deleted_at.is_(None), Item.deleted_at.is_(None)))
    if item_filter is not None:
        stmt = stmt.where(item_filter)
    rows = db.execute(stmt).all()
    meter_cache: dict[int, Meters] = {}
    overdue, due = [], []
    for task, item in rows:
        meters = meter_cache.setdefault(item.id, meters_for(db, item.id))
        out = task_out(task, meters, today)
        out["item"] = {"id": item.id, "name": item.product.name, "label": item.label}
        if out["status"] == "overdue":
            overdue.append(out)
        elif out["status"] in ("due_soon", "ok", "deferred") and (
            out["status"] == "due_soon" or (out["days_left"] is not None and out["days_left"] <= within_days)
        ):
            due.append(out)
    overdue.sort(key=lambda t: t["days_left"] if t["days_left"] is not None else 0)
    due.sort(key=lambda t: t["days_left"] if t["days_left"] is not None else 0)

    prof_stmt = select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id).where(Item.deleted_at.is_(None))
    if item_filter is not None:
        prof_stmt = prof_stmt.where(item_filter)
    with_history = set(db.scalars(select(ServiceEvent.item_id).where(ServiceEvent.deleted_at.is_(None),
                                                                      ServiceEvent.outcome == ServiceOutcome.completed)))
    no_records = [{"id": item.id, "name": item.product.name, "equipment_type": p.equipment_type}
                  for p, item in db.execute(prof_stmt).all() if item.id not in with_history]

    recent_stmt = (select(ServiceEvent).where(ServiceEvent.deleted_at.is_(None))
                   .order_by(ServiceEvent.performed_on.desc(), ServiceEvent.id.desc()).limit(15))
    if item_filter is not None:
        recent_stmt = recent_stmt.join(Item, Item.id == ServiceEvent.item_id).where(item_filter)
    recent = []
    for e in db.scalars(recent_stmt):
        out = event_out(e)
        out["item"] = {"id": e.item_id, "name": (e.snapshot or {}).get("item_name")}
        recent.append(out)
    return {"within_days": within_days, "overdue": overdue, "due": due, "no_records": no_records, "recent": recent,
            "equipment_count": len(db.execute(prof_stmt).all())}


def counts(db: Session, today: date | None = None) -> dict[str, int]:
    d = dashboard(db, within_days=0, today=today)
    return {"maintenance_overdue": len(d["overdue"]), "maintenance_due_soon": sum(1 for t in d["due"] if t["status"] == "due_soon")}


def attachments_count(db: Session, event_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Attachment).where(Attachment.service_event_id == event_id)) or 0

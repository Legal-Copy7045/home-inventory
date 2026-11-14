"""Equipment: season mode, fuel & consumables, meter readings due, the
service history PDF and "is it worth fixing?"."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import Item, MaintenanceTask, ShoppingListItem
from ..services import equipment as eq
from ..services import maintenance as mnt
from .maintenance import maintenance_plan
from .routes import _get_or_404, _resolver

router = APIRouter(prefix="/api/v1")
Season = Literal["spring", "autumn"]


@router.get("/maintenance/season")
def season(season: Season | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Getting the equipment ready for spring, or put away for winter."""
    which = season or eq.which_season(hemisphere=eq.hemisphere(db))
    out = eq.season_jobs(maintenance_plan(owner="household", actor=actor, db=db), which)
    out["consumables"] = eq.consumables(db)
    return out


@router.post("/maintenance/season/shopping")
def season_shopping(season: Season | None = None, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Put every part the season's jobs still need on the shopping list."""
    from ..services import parts as parts_svc
    from ..services import replenishment as repl

    which = season or eq.which_season(hemisphere=eq.hemisphere(db))
    plan = eq.season_jobs(maintenance_plan(owner="household", actor=actor, db=db), which)
    open_ids = set(db.scalars(select(ShoppingListItem.product_id).where(ShoppingListItem.status.in_(repl.OPEN))))
    added = []
    for m in plan["machines"]:
        for t in m["jobs"]:
            task = db.get(MaintenanceTask, t["id"])
            for ln in parts_svc.task_readiness(db, task):
                buy = ln.get("buy")
                if ln["status"] in ("missing", "partial") and buy and buy["product_id"] not in open_ids:
                    try:
                        repl.add_to_list(db, actor, buy["product_id"], buy["quantity"], None,
                                         f"{which.title()}: {t['title']} on {m['item']['name']}"[:300])
                    except repl.ReplenishmentError:
                        continue
                    open_ids.add(buy["product_id"])
                    added.append(buy["name"])
    db.commit()
    return {"added": added}


@router.get("/maintenance/consumables")
def consumables(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return {"consumables": eq.consumables(db)}


@router.get("/maintenance/readings-due")
def readings_due(days: int = Query(30, ge=7, le=365), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Hour-meter machines without a sensor that need a meter photo."""
    return {"machines": eq.readings_due(db, days)}


@router.get("/items/{item_id}/service-history.pdf")
def service_history(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    summary = mnt.item_maintenance(db, item, lambda _id: [])
    pdf = eq.service_history_pdf(db, item, summary, _resolver(db).describe_item(item)["path"])
    name = "".join(c if c.isalnum() else "-" for c in item.product.name).strip("-")[:60] or "item"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="service-history-{name}-{date.today()}.pdf"'})


@router.get("/items/{item_id}/worth-fixing")
def worth_fixing(item_id: int, repair_cost: str, actor: Actor = Depends(current_actor),
                 db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    try:
        cost = Decimal(repair_cost.replace(",", "").strip())
    except (InvalidOperation, AttributeError) as err:
        raise HTTPException(422, "Enter the repair cost as a number") from err
    if cost < 0:
        raise HTTPException(422, "The repair cost can't be negative")
    from ..services import stats as stats_svc

    currency = item.currency or stats_svc.currency(db)
    return eq.worth_fixing(item, cost, currency)


# ---------------------------------------------------------------------------
# The Equipment section (managed equipment over the shared item model)
# ---------------------------------------------------------------------------


def _managed_item(db: Session, item_id: int):
    from ..models import EquipmentProfile

    item = _get_or_404(db, Item, item_id)
    prof = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    if prof is None or not prof.managed:
        raise HTTPException(404, "This item isn't managed as equipment")
    return item, prof


@router.get("/equipment")
def list_equipment(q: str | None = Query(None, max_length=200), equipment_type: str | None = Query(None, max_length=80),
                   lifecycle: str | None = Query(None, max_length=20),
                   attention: Literal["overdue", "due_soon", "ok"] | None = None,
                   actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from ..services import equipment_hub as hub

    rows = hub.equipment_list(db, q=q, equipment_type=equipment_type, lifecycle=lifecycle, attention=attention)
    types = sorted({r["equipment_type"] for r in hub.equipment_list(db) if r["equipment_type"]}) if not q else None
    return {"equipment": rows, "types": types, "lifecycle": hub.LIFECYCLE_LABELS}


@router.get("/equipment/{item_id}")
def equipment_overview(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Everything the equipment page shows at the top: identity, condition,
    what's due, the plan's source, parts, costs and recent history."""
    from ..services import equipment_hub as hub
    from ..services import inventory as inv

    item, prof = _managed_item(db, item_id)
    r = _resolver(db)
    tasks = hub._tasks(db, item.id)
    meters = mnt.meters_for(db, item.id)
    rates = mnt.rates_for(db, {item.id}, date.today()).get(item.id)
    touts = [mnt.task_out(t, meters, None, rates) for t in tasks]
    events = db.scalars(select(mnt.ServiceEvent).where(mnt.ServiceEvent.item_id == item.id,
                                                       mnt.ServiceEvent.deleted_at.is_(None))
                        .order_by(mnt.ServiceEvent.performed_on.desc(), mnt.ServiceEvent.id.desc()).limit(5)).all()
    today = date.today()
    w = item.warranty_expires
    return {
        "item": inv.item_out(item, r, db, detail=True),
        "profile": mnt.profile_out(prof),
        "lifecycle_label": hub.LIFECYCLE_LABELS.get(prof.lifecycle_status, prof.lifecycle_status),
        "meters": {"hours": float(meters.hours) if meters.hours is not None else None,
                   "cycles": float(meters.cycles) if meters.cycles is not None else None},
        "usage": mnt.usage(db, item.id) if prof.meter in ("hours", "both") else None,
        "summary": hub._summary(touts),
        "upcoming": sorted([t for t in touts if t["status"] in ("overdue", "due_soon")],
                           key=lambda t: (hub.URGENCY[t["status"]], hub._days(t)))[:8],
        "last_completed": mnt.event_out(events[0]) if events else None,
        "recent": [mnt.event_out(e) for e in events],
        "plan": hub.manual_status(db, item, tasks),
        "parts": hub.equipment_parts(db, item),
        "costs": hub.costs(db, item.id),
        "warranty": {"expires": w.isoformat(), "active": w >= today, "days_left": (w - today).days} if w else None,
        "entities": [e for e in (prof.hours_entity_id, prof.cycles_entity_id) if e],
    }


@router.get("/equipment/{item_id}/checklist")
def equipment_checklist(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from ..services import equipment_hub as hub

    item, _ = _managed_item(db, item_id)
    return hub.checklist(db, item)


@router.get("/equipment/{item_id}/checklist.pdf")
def equipment_checklist_pdf(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from ..services import equipment_hub as hub

    item, _ = _managed_item(db, item_id)
    pdf = hub.checklist_pdf(db, item)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="checklist-{item.id}.pdf"'})


@router.get("/maintenance/reports/{kind}")
def maintenance_report(kind: Literal["upcoming", "overdue", "history", "consolidated"],
                       days: int = Query(30, ge=1, le=3650), since: date | None = None,
                       actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from ..services import equipment_hub as hub

    return hub.report(db, kind, days=days, since=since)


@router.get("/voice/maintenance")
def voice_maintenance(kind: Literal["due", "parts", "have_parts", "last_done", "attention", "recent"] = "due",
                      range: Literal["overdue", "today", "week", "month", "upcoming", "all"] = "upcoming",  # noqa: A002
                      equipment: str | None = Query(None, max_length=200), equipment_id: int | None = None,
                      task: str | None = Query(None, max_length=200),
                      actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """A spoken maintenance answer for the Home Assistant voice intents.
    ``choose`` lists the equipment to ask about when the words are ambiguous.
    The same visibility as the app: household equipment only."""
    from ..services import voice_maintenance as vm

    return vm.answer(db, kind, range, equipment=equipment, equipment_id=equipment_id, task=task)

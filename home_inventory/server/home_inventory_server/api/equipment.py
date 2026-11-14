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

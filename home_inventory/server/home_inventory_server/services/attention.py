"""Needs attention: one list of what to take care of, most urgent first.

It only gathers what other parts of the App already know (maintenance,
parts, stock, taken and lent things, repairs, enrichment, check-ups,
warranties) into groups, each with a severity, a count, a few entries
linking to where to act, and a link to the full list.  Nothing here changes
data.  Customer repair jobs appear only for people who may see customers
(the session's privacy filter hides them otherwise).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    DocumentReading,
    IdentificationStatus,
    Item,
    ItemStatus,
    Loan,
    MaintenanceTask,
    ManualReading,
    Product,
    RepairJob,
)
from . import equipment_hub as hub
from . import parts as parts_svc
from . import stats as stats_svc
from . import takeaway
from .customers import OPEN_STATUSES, household_only
from .placement import Resolver

SHOWN = 6
ORDER = {"urgent": 0, "soon": 1, "info": 2}


def _group(key: str, title: str, severity: str, rows: list[dict[str, Any]], link: str, count: int | None = None,
           hint: str | None = None) -> dict[str, Any] | None:
    n = count if count is not None else len(rows)
    if not n:
        return None
    return {"key": key, "title": title, "severity": severity, "count": n, "items": rows[:SHOWN], "link": link,
            "hint": hint}


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def collect(db: Session, can_view_customers: bool, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    now = datetime.now(UTC)
    groups: list[dict[str, Any] | None] = []

    # Maintenance
    overdue = hub.report(db, "overdue", today=today)["tasks"]
    groups.append(_group("maintenance_overdue", "Overdue maintenance", "urgent", [
        {"title": f"{t['item']['name']}: {t['title']}", "detail": _due(t), "link": f"/equipment/{t['item']['id']}"}
        for t in overdue], "/maintenance/reports?kind=overdue"))
    soon = [t for t in hub.report(db, "upcoming", days=14, today=today)["tasks"]]
    groups.append(_group("maintenance_due", "Maintenance due in the next two weeks", "soon", [
        {"title": f"{t['item']['name']}: {t['title']}", "detail": _due(t), "link": f"/equipment/{t['item']['id']}"}
        for t in soon], "/maintenance/reports?kind=upcoming&days=30"))
    short = [ln for ln in parts_svc.readiness(db, within_days=30)["lines"] if ln["status"] in ("missing", "partial")]
    groups.append(_group("parts_short", "Parts needed for upcoming maintenance", "soon", [
        {"title": ln["product"]["name"],
         "detail": f"short {ln['short']:g}{(' ' + ln['unit']) if ln['unit'] else ''} for "
                   + ", ".join(sorted({t['item']['name'] for t in ln.get('tasks', [])})),
         "link": "/maintenance"} for ln in short], "/maintenance"))

    # Stock
    low = [r for r in stats_svc.low_stock_list(db) if r["min_quantity"] is not None and r["quantity"] < r["min_quantity"]]
    groups.append(_group("low_stock", "Running low", "soon", [
        {"title": r["name"], "detail": f"{r['quantity']} left (alert below {r['min_quantity']})",
         "link": f"/items/{r['item_id']}" if r.get("item_id") else "/shopping"} for r in low], "/shopping"))

    # Things out of place
    r = Resolver.load(db)
    taken = takeaway.taken_items(db)
    stale = [it for it in taken if _aware(it.updated_at) and now - _aware(it.updated_at) > timedelta(days=2)]
    groups.append(_group("taken", "Taken out and not put back", "soon" if stale else "info", [
        {"title": it.product.name, "detail": f"{takeaway.taker_name(db, it) or 'someone'} has it; it lives in "
                                             f"{r.describe(*takeaway.destination(it))['path']}",
         "link": f"/items/{it.id}"} for it in taken], "/put-away"))
    loans = db.scalars(select(Loan).join(Item, Item.id == Loan.item_id).where(
        Loan.returned_at.is_(None), Loan.expected_return.is_not(None), Loan.expected_return < today,
        Item.deleted_at.is_(None))).all()
    groups.append(_group("loans_overdue", "Lent out and overdue back", "urgent", [
        {"title": lo.item.product.name, "detail": f"{lo.borrower}, due back {lo.expected_return.isoformat()}",
         "link": f"/items/{lo.item_id}"} for lo in loans], "/loans"))
    moved = db.scalars(select(Item).where(Item.deleted_at.is_(None), Item.status == ItemStatus.temporarily_relocated,
                                          household_only())).all()
    groups.append(_group("temporary", "Moved just for now", "info", [
        {"title": it.product.name, "detail": f"usually in {r.describe(it.home_container_id, it.home_location_id)['path']}",
         "link": f"/items/{it.id}"} for it in moved], "/loans"))

    # Customer repairs
    if can_view_customers:
        jobs = db.scalars(select(RepairJob).where(RepairJob.deleted_at.is_(None),
                                                  RepairJob.status.in_((*OPEN_STATUSES, "completed")))
                          .order_by(RepairJob.created_at)).all()
        groups.append(_group("repairs", "Customer equipment in for repair", "soon", [
            {"title": f"{j.number}: {(db.get(Item, j.item_id).product.name if j.item_id and db.get(Item, j.item_id) else 'equipment')}",
             "detail": "ready for collection" if j.status == "completed" else j.status.replace("_", " "),
             "link": f"/repairs/{j.id}"} for j in jobs], "/repairs"))

    # Information to finish or check
    review = db.scalars(select(MaintenanceTask).where(MaintenanceTask.deleted_at.is_(None),
                                                      MaintenanceTask.review_status == "needs_review")).all()
    readings = db.scalars(select(ManualReading).where(ManualReading.status.in_(("ready", "failed")))
                          .order_by(ManualReading.created_at.desc())).all()
    docs = db.scalars(select(DocumentReading).where(DocumentReading.status == "ready")).all()
    rows = [{"title": f"{db.get(Item, t.item_id).product.name}: {t.title}", "detail": "the manual now says something different",
             "link": f"/equipment/{t.item_id}"} for t in review if db.get(Item, t.item_id)]
    rows += [{"title": db.get(Item, m.item_id).product.name if db.get(Item, m.item_id) else "Equipment",
              "detail": "manual read: review the plan" if m.status == "ready" else f"reading the manual failed: {m.error or 'try again'}",
              "link": f"/items/{m.item_id}"} for m in readings]
    rows += [{"title": db.get(Item, d.item_id).product.name if d.item_id and db.get(Item, d.item_id) else "A document",
              "detail": "a document was read: check what it found", "link": f"/items/{d.item_id}"} for d in docs if d.item_id]
    groups.append(_group("review", "Results to review", "soon", rows, "/equipment"))
    no_plan = [e for e in hub.equipment_list(db, today=today) if e["plan"] in ("generic", "no_manual", "manual_not_read")
               and e["lifecycle_status"] not in ("retired", "disposed")]
    groups.append(_group("equipment_plan", "Equipment without its manufacturer's schedule", "info", [
        {"title": e["name"], "detail": {"generic": "using a generic schedule", "no_manual": "no manual on file",
                                        "manual_not_read": "manual attached, not read yet"}[e["plan"]],
         "link": f"/equipment/{e['id']}"} for e in no_plan], "/equipment"))
    unfinished = db.scalar(select(func.count()).select_from(Item).join(Product, Product.id == Item.product_id).where(
        Item.deleted_at.is_(None), Product.identification_status == IdentificationStatus.pending)) or 0
    groups.append(_group("to_finish", "Items to finish", "info", [], "/todo", count=unfinished,
                         hint=f"{unfinished} item{'s' if unfinished != 1 else ''} still need{'s' if unfinished == 1 else ''} details"))
    from . import checkups

    places = checkups.due(db, limit=SHOWN)
    groups.append(_group("checkups", "Places to check", "info", [
        {"title": p["path"] or p["name"], "detail": "never checked" if not p.get("last_checked") else
         f"last checked {p['last_checked'][:10]}", "link": f"/verify?{p['type']}={p['id']}"} for p in places], "/verify"))
    soon_w = today + timedelta(days=30)
    warr = db.scalars(select(Item).where(Item.deleted_at.is_(None), Item.warranty_expires.is_not(None),
                                         Item.warranty_expires >= today, Item.warranty_expires <= soon_w, household_only())
                      .order_by(Item.warranty_expires)).all()
    groups.append(_group("warranties", "Warranties ending within a month", "info", [
        {"title": it.product.name, "detail": f"ends {it.warranty_expires.isoformat()}", "link": f"/items/{it.id}"}
        for it in warr], "/loans"))
    expiring = db.scalars(select(Item).where(Item.deleted_at.is_(None), Item.quantity > 0, Item.expires_on.is_not(None),
                                             Item.expires_on <= today + timedelta(days=14), household_only())
                          .order_by(Item.expires_on)).all()
    groups.append(_group("expiring", "Use-by dates", "soon", [
        {"title": it.product.name, "detail": ("expired " if it.expires_on < today else "expires ") + it.expires_on.isoformat(),
         "link": f"/items/{it.id}"} for it in expiring], "/loans"))
    from . import valuation

    vj = valuation.job(db)
    if vj and vj.get("status") == "failed":
        groups.append(_group("jobs", "Background work that stopped", "info", [
            {"title": "Value estimates", "detail": vj.get("error") or "stopped", "link": "/reports"}], "/reports"))

    out = [g for g in groups if g]
    out.sort(key=lambda g: ORDER[g["severity"]])
    return {"groups": out, "total": sum(g["count"] for g in out),
            "urgent": sum(g["count"] for g in out if g["severity"] == "urgent"),
            "soon": sum(g["count"] for g in out if g["severity"] == "soon")}


def _due(t: dict[str, Any]) -> str:
    if t["status"] == "overdue":
        bits = []
        if t.get("days_left") is not None and t["days_left"] < 0:
            bits.append(f"{-t['days_left']} days")
        if t.get("hours_left") is not None and t["hours_left"] <= 0:
            bits.append(f"{abs(t['hours_left']):g} h")
        return "overdue" + (f" by {' / '.join(bits)}" if bits else "")
    if t.get("days_left") is not None:
        return "due today" if t["days_left"] == 0 else f"due in {t['days_left']} days"
    if t.get("hours_left") is not None:
        return f"due in {t['hours_left']:g} h"
    return "due soon"

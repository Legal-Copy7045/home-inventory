"""Managed equipment: the Equipment section's list, overview, checklist,
parts status and maintenance reports.

Equipment is an ordinary item with a *managed* EquipmentProfile: the same
item, product, stock, search, barcode and voice records as everything else.
This module only gathers what the equipment pages show; schedules,
completion and parts logic stay in ``maintenance`` and ``parts``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Attachment,
    AttachmentKind,
    EquipmentProfile,
    Item,
    MaintenanceTask,
    ManualReading,
    ServiceEvent,
    ServiceOutcome,
)
from . import maintenance as mnt
from . import parts as parts_svc
from .customers import household_only
from .placement import Resolver
from .textmatch import matches_words

GROUP_LABELS = {
    "before_use": "Before each use",
    "after_use": "After each use",
    "hours": "By operating hours / uses",
    "periodic": "Periodic",
    "seasonal": "Seasonal",
    "annual": "Annual",
    "storage": "Storage and winterising",
    "one_time": "One-time",
}
GROUP_ORDER = list(GROUP_LABELS)
LIFECYCLE_LABELS = {"active": "Active", "storage": "In storage", "under_repair": "Under repair", "loaned": "Loaned out",
                    "retired": "Retired", "disposed": "Disposed of"}
URGENCY = {"overdue": 0, "due_soon": 1, "deferred": 2, "ok": 3, "manual": 4, "not_applicable": 5}


def _managed(db: Session):
    return (select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id)
            .where(EquipmentProfile.managed.is_(True), Item.deleted_at.is_(None), household_only()))


def _tasks(db: Session, item_id: int) -> list[MaintenanceTask]:
    return list(db.scalars(select(MaintenanceTask).where(MaintenanceTask.item_id == item_id,
                                                         MaintenanceTask.deleted_at.is_(None))
                           .order_by(MaintenanceTask.sort_order, MaintenanceTask.id)))


def manual_status(db: Session, item: Item, tasks: list[MaintenanceTask]) -> dict[str, Any]:
    """Whether the maintenance plan comes from this model's own manual."""
    manuals = db.scalars(select(Attachment).where(Attachment.item_id == item.id, Attachment.kind == AttachmentKind.manual)
                         .order_by(Attachment.id)).all()
    applied = db.scalars(select(ManualReading).where(ManualReading.item_id == item.id, ManualReading.status == "applied")
                         .order_by(ManualReading.applied_at.desc())).first()
    sources = {t.source for t in tasks if not t.not_applicable}
    if applied is not None and "manual" in sources:
        state = "from_manual"
        check = (applied.result or {}).get("model_check") or {}
        if check.get("confirm"):
            state = "from_manual_confirmed"  # a person confirmed an uncertain model match
    elif "generic" in sources:
        state = "generic"
    elif manuals:
        state = "manual_not_read"
    else:
        state = "no_manual"
    return {"state": state, "manuals": [{"id": a.id, "name": a.original_name, "url": f"api/v1/attachments/{a.id}/file"}
                                        for a in manuals],
            "applied_reading_id": applied.id if applied else None,
            "needs_review": sum(1 for t in tasks if t.review_status == "needs_review" and not t.not_applicable)}


def _summary(tasks_out: list[dict[str, Any]]) -> dict[str, Any]:
    active = [t for t in tasks_out if t["status"] != "not_applicable"]
    nxt = sorted((t for t in active if t["status"] in ("overdue", "due_soon", "ok", "deferred")),
                 key=lambda t: (URGENCY[t["status"]], _days(t)))
    return {"overdue": sum(1 for t in active if t["status"] == "overdue"),
            "due_soon": sum(1 for t in active if t["status"] == "due_soon"),
            "tasks": len(active),
            "next": ({"id": nxt[0]["id"], "title": nxt[0]["title"], "status": nxt[0]["status"],
                      "next_due_on": nxt[0]["next_due_on"], "hours_left": nxt[0]["hours_left"],
                      "days_left": nxt[0]["days_left"]} if nxt else None),
            "attention": "overdue" if any(t["status"] == "overdue" for t in active)
            else "due_soon" if any(t["status"] == "due_soon" for t in active) else "ok"}


def _days(t: dict[str, Any]) -> int:
    days = [t["days_left"]] if t.get("days_left") is not None else []
    if t.get("estimated_due_on"):
        days.append((date.fromisoformat(t["estimated_due_on"]) - date.today()).days)
    return min(days) if days else 99999


def equipment_list(db: Session, q: str | None = None, equipment_type: str | None = None,
                   lifecycle: str | None = None, attention: str | None = None,
                   today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    r = Resolver.load(db)
    rows = db.execute(_managed(db)).all()
    rates = mnt.rates_for(db, {i.id for _, i in rows}, today)
    last = defaultdict(lambda: None)
    for e in db.scalars(select(ServiceEvent).where(ServiceEvent.deleted_at.is_(None),
                                                   ServiceEvent.outcome == ServiceOutcome.completed)
                        .order_by(ServiceEvent.performed_on)):
        last[e.item_id] = e
    out = []
    for prof, item in rows:
        p = item.product
        text = " ".join(x for x in (p.name, p.manufacturer, p.model_number, item.label, item.serial_number,
                                    prof.equipment_type, p.tags) if x)
        if q and not matches_words(q, text):
            continue
        if equipment_type and (prof.equipment_type or "").lower() != equipment_type.lower():
            continue
        if lifecycle and prof.lifecycle_status != lifecycle:
            continue
        meters = mnt.meters_for(db, item.id)
        tasks = _tasks(db, item.id)
        touts = [mnt.task_out(t, meters, today, rates.get(item.id)) for t in tasks]
        summ = _summary(touts)
        if attention and summ["attention"] != attention:
            continue
        e = last[item.id]
        out.append({
            "id": item.id, "name": p.name, "label": item.label, "manufacturer": p.manufacturer,
            "model_number": p.model_number, "serial_number": item.serial_number,
            "equipment_type": prof.equipment_type, "lifecycle_status": prof.lifecycle_status,
            "lifecycle_label": LIFECYCLE_LABELS.get(prof.lifecycle_status, prof.lifecycle_status),
            "hours": float(meters.hours) if meters.hours is not None else None,
            "placement": r.describe_item(item)["path"],
            "photo_url": f"api/v1/attachments/{item.primary_photo_id}/thumb" if item.primary_photo_id else None,
            "last_service": {"on": e.performed_on.isoformat(), "title": e.title} if e else None,
            "plan": manual_status(db, item, tasks)["state"],
            **summ,
        })
    order = {"overdue": 0, "due_soon": 1, "ok": 2}
    out.sort(key=lambda x: (order[x["attention"]], x["name"].lower()))
    return out


def checklist(db: Session, item: Item, today: date | None = None) -> dict[str, Any]:
    """The equipment's tasks grouped as a service checklist, with where
    each one comes from."""
    meters = mnt.meters_for(db, item.id)
    rates = mnt.rates_for(db, {item.id}, today or date.today()).get(item.id)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    r = Resolver.load(db)
    for t in _tasks(db, item.id):
        out = mnt.task_out(t, meters, today, rates)
        out["parts"] = parts_svc.task_readiness(db, t)
        for line in out["parts"]:
            for src in line["sources"]:
                for it in src["items"]:
                    held = db.get(Item, it["id"])
                    it["where"] = r.describe_item(held)["path"] if held else None
        groups[out["schedule_group"]].append(out)
    return {
        "groups": [{"key": g, "label": GROUP_LABELS[g], "tasks": sorted(groups[g], key=lambda t: (t["not_applicable"],
                                                                                                    URGENCY[t["status"]]))}
                   for g in GROUP_ORDER if groups.get(g)],
        "meters": {"hours": float(meters.hours) if meters.hours is not None else None,
                   "cycles": float(meters.cycles) if meters.cycles is not None else None},
    }


def equipment_parts(db: Session, item: Item, within_days: int = 30, today: date | None = None) -> list[dict[str, Any]]:
    """Every part and consumable its active tasks use (one service each),
    with stock, where it's kept, what to buy, and whether a task due soon
    needs it.  Shared parts are one stock record whatever uses them."""
    today = today or date.today()
    r = Resolver.load(db)
    meters = mnt.meters_for(db, item.id)
    groups: dict[int, list] = {}
    soon: set[int] = set()
    for t in _tasks(db, item.id):
        if t.not_applicable:
            continue
        st = mnt.task_out(t, meters, today)
        for tp in parts_svc.task_parts(db, t.id):
            need = parts_svc._need_for(db, tp)
            need.tasks.append({"id": t.id, "title": t.title, "status": st["status"]})
            groups.setdefault(need.key, []).append(need)
            if st["status"] in ("overdue", "due_soon") or (st["days_left"] is not None and st["days_left"] <= within_days):
                soon.add(need.key)
    lines = []
    for key, needs in groups.items():
        line = parts_svc.evaluate(db, needs)
        line["tasks"] = [t for n in needs for t in n.tasks]
        line["needed_soon"] = key in soon
        for src in line["sources"]:
            for it in src["items"]:
                held = db.get(Item, it["id"])
                it["where"] = r.describe_item(held)["path"] if held else None
        lines.append(line)
    order = {"missing": 0, "partial": 1, "uncertain": 2, "in_stock": 3}
    lines.sort(key=lambda x: (not x["needed_soon"], order[x["status"]], x["product"]["name"].lower()))
    return lines


def costs(db: Session, item_id: int) -> dict[str, Any]:
    labor = parts = Decimal(0)
    n = 0
    for e in db.scalars(select(ServiceEvent).where(ServiceEvent.item_id == item_id, ServiceEvent.deleted_at.is_(None))):
        labor += e.labor_cost or 0
        parts += sum((p.quantity * (p.unit_cost or 0) for p in e.parts), Decimal(0))
        n += 1
    return {"services": n, "labor": float(labor), "parts": float(parts), "total": float(labor + parts)}


# --------------------------------------------------------------------- reports


def report(db: Session, kind: str, days: int = 30, since: date | None = None, today: date | None = None) -> dict[str, Any]:
    """Maintenance across all managed equipment.

    ``upcoming``: due within ``days`` (and due soon); ``overdue``: by
    urgency; ``history``: completed work since ``since``; ``consolidated``:
    each piece of equipment with its status and next jobs."""
    today = today or date.today()
    managed = select(EquipmentProfile.item_id).where(EquipmentProfile.managed.is_(True))
    if kind in ("upcoming", "overdue"):
        dash = mnt.dashboard(db, within_days=days, today=today, item_filter=Item.id.in_(managed))
        if kind == "overdue":
            rows = sorted(dash["overdue"], key=lambda t: (_overdue_by(t), t["item"]["name"].lower()))
        else:
            rows = [t for t in dash["due"] if t["status"] in ("due_soon", "ok", "deferred")]
        return {"kind": kind, "days": days, "tasks": rows}
    if kind == "history":
        since = since or (today - timedelta(days=365))
        rows = []
        for e in db.scalars(select(ServiceEvent).join(Item, Item.id == ServiceEvent.item_id)
                            .where(ServiceEvent.deleted_at.is_(None), ServiceEvent.performed_on >= since,
                                   ServiceEvent.outcome == ServiceOutcome.completed, household_only())
                            .order_by(ServiceEvent.performed_on.desc(), ServiceEvent.id.desc())):
            out = mnt.event_out(e)
            out["item"] = {"id": e.item_id, "name": (e.snapshot or {}).get("item_name")}
            rows.append(out)
        total = sum((x["labor_cost"] or 0) + (x["parts_cost"] or 0) for x in rows)
        return {"kind": kind, "since": since.isoformat(), "events": rows, "total_cost": round(total, 2)}
    if kind == "consolidated":
        return {"kind": kind, "equipment": equipment_list(db, today=today)}
    raise ValueError(kind)


def _overdue_by(t: dict[str, Any]) -> float:
    """More negative = more overdue (days, or hours scaled to days)."""
    cands = []
    if t.get("days_left") is not None:
        cands.append(t["days_left"])
    if t.get("hours_left") is not None:
        cands.append(t["hours_left"] * 3)  # an hour over is roughly a few days of use
    return min(cands) if cands else 0


# --------------------------------------------------------------------- checklist PDF


def _interval(t: dict[str, Any]) -> str:
    bits = []
    if t.get("interval_hours"):
        bits.append(f"every {t['interval_hours']:g} h")
    if t.get("interval_cycles"):
        bits.append(f"every {t['interval_cycles']} uses")
    if t.get("interval_days"):
        d = t["interval_days"]
        bits.append("every year" if d == 365 else f"every {d // 30} months" if d % 30 == 0 and d >= 60 else f"every {d} days")
    if not bits:
        return {"before_use": "before each use", "after_use": "after each use", "storage": "before storage",
                "seasonal": "each season"}.get(t.get("schedule_group"), "as needed")
    return " or ".join(bits) + (" (whichever first)" if len(bits) > 1 else "")


def checklist_pdf(db: Session, item: Item, today: date | None = None) -> bytes:
    """A printable service checklist: each job with its interval, when it
    was last done and is next due, the parts it needs and where they are,
    and where the job comes from (the manual's page, or "generic")."""
    import io
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    today = today or date.today()
    p = item.product
    prof = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    data = checklist(db, item, today)
    plan = manual_status(db, item, _tasks(db, item.id))
    ss = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=10)
    cell = ParagraphStyle("cell", parent=ss["Normal"], fontSize=9, leading=11)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Maintenance checklist - {p.name}")
    story = [Paragraph(escape(f"Maintenance checklist: {p.name}"), ss["Title"])]
    ident = [x for x in (p.manufacturer, p.model_number and f"model {p.model_number}",
                         item.serial_number and f"serial {item.serial_number}",
                         prof and prof.engine_model and f"engine {prof.engine_manufacturer or ''} {prof.engine_model}".strip()) if x]
    if ident:
        story.append(Paragraph(escape(" · ".join(ident)), ss["Normal"]))
    hours = data["meters"]["hours"]
    story.append(Paragraph(escape(f"Printed {today.isoformat()}" + (f" · hour meter {hours:g} h" if hours is not None else "")),
                           small))
    source_note = {
        "from_manual": "Tasks marked MANUAL come from the manufacturer's manual (file and page given).",
        "from_manual_confirmed": "Tasks marked MANUAL come from a manual whose model match was confirmed by hand.",
        "generic": "Tasks marked GENERIC are a typical schedule, NOT this model's manual: check them against the manual.",
        "manual_not_read": "A manual is attached but its schedule hasn't been read in yet.",
        "no_manual": "No manufacturer's manual on file: these tasks were added by hand.",
    }[plan["state"]]
    story += [Paragraph(escape(source_note), small), Spacer(1, 4 * mm)]
    for g in data["groups"]:
        story.append(Paragraph(escape(g["label"]), ss["Heading3"]))
        rows = [["", "Task", "When", "Last done / next due", "Parts", "Source"]]
        for t in g["tasks"]:
            if t["not_applicable"]:
                continue
            last = t["last_done_on"] or "-"
            if t["last_done_hours"] is not None:
                last += f" @ {t['last_done_hours']:g} h"
            nxt = t["next_due_on"] or ""
            if t["next_due_hours"] is not None:
                nxt += (" / " if nxt else "") + f"{t['next_due_hours']:g} h"
            status = {"overdue": " OVERDUE", "due_soon": " due soon"}.get(t["status"], "")
            parts = []
            for line in t["parts"]:
                where = next((it.get("where") for s in line["sources"] for it in s["items"] if it.get("where")), None)
                parts.append(f"{line['product']['name']} ×{line['quantity']:g}{(' ' + line['unit']) if line['unit'] else ''}"
                             + (f" (have: {where})" if line["status"] == "in_stock" and where else " (have)"
                                if line["status"] == "in_stock" else " (BUY)" if line["status"] in ("missing", "partial") else ""))
            src = {"manual": "MANUAL", "generic": "GENERIC", "user": "added by hand"}.get(t["source"], t["source"])
            if t["source"] == "manual" and t["source_ref"]:
                src += f": {t['source_ref']}"
            if t["review_status"] == "needs_review":
                src += " (review)"
            desc = escape(t["title"]) + (f"<br/><font size=7>{escape(t['instructions'][:300])}</font>" if t["instructions"] else "")
            rows.append(["☐", Paragraph(desc, cell), Paragraph(escape(_interval(t)), cell),
                         Paragraph(escape(f"{last} → {nxt or '-'}{status}"), cell),
                         Paragraph(escape("; ".join(parts) or "-"), cell), Paragraph(escape(src), small)])
        tbl = Table(rows, colWidths=[6 * mm, 52 * mm, 28 * mm, 34 * mm, 40 * mm, 22 * mm], repeatRows=1)
        tbl.setStyle(TableStyle([("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("FONTSIZE", (0, 1), (0, -1), 12), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                                 ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
        story += [tbl, Spacer(1, 3 * mm)]
    if not data["groups"]:
        story.append(Paragraph("No maintenance tasks yet.", ss["Normal"]))
    story += [Spacer(1, 6 * mm), Paragraph("Notes / date / hours / initials:", ss["Normal"])]
    story += [Paragraph("_" * 95, ss["Normal"]) for _ in range(4)]
    doc.build(story)
    return buf.getvalue()

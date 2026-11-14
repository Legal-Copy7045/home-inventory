"""Equipment helpers: season mode, fuel & consumables, meter-reading
reminders, the service history PDF and "is it worth fixing?"."""

from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..events import queue_event
from ..models import EquipmentProfile, Item, MeterKind, MeterReading, Product, ServiceEvent, Setting

# --------------------------------------------------------------------------- season mode

SPRING_WORDS = ("pre-season", "pre season", "inspect", "oil", "spark", "air filter", "blade", "sharpen", "chain",
                "fuel filter", "tune", "charge", "grease", "tyre", "tire")
AUTUMN_WORDS = ("winteri", "stabil", "run dry", "drain", "store", "battery", "clean", "fog")


def which_season(today: date | None = None, hemisphere: str = "north") -> str:
    """The season to get ready for. North: spring from December to June,
    autumn from July to November; south: six months on."""
    m = (today or date.today()).month
    if hemisphere == "south":
        m = (m + 5) % 12 + 1
    return "autumn" if 7 <= m <= 11 else "spring"


def hemisphere(db: Session) -> str:
    s = db.get(Setting, "hemisphere")
    return "south" if s is not None and s.value == "south" else "north"


def season_jobs(plan: dict[str, Any], season: str) -> dict[str, Any]:
    """From the maintenance plan: each machine's jobs for the season (and
    anything overdue or due soon), and one list of parts still to get."""
    words = SPRING_WORDS if season == "spring" else AUTUMN_WORDS
    machines, need = [], {}
    for e in plan["equipment"]:
        jobs = [t for t in e["tasks"]
                if any(w in t["title"].lower() for w in words) or t["status"] in ("overdue", "due_soon")]
        if not jobs:
            continue
        for t in jobs:
            for p in t.get("parts") or []:
                if p["status"] in ("missing", "partial"):
                    n = need.setdefault(p["name"], {"name": p["name"], "quantity": 0, "unit": p.get("unit"), "for": []})
                    n["quantity"] += p.get("quantity") or 1
                    n["for"].append(f"{e['item']['name']}: {t['title']}")
        machines.append({"item": e["item"], "fuel_type": (e.get("profile") or {}).get("fuel_type"), "jobs": jobs})
    return {"season": season, "machines": machines, "parts_to_get": sorted(need.values(), key=lambda n: n["name"].lower())}


# --------------------------------------------------------------------------- fuel & consumables

# What a machine's fuel/oil needs are called, and words that find it in the inventory.
CONSUMABLES = {
    "2-stroke oil": ("2 stroke", "2-stroke", "two stroke", "2t oil", "two-stroke", "2 cycle", "2-cycle"),
    "Petrol / gasoline": ("petrol", "gasoline", "unleaded", "fuel can", "jerry can", "e5", "e10"),
    "Engine oil": ("engine oil", "sae30", "sae 30", "10w30", "10w-30", "4 stroke", "4-stroke", "four stroke"),
    "Chain / bar oil": ("bar oil", "chain oil", "bar and chain", "bar & chain"),
    "Fuel stabiliser": ("stabiliser", "stabilizer", "sta-bil", "stabil"),
    "Diesel": ("diesel",),
}


def needs_for(profile: dict[str, Any] | None, equipment_type: str | None, specs: list[dict] | None) -> set[str]:
    fuel = ((profile or {}).get("fuel_type") or "").lower()
    kind = (equipment_type or "").lower()
    out: set[str] = set()
    if "2-stroke" in fuel or "2 stroke" in fuel or "mix" in fuel:
        out |= {"2-stroke oil", "Petrol / gasoline"}
    elif "diesel" in fuel:
        out.add("Diesel")
    elif fuel and "electric" not in fuel and "battery" not in fuel:
        out |= {"Petrol / gasoline", "Engine oil"}
    if "chainsaw" in kind or any("chain" in (s.get("label") or "").lower() for s in specs or []):
        out.add("Chain / bar oil")
    if out & {"Petrol / gasoline"}:
        out.add("Fuel stabiliser")
    return out


def consumables(db: Session) -> list[dict[str, Any]]:
    """Each fuel or oil your machines use, which machines, and what you have."""
    users: dict[str, list[str]] = {}
    for prof, item in db.execute(select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id)
                                 .where(Item.deleted_at.is_(None))):
        for need in needs_for({"fuel_type": prof.fuel_type}, prof.equipment_type, prof.specs):
            users.setdefault(need, []).append(item.product.name)
    if not users:
        return []
    stock = db.execute(select(Item, Product).join(Product, Product.id == Item.product_id)
                       .where(Item.deleted_at.is_(None))).all()
    out = []
    for need, machines in sorted(users.items()):
        words = CONSUMABLES[need]
        have = [{"item_id": it.id, "name": p.name, "quantity": it.quantity, "min_quantity": p.min_quantity}
                for it, p in stock
                if any(w in f"{p.name} {p.tags or ''} {p.description or ''}".lower() for w in words)
                and not any(it.product.name == m for m in machines)]
        total = sum(h["quantity"] for h in have)
        low = any(h["min_quantity"] is not None and h["quantity"] < h["min_quantity"] for h in have)
        out.append({"need": need, "machines": sorted(set(machines)), "have": have,
                    "status": "none" if total == 0 else ("low" if low else "ok")})
    return out


# --------------------------------------------------------------------------- meter-reading reminders

READINGS_KEY = "meter_reminder_last"


def readings_due(db: Session, days: int = 30) -> list[dict[str, Any]]:
    """Hour-meter machines without a sensor whose last reading is old."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    out = []
    for prof, item in db.execute(select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id)
                                 .where(Item.deleted_at.is_(None))):
        if prof.meter not in ("hours", "both") or prof.hours_entity_id:
            continue
        last = db.scalar(select(func.max(MeterReading.recorded_at))
                         .where(MeterReading.item_id == item.id, MeterReading.kind == MeterKind.hours))
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is None or last < cutoff:
            out.append({"item_id": item.id, "name": item.product.name,
                        "last_reading": last.isoformat() if last else None,
                        "days": (datetime.now(UTC) - last).days if last else None})
    return out


def monthly_reading_reminder(db: Session) -> int:
    s = db.get(Setting, READINGS_KEY)
    now = datetime.now(UTC)
    if s is not None and s.value:
        try:
            if now - datetime.fromisoformat(s.value) < timedelta(days=28):
                return 0
        except (TypeError, ValueError):
            pass
    due = readings_due(db)
    for m in due:
        queue_event(db, "meter_reading_due", m)
    if s is None:
        db.add(Setting(key=READINGS_KEY, value=now.isoformat()))
    else:
        s.value = now.isoformat()
    return len(due)


# --------------------------------------------------------------------------- is it worth fixing?


def worth_fixing(item: Item, repair_cost: Decimal, currency: str) -> dict[str, Any]:
    """Compare a repair with replacing: the rough rule of thumb is that a
    repair over half the cost of a new one, or over the second-hand value of
    an old machine, is rarely worth it."""
    est = (item.ai_metadata or {}).get("value_estimate") or {}
    new = Decimal(str(est["replacement"])) if est.get("replacement") else None
    used = None
    if est.get("used_high") or est.get("used_low"):
        used = Decimal(str(est.get("used_high") or est.get("used_low")))
    paid = item.purchase_price
    age = None
    if item.purchase_date:
        age = round((date.today() - item.purchase_date).days / 365.25, 1)
    basis = new or paid
    if basis is None:
        return {"verdict": "unknown", "reason": "No price to compare with: add what it cost, or estimate its value with AI.",
                "repair_cost": str(repair_cost), "replacement": None, "second_hand": None, "age_years": age}
    ratio = float(repair_cost / basis) if basis else 1.0
    if used is not None and repair_cost > used and (age or 0) >= 5:
        verdict, reason = "replace", f"The repair costs more than it's worth second-hand (about {used} {currency})."
    elif ratio > 0.5:
        verdict, reason = "replace", f"The repair is {round(ratio * 100)}% of the price of a new one."
    elif ratio > 0.3:
        verdict, reason = "think", f"The repair is {round(ratio * 100)}% of a new one: worth it if it's otherwise in good shape."
    else:
        verdict, reason = "fix", f"The repair is only {round(ratio * 100)}% of the price of a new one."
    return {"verdict": verdict, "reason": reason, "repair_cost": str(repair_cost),
            "replacement": str(new) if new is not None else None, "paid": str(paid) if paid is not None else None,
            "second_hand": str(used) if used is not None else None, "age_years": age}


# --------------------------------------------------------------------------- service history PDF


def service_history_pdf(db: Session, item: Item, summary: dict[str, Any], place: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    def esc(s: Any) -> str:
        return re.sub(r"[<>&]", lambda m: {"<": "&lt;", ">": "&gt;", "&": "&amp;"}[m.group(0)], str(s or ""))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"Service history: {item.product.name}")
    st = getSampleStyleSheet()
    p = item.product
    prof = summary.get("profile") or {}
    flow = [Paragraph(f"Service history: {esc(p.name)}", st["Title"])]
    facts = [("Make / model", " ".join(x for x in (p.manufacturer, p.model_number) if x) or "—"),
             ("Serial number", item.serial_number or "—"),
             ("Engine", " ".join(x for x in (prof.get("engine_manufacturer"), prof.get("engine_model")) if x) or "—"),
             ("Fuel", prof.get("fuel_type") or "—"),
             ("Bought", f"{item.purchase_date or '—'}{f' from {item.retailer}' if item.retailer else ''}"),
             ("Hours now", summary.get("meters", {}).get("hours") if summary.get("meters", {}).get("hours") is not None else "—"),
             ("Kept at", place),
             ("Printed", date.today().isoformat())]
    t = Table([[Paragraph(f"<b>{esc(k)}</b>", st["Normal"]), Paragraph(esc(v), st["Normal"])] for k, v in facts],
              colWidths=[38 * mm, None])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    flow += [t, Spacer(1, 8)]
    specs = [s for s in (prof.get("specs") or []) if s.get("value") or s.get("part_number")]
    if specs:
        flow.append(Paragraph("Specifications and parts", st["Heading2"]))
        flow.append(Table([[esc(s.get("label")), esc(" ".join(x for x in (s.get("value"), s.get("part_number")) if x))] for s in specs],
                          colWidths=[50 * mm, None], style=[("FONTSIZE", (0, 0), (-1, -1), 9)]))
    events = db.scalars(select(ServiceEvent).where(ServiceEvent.item_id == item.id, ServiceEvent.deleted_at.is_(None))
                        .order_by(ServiceEvent.performed_on.desc())).all()
    flow.append(Paragraph(f"Work done ({len(events)})", st["Heading2"]))
    if events:
        rows = [["Date", "Hours", "Work", "Parts", "By"]]
        for e in events:
            parts = "; ".join(f"{pt.quantity:g} × {pt.description}" for pt in e.parts) if e.parts else ""
            work = e.title + (f": {e.work_performed}" if e.work_performed else "")
            rows.append([str(e.performed_on), f"{e.hours:g}" if e.hours is not None else "",
                         Paragraph(esc(work), st["BodyText"]), Paragraph(esc(parts), st["BodyText"]), esc(e.performed_by)])
        tbl = Table(rows, colWidths=[22 * mm, 14 * mm, None, 50 * mm, 24 * mm], repeatRows=1)
        tbl.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5")),
                                 ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d1d9")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        flow.append(tbl)
    else:
        flow.append(Paragraph("No service recorded yet.", st["Normal"]))
    tasks = [t for t in summary.get("tasks", []) if t.get("status") != "not_applicable"]
    if tasks:
        flow.append(Paragraph("Maintenance schedule", st["Heading2"]))
        rows = [["Job", "Every", "Last done", "Next due"]]
        for t in tasks:
            every = " or ".join(x for x in (f"{t['interval_days']} days" if t.get("interval_days") else "",
                                            f"{t['interval_hours']} h" if t.get("interval_hours") else "") if x) or "—"
            rows.append([Paragraph(esc(t["title"]), st["BodyText"]), every, t.get("last_done_on") or "—",
                         t.get("next_due_on") or (f"{t['next_due_hours']} h" if t.get("next_due_hours") is not None else "—")])
        tbl = Table(rows, colWidths=[None, 34 * mm, 26 * mm, 26 * mm], repeatRows=1)
        tbl.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f5")),
                                 ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c9d1d9"))]))
        flow.append(tbl)
    doc.build(flow)
    return buf.getvalue()

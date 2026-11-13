"""Seasonal checklists: the yearly jobs that belong to a time of year.

For the next three months this lists the maintenance already scheduled
(by date), and suggests seasonal jobs for your equipment that aren't set
up yet (soonest first, those in the next three months marked), such as winterising a mower in autumn or a pre-season check in
spring.  A suggestion becomes an ordinary yearly task, first due on the 1st
of its month.  Months are for the northern hemisphere unless the
``hemisphere`` setting is "south".
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor
from ..models import EquipmentProfile, Item, MaintenanceTask, Setting
from . import maintenance as mnt

# (match words in the equipment type or name, title, month in the north, why)
SEASONAL: list[tuple[tuple[str, ...], str, int, str]] = [
    (("mower", "tractor"), "Pre-season inspection", 3, "Blade, oil, air filter and tyres before the first cut."),
    (("mower", "tractor"), "Winterize (fuel stabilizer or run dry)", 10, "Stale fuel is the commonest reason a mower won't start in spring."),
    (("trimmer", "strimmer", "blower", "hedge"), "Winterize (run dry)", 10, "Run the fuel out before storing it for winter."),
    (("pressure washer",), "Winterize pump (pump saver)", 10, "Water left in the pump can freeze and crack it."),
    (("snow",), "Pre-season inspection", 10, "Shear pins, auger, oil and a test run before the first snow."),
    (("snow",), "Summer storage (run dry)", 4, "Run the fuel out and store it clean."),
    (("generator",), "Pre-winter test run under load", 10, "Make sure it starts before you need it in a power cut."),
    (("chainsaw",), "Pre-season check (chain, bar, oiler)", 9, "Before the firewood season."),
]

SEASONS_NORTH = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
                 6: "Summer", 7: "Summer", 8: "Summer", 9: "Autumn", 10: "Autumn", 11: "Autumn"}


def hemisphere(db: Session) -> str:
    s = db.get(Setting, "hemisphere")
    return "south" if s is not None and s.value == "south" else "north"


def _month(north_month: int, hemi: str) -> int:
    return north_month if hemi == "north" else (north_month + 5) % 12 + 1


def season_name(month: int, hemi: str) -> str:
    return SEASONS_NORTH[month if hemi == "north" else (month + 5) % 12 + 1]


def _months_ahead(today: date, n: int = 3) -> list[int]:
    return [(today.month - 1 + i) % 12 + 1 for i in range(n)]


def _first_of(month: int, today: date) -> date:
    year = today.year if month >= today.month else today.year + 1
    return date(year, month, 1)


def checklist(db: Session, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    hemi = hemisphere(db)
    months = _months_ahead(today)
    end_month = months[-1]
    end = date(_first_of(end_month, today).year, end_month, calendar.monthrange(_first_of(end_month, today).year, end_month)[1])

    scheduled = []
    rows = db.execute(select(MaintenanceTask, Item).join(Item, Item.id == MaintenanceTask.item_id).where(
        MaintenanceTask.deleted_at.is_(None), Item.deleted_at.is_(None), MaintenanceTask.not_applicable.is_(False),
        MaintenanceTask.next_due_on.is_not(None), MaintenanceTask.next_due_on <= end)).all()
    for task, item in sorted(rows, key=lambda r: r[0].next_due_on):
        scheduled.append({"task_id": task.id, "title": task.title, "due_on": task.next_due_on.isoformat(),
                          "overdue": task.next_due_on < today, "item": {"id": item.id, "name": item.product.name}})

    suggestions = []
    profiles = db.execute(select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id)
                          .where(Item.deleted_at.is_(None))).all()
    for profile, item in profiles:
        text = f"{profile.equipment_type or ''} {item.product.name}".lower()
        titles = {t.lower() for t in db.scalars(select(MaintenanceTask.title).where(
            MaintenanceTask.item_id == item.id, MaintenanceTask.deleted_at.is_(None)))}
        for n, (words, title, north_month, why) in enumerate(SEASONAL):
            month = _month(north_month, hemi)
            if not any(w in text for w in words) or title.lower() in titles:
                continue
            suggestions.append({"key": n, "title": title, "why": why, "month": month, "this_season": month in months,
                                "month_name": calendar.month_name[month], "due_on": _first_of(month, today).isoformat(),
                                "item": {"id": item.id, "name": item.product.name}})
    suggestions.sort(key=lambda s: (s["due_on"], s["item"]["name"].lower()))
    return {"season": season_name(today.month, hemi), "hemisphere": hemi,
            "months": [calendar.month_name[m] for m in months], "scheduled": scheduled, "suggestions": suggestions}


def add(db: Session, actor: Actor, item: Item, key: int, today: date | None = None) -> MaintenanceTask:
    if not 0 <= key < len(SEASONAL):
        raise mnt.MaintenanceError("Unknown seasonal job")
    _words, title, north_month, why = SEASONAL[key]
    month = _month(north_month, hemisphere(db))
    return mnt.create_task(db, actor, item, {"title": title, "instructions": why, "interval_days": 365,
                                             "first_due_on": _first_of(month, today or date.today())})

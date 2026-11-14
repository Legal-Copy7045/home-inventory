"""Spoken maintenance answers for Home Assistant voice.

The integration's intents ask here and say what comes back, so every voice
platform (Assist, Alexa, Siri through Assist) gets the same, tested answers
from real schedules.  Answers are short: a handful of jobs, then "and N
more".  Which equipment a question means is resolved from its name, make,
model, label, type and aliases; when several could match, the caller is
given the candidates to ask about instead of a guess.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EquipmentProfile, Item, ServiceEvent, ServiceOutcome
from . import equipment_hub as hub
from . import maintenance as mnt
from .customers import household_only
from .textmatch import stem, word_matches, words

RANGES = ("overdue", "today", "week", "month", "upcoming", "all")
STOP = {"the", "my", "a", "an", "our", "on", "in", "for", "of", "to", "i", "did", "do", "last", "time", "when", "it"}
MAX_SPOKEN = 4


# --------------------------------------------------------------------- equipment


def spoken_name(item: Item, prof: EquipmentProfile | None) -> str:
    """What people call it: its label, else make + type ("Toro lawn mower"),
    else its name."""
    p = item.product
    if item.label:
        return item.label
    if p.manufacturer and prof and prof.equipment_type:
        return f"{p.manufacturer} {prof.equipment_type.lower()}"
    return p.name


def _terms(item: Item, prof: EquipmentProfile) -> list[str]:
    p = item.product
    ws = words(" ".join(x for x in (p.name, p.manufacturer, p.model_number, item.label, item.serial_number,
                                    prof.equipment_type, p.tags) if x))
    # "lawnmower" should find "lawn mower" and the other way round.
    return ws + [a + b for a, b in zip(ws, ws[1:])]


def resolve(db: Session, text: str | None) -> list[dict[str, Any]]:
    """Managed equipment the words could mean, best first, each with a score
    (1.0 = every word matched)."""
    asked = [w for w in words(text or "") if w not in STOP]
    if not asked:
        return []
    out = []
    for prof, item in db.execute(select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id)
                                 .where(EquipmentProfile.managed.is_(True), Item.deleted_at.is_(None), household_only())):
        terms = _terms(item, prof)
        hits = sum(1 for q in asked if any(word_matches(q, t) for t in terms))
        joined = "".join(asked)
        if any(t == joined or (len(joined) >= 5 and t.startswith(joined)) for t in terms):
            hits = len(asked)
        score = hits / len(asked)
        if score >= 0.5:
            fuzzy = SequenceMatcher(None, " ".join(asked), spoken_name(item, prof).lower()).ratio()
            out.append({"id": item.id, "name": spoken_name(item, prof), "full_name": item.product.name,
                        "score": round(score + fuzzy * 0.1, 3), "item": item, "profile": prof})
    out.sort(key=lambda c: -c["score"])
    return out


def _clear(cands: list[dict[str, Any]]) -> bool:
    return len(cands) == 1 or (len(cands) > 1 and cands[0]["score"] >= 1 and cands[1]["score"] < 1)


# --------------------------------------------------------------------- wording


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _list(xs: list[str]) -> str:
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def _when(t: dict[str, Any], today: date) -> str:
    """'is overdue by 3 operating hours', 'is due in 5 operating hours',
    'is due next month'..."""
    if t["status"] == "overdue":
        bits = []
        if t.get("hours_left") is not None and t["hours_left"] <= 0:
            h = abs(t["hours_left"])
            bits.append(f"by {h:g} operating hour{'' if h == 1 else 's'}" if h else "now")
        if t.get("days_left") is not None and t["days_left"] < 0:
            d = -t["days_left"]
            bits.append(f"by {d} day{'' if d == 1 else 's'}")
        return "is overdue" + (f" {bits[0]}" if bits else "")
    parts = []
    if t.get("hours_left") is not None and t.get("interval_hours"):
        h = t["hours_left"]
        parts.append(f"within the next {h:g} operating hour{'' if h == 1 else 's'}")
    d = t.get("days_left")
    if d is None and t.get("estimated_due_on"):
        d = (date.fromisoformat(t["estimated_due_on"]) - today).days
    if d is not None:
        parts.append("today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days" if d < 7 else "next week" if d < 14
                     else f"in {d // 7} weeks" if d < 30 else "next month" if d < 60 else f"in {d // 30} months")
    return "is due " + (" or ".join(parts) if parts else "soon")


def _task_phrase(t: dict[str, Any], name: str | None, today: date) -> str:
    title = t["title"][0].lower() + t["title"][1:]
    return f"{f'the {name} ' if name else ''}{title} {_when(t, today)}".strip()


def _in_range(t: dict[str, Any], rng: str, today: date) -> bool:
    if t["status"] in ("not_applicable", "manual"):
        return False  # disabled, or done as needed (before each use...): nothing is "due"
    if t["status"] == "overdue":
        return True
    if rng == "overdue":
        return False
    if rng == "all":
        return t["status"] in ("due_soon", "ok", "deferred")
    days = [t["days_left"]] if t.get("days_left") is not None else []
    if t.get("estimated_due_on"):
        days.append((date.fromisoformat(t["estimated_due_on"]) - today).days)
    soonest = min(days) if days else None
    limit = {"today": 0, "week": 7, "month": 31, "upcoming": 31}[rng]
    if soonest is not None and soonest <= limit:
        return True
    # Counted in hours and due soon by hours (no date to compare).
    return rng in ("upcoming", "month") and t["status"] == "due_soon"


def _range_words(rng: str) -> str:
    return {"overdue": "overdue", "today": "due today", "week": "due this week", "month": "due this month",
            "upcoming": "coming up", "all": "scheduled"}[rng]


# --------------------------------------------------------------------- answers


def _tasks_for(db: Session, item: Item, today: date) -> list[dict[str, Any]]:
    meters = mnt.meters_for(db, item.id)
    rates = mnt.rates_for(db, {item.id}, today).get(item.id)
    return [mnt.task_out(t, meters, today, rates) for t in hub._tasks(db, item.id)]


def _order(t: dict[str, Any]) -> tuple:
    return (hub.URGENCY.get(t["status"], 9), hub._days(t))


def due(db: Session, rng: str, cand: dict[str, Any] | None, today: date) -> str:
    if cand is not None:
        item, name = cand["item"], cand["name"]
        tasks = sorted((t for t in _tasks_for(db, item, today) if _in_range(t, rng, today)), key=_order)
        if not tasks:
            return f"Nothing is {_range_words(rng)} for your {name}."
        said = [_task_phrase(t, None, today) for t in tasks[:MAX_SPOKEN]]
        more = f", and {len(tasks) - MAX_SPOKEN} more" if len(tasks) > MAX_SPOKEN else ""
        head = f"Your {name} has {_plural(len(tasks), 'maintenance task', 'maintenance tasks')} {_range_words(rng)}."
        body = " ".join(f"The {s}." for s in said[:-1]) + (" " if len(said) > 1 else "") + f"The {said[-1]}{more}."
        stock = _stock_sentence(db, item, {t["id"] for t in tasks})
        return " ".join(x for x in (head, body, stock) if x)
    rows = []
    for prof, item in db.execute(select(EquipmentProfile, Item).join(Item, Item.id == EquipmentProfile.item_id)
                                 .where(EquipmentProfile.managed.is_(True), Item.deleted_at.is_(None), household_only())):
        name = spoken_name(item, prof)
        rows += [(t, name) for t in _tasks_for(db, item, today) if _in_range(t, rng, today)]
    if not rows:
        return f"No maintenance is {_range_words(rng)}."
    rows.sort(key=lambda r: _order(r[0]))
    said = [_task_phrase(t, n, today) for t, n in rows[:MAX_SPOKEN]]
    said = [s[0].upper() + s[1:] for s in said]
    more = f" And {len(rows) - MAX_SPOKEN} more." if len(rows) > MAX_SPOKEN else ""
    head = f"You have {_plural(len(rows), 'maintenance task', 'maintenance tasks')} {_range_words(rng)}."
    return head + " " + " ".join(f"{s}." for s in said) + more


def _stock_sentence(db: Session, item: Item, task_ids: set[int]) -> str:
    lines = [ln for ln in hub.equipment_parts(db, item) if any(t["id"] in task_ids for t in ln["tasks"])]
    if not lines:
        return ""
    have = [ln["product"]["name"].lower() for ln in lines if ln["status"] == "in_stock"]
    need = [ln["product"]["name"].lower() for ln in lines if ln["status"] in ("missing", "partial")]
    out = []
    if have:
        out.append(f"You have the {_list(have)} in stock.")
    if need:
        out.append(f"You need to buy {_list(need)}.")
    return " ".join(out)


def parts(db: Session, cand: dict[str, Any], check: bool, today: date) -> str:
    """Parts for the next service: the jobs overdue or due soon, else the next one."""
    item, name = cand["item"], cand["name"]
    tasks = sorted((t for t in _tasks_for(db, item, today) if t["status"] not in ("not_applicable", "manual")), key=_order)
    due_now = [t for t in tasks if t["status"] in ("overdue", "due_soon")] or tasks[:1]
    if not due_now:
        return f"Your {name} has no scheduled maintenance."
    ids = {t["id"] for t in due_now}
    lines = [ln for ln in hub.equipment_parts(db, item) if any(t["id"] in ids for t in ln["tasks"])]
    jobs = _list([t["title"].lower() for t in due_now[:3]])
    if not lines:
        return f"The next service on your {name} is {jobs}. No parts are listed for it."
    have = [ln["product"]["name"].lower() for ln in lines if ln["status"] == "in_stock"]
    need = [ln["product"]["name"].lower() for ln in lines if ln["status"] in ("missing", "partial")]
    unsure = [ln["product"]["name"].lower() for ln in lines if ln["status"] == "uncertain"]
    if check:
        if not need and not unsure:
            return f"Yes, you have everything for the next service on your {name}: {_list(have)}."
        bits = [f"Not quite. For your {name} you need to buy {_list(need)}." if need else
                f"Probably. For your {name}, check {_list(unsure)}."]
        if have:
            bits.append(f"You have the {_list(have)}.")
        return " ".join(bits)
    bits = [f"For the next service on your {name} ({jobs}) you need {_list([ln['product']['name'].lower() for ln in lines])}."]
    if have:
        bits.append(f"You have the {_list(have)}.")
    if need:
        bits.append(f"You need to buy {_list(need)}.")
    if unsure:
        bits.append(f"Check whether you have {_list(unsure)}.")
    return " ".join(bits)


def _task_words(text: str) -> set[str]:
    return {stem(w) for w in words(text) if w not in STOP}


def last_done(db: Session, cand: dict[str, Any] | None, task_text: str) -> str:
    asked = _task_words(task_text)
    if not asked:
        return "Which job do you mean?"
    stmt = (select(ServiceEvent).join(Item, Item.id == ServiceEvent.item_id)
            .where(ServiceEvent.deleted_at.is_(None), ServiceEvent.outcome == ServiceOutcome.completed, household_only()))
    if cand is not None:
        stmt = stmt.where(ServiceEvent.item_id == cand["id"])
    best, best_score = None, 0.0
    for e in db.scalars(stmt.order_by(ServiceEvent.performed_on.desc(), ServiceEvent.id.desc())):
        have = _task_words(" ".join(x for x in (e.title, e.task_title, e.work_performed) if x))
        hits = sum(1 for a in asked if any(word_matches(a, h) for h in have))
        score = hits / len(asked)
        if score > best_score:  # newest first: ties keep the most recent
            best, best_score = e, score
    where = f" on your {cand['name']}" if cand else ""
    if best is None or best_score < 0.5:
        return f"I have no record of {task_text}{where}."
    on = best.performed_on.strftime("%-d %B %Y")
    at = f", at {float(best.hours):g} operating hours" if best.hours is not None else ""
    what = best.title[0].lower() + best.title[1:]
    whose = "" if cand else f" on the {(best.snapshot or {}).get('item_name') or 'equipment'}"
    return f"The last {what}{where}{whose} was on {on}{at}."


def attention(db: Session, today: date) -> str:
    rows = [r for r in hub.equipment_list(db, today=today) if r["attention"] != "ok"
            and r["lifecycle_status"] not in ("retired", "disposed")]
    if not rows:
        return "Nothing needs attention: all your equipment is up to date."
    said = []
    for r in rows[:MAX_SPOKEN]:
        prof_item = db.get(Item, r["id"])
        prof = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == r["id"]))
        name = spoken_name(prof_item, prof)
        nxt = r["next"]
        what = nxt["title"].lower() if nxt else "maintenance"
        state = "overdue" if r["attention"] == "overdue" else "due soon"
        n = r["overdue"] if r["attention"] == "overdue" else r["due_soon"]
        said.append(f"the {name} has {'an' if n == 1 and state == 'overdue' else n if n > 1 else 'a'} {state} "
                    f"{'job' if n == 1 else 'jobs'}{f', {what}' if n == 1 else f', starting with {what}'}")
    more = f" And {len(rows) - MAX_SPOKEN} more." if len(rows) > MAX_SPOKEN else ""
    return f"{_plural(len(rows), 'piece of equipment needs', 'pieces of equipment need')} attention: {_list(said)}.{more}"


def recent(db: Session, today: date, days: int = 30) -> str:
    since = today - timedelta(days=days)
    rows = db.scalars(select(ServiceEvent).join(Item, Item.id == ServiceEvent.item_id)
                      .where(ServiceEvent.deleted_at.is_(None), ServiceEvent.outcome == ServiceOutcome.completed,
                             ServiceEvent.performed_on >= since, household_only())
                      .order_by(ServiceEvent.performed_on.desc(), ServiceEvent.id.desc())).all()
    if not rows:
        return "You haven't recorded any maintenance in the last month."
    said = [f"{e.title[0].lower() + e.title[1:]} on the {(e.snapshot or {}).get('item_name') or 'equipment'} "
            f"on {e.performed_on.strftime('%-d %B')}" for e in rows[:MAX_SPOKEN]]
    more = f", and {len(rows) - MAX_SPOKEN} more" if len(rows) > MAX_SPOKEN else ""
    return f"In the last month you completed {_plural(len(rows), 'job', 'jobs')}: {_list(said)}{more}."


def answer(db: Session, kind: str, rng: str = "upcoming", equipment: str | None = None, equipment_id: int | None = None,
           task: str | None = None, today: date | None = None) -> dict[str, Any]:
    """``{"speech": ...}``, or ``{"choose": [...], "speech": question}`` when
    several pieces of equipment could be meant."""
    today = today or date.today()
    cand = None
    if kind == "last_done" and not equipment and equipment_id is None and task:
        # "change the oil in the mower": the machine can come at the end.
        m = re.match(r"(?i)^(.+?)\s+(?:in|on|of|for)\s+(?:the\s+|my\s+)?(.+)$", task.strip())
        if m and resolve(db, m.group(2)):
            task, equipment = m.group(1), m.group(2)
    if equipment_id is not None or (equipment and equipment.strip()):
        cands = resolve(db, equipment) if equipment_id is None else []
        if equipment_id is not None:
            prof = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == equipment_id,
                                                            EquipmentProfile.managed.is_(True)))
            item = db.get(Item, equipment_id)
            if prof is None or item is None or item.deleted_at is not None or db.scalar(
                    select(Item.id).where(Item.id == equipment_id, household_only())) is None:
                return {"speech": "I couldn't find that equipment."}
            cands = [{"id": item.id, "name": spoken_name(item, prof), "item": item, "profile": prof, "score": 1}]
        if not cands:
            return {"speech": f"I couldn't find any equipment called {equipment}."}
        if not _clear(cands):
            shown = cands[:4]
            names = [c["full_name"] if sum(1 for x in shown if x["name"] == c["name"]) > 1 else c["name"] for c in shown]
            return {"choose": [{"id": c["id"], "name": n} for c, n in zip(shown, names)],
                    "speech": f"Which one: {_list(names)}?"}
        cand = cands[0]
    if kind == "due":
        speech = due(db, rng if rng in RANGES else "upcoming", cand, today)
    elif kind in ("parts", "have_parts"):
        if cand is None:
            return {"speech": "Which equipment do you mean?"}
        speech = parts(db, cand, kind == "have_parts", today)
    elif kind == "last_done":
        speech = last_done(db, cand, task or "")
    elif kind == "attention":
        speech = attention(db, today)
    elif kind == "recent":
        speech = recent(db, today)
    else:
        raise ValueError(kind)
    out: dict[str, Any] = {"speech": speech}
    if cand is not None:
        out["equipment"] = {"id": cand["id"], "name": cand["name"]}
    return out

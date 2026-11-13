"""Maintenance due notices and automatic shopping-list additions.

Once a day (with the warranty and expiry checks) every task that has become
due soon or overdue is announced once per service interval as a
``maintenance_due`` event, saying whether its parts are in stock.  The
Home Assistant integration fires it on the bus and can send it as a phone
notification.

With "Add missing maintenance parts to the shopping list" on (the default),
parts a due task needs and that aren't in stock are added to the shopping
list, unless they're already on it.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, system_user
from ..events import queue_event
from ..models import MaintenanceTask, Role, Setting, ShoppingListItem
from . import maintenance as mnt
from . import parts as parts_svc
from . import replenishment as repl

_LOGGER = logging.getLogger(__name__)

AUTO_SHOPPING = "maintenance_auto_shopping"


def auto_shopping(db: Session) -> bool:
    s = db.get(Setting, AUTO_SHOPPING)
    return True if s is None or s.value is None else bool(s.value)


def _actor(db: Session) -> Actor:
    u = system_user(db, "maintenance", "Maintenance reminders")
    return Actor(user_id=u.id, ha_user_id=u.ha_user_id, name="Maintenance reminders", role=Role.admin, channel="system")


def due_key(task: MaintenanceTask) -> str:
    """Changes when the task is done or rescheduled, so each interval gets one notice."""
    parts = (task.next_due_on, task.next_due_hours, task.next_due_cycles,
             task.last_done_on, task.last_done_hours, task.last_done_cycles)
    return "|".join("" if p is None else str(p) for p in parts)[:80]


def _message(item_name: str, t: dict[str, Any], missing: list[str]) -> str:
    when = "is overdue" if t["status"] == "overdue" else "is due soon"
    parts = f" Parts needed: {', '.join(missing)}." if missing else " Parts are in stock." if t.get("_has_parts") else ""
    return f"{item_name}: {t['title']} {when}.{parts}"


def check(db: Session) -> dict[str, int]:
    """Send notices for newly due tasks and add missing parts.  Returns counts."""
    dash = mnt.dashboard(db, within_days=0)
    tasks = dash["overdue"] + [t for t in dash["due"] if t["status"] == "due_soon"]
    sent = added = 0
    actor: Actor | None = None
    shop = auto_shopping(db)
    for t in tasks:
        task = db.get(MaintenanceTask, t["id"])
        key = due_key(task)
        if task.due_notified_key == key:
            continue
        task.due_notified_key = key
        lines = parts_svc.task_readiness(db, task)
        missing = [ln["product"]["name"] for ln in lines if ln["status"] in ("missing", "partial")]
        t["_has_parts"] = bool(lines)
        to_buy = []
        if shop:
            open_ids = set(db.scalars(select(ShoppingListItem.product_id).where(ShoppingListItem.status.in_(repl.OPEN))))
            for ln in lines:
                buy = ln.get("buy")
                if ln["status"] in ("missing", "partial") and buy and buy["product_id"] not in open_ids:
                    actor = actor or _actor(db)
                    try:
                        repl.add_to_list(db, actor, buy["product_id"], buy["quantity"], None,
                                         f"For {t['title']} on {t['item']['name']}"[:300])
                    except repl.ReplenishmentError as err:
                        _LOGGER.debug("Not added to the shopping list: %s", err)
                        continue
                    open_ids.add(buy["product_id"])
                    to_buy.append(buy["name"])
                    added += 1
        queue_event(db, "maintenance_due", {
            "item_id": t["item"]["id"],
            "item_name": t["item"]["name"],
            "task_id": task.id,
            "title": task.title,
            "status": t["status"],
            "next_due_on": t.get("next_due_on"),
            "days_left": t.get("days_left"),
            "hours_left": t.get("hours_left"),
            "estimated_due_on": t.get("estimated_due_on"),
            "parts_ready": bool(lines) and not missing,
            "parts_missing": missing,
            "added_to_shopping_list": to_buy,
            "message": _message(t["item"]["name"], t, missing),
        })
        sent += 1
    db.commit()
    return {"sent": sent, "added_to_shopping_list": added}

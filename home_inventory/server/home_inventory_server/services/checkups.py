"""Shelf check-ups: which place most needs a fresh photo inventory or check.

A place's last check-up is its latest confirmed photo inventory or finished
inventory check (Check this place). Places
that hold things and were never checked come first (most things first),
then the longest ago. Once a month the top one is published as a
``checkup_due`` event, so Home Assistant can remind you.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..events import queue_event
from ..models import Container, ContainerScan, Item, Location, ScanStatus, Setting, VerificationSession
from .placement import Resolver

NOTICE_KEY = "checkup_last_notice"
NOTICE_EVERY = timedelta(days=28)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def due(db: Session, limit: int = 10) -> list[dict[str, Any]]:
    r = Resolver.load(db)
    counts: dict[tuple[str, int], int] = {}
    for cid, n in db.execute(select(Item.container_id, func.count(Item.id))
                             .where(Item.deleted_at.is_(None), Item.container_id.is_not(None)).group_by(Item.container_id)):
        counts[("container", cid)] = n
    for lid, n in db.execute(select(Item.location_id, func.count(Item.id))
                             .where(Item.deleted_at.is_(None), Item.location_id.is_not(None)).group_by(Item.location_id)):
        counts[("location", lid)] = n
    checked: dict[tuple[str, int], datetime] = {}
    for cid, lid, at in db.execute(select(ContainerScan.container_id, ContainerScan.location_id, func.max(ContainerScan.confirmed_at))
                                   .where(ContainerScan.status == ScanStatus.confirmed)
                                   .group_by(ContainerScan.container_id, ContainerScan.location_id)):
        key = ("container", cid) if cid else ("location", lid)
        checked[key] = _aware(at)
    # A finished inventory check counts as a check-up too.
    for kind, sid, at in db.execute(select(VerificationSession.scope_type, VerificationSession.scope_id,
                                           func.max(VerificationSession.finished_at))
                                    .where(VerificationSession.finished_at.is_not(None),
                                           VerificationSession.scope_type.in_(("container", "location")))
                                    .group_by(VerificationSession.scope_type, VerificationSession.scope_id)):
        at = _aware(at)
        if checked.get((kind, sid)) is None or at > checked[(kind, sid)]:
            checked[(kind, sid)] = at
    now = datetime.now(UTC)
    out = []
    for (kind, pid), n in counts.items():
        obj = db.get(Container if kind == "container" else Location, pid)
        if obj is None or obj.deleted_at is not None:
            continue
        last = checked.get((kind, pid))
        path = r.describe(pid, None)["path"] if kind == "container" else r.location_path(pid)
        out.append({"type": kind, "id": pid, "name": obj.name, "path": path, "things": n,
                    "last_checked": last.isoformat() if last else None,
                    "days": (now - last).days if last else None})
    out.sort(key=lambda p: (p["days"] is not None, -(p["days"] or 0) if p["days"] is not None else -p["things"]))
    return out[:limit]


def monthly_notice(db: Session) -> dict[str, Any] | None:
    """Publish this month's suggestion (at most every 28 days)."""
    s = db.get(Setting, NOTICE_KEY)
    now = datetime.now(UTC)
    if s is not None and s.value:
        try:
            if now - datetime.fromisoformat(s.value) < NOTICE_EVERY:
                return None
        except (TypeError, ValueError):
            pass
    places = due(db, 1)
    if not places:
        return None
    p = places[0]
    if p["days"] is not None and p["days"] < 30:
        return None  # everything was checked recently
    queue_event(db, "checkup_due", {"type": p["type"], "id": p["id"], "name": p["name"], "path": p["path"],
                                    "things": p["things"], "last_checked": p["last_checked"]})
    if s is None:
        db.add(Setting(key=NOTICE_KEY, value=now.isoformat()))
    else:
        s.value = now.isoformat()
    return p

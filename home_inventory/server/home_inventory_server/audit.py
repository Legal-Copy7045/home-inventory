"""Inventory history (audit trail)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from .auth import Actor
from .models import HistoryEvent


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def snapshot(obj: Any, fields: Iterable[str]) -> dict[str, Any]:
    return {f: _plain(getattr(obj, f)) for f in fields}


def diff(before: dict[str, Any], after: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    changed = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}


def record(
    db: Session,
    actor: Actor | None,
    action: str,
    entity_type: str,
    entity_id: int,
    summary: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    related_containers: Iterable[int | None] = (),
) -> HistoryEvent:
    related = sorted({c for c in related_containers if c})
    ev = HistoryEvent(
        user_id=actor.user_id if actor else None,
        user_name=actor.name if actor else "System",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        before={k: _plain(v) for k, v in before.items()} if before else None,
        after={k: _plain(v) for k, v in after.items()} if after else None,
        related_container_ids="," + ",".join(str(c) for c in related) + "," if related else None,
    )
    db.add(ev)
    return ev

"""Operating hours / cycles read from Home Assistant sensors.

An equipment profile can name an entity for each meter (e.g. a smart-plug
runtime sensor, a ``history_stats`` sensor or a counter).  Every 15 minutes
its value is converted to hours (from h / min / s / d units) and stored as a
``source="sensor"`` reading when it has moved on, so maintenance due
dates follow real use without typing readings in.

A value lower than the last reading (a reset sensor, a replaced plug) is
not recorded: the user is shown why on the item page instead, and can
enter a correction.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, Role, system_user
from ..ha_api import HAClient, HAUnavailable
from ..models import EquipmentProfile, Item, MeterKind
from . import maintenance as mt

_LOGGER = logging.getLogger(__name__)
HOURS_PER = {"h": Decimal(1), "hr": Decimal(1), "hrs": Decimal(1), "hours": Decimal(1), "hour": Decimal(1),
             "min": Decimal(1) / 60, "mins": Decimal(1) / 60, "minutes": Decimal(1) / 60,
             "s": Decimal(1) / 3600, "sec": Decimal(1) / 3600, "seconds": Decimal(1) / 3600,
             "d": Decimal(24), "days": Decimal(24)}


def to_value(state: dict[str, Any] | None, kind: MeterKind) -> tuple[Decimal | None, str | None]:
    """The reading a state represents, or why it can't be used."""
    if state is None:
        return None, "entity not found"
    raw = state.get("state")
    if raw in (None, "unknown", "unavailable", ""):
        return None, f"sensor is {raw or 'empty'}"
    try:
        v = Decimal(str(raw))
    except InvalidOperation:
        return None, f"not a number: {str(raw)[:30]}"
    if v < 0:
        return None, "negative value"
    if kind is MeterKind.hours:
        unit = str((state.get("attributes") or {}).get("unit_of_measurement") or "h").strip().lower()
        if unit not in HOURS_PER:
            return None, f"unit '{unit}' isn't a time unit"
        v = v * HOURS_PER[unit]
    return v.quantize(Decimal("0.1")), None


def _actor(db: Session) -> Actor:
    u = system_user(db, "ha_sensor", "Home Assistant sensor")
    return Actor(user_id=u.id, ha_user_id=u.ha_user_id, name="Home Assistant sensor", role=Role.admin, channel="system")


def sync_profile(db: Session, client: HAClient, profile: EquipmentProfile, actor: Actor | None = None) -> list[dict[str, Any]]:
    item = db.get(Item, profile.item_id)
    if item is None or item.deleted_at is not None:
        return []
    out = []
    for kind, entity in ((MeterKind.hours, profile.hours_entity_id), (MeterKind.cycles, profile.cycles_entity_id)):
        if not entity:
            continue
        try:
            value, problem = to_value(client.state(entity), kind)
        except HAUnavailable as err:
            _LOGGER.info("Reading %s failed: %s", entity, err)
            out.append({"kind": kind.value, "entity_id": entity, "recorded": False,
                        "problem": "Home Assistant didn't answer; try again later"})
            continue
        last = mt.current_meter(db, item.id, kind)
        recorded = False
        if value is not None:
            if last is not None and value < last:
                problem = f"sensor shows {value}, below the last reading {last} (reset?) - not recorded"
            elif last is None or value > last:
                mt.add_reading(db, actor or _actor(db), item, kind, value, source="sensor", note=f"From {entity}")
                recorded = True
        out.append({"kind": kind.value, "entity_id": entity, "value": str(value) if value is not None else None,
                    "recorded": recorded, "problem": problem, "checked_at": datetime.now(UTC).isoformat()})
    return out


def sync_all(db: Session, client: HAClient) -> int:
    profiles = db.scalars(select(EquipmentProfile).where(
        (EquipmentProfile.hours_entity_id.is_not(None)) | (EquipmentProfile.cycles_entity_id.is_not(None))
    )).all()
    if not profiles:
        return 0
    actor = _actor(db)
    n = 0
    for p in profiles:
        for res in sync_profile(db, client, p, actor):
            n += res["recorded"]
            if res.get("problem"):
                _LOGGER.info("Meter sensor %s for item %s: %s", res["entity_id"], p.item_id, res["problem"])
    db.commit()
    return n


def candidates(client: HAClient) -> list[dict[str, Any]]:
    """Entities that could be a runtime / usage meter, best first."""
    out = []
    for s in client.states():
        eid = s.get("entity_id", "")
        if eid.split(".")[0] not in ("sensor", "counter", "input_number"):
            continue
        attrs = s.get("attributes") or {}
        unit = str(attrs.get("unit_of_measurement") or "").lower()
        try:
            Decimal(str(s.get("state")))
        except InvalidOperation:
            continue
        time_like = unit in HOURS_PER or attrs.get("device_class") == "duration"
        out.append({"entity_id": eid, "name": attrs.get("friendly_name") or eid, "state": s.get("state"),
                    "unit": attrs.get("unit_of_measurement"), "time": time_like})
    out.sort(key=lambda e: (not e["time"], e["name"].lower()))
    return out[:300]

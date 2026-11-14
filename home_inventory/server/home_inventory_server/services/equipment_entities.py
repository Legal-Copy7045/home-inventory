"""Home Assistant entities linked to equipment.

Two kinds of link, kept separate on purpose:

* **Meters** (operating hours, uses): the equipment profile's
  ``hours_entity_id`` / ``cycles_entity_id``.  ``sensor_meters`` reads them
  and records validated readings (time units only, never backwards), which
  feed maintenance due dates.
* **Everything else** (battery level, energy, power, an on/off status):
  ``EquipmentEntity`` rows, shown on the equipment page.  They are never
  turned into readings or maintenance events.

Linking is always a person's decision: ``check`` shows the entity's current
state and whether it suits the purpose, then ``link`` saves it (refusing an
entity Home Assistant doesn't know).  A link can be removed at any time.
An entity that is unavailable later is shown as such and never breaks the
page.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..ha_api import HAClient, HAUnavailable
from ..models import EquipmentEntity, EquipmentProfile, Item
from .sensor_meters import HOURS_PER

ENTITY_RE = re.compile(r"^[a-z_]{1,40}\.[a-z0-9_]{1,200}$")
PURPOSES: dict[str, dict[str, Any]] = {
    "runtime": {"label": "Operating hours", "domains": ("sensor", "counter", "input_number"), "units": tuple(HOURS_PER),
                "numeric": True},
    "usage": {"label": "Uses / cycles", "domains": ("sensor", "counter", "input_number"), "units": None, "numeric": True},
    "battery": {"label": "Battery", "domains": ("sensor",), "units": ("%",), "numeric": True},
    "energy": {"label": "Energy used", "domains": ("sensor",), "units": ("wh", "kwh", "mwh"), "numeric": True},
    "power": {"label": "Power", "domains": ("sensor",), "units": ("w", "kw"), "numeric": True},
    "status": {"label": "On / off", "domains": ("binary_sensor", "switch", "input_boolean", "sensor"), "units": None,
               "numeric": False},
    "other": {"label": "Other", "domains": None, "units": None, "numeric": False},
}
UNAVAILABLE = ("unavailable", "unknown", "", None)


class EntityError(ValueError):
    pass


def _profile(db: Session, item: Item) -> EquipmentProfile:
    prof = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    if prof is None or not prof.managed:
        raise EntityError("Link Home Assistant entities to managed equipment")
    return prof


def suitability(entity_id: str, purpose: str, state: dict[str, Any] | None) -> list[str]:
    """Why an entity may not suit a purpose (empty: it looks right)."""
    p = PURPOSES[purpose]
    problems = []
    domain = entity_id.split(".")[0]
    if p["domains"] and domain not in p["domains"]:
        problems.append(f"{p['label']} usually comes from a {' or '.join(p['domains'])} entity, not {domain}")
    if state is None:
        return problems + ["Home Assistant doesn't know this entity"]
    attrs = state.get("attributes") or {}
    unit = str(attrs.get("unit_of_measurement") or "").strip().lower()
    if p["units"] and unit not in p["units"]:
        problems.append(f"its unit is '{unit or 'none'}', not one for {p['label'].lower()}")
    value = state.get("state")
    if p["numeric"] and value not in UNAVAILABLE:
        try:
            Decimal(str(value))
        except InvalidOperation:
            problems.append(f"its state '{value}' isn't a number")
    if value in UNAVAILABLE:
        problems.append("it's unavailable right now")
    return problems


def check(client: HAClient | None, entity_id: str, purpose: str) -> dict[str, Any]:
    """What linking this entity would show, for the person to confirm."""
    entity_id = entity_id.strip().lower()
    if not ENTITY_RE.match(entity_id):
        raise EntityError("That isn't an entity id (like sensor.mower_battery)")
    if purpose not in PURPOSES:
        raise EntityError("Choose what the entity tells you")
    if client is None:
        raise EntityError("The App can't reach Home Assistant to check the entity")
    try:
        state = client.state(entity_id)
    except HAUnavailable as err:
        raise EntityError(f"Couldn't check it: {err}") from err
    return {"entity_id": entity_id, "purpose": purpose, "purpose_label": PURPOSES[purpose]["label"],
            "exists": state is not None, "state": _state_out(state), "problems": suitability(entity_id, purpose, state)}


def link(db: Session, actor: Actor, client: HAClient | None, item: Item, entity_id: str, purpose: str,
         confirm: bool, label: str | None = None) -> dict[str, Any]:
    """Save the link, after the person has seen ``check`` (``confirm``)."""
    prof = _profile(db, item)
    result = check(client, entity_id, purpose)
    if not result["exists"]:
        raise EntityError("Home Assistant doesn't know this entity")
    if not confirm:
        raise EntityError("Confirm the entity after checking it")
    if purpose in ("runtime", "usage"):
        if result["problems"]:
            # Meter readings feed due dates: only a suitable sensor is accepted.
            raise EntityError("Not suitable as a meter: " + "; ".join(result["problems"]))
        setattr(prof, "hours_entity_id" if purpose == "runtime" else "cycles_entity_id", result["entity_id"])
        if purpose == "runtime" and prof.meter in ("none", "cycles"):
            prof.meter = "both" if prof.meter == "cycles" else "hours"
        if purpose == "usage" and prof.meter in ("none", "hours"):
            prof.meter = "both" if prof.meter == "hours" else "cycles"
    else:
        row = db.scalar(select(EquipmentEntity).where(EquipmentEntity.item_id == item.id,
                                                      EquipmentEntity.entity_id == result["entity_id"]))
        if row is None:
            row = EquipmentEntity(item_id=item.id, entity_id=result["entity_id"], created_by_id=actor.user_id)
            db.add(row)
        row.purpose, row.label = purpose, (label or None)
    audit.record(db, actor, "entity_linked", "item", item.id,
                 f"{item.product.name}: linked {result['entity_id']} ({PURPOSES[purpose]['label'].lower()})")
    return result


def unlink(db: Session, actor: Actor, item: Item, entity_id: str) -> None:
    prof = _profile(db, item)
    removed = False
    if prof.hours_entity_id == entity_id:
        prof.hours_entity_id, removed = None, True
    if prof.cycles_entity_id == entity_id:
        prof.cycles_entity_id, removed = None, True
    for row in db.scalars(select(EquipmentEntity).where(EquipmentEntity.item_id == item.id,
                                                        EquipmentEntity.entity_id == entity_id)):
        db.delete(row)
        removed = True
    if not removed:
        raise EntityError("That entity isn't linked")
    audit.record(db, actor, "entity_unlinked", "item", item.id, f"{item.product.name}: unlinked {entity_id}")


def _state_out(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    attrs = state.get("attributes") or {}
    value = state.get("state")
    return {"value": value, "unit": attrs.get("unit_of_measurement"), "name": attrs.get("friendly_name"),
            "available": value not in UNAVAILABLE, "last_changed": state.get("last_changed")}


def linked(db: Session, client: HAClient | None, item: Item) -> list[dict[str, Any]]:
    """Every linked entity with its current state.  Never fails: Home
    Assistant being unreachable or an entity gone shows as unavailable."""
    prof = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    rows: list[tuple[str, str, str | None]] = []
    if prof is not None:
        if prof.hours_entity_id:
            rows.append((prof.hours_entity_id, "runtime", None))
        if prof.cycles_entity_id:
            rows.append((prof.cycles_entity_id, "usage", None))
    rows += [(e.entity_id, e.purpose, e.label) for e in db.scalars(
        select(EquipmentEntity).where(EquipmentEntity.item_id == item.id).order_by(EquipmentEntity.id))]
    out = []
    for eid, purpose, label in rows:
        state, error = None, None
        if client is None:
            error = "Home Assistant isn't reachable from the App"
        else:
            try:
                state = client.state(eid)
                if state is None:
                    error = "Home Assistant no longer has this entity"
            except HAUnavailable:
                error = "Home Assistant didn't answer"
        out.append({"entity_id": eid, "purpose": purpose, "purpose_label": PURPOSES[purpose]["label"], "label": label,
                    "meter": purpose in ("runtime", "usage"), "state": _state_out(state), "error": error})
    return out

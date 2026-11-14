"""Take it / put away.

*Take it* marks an item as with you (status ``in_use``) without moving it:
its record still says where it lives.  *Put away* at a scanned place then
either puts it back (it's that item's place, or the home it was
temporarily moved from) or, somewhere new, moves it there for good or just
this once (a temporary move that remembers the usual place).
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor
from ..models import Item, ItemStatus
from ..schemas import MoveIn
from .inventory import Conflict, move_item, return_item_home, set_status
from .placement import Resolver, validate_item_target

TAKEN_PREFIX = "Taken by "


def taken_note(actor: Actor) -> str:
    return f"{TAKEN_PREFIX}{actor.name}"[:1000]


def is_taken(item: Item) -> bool:
    return item.status is ItemStatus.in_use and (item.status_note or "").startswith(TAKEN_PREFIX)


def take(db: Session, actor: Actor, item: Item) -> Item:
    if item.deleted_at is not None:
        raise Conflict("This item has been deleted")
    if item.status is ItemStatus.on_loan:
        raise Conflict("This item is on loan; mark the loan returned first")
    if is_taken(item):
        return item
    return set_status(db, actor, item, ItemStatus.in_use, taken_note(actor))


def taken_items(db: Session) -> list[Item]:
    return list(db.scalars(
        select(Item).where(Item.deleted_at.is_(None), Item.status == ItemStatus.in_use,
                           Item.status_note.like(f"{TAKEN_PREFIX}%")).order_by(Item.updated_at.desc())
    ))


Mode = Literal["auto", "move", "temporary"]


def put_away(db: Session, actor: Actor, item: Item, container_id: int | None, location_id: int | None,
             mode: Mode = "auto") -> dict[str, Any]:
    """Returns ``{"result": "back" | "home" | "moved" | "temporary" | "choose"}``.
    ``choose``: the place is new to this item and ``mode`` was ``auto``;
    nothing changed, ask "move it here, or just this once?"."""
    if container_id is None and location_id is None:
        raise Conflict("Scan a box or shelf label to put it away")
    validate_item_target(db, container_id, location_id)
    if item.status is ItemStatus.on_loan:
        raise Conflict("This item is on loan; mark the loan returned first")
    target = (container_id, location_id)
    here = (item.container_id, item.location_id)
    home = (item.home_container_id, item.home_location_id)
    if target == here:
        if item.status in (ItemStatus.in_use, ItemStatus.missing):
            set_status(db, actor, item, ItemStatus.stored, "Put away")
        return {"result": "back", "item": item}
    if home != (None, None) and target == home:
        return {"result": "home", "item": return_item_home(db, actor, item)}
    if mode == "auto":
        return {"result": "choose", "item": item}
    if item.status in (ItemStatus.in_use, ItemStatus.missing):
        set_status(db, actor, item, ItemStatus.stored, None)
    moved = move_item(db, actor, item, MoveIn(container_id=container_id, location_id=location_id,
                                             temporary=mode == "temporary", note="Put away"))
    return {"result": "temporary" if mode == "temporary" else "moved", "item": moved}


def destination(item: Item) -> tuple[int | None, int | None]:
    """Where a taken item goes back: its usual place when it was moved just
    for now, otherwise where its record says it lives."""
    if item.home_container_id is not None or item.home_location_id is not None:
        return item.home_container_id, item.home_location_id
    return item.container_id, item.location_id


def route(r: Resolver, items: list[Item]) -> list[dict[str, Any]]:
    """Put-away route: the items grouped by where they go, one stop per place,
    in walking order (room by room, then shelf by shelf, following the tree)."""
    stops: dict[tuple[int | None, int | None], dict[str, Any]] = {}
    for it in items:
        dest = destination(it)
        if dest not in stops:
            where = r.describe(*dest)
            room = r.room(*dest)
            stops[dest] = {"container_id": dest[0], "location_id": dest[1], "path": where["path"],
                           "room": room.name if room else None,
                           "_key": [p.casefold() for p in where["path"].split(" › ")] if dest != (None, None) else ["\uffff"],
                           "items": []}
        stops[dest]["items"].append(it)
    ordered = sorted(stops.values(), key=lambda s: s["_key"])
    for n, s in enumerate(ordered, 1):
        s.pop("_key")
        s["stop"] = n
    return ordered

"""Photo inventory: AI analysis of a container or shelf photo → reviewed
inventory updates.

A scan targets either a container (photo of its open contents) or a
location such as a shelf, bench or wall (photo of what is on it; new items
become loose items there).

Workflow: upload a photo → ``ContainerScan`` row
(status pending) → AI analysis stores *suggestions* (status ready) → the
user reviews/edits/adds/removes → confirm applies the reviewed list.

Re-scanning: each suggestion is matched against what the container already
holds so the review screen proposes "already here (n)" / "increase" rather
than creating duplicates.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..events import queue_event
from ..models import Attachment, Container, ContainerScan, IdentificationStatus, Item, ItemStatus, Location, ScanStatus
from ..schemas import ContainerIn, ItemCreate, ItemPatch, LocationIn, ScanConfirmIn
from . import inventory as inv
from .placement import Resolver
from .textmatch import stem


def _norm(s: str | None) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(stem(w) for w in s.split())


def similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    wa, wb = set(a.split()), set(b.split())
    jacc = len(wa & wb) / len(wa | wb)
    return max(ratio, jacc)


def match_existing(suggestions: list[dict[str, Any]], items: list[Item]) -> list[dict[str, Any]]:
    used: set[int] = set()
    out = []
    for s in suggestions:
        best, best_score = None, 0.0
        for it in items:
            if it.id in used:
                continue
            score = similarity(s.get("name", ""), it.product.name)
            if s.get("model_number") and it.product.model_number and _norm(s["model_number"]) == _norm(it.product.model_number):
                score = 1.0
            if score > best_score:
                best, best_score = it, score
        entry = dict(s)
        if best is not None and best_score >= 0.72:
            used.add(best.id)
            entry["match"] = {
                "item_id": best.id,
                "name": best.product.name,
                "quantity": best.quantity,
                "similarity": round(best_score, 2),
            }
            qty = int(s.get("quantity") or 1)
            entry["proposed_action"] = "increase" if qty > best.quantity else "skip"
        else:
            entry["match"] = None
            entry["proposed_action"] = "add"
        out.append(entry)
    return out


def scan_out(scan: ContainerScan, db: Session | None = None) -> dict[str, Any]:
    photo = db.get(Attachment, scan.photo_id) if (db is not None and scan.photo_id) else None
    suggestions = scan.suggestions or []
    if db is not None and not db.info.get("can_view_customers", True):
        # Matches were worked out by whoever took the photo; don't name items
        # this user can't see (the read-only privacy filter hides them).
        suggestions = [
            {**sg, "match": None} if sg.get("match") and db.get(Item, sg["match"].get("item_id")) is None else sg
            for sg in suggestions
        ]
    return {
        "id": scan.id,
        "container_id": scan.container_id,
        "location_id": scan.location_id,
        "status": scan.status.value,
        "provider": scan.provider,
        "model": scan.model,
        "suggestions": suggestions,
        # Layout the AI saw (racks and their shelves) and the existing places in the area.
        "units": (scan.raw_response or {}).get("units") or [],
        "places": area_places(db, scan) if db is not None else [],
        "error": scan.error,
        "photo": inv.attachment_out(photo),
        "photo_id": scan.photo_id,
        "created_at": scan.created_at.isoformat(),
        "confirmed_at": scan.confirmed_at.isoformat() if scan.confirmed_at else None,
    }


def _area_ids(db: Session, scan: ContainerScan) -> set[int]:
    """The scanned location and every place under it (its racks and shelves)."""
    return Resolver.load(db).descendant_location_ids(scan.location_id) if scan.location_id is not None else set()


def _current_items(db: Session, scan: ContainerScan) -> list[Item]:
    """What the scanned place already holds (for matching): a container's own
    items, or the loose items anywhere in a location's area."""
    stmt = select(Item).where(Item.deleted_at.is_(None))
    if scan.container_id is not None:
        stmt = stmt.where(Item.container_id == scan.container_id)
    else:
        stmt = stmt.where(Item.location_id.in_(_area_ids(db, scan)), Item.container_id.is_(None))
    return list(db.scalars(stmt))


def _current_containers(db: Session, scan: ContainerScan) -> list[Container]:
    stmt = select(Container).where(Container.deleted_at.is_(None))
    if scan.container_id is not None:
        stmt = stmt.where(Container.parent_container_id == scan.container_id)
    else:
        stmt = stmt.where(Container.location_id.in_(_area_ids(db, scan)))
    return list(db.scalars(stmt))


def _match_containers(entries: list[dict[str, Any]], containers: list[Container]) -> None:
    """Boxes already recorded here are proposed as "already recorded"."""
    used: set[int] = set()
    for e in entries:
        if e.get("kind") != "container":
            continue
        best, score = None, 0.0
        for c in containers:
            if c.id in used:
                continue
            sc = similarity(e.get("name", ""), c.name)
            if sc > score:
                best, score = c, sc
        e["match"] = None
        e["proposed_action"] = "add"
        if best is not None and score >= 0.8:
            used.add(best.id)
            e["container_match"] = {"container_id": best.id, "name": best.name, "number": best.number}
            e["proposed_action"] = "skip"
    # Second pass: the AI often describes the same plain box differently each
    # time ("Plastic container" / "Plastic storage bin").  A box that isn't
    # matched yet is taken to be one already recorded here when both are
    # plain boxes of a similar kind - shown as "already recorded" in the
    # review, where it can be changed to "add".
    for e in entries:
        if e.get("kind") != "container" or e.get("container_match"):
            continue
        best, score = None, 0.0
        for c in containers:
            if c.id in used or not (_plain_box(c.name) and _plain_box(e.get("name", ""))):
                continue
            sc = similarity(e.get("name", ""), c.name) + (0.3 if _box_word(c.name) == _box_word(e.get("name", "")) else 0)
            if sc > score:
                best, score = c, sc
        if best is not None and score >= 0.55:
            used.add(best.id)
            e["container_match"] = {"container_id": best.id, "name": best.name, "number": best.number}
            e["proposed_action"] = "skip"


_BOX_WORDS = ("box", "bin", "tote", "crate", "container", "case", "basket", "tub", "tray", "bag", "drawer", "chest")


def _box_word(name: str) -> str | None:
    words = _norm(name).split()
    return next((w for w in reversed(words) if w in _BOX_WORDS), None)


def _plain_box(name: str) -> bool:
    """A box named only by what it looks like ("Clear plastic tote"), not by a label."""
    words = _norm(name).split()
    plain = {"plastic", "clear", "black", "blue", "grey", "gray", "white", "red", "green", "large", "small", "big",
             "storage", "cardboard", "metal", "wooden", "wood", "lidded", "stackable", "translucent", "opaque",
             "medium", "tall", "short", "yellow", "orange", "brown", "dark", "light", "with", "lid"}
    return bool(words) and _box_word(name) is not None and all(w in plain or w in _BOX_WORDS for w in words)


def area_places(db: Session, scan: ContainerScan) -> list[dict[str, Any]]:
    """The scanned location and the places under it, for "put on" choices."""
    if scan.location_id is None:
        return []
    r = Resolver.load(db)
    ids = r.descendant_location_ids(scan.location_id)
    return sorted(
        ({"id": i, "name": r.locations[i].name, "parent_id": r.locations[i].parent_id, "path": r.location_path(i)}
         for i in ids if i in r.locations),
        key=lambda p: (p["id"] != scan.location_id, p["path"].lower()),
    )


def analyse(db: Session, scan: ContainerScan, ai, image: bytes) -> ContainerScan:
    if scan.container_id is not None:
        container = db.get(Container, scan.container_id)
        hint, shelf = (f"Container: {container.name}" if container else None), False
    else:
        loc = db.get(Location, scan.location_id)
        hint, shelf = (f"Place: {loc.name}" + (f" ({loc.kind})" if loc.kind else "") if loc else None), True
    scan.status = ScanStatus.analysing
    db.commit()
    current_items, current_boxes = _current_items(db, scan), _current_containers(db, scan)
    known = [c.name for c in current_boxes] + [i.product.name for i in current_items]
    try:
        res = (ai.identify_area(image, hint=hint, known=known) if shelf
               else ai.identify_container(image, hint=hint, known=known))
    except Exception as err:  # noqa: BLE001 - failures are recorded, nothing is lost
        scan.status = ScanStatus.failed
        scan.error = str(err)[:1000]
        db.commit()
        return scan
    entries = res.data["items"]
    things = [e for e in entries if e.get("kind", "item") == "item"]
    boxes = [e for e in entries if e.get("kind") == "container"]
    matched = match_existing(things, current_items)
    _match_containers(boxes, current_boxes)
    scan.suggestions = matched + boxes
    scan.raw_response = {"notes": res.data.get("notes"), "elapsed_ms": res.elapsed_ms, "units": res.data.get("units") or []}
    scan.provider, scan.model = res.provider, res.model
    scan.status = ScanStatus.ready
    scan.error = None
    db.commit()
    return scan


def confirm(db: Session, actor: Actor, scan: ContainerScan, data: ScanConfirmIn, crop=None) -> dict[str, Any]:
    """Apply the reviewed list.  ``crop(box)`` (optional) returns a new photo
    Attachment of that part of the scan photo, for each new item's picture."""
    if scan.status in (ScanStatus.confirmed, ScanStatus.discarded):
        raise inv.Conflict("This scan has already been handled")
    if scan.container_id is not None:
        container, location = inv.get_container(db, scan.container_id), None
        place_name, here = container.name, "in this container"
    else:
        container, location = None, _get_location(db, scan.location_id)
        place_name, here = location.name, "here"
    held = {i.id for i in _current_items(db, scan)}
    created, increased, removed, boxes = [], [], [], []
    place_for = _make_places(db, actor, scan, data) if location is not None else None
    for entry in data.items:
        if entry.action == "skip":
            continue
        target = place_for(entry) if place_for is not None else None
        if entry.kind == "container":
            if not entry.name:
                raise inv.Conflict("Each new box needs a name")
            box = inv.create_container(db, actor, ContainerIn(
                name=entry.name, container_type=entry.container_type, description=entry.description,
                location_id=target if container is None else None,
                parent_container_id=container.id if container is not None else None,
            ))
            boxes.append(box.id)
            pic = crop(entry.box) if (crop is not None and entry.box) else None
            if pic is not None:
                pic.container_id = box.id
            continue
        if entry.action == "increase":
            if not entry.existing_item_id:
                raise inv.Conflict("An existing item is required to increase quantity")
            item = inv.get_item(db, entry.existing_item_id)
            if item.id not in held:
                raise inv.Conflict(f"That item is not {here}")
            if entry.quantity != item.quantity:
                inv.update_item(db, actor, item, ItemPatch(quantity=entry.quantity))
                increased.append(item.id)
            continue
        if not entry.name and not entry.product_id:
            raise inv.Conflict("Each new item needs a name")
        items = inv.create_items(
            db,
            actor,
            ItemCreate(
                product_id=entry.product_id,
                name=entry.name,
                description=entry.description,
                category=entry.category,
                manufacturer=entry.manufacturer,
                model_number=entry.model_number,
                quantity=entry.quantity,
                container_id=container.id if container else None,
                location_id=target if location else None,
                notes=entry.notes,
                identification_status=(IdentificationStatus.confirmed if entry.identified
                                       else IdentificationStatus.pending),
                ai_metadata={"source": "container_scan", "scan_id": scan.id, "confidence": entry.confidence,
                             "provider": scan.provider, "model": scan.model},
            ),
        )
        created.extend(i.id for i in items)
        # Each new item gets its own cropped picture when the AI said where it
        # is; otherwise the whole photo.
        own = crop(entry.box) if (crop is not None and entry.box) else None
        for i in items:
            if own is not None and i.primary_photo_id is None:
                own.item_id = i.id
                i.primary_photo_id = own.id
                own = None
            elif i.primary_photo_id is None and scan.photo_id:
                i.primary_photo_id = scan.photo_id
    missing = []
    for item_id in dict.fromkeys(data.missing_item_ids):
        item = inv.get_item(db, item_id)
        if item.id not in held:
            raise inv.Conflict(f"That item is not {here}")
        if item_id in data.remove_item_ids:
            continue
        if item.status is not ItemStatus.missing:
            inv.set_status(db, actor, item, ItemStatus.missing, f"Not seen when {place_name} was re-scanned")
        missing.append(item_id)
    for item_id in data.remove_item_ids:
        item = inv.get_item(db, item_id)
        if item.id not in held:
            raise inv.Conflict(f"That item is not {here}")
        inv.delete_item(db, actor, item, reason=f"Not found when {place_name} was re-scanned")
        removed.append(item_id)
    scan.status = ScanStatus.confirmed
    scan.confirmed_at = datetime.now(UTC)
    summary = f"Confirmed photo inventory of {place_name}: {len(created)} added, {len(increased)} updated, {len(removed)} removed"
    if boxes:
        summary += f", {len(boxes)} box{'es' if len(boxes) != 1 else ''} added"
    if missing:
        summary += f", {len(missing)} marked missing"
    after = {"scan_id": scan.id, "created": created, "increased": increased, "removed": removed, "missing": missing,
             "containers": boxes}
    if container is not None:
        audit.record(db, actor, "inventory_updated", "container", container.id, summary, after=after,
                     related_containers=[container.id])
        queue_event(
            db,
            "container_inventory_updated",
            {"container_id": container.id, "name": container.name, "added": len(created), "updated": len(increased),
             "removed": len(removed), "user": actor.name},
        )
    else:
        audit.record(db, actor, "inventory_updated", "location", location.id, summary, after=after)
    return {"created": created, "increased": increased, "removed": removed, "missing": missing, "containers": boxes}


def _make_places(db: Session, actor: Actor, scan: ContainerScan, data: ScanConfirmIn):
    """Create the new racks/shelves, then return ``entry -> location id``.

    Places must stay inside the scanned area: existing ones under the scanned
    location, new ones under those (or under other new ones listed earlier).
    """
    area = _area_ids(db, scan)
    made: dict[str, int] = {}
    for p in data.new_locations:
        if p.key in made:
            raise inv.Conflict(f"Two new places share the key {p.key}")
        if p.parent_key is not None:
            if p.parent_key not in made:
                raise inv.Conflict(f"New place {p.name}: its parent must be listed before it")
            parent = made[p.parent_key]
        else:
            parent = p.parent_id if p.parent_id is not None else scan.location_id
            if parent not in area:
                raise inv.Conflict(f"New place {p.name} must be inside the photographed area")
        loc = inv.create_location(db, actor, LocationIn(name=p.name, parent_id=parent, kind=p.kind))
        made[p.key] = loc.id

    def place_for(entry) -> int:
        if entry.location_key is not None:
            if entry.location_key not in made:
                raise inv.Conflict(f"Unknown new place {entry.location_key}")
            return made[entry.location_key]
        if entry.location_id is not None:
            if entry.location_id not in area:
                raise inv.Conflict("Things can only be put on places inside the photographed area")
            return entry.location_id
        return scan.location_id

    return place_for


def _get_location(db: Session, location_id: int | None) -> Location:
    loc = db.get(Location, location_id) if location_id is not None else None
    if loc is None or loc.deleted_at is not None:
        raise inv.NotFound("Location not found")
    return loc


def attach_scan_photo(db: Session, scan: ContainerScan) -> Attachment | None:
    return db.get(Attachment, scan.photo_id) if scan.photo_id else None


# ---------------------------------------------------------------------------
# Redoing cut-outs
# ---------------------------------------------------------------------------

CUT_CAPTION = "Cut from photo inventory"


def _cut_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def recrop(db: Session, actor: Actor, scan: ContainerScan, ai, image: bytes, crop, media) -> dict[str, Any]:
    """Make the cut-out photos of a confirmed photo inventory again.

    The AI is asked once more where each thing is in the same photo; every
    item and box this inventory created gets a new cut-out of its own entry.
    One whose entry can't be found (or has no position) loses its cut-out and
    shows the whole photo instead.  Pictures a person added or chose since are
    left alone.
    """
    from ..models import HistoryEvent

    if scan.status != ScanStatus.confirmed:
        raise inv.Conflict("Only a confirmed photo inventory can have its cut-outs redone")
    if scan.container_id is not None:
        container = db.get(Container, scan.container_id)
        hint, area = (f"Container: {container.name}" if container else None), False
    else:
        loc = db.get(Location, scan.location_id)
        hint, area = (f"Place: {loc.name}" if loc else None), True
    res = ai.identify_area(image, hint=hint) if area else ai.identify_container(image, hint=hint)
    entries = [e for e in res.data["items"] if e.get("box")]

    # Filtered in SQLite (JSON fields) rather than loading every item and the whole history.
    items = list(db.scalars(select(Item).where(Item.deleted_at.is_(None),
                                               func.json_extract(Item.ai_metadata, "$.scan_id") == scan.id)))
    entity = ("container", scan.container_id) if scan.container_id is not None else ("location", scan.location_id)
    box_ids: list[int] = []
    for ev in db.scalars(select(HistoryEvent).where(HistoryEvent.action == "inventory_updated",
                                                    HistoryEvent.entity_type == entity[0],
                                                    HistoryEvent.entity_id == entity[1],
                                                    func.json_extract(HistoryEvent.after, "$.scan_id") == scan.id)):
        box_ids.extend((ev.after or {}).get("containers") or [])
    boxes = [c for c in (db.get(Container, i) for i in dict.fromkeys(box_ids)) if c is not None and c.deleted_at is None]

    used: set[int] = set()

    def best(name: str, kind: str) -> dict[str, Any] | None:
        target, pick, score = _cut_name(name), None, 0.0
        for n, e in enumerate(entries):
            if n in used:
                continue
            r = SequenceMatcher(None, target, _cut_name(e.get("name") or "")).ratio()
            if e.get("kind", "item") == kind:
                r += 0.05
            if r > score:
                pick, score = n, r
        if pick is None or score < 0.7:
            return None
        used.add(pick)
        return entries[pick]

    def drop(att: Attachment) -> None:
        for rel in (att.stored_path, att.thumb_path):
            media.delete(rel)
        db.delete(att)

    fixed = removed = kept = 0
    for item in items:
        old = db.get(Attachment, item.primary_photo_id) if item.primary_photo_id else None
        if old is None or old.caption != CUT_CAPTION:
            kept += 1  # a picture chosen by a person stays
            continue
        entry = best(item.product.name, "item")
        new = crop(entry["box"]) if entry else None
        if new is not None:
            new.item_id = item.id
            item.primary_photo_id = new.id
            fixed += 1
        else:
            item.primary_photo_id = scan.photo_id
            removed += 1
        db.flush()
        drop(old)
    for box in boxes:
        olds = [a for a in db.scalars(select(Attachment).where(Attachment.container_id == box.id,
                                                                Attachment.caption == CUT_CAPTION))]
        if not olds:
            kept += 1
            continue
        entry = best(box.name, "container")
        new = crop(entry["box"]) if entry else None
        if new is not None:
            new.container_id = box.id
            fixed += 1
        else:
            removed += 1
        for a in olds:
            drop(a)
    db.flush()
    place = scan.container_id and "container" or "location"
    audit.record(db, actor, "scan_recropped", place, scan.container_id or scan.location_id,
                 f"Redid photo inventory cut-outs: {fixed} new, {removed} replaced by the whole photo",
                 related_containers=[scan.container_id] if scan.container_id else ())
    return {"fixed": fixed, "whole_photo": removed, "unchanged": kept}

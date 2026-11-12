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

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..events import queue_event
from ..models import Attachment, Container, ContainerScan, IdentificationStatus, Item, Location, ScanStatus
from ..schemas import ItemCreate, ItemPatch, ScanConfirmIn
from . import inventory as inv


def _norm(s: str | None) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(w[:-1] if w.endswith("s") and len(w) > 3 else w for w in s.split())


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
        "error": scan.error,
        "photo": inv.attachment_out(photo),
        "photo_id": scan.photo_id,
        "created_at": scan.created_at.isoformat(),
        "confirmed_at": scan.confirmed_at.isoformat() if scan.confirmed_at else None,
    }


def _current_items(db: Session, scan: ContainerScan) -> list[Item]:
    """What the scanned place already holds directly (for matching)."""
    stmt = select(Item).where(Item.deleted_at.is_(None))
    if scan.container_id is not None:
        stmt = stmt.where(Item.container_id == scan.container_id)
    else:
        stmt = stmt.where(Item.location_id == scan.location_id, Item.container_id.is_(None))
    return list(db.scalars(stmt))


def analyse(db: Session, scan: ContainerScan, ai, image: bytes) -> ContainerScan:
    if scan.container_id is not None:
        container = db.get(Container, scan.container_id)
        hint, shelf = (f"Container: {container.name}" if container else None), False
    else:
        loc = db.get(Location, scan.location_id)
        hint, shelf = (f"Place: {loc.name}" + (f" ({loc.kind})" if loc.kind else "") if loc else None), True
    scan.status = ScanStatus.analysing
    db.commit()
    try:
        res = ai.identify_container(image, hint=hint, shelf=shelf)
    except Exception as err:  # noqa: BLE001 - failures are recorded, nothing is lost
        scan.status = ScanStatus.failed
        scan.error = str(err)[:1000]
        db.commit()
        return scan
    scan.suggestions = match_existing(res.data["items"], _current_items(db, scan))
    scan.raw_response = {"notes": res.data.get("notes"), "elapsed_ms": res.elapsed_ms}
    scan.provider, scan.model = res.provider, res.model
    scan.status = ScanStatus.ready
    scan.error = None
    db.commit()
    return scan


def confirm(db: Session, actor: Actor, scan: ContainerScan, data: ScanConfirmIn) -> dict[str, Any]:
    if scan.status in (ScanStatus.confirmed, ScanStatus.discarded):
        raise inv.Conflict("This scan has already been handled")
    if scan.container_id is not None:
        container, location = inv.get_container(db, scan.container_id), None
        place_name, here = container.name, "in this container"
    else:
        container, location = None, _get_location(db, scan.location_id)
        place_name, here = location.name, "here"
    held = {i.id for i in _current_items(db, scan)}
    created, increased, removed = [], [], []
    for entry in data.items:
        if entry.action == "skip":
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
                location_id=location.id if location else None,
                notes=entry.notes,
                identification_status=IdentificationStatus.confirmed,
                ai_metadata={"source": "container_scan", "scan_id": scan.id, "confidence": entry.confidence,
                             "provider": scan.provider, "model": scan.model},
            ),
        )
        created.extend(i.id for i in items)
        # Link the container photo to the new item records so they have a picture.
        if scan.photo_id:
            for i in items:
                if i.primary_photo_id is None:
                    i.primary_photo_id = scan.photo_id
    for item_id in data.remove_item_ids:
        item = inv.get_item(db, item_id)
        if item.id not in held:
            raise inv.Conflict(f"That item is not {here}")
        inv.delete_item(db, actor, item, reason=f"Not found when {place_name} was re-scanned")
        removed.append(item_id)
    scan.status = ScanStatus.confirmed
    scan.confirmed_at = datetime.now(UTC)
    summary = f"Confirmed photo inventory of {place_name}: {len(created)} added, {len(increased)} updated, {len(removed)} removed"
    after = {"scan_id": scan.id, "created": created, "increased": increased, "removed": removed}
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
    return {"created": created, "increased": increased, "removed": removed}


def _get_location(db: Session, location_id: int | None) -> Location:
    loc = db.get(Location, location_id) if location_id is not None else None
    if loc is None or loc.deleted_at is not None:
        raise inv.NotFound("Location not found")
    return loc


def attach_scan_photo(db: Session, scan: ContainerScan) -> Attachment | None:
    return db.get(Attachment, scan.photo_id) if scan.photo_id else None

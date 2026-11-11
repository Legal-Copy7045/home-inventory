"""Container photo analysis → reviewed inventory updates.

Workflow: upload a photo of the open container → ``ContainerScan`` row
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
from ..models import Attachment, Container, ContainerScan, IdentificationStatus, Item, ScanStatus
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
    return {
        "id": scan.id,
        "container_id": scan.container_id,
        "status": scan.status.value,
        "provider": scan.provider,
        "model": scan.model,
        "suggestions": scan.suggestions or [],
        "error": scan.error,
        "photo": inv.attachment_out(photo),
        "photo_id": scan.photo_id,
        "created_at": scan.created_at.isoformat(),
        "confirmed_at": scan.confirmed_at.isoformat() if scan.confirmed_at else None,
    }


def analyse(db: Session, scan: ContainerScan, ai, image: bytes) -> ContainerScan:
    container = db.get(Container, scan.container_id)
    scan.status = ScanStatus.analysing
    db.commit()
    try:
        res = ai.identify_container(image, hint=f"Container: {container.name}" if container else None)
    except Exception as err:  # noqa: BLE001 - failures are recorded, nothing is lost
        scan.status = ScanStatus.failed
        scan.error = str(err)[:1000]
        db.commit()
        return scan
    current = db.scalars(select(Item).where(Item.container_id == scan.container_id, Item.deleted_at.is_(None))).all()
    scan.suggestions = match_existing(res.data["items"], list(current))
    scan.raw_response = {"notes": res.data.get("notes"), "elapsed_ms": res.elapsed_ms}
    scan.provider, scan.model = res.provider, res.model
    scan.status = ScanStatus.ready
    scan.error = None
    db.commit()
    return scan


def confirm(db: Session, actor: Actor, scan: ContainerScan, data: ScanConfirmIn) -> dict[str, Any]:
    if scan.status in (ScanStatus.confirmed, ScanStatus.discarded):
        raise inv.Conflict("This scan has already been handled")
    container = inv.get_container(db, scan.container_id)
    created, increased, removed = [], [], []
    for entry in data.items:
        if entry.action == "skip":
            continue
        if entry.action == "increase":
            if not entry.existing_item_id:
                raise inv.Conflict("An existing item is required to increase quantity")
            item = inv.get_item(db, entry.existing_item_id)
            if item.container_id != container.id:
                raise inv.Conflict("That item is not in this container")
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
                container_id=container.id,
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
        if item.container_id != container.id:
            raise inv.Conflict("That item is not in this container")
        inv.delete_item(db, actor, item, reason="Not found when container was re-scanned")
        removed.append(item_id)
    scan.status = ScanStatus.confirmed
    scan.confirmed_at = datetime.now(UTC)
    audit.record(
        db,
        actor,
        "inventory_updated",
        "container",
        container.id,
        f"Confirmed photo inventory of {container.name}: {len(created)} added, {len(increased)} updated, {len(removed)} removed",
        after={"scan_id": scan.id, "created": created, "increased": increased, "removed": removed},
        related_containers=[container.id],
    )
    queue_event(
        db,
        "container_inventory_updated",
        {"container_id": container.id, "name": container.name, "added": len(created), "updated": len(increased),
         "removed": len(removed), "user": actor.name},
    )
    return {"created": created, "increased": increased, "removed": removed}


def attach_scan_photo(db: Session, scan: ContainerScan) -> Attachment | None:
    return db.get(Attachment, scan.photo_id) if scan.photo_id else None

"""NFC tags (and any other physical tag that carries a link).

A tag stores ``<app address>/n/<ref>``.  Opening it goes through Home
Assistant's login like every other page; :func:`resolve` then turns the
opaque ``ref`` into the record.  Unknown, inactive and lost tags all give
the same answer, so a tag reveals nothing on its own.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import TAG_TARGETS, Container, EquipmentProfile, Item, Kit, Location, Tag

ACTIONS: dict[str, tuple[str, ...]] = {
    "item": ("view", "move", "adjust", "service", "meter", "repair", "return"),
    "container": ("view", "scan"),
    "location": ("view",),
    "kit": ("view",),
}
REF_RE = re.compile(r"(?:/n/|[?&]n=|^HINV:N:)([A-Za-z0-9_-]{16,40})(?=$|[/?#&])", re.I)
NOT_RECOGNISED = "Tag not recognised"


class TagError(ValueError):
    pass


class TagNotFound(LookupError):
    pass


def new_ref(db: Session) -> str:
    while True:
        ref = secrets.token_urlsafe(16)  # 22 characters, 128 bits
        if not db.scalar(select(Tag.id).where(Tag.ref == ref)):
            return ref


def parse_ref(text: str) -> str | None:
    """Accept a full tag link, ``…?n=ref`` or a bare reference."""
    text = (text or "").strip()
    m = REF_RE.search(text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{16,40}", text):
        return text
    return None


def normalise_hardware_id(value: str | None) -> str | None:
    if not value:
        return None
    hw = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    return hw or None


_TARGETS = {"item": Item, "container": Container, "location": Location, "kit": Kit}


def _target(db: Session, target_type: str, target_id: int):
    if target_type not in TAG_TARGETS:
        raise TagError("Tags can be put on items, containers, locations and kits")
    obj = db.get(_TARGETS[target_type], target_id)
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        raise TagError(f"That {target_type} doesn't exist")
    return obj


def target_name(db: Session, target_type: str, target_id: int) -> str | None:
    obj = db.get(_TARGETS[target_type], target_id)
    if obj is None or getattr(obj, "deleted_at", None) is not None:
        return None
    if target_type == "item":
        return obj.product.name + (f" ({obj.label})" if obj.label else "")
    if target_type == "container":
        return (f"#{obj.number} " if obj.number else "") + obj.name
    return obj.name


def _audit_target(db: Session, target_type: str, target_id: int) -> str:
    """Target name for history; never names customer equipment (see customers.audit_name)."""
    if target_type == "item":
        from .customers import audit_name

        item = db.get(Item, target_id)
        return audit_name(db, item) if item is not None and item.deleted_at is None else "a deleted record"
    return target_name(db, target_type, target_id) or "a deleted record"


def duplicate_of(db: Session, hardware_id: str | None, exclude_id: int | None = None) -> Tag | None:
    hw = normalise_hardware_id(hardware_id)
    if not hw:
        return None
    stmt = select(Tag).where(Tag.hardware_id == hw, Tag.status == "active")
    if exclude_id:
        stmt = stmt.where(Tag.id != exclude_id)
    return db.scalar(stmt)


def _check_action(target_type: str, action: str | None) -> str:
    action = action or "view"
    if action not in ACTIONS[target_type]:
        raise TagError(f"'{action}' isn't available for a {target_type}")
    return action


def create(db: Session, actor: Actor, target_type: str, target_id: int, label: str | None, hardware_id: str | None,
           default_action: str | None, replaces: Tag | None = None) -> Tag:
    _target(db, target_type, target_id)
    hw = normalise_hardware_id(hardware_id)
    dup = duplicate_of(db, hw)
    if dup is not None:
        raise TagError(f"This tag is already assigned to {dup.target_type} “{target_name(db, dup.target_type, dup.target_id) or '?'}”. "
                       "Reassign or deactivate it there first.")
    tag = Tag(ref=new_ref(db), target_type=target_type, target_id=target_id, label=label, hardware_id=hw,
              default_action=_check_action(target_type, default_action), created_by_id=actor.user_id,
              replaces_tag_id=replaces.id if replaces else None)
    db.add(tag)
    db.flush()
    audit.record(db, actor, "tag_assigned", target_type, target_id,
                 f"NFC tag {'replaced' if replaces else 'assigned'} for {target_name(db, target_type, target_id)}")
    return tag


def update(db: Session, actor: Actor, tag: Tag, data: dict[str, Any]) -> Tag:
    if "target_type" in data or "target_id" in data:
        ttype = data.get("target_type", tag.target_type)
        tid = data.get("target_id", tag.target_id)
        _target(db, ttype, tid)
        if (ttype, tid) != (tag.target_type, tag.target_id):
            before = _audit_target(db, tag.target_type, tag.target_id)
            tag.target_type, tag.target_id = ttype, tid
            if tag.default_action not in ACTIONS[ttype]:
                tag.default_action = "view"
            audit.record(db, actor, "tag_reassigned", ttype, tid, f"NFC tag moved from {before} to {_audit_target(db, ttype, tid)}")
    if "status" in data and data["status"] != tag.status:
        if data["status"] == "active" and duplicate_of(db, tag.hardware_id, exclude_id=tag.id):
            raise TagError("Another active tag has the same chip")
        tag.status = data["status"]
        audit.record(db, actor, f"tag_{tag.status}", tag.target_type, tag.target_id,
                     f"NFC tag for {target_name(db, tag.target_type, tag.target_id)} marked {tag.status}")
    if "label" in data:
        tag.label = data["label"]
    if "default_action" in data and data["default_action"]:
        tag.default_action = _check_action(tag.target_type, data["default_action"])
    if "hardware_id" in data:
        hw = normalise_hardware_id(data["hardware_id"])
        if hw and tag.status == "active" and duplicate_of(db, hw, exclude_id=tag.id):
            raise TagError("Another active tag has the same chip")
        tag.hardware_id = hw
    return tag


def replace(db: Session, actor: Actor, tag: Tag, hardware_id: str | None) -> Tag:
    """Lost or damaged tag: retire it and issue a new reference for the same record."""
    tag.status = "lost"
    db.flush()
    return create(db, actor, tag.target_type, tag.target_id, tag.label, hardware_id, tag.default_action, replaces=tag)


def resolve(db: Session, ref_or_link: str, hardware_id: str | None = None) -> Tag:
    ref = parse_ref(ref_or_link)
    tag = db.scalar(select(Tag).where(Tag.ref == ref)) if ref else None
    if tag is None and hardware_id:
        tag = duplicate_of(db, hardware_id)
    if tag is None or tag.status != "active":
        raise TagNotFound(NOT_RECOGNISED)
    if target_name(db, tag.target_type, tag.target_id) is None:
        raise TagNotFound("This tag's item was deleted (see Trash)")
    tag.last_scanned_at = datetime.now(UTC)
    tag.scan_count = (tag.scan_count or 0) + 1
    return tag


def link_for(tag: Tag, base: str) -> str:
    return f"{base.rstrip('/')}/n/{tag.ref}" if base else f"HINV:N:{tag.ref}"


def tag_out(db: Session, tag: Tag, base: str) -> dict[str, Any]:
    return {
        "id": tag.id,
        "ref": tag.ref,
        "url": link_for(tag, base),
        "target_type": tag.target_type,
        "target_id": tag.target_id,
        "target_name": target_name(db, tag.target_type, tag.target_id),
        "label": tag.label,
        "status": tag.status,
        "hardware_id": tag.hardware_id,
        "default_action": tag.default_action,
        "actions": list(ACTIONS[tag.target_type]),
        "created_at": tag.created_at.isoformat(),
        "last_scanned_at": tag.last_scanned_at.isoformat() if tag.last_scanned_at else None,
        "scan_count": tag.scan_count,
        "replaces_tag_id": tag.replaces_tag_id,
        # Managed equipment opens its equipment page (checklist, hours, manual...).
        "equipment": tag.target_type == "item" and db.scalar(select(EquipmentProfile.id).where(
            EquipmentProfile.item_id == tag.target_id, EquipmentProfile.managed.is_(True))) is not None,
    }

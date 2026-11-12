"""Item relationships (accessory of, component of, …) and kits.

Items stay independent: their own placement, QR code, search entry and
history.  A relation or kit membership is only a link; removing it never
touches the items, and moving one item never moves another.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import RELATION_TYPES, Container, Item, ItemRelation, ItemStatus, Kit, KitMember, Location
from .customers import audit_name
from .placement import Resolver

# label seen from the "from" item, label seen from the "to" item
LABELS: dict[str, tuple[str, str]] = {
    "accessory_of": ("Accessory of", "Accessory"),
    "component_of": ("Component of", "Component"),
    "compatible_with": ("Compatible with", "Compatible with"),
    "stored_with": ("Stored with", "Stored with"),
    "replacement_for": ("Replacement for", "Replaced by"),
}


class RelationError(ValueError):
    pass


def relation_types() -> list[dict[str, str]]:
    return [{"key": k, "label": v[0], "inverse": v[1]} for k, v in LABELS.items()] + [
        {"key": "custom", "label": "Other (your own words)", "inverse": ""}
    ]


def _live_item(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise RelationError("Item not found")
    return item


def add_relation(db: Session, actor: Actor, from_id: int, to_id: int, relation_type: str,
                 custom_label: str | None, note: str | None) -> ItemRelation:
    if relation_type not in RELATION_TYPES:
        raise RelationError("Unknown relationship type")
    if from_id == to_id:
        raise RelationError("An item can't be related to itself")
    if relation_type == "custom" and not (custom_label or "").strip():
        raise RelationError("Describe the relationship")
    if relation_type != "custom":
        custom_label = None
    a, b = _live_item(db, from_id), _live_item(db, to_id)
    symmetric = relation_type in ("compatible_with", "stored_with")
    dup = select(ItemRelation).where(ItemRelation.relation_type == relation_type,
                                     ItemRelation.custom_label.is_(None) if custom_label is None else ItemRelation.custom_label == custom_label)
    pairs = [(a.id, b.id)] + ([(b.id, a.id)] if symmetric else [])
    if db.scalar(dup.where(or_(*[(ItemRelation.from_item_id == x) & (ItemRelation.to_item_id == y) for x, y in pairs]))):
        raise RelationError("These items are already linked that way")
    rel = ItemRelation(from_item_id=a.id, to_item_id=b.id, relation_type=relation_type, custom_label=custom_label,
                       note=note, created_by_id=actor.user_id)
    db.add(rel)
    db.flush()
    label = custom_label or LABELS[relation_type][0].lower()
    audit.record(db, actor, "related", "item", a.id, f"{audit_name(db, a)} is {label} {audit_name(db, b)}")
    return rel


def remove_relation(db: Session, actor: Actor, rel: ItemRelation) -> None:
    a, b = db.get(Item, rel.from_item_id), db.get(Item, rel.to_item_id)
    db.delete(rel)
    audit.record(db, actor, "unrelated", "item", a.id, f"Unlinked {audit_name(db, a)} and {audit_name(db, b)}")


def _brief(item: Item, r: Resolver) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.product.name,
        "label": item.label,
        "quantity": item.quantity,
        "status": item.status.value,
        "placement": r.describe_item(item)["path"],
    }


def relations_for(db: Session, item_ids: list[int], r: Resolver | None = None) -> dict[int, list[dict[str, Any]]]:
    """Relations of each item, labelled from that item's side, both directions."""
    if not item_ids:
        return {}
    r = r or Resolver.load(db)
    rows = db.scalars(select(ItemRelation).where(or_(ItemRelation.from_item_id.in_(item_ids),
                                                     ItemRelation.to_item_id.in_(item_ids)))).all()
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in item_ids}
    cache: dict[int, Item | None] = {}

    def item(i: int) -> Item | None:
        if i not in cache:
            it = db.get(Item, i)
            cache[i] = it if it is not None and it.deleted_at is None else None
        return cache[i]

    for rel in rows:
        for me, other_id, side in ((rel.from_item_id, rel.to_item_id, 0), (rel.to_item_id, rel.from_item_id, 1)):
            if me not in out:
                continue
            other = item(other_id)
            if other is None:
                continue  # the other item is in the trash: hide, keep the link
            if rel.relation_type == "custom":
                label = rel.custom_label if side == 0 else f"{rel.custom_label} (reverse)"
            else:
                label = LABELS[rel.relation_type][side]
            out[me].append({"id": rel.id, "type": rel.relation_type, "label": label, "direction": "out" if side == 0 else "in",
                            "note": rel.note, "item": _brief(other, r)})
    for lst in out.values():
        lst.sort(key=lambda x: (x["label"], x["item"]["name"].lower()))
    return out


# ---------------------------------------------------------------------------
# Kits
# ---------------------------------------------------------------------------


def _check_home(db: Session, container_id: int | None, location_id: int | None) -> None:
    if container_id and location_id:
        raise RelationError("Choose a container or a location, not both")
    if container_id and (db.get(Container, container_id) is None):
        raise RelationError("Container not found")
    if location_id and (db.get(Location, location_id) is None):
        raise RelationError("Location not found")


def create_kit(db: Session, actor: Actor, data: dict[str, Any]) -> Kit:
    _check_home(db, data.get("container_id"), data.get("location_id"))
    kit = Kit(**data)
    db.add(kit)
    db.flush()
    audit.record(db, actor, "kit_added", "kit", kit.id, f"Created kit {kit.name}")
    return kit


def update_kit(db: Session, actor: Actor, kit: Kit, data: dict[str, Any]) -> Kit:
    cid = data.get("container_id", kit.container_id)
    lid = data.get("location_id", kit.location_id)
    if "container_id" in data and data["container_id"]:
        lid = None
        data["location_id"] = None
    elif "location_id" in data and data["location_id"]:
        cid = None
        data["container_id"] = None
    _check_home(db, cid, lid)
    for k, v in data.items():
        setattr(kit, k, v)
    audit.record(db, actor, "kit_changed", "kit", kit.id, f"Updated kit {kit.name}")
    return kit


def delete_kit(db: Session, actor: Actor, kit: Kit) -> None:
    """Soft delete; member items are untouched."""
    kit.deleted_at = datetime.now(UTC)
    audit.record(db, actor, "kit_deleted", "kit", kit.id, f"Deleted kit {kit.name}")


def add_member(db: Session, actor: Actor, kit: Kit, item_id: int, quantity: int, optional: bool, note: str | None) -> KitMember:
    item = _live_item(db, item_id)
    if any(m.item_id == item.id for m in kit.members):
        raise RelationError(f"{item.product.name} is already in this kit")
    m = KitMember(item_id=item.id, quantity=quantity, optional=optional, note=note)
    kit.members.append(m)
    db.flush()
    audit.record(db, actor, "kit_member_added", "kit", kit.id, f"Added {audit_name(db, item)} to kit {kit.name}")
    return m


def remove_member(db: Session, actor: Actor, kit: Kit, member: KitMember) -> None:
    item = db.get(Item, member.item_id)
    kit.members.remove(member)
    audit.record(db, actor, "kit_member_removed", "kit", kit.id, f"Removed {audit_name(db, item)} from kit {kit.name}")


def _member_state(item: Item | None, member: KitMember, kit: Kit, r: Resolver) -> tuple[str, dict[str, Any] | None]:
    if item is None or item.deleted_at is not None:
        return "missing", None
    placement = r.describe_item(item)
    if item.status is ItemStatus.missing:
        return "missing", placement
    if any(lo.returned_at is None for lo in item.loans):
        return "on_loan", placement
    if item.quantity < member.quantity:
        return "short", placement
    if kit.container_id:
        with_kit = kit.container_id in [c["id"] for c in placement["container_path"]]
    elif kit.location_id:
        with_kit = kit.location_id in [loc["id"] for loc in placement["location_path"]]
    else:
        with_kit = True
    return ("present" if with_kit else "elsewhere"), placement


STATE_LABELS = {"present": "With the kit", "elsewhere": "Stored separately", "on_loan": "On loan", "short": "Not enough",
                "missing": "Missing"}


def kit_out(db: Session, kit: Kit, detail: bool = False, r: Resolver | None = None) -> dict[str, Any]:
    r = r or Resolver.load(db)
    members = []
    counts = {k: 0 for k in STATE_LABELS}
    for m in kit.members:
        item = db.get(Item, m.item_id)
        state, placement = _member_state(item, m, kit, r)
        counts[state] += 1
        if detail:
            members.append({
                "id": m.id, "item_id": m.item_id, "quantity": m.quantity, "optional": m.optional, "note": m.note,
                "state": state, "state_label": STATE_LABELS[state],
                "item": {"id": item.id, "name": item.product.name, "label": item.label, "quantity": item.quantity,
                         "placement": placement["path"] if placement else None} if item and item.deleted_at is None else None,
            })
    home = r.describe(kit.container_id, kit.location_id) if (kit.container_id or kit.location_id) else None
    required_missing = sum(1 for m in kit.members
                           if not m.optional and _member_state(db.get(Item, m.item_id), m, kit, r)[0] in ("missing", "short"))
    out = {
        "id": kit.id,
        "name": kit.name,
        "description": kit.description,
        "container_id": kit.container_id,
        "location_id": kit.location_id,
        "home": home["path"] if home else None,
        "member_count": len(kit.members),
        "counts": counts,
        "complete": required_missing == 0,
    }
    if detail:
        out["members"] = members
    return out


def kits_for_item(db: Session, item_id: int) -> list[dict[str, Any]]:
    kits = db.scalars(select(Kit).join(KitMember, KitMember.kit_id == Kit.id)
                      .where(KitMember.item_id == item_id, Kit.deleted_at.is_(None)).order_by(Kit.name)).all()
    return [{"id": k.id, "name": k.name} for k in kits]

"""Item relationships and kits."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import Item, ItemRelation, Kit, KitMember
from ..schemas import BulkKitIn, KitIn, KitMemberIn, KitMemberPatch, KitPatch, RelationIn
from ..services import relations as rel_svc

router = APIRouter(prefix="/api/v1")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except rel_svc.RelationError as err:
        raise HTTPException(422, str(err)) from err


def _item(db: Session, item_id: int) -> Item:
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, "Item not found")
    return item


def _kit(db: Session, kit_id: int) -> Kit:
    kit = db.get(Kit, kit_id)
    if kit is None or kit.deleted_at is not None:
        raise HTTPException(404, "Kit not found")
    return kit


@router.get("/relation-types")
def relation_types(actor: Actor = Depends(current_actor)):
    return rel_svc.relation_types()


@router.get("/items/{item_id}/relations")
def item_relations(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    _item(db, item_id)
    return {"relations": rel_svc.relations_for(db, [item_id])[item_id], "kits": rel_svc.kits_for_item(db, item_id)}


@router.post("/items/{item_id}/relations", status_code=201)
def add_relation(item_id: int, data: RelationIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    _item(db, item_id)
    a, b = (item_id, data.other_item_id) if data.direction == "this_to_other" else (data.other_item_id, item_id)
    _run(rel_svc.add_relation, db, actor, a, b, data.relation_type, data.custom_label, data.note)
    db.commit()
    return {"relations": rel_svc.relations_for(db, [item_id])[item_id], "kits": rel_svc.kits_for_item(db, item_id)}


@router.delete("/relations/{relation_id}", status_code=204)
def remove_relation(relation_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    rel = db.get(ItemRelation, relation_id)
    if rel is None:
        raise HTTPException(404, "Relationship not found")
    rel_svc.remove_relation(db, actor, rel)
    db.commit()


@router.get("/kits")
def list_kits(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    kits = db.scalars(select(Kit).where(Kit.deleted_at.is_(None)).order_by(Kit.name)).all()
    return [rel_svc.kit_out(db, k) for k in kits]


@router.post("/kits", status_code=201)
def create_kit(data: KitIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    kit = _run(rel_svc.create_kit, db, actor, data.model_dump(exclude={"item_ids"}))
    for iid in data.item_ids:
        _run(rel_svc.add_member, db, actor, kit, iid, 1, False, None)
    db.commit()
    return rel_svc.kit_out(db, kit, detail=True)


@router.get("/kits/{kit_id}")
def get_kit(kit_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return rel_svc.kit_out(db, _kit(db, kit_id), detail=True)


@router.patch("/kits/{kit_id}")
def change_kit(kit_id: int, data: KitPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    kit = _kit(db, kit_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("name") is None:
        fields.pop("name", None)
    _run(rel_svc.update_kit, db, actor, kit, fields)
    db.commit()
    return rel_svc.kit_out(db, kit, detail=True)


@router.delete("/kits/{kit_id}", status_code=204)
def delete_kit(kit_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    rel_svc.delete_kit(db, actor, _kit(db, kit_id))
    db.commit()


@router.post("/kits/{kit_id}/members", status_code=201)
def add_member(kit_id: int, data: KitMemberIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    kit = _kit(db, kit_id)
    _run(rel_svc.add_member, db, actor, kit, data.item_id, data.quantity, data.optional, data.note)
    db.commit()
    return rel_svc.kit_out(db, kit, detail=True)


@router.post("/kits/{kit_id}/members/bulk")
def add_members(kit_id: int, data: BulkKitIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Add several items to a kit (multi-select).  Items already in the kit
    are skipped; anything else wrong rolls the whole request back."""
    kit = _kit(db, kit_id)
    present = {m.item_id for m in kit.members}
    added = []
    for item_id in dict.fromkeys(data.item_ids):
        _item(db, item_id)
        if item_id in present:
            continue
        _run(rel_svc.add_member, db, actor, kit, item_id, 1, False, None)
        added.append(item_id)
    db.commit()
    return {"added": added, "kit": rel_svc.kit_out(db, kit, detail=True)}


@router.patch("/kits/{kit_id}/members/{member_id}")
def change_member(kit_id: int, member_id: int, data: KitMemberPatch, actor: Actor = Depends(require_member),
                  db: Session = Depends(get_db)):
    kit = _kit(db, kit_id)
    m = db.get(KitMember, member_id)
    if m is None or m.kit_id != kit.id:
        raise HTTPException(404, "Kit member not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None or k == "note":
            setattr(m, k, v)
    db.commit()
    return rel_svc.kit_out(db, kit, detail=True)


@router.delete("/kits/{kit_id}/members/{member_id}", status_code=204)
def remove_member(kit_id: int, member_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    kit = _kit(db, kit_id)
    m = db.get(KitMember, member_id)
    if m is None or m.kit_id != kit.id:
        raise HTTPException(404, "Kit member not found")
    rel_svc.remove_member(db, actor, kit, m)
    db.commit()

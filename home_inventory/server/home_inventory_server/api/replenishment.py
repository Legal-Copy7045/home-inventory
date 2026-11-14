"""Replenishment, product sources and the shopping list."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import Product, ProductSource, ShoppingListItem
from ..schemas import HandoffIn, PurchasedIn, RestockIn, ShoppingAddIn, ShoppingPatch, SourceIn, SourcePatch
from ..services import inventory as inv
from ..services import replenishment as repl
from ..services import retailers

router = APIRouter(prefix="/api/v1")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except repl.ReplenishmentError as err:
        raise HTTPException(422, str(err)) from err
    except inv.Conflict as err:
        raise HTTPException(409, str(err)) from err


def _product_detail(db: Session, product_id: int) -> dict:
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise HTTPException(404, "Product not found")
    settings = repl.retailer_settings(db)
    prof = repl.profile_for(db, product_id)
    sources = db.scalars(select(ProductSource).where(ProductSource.product_id == product_id)
                         .order_by(ProductSource.preferred.desc(), ProductSource.id)).all()
    open_items = {product_id: [x for x in repl.open_list(db) if x.product_id == product_id]}
    return {
        "profile": {
            "enabled": prof.enabled if prof else False,
            "configured": prof is not None,
            "target_stock": prof.target_stock if prof else None,
            "reorder_quantity": prof.reorder_quantity if prof else None,
            "unit": prof.unit if prof else None,
            "preferred_name": prof.preferred_name if prof else None,
            "notes": prof.notes if prof else None,
            "last_purchased_on": prof.last_purchased_on.isoformat() if prof and prof.last_purchased_on else None,
            "last_price": float(prof.last_price) if prof and prof.last_price is not None else None,
        },
        "status": repl.product_row(db, product, settings, open_items),
        "sources": [repl.source_out(s, settings, product.name) for s in sources],
        "providers": retailers.providers_out(),
    }


@router.get("/retailers")
def list_retailers(actor: Actor = Depends(current_actor)):
    return retailers.providers_out()


@router.get("/replenishment")
def replenishment_dashboard(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return repl.dashboard(db)


@router.get("/products/{product_id}/restock")
def get_restock(product_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return _product_detail(db, product_id)


@router.put("/products/{product_id}/restock")
def save_restock(product_id: int, data: RestockIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    fields = data.model_dump(exclude_unset=True)
    if fields.get("enabled") is None:
        fields.pop("enabled", None)
    _run(repl.save_profile, db, actor, product_id, fields)
    db.commit()
    return _product_detail(db, product_id)


@router.post("/products/{product_id}/sources", status_code=201)
def add_source(product_id: int, data: SourceIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    fields = data.model_dump(exclude_unset=True)
    if fields.get("preferred") is None:
        fields.pop("preferred", None)
    fields["retailer"] = data.retailer
    _run(repl.save_source, db, actor, product_id, fields)
    db.commit()
    return _product_detail(db, product_id)


@router.patch("/sources/{source_id}")
def change_source(source_id: int, data: SourcePatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    src = db.get(ProductSource, source_id)
    if src is None:
        raise HTTPException(404, "Shop not found")
    fields = {k: v for k, v in data.model_dump(exclude_unset=True).items()
              if not (k in ("retailer", "pack_size", "preferred") and v is None)}
    _run(repl.save_source, db, actor, src.product_id, fields, src)
    db.commit()
    return _product_detail(db, src.product_id)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    src = db.get(ProductSource, source_id)
    if src is None:
        raise HTTPException(404, "Shop not found")
    db.delete(src)
    db.commit()


@router.get("/shopping-list")
def shopping_list(include_closed: bool = Query(False), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return repl.list_out(db, include_closed)


@router.post("/shopping-list", status_code=201)
def add_to_list(data: ShoppingAddIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    _run(repl.add_to_list, db, actor, data.product_id, data.quantity, data.source_id, data.note)
    db.commit()
    return repl.list_out(db)


def _entry(db: Session, entry_id: int) -> ShoppingListItem:
    e = db.get(ShoppingListItem, entry_id)
    if e is None:
        raise HTTPException(404, "Shopping list entry not found")
    return e


@router.patch("/shopping-list/{entry_id}")
def change_entry(entry_id: int, data: ShoppingPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    fields = data.model_dump(exclude_unset=True)
    for k in ("quantity", "status"):
        if fields.get(k) is None:
            fields.pop(k, None)
    _run(repl.update_entry, db, actor, _entry(db, entry_id), fields)
    db.commit()
    return repl.list_out(db)


@router.post("/shopping-list/handoff")
def open_at_shop(data: HandoffIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Links to buy the chosen entries. They become 'opened at shop', never 'in cart'."""
    entries = [_entry(db, i) for i in data.entry_ids]
    out = _run(repl.handoff, db, actor, entries)
    db.commit()
    return out


@router.post("/shopping-list/{entry_id}/purchased")
def purchased(entry_id: int, data: PurchasedIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    _run(repl.mark_purchased, db, actor, _entry(db, entry_id), data.quantity, data.unit_price, data.add_to_stock, data.item_id)
    db.commit()
    return repl.list_out(db)


@router.get("/replenishment/demand")
def replenishment_demand(days: int = Query(30, ge=1, le=365), actor: Actor = Depends(current_actor),
                         db: Session = Depends(get_db)):
    """Everything that needs buying, one line per part (maintenance due in
    ``days``, below-minimum stock, repair reservations), by retailer."""
    from ..services import demand

    return demand.demand(db, days)

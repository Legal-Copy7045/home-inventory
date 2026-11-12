"""Replenishment: what to buy, where, and the shopping list.

Stock is the household total across all holdings of a product (customer
equipment excluded, items marked missing excluded).  The minimum is the
product's low-stock threshold (``min_quantity``); the target defaults to
the minimum.

Suggested quantity::

    if stock >= target: 0
    else: max(target - stock, reorder_quantity or 0), rounded up to whole packs

e.g. stock 1, minimum 2, target 5 -> 4.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..events import queue_event
from ..models import Item, ItemStatus, Product, ProductSource, ReplenishmentProfile, Setting, ShoppingListItem
from . import retailers
from .customers import household_only
from .inventory import adjust_quantity, product_total

OPEN = ("listed", "handed_off", "in_cart")


class ReplenishmentError(ValueError):
    pass


def retailer_settings(db: Session) -> dict[str, str]:
    out = {}
    for key in ("amazon_domain", "amazon_associate_tag"):
        s = db.get(Setting, key)
        if s and s.value:
            out[key] = str(s.value)
    return out


def _money(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# Profiles and sources
# ---------------------------------------------------------------------------


def _product(db: Session, product_id: int) -> Product:
    p = db.get(Product, product_id)
    if p is None or p.deleted_at is not None:
        raise ReplenishmentError("Product not found")
    return p


def profile_for(db: Session, product_id: int) -> ReplenishmentProfile | None:
    return db.scalar(select(ReplenishmentProfile).where(ReplenishmentProfile.product_id == product_id))


def save_profile(db: Session, actor: Actor, product_id: int, data: dict[str, Any]) -> ReplenishmentProfile:
    product = _product(db, product_id)
    prof = profile_for(db, product_id)
    if prof is None:
        prof = ReplenishmentProfile(product_id=product_id)
        db.add(prof)
    if "min_quantity" in data:
        product.min_quantity = data.pop("min_quantity")
    for k, v in data.items():
        setattr(prof, k, v)
    if prof.target_stock is not None and product.min_quantity is not None and prof.target_stock < product.min_quantity:
        raise ReplenishmentError("The restock level can't be below the minimum")
    db.flush()
    audit.record(db, actor, "restock_settings", "item", _first_item_id(db, product_id) or 0, f"{product.name}: restock settings updated")
    return prof


def _first_item_id(db: Session, product_id: int) -> int | None:
    return db.scalar(select(Item.id).where(Item.product_id == product_id, Item.deleted_at.is_(None)).order_by(Item.id))


def source_out(s: ProductSource, settings: dict[str, str], term: str) -> dict[str, Any]:
    prov = retailers.provider(s.retailer)
    link, kind = retailers.link_for(s, term, settings)
    return {
        "id": s.id,
        "retailer": s.retailer,
        "retailer_label": s.retailer_name if s.retailer == "other" and s.retailer_name else prov.name,
        "url": s.url,
        "retailer_product_id": s.retailer_product_id,
        "gtin": s.gtin,
        "product_name": s.product_name,
        "brand_model": s.brand_model,
        "pack_size": s.pack_size,
        "estimated_price": _money(s.estimated_price),
        "preferred": s.preferred,
        "notes": s.notes,
        "link": link,
        "link_kind": kind,
        "capabilities": prov.capabilities.as_dict(),
    }


def save_source(db: Session, actor: Actor, product_id: int, data: dict[str, Any], source: ProductSource | None = None) -> ProductSource:
    product = _product(db, product_id)
    retailer = data.get("retailer", source.retailer if source else "other")
    prov = retailers.provider(retailer)
    try:
        if "url" in data:
            data["url"] = prov.validate_url(data["url"])
        if "retailer_product_id" in data or ("url" in data and data["url"]):
            rid = data.get("retailer_product_id") or prov.id_from_url(data.get("url"))
            data["retailer_product_id"] = prov.normalise_id(rid)
    except ValueError as err:
        raise ReplenishmentError(str(err)) from err
    if source is None:
        if not db.scalar(select(ProductSource.id).where(ProductSource.product_id == product_id)):
            data.setdefault("preferred", True)  # the first source is the preferred one
        source = ProductSource(product_id=product_id, retailer=retailer)
        db.add(source)
    for k, v in data.items():
        setattr(source, k, v)
    db.flush()
    if source.preferred:
        for other in db.scalars(select(ProductSource).where(ProductSource.product_id == product_id, ProductSource.id != source.id)):
            other.preferred = False
    audit.record(db, actor, "restock_source", "item", _first_item_id(db, product_id) or 0,
                 f"{product.name}: buy from {source.retailer_name or prov.name}")
    return source


def preferred_source(db: Session, product_id: int) -> ProductSource | None:
    return db.scalar(select(ProductSource).where(ProductSource.product_id == product_id)
                     .order_by(ProductSource.preferred.desc(), ProductSource.id))


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def suggested_quantity(stock: int, minimum: int | None, target: int | None, reorder: int | None, pack: int = 1) -> int:
    target = target if target is not None else minimum
    if target is None or stock >= target:
        return 0
    qty = max(target - stock, reorder or 0)
    pack = max(pack, 1)
    return int(math.ceil(qty / pack) * pack)


def _state(stock: int, minimum: int | None, target: int | None) -> str:
    if minimum is not None and stock < minimum:
        return "below_minimum"
    t = target if target is not None else minimum
    if minimum is not None and t is not None and stock < t and stock <= minimum:
        return "at_minimum"
    if t is not None and stock < t:
        return "approaching"
    return "ok"


def product_row(db: Session, product: Product, settings: dict[str, str], open_items: dict[int, list[ShoppingListItem]]) -> dict[str, Any]:
    prof = profile_for(db, product.id)
    stock = product_total(db, product.id)
    src = preferred_source(db, product.id)
    target = prof.target_stock if prof else None
    suggestion = suggested_quantity(stock, product.min_quantity, target, prof.reorder_quantity if prof else None,
                                    src.pack_size if src else 1)
    listed = open_items.get(product.id, [])
    price = src.estimated_price if src and src.estimated_price is not None else (prof.last_price if prof else None)
    return {
        "product_id": product.id,
        "item_id": _first_item_id(db, product.id),
        "name": (prof.preferred_name if prof and prof.preferred_name else None) or product.name,
        "stock": stock,
        "minimum": product.min_quantity,
        "target": target if target is not None else product.min_quantity,
        "reorder_quantity": prof.reorder_quantity if prof else None,
        "unit": prof.unit if prof else None,
        "state": _state(stock, product.min_quantity, target),
        "suggested_quantity": suggestion,
        "estimated_cost": float(price * suggestion) if price is not None and suggestion else None,
        "source": source_out(src, settings, product.name) if src else None,
        "on_list": [{"id": x.id, "status": x.status, "quantity": x.quantity} for x in listed],
        "in_cart": any(x.status == "in_cart" for x in listed),
        "enabled": prof.enabled if prof else True,
        "last_purchased_on": prof.last_purchased_on.isoformat() if prof and prof.last_purchased_on else None,
    }


def dashboard(db: Session) -> dict[str, Any]:
    settings = retailer_settings(db)
    products = db.scalars(
        select(Product).where(Product.deleted_at.is_(None), Product.min_quantity.is_not(None))
        .where(Product.id.in_(select(Item.product_id).where(Item.deleted_at.is_(None), household_only())))
        .order_by(Product.name)
    ).all()
    tracked_ids = {p.id for p in products}
    extra = db.scalars(select(Product).join(ReplenishmentProfile, ReplenishmentProfile.product_id == Product.id)
                       .where(Product.deleted_at.is_(None), ReplenishmentProfile.enabled.is_(True))).all()
    products = list(products) + [p for p in extra if p.id not in tracked_ids]
    open_items: dict[int, list[ShoppingListItem]] = {}
    for x in db.scalars(select(ShoppingListItem).where(ShoppingListItem.status.in_(OPEN))):
        open_items.setdefault(x.product_id, []).append(x)
    rows = [product_row(db, p, settings, open_items) for p in products]
    rows = [r for r in rows if r["enabled"]]
    order = {"below_minimum": 0, "at_minimum": 1, "approaching": 2, "ok": 3}
    rows.sort(key=lambda r: (order[r["state"]], r["name"].lower()))
    needs = [r for r in rows if r["state"] != "ok"]
    return {
        "below_minimum": [r for r in rows if r["state"] == "below_minimum"],
        "approaching": [r for r in rows if r["state"] in ("at_minimum", "approaching")],
        "ok": [r for r in rows if r["state"] == "ok"],
        "needs_count": sum(1 for r in needs if r["state"] == "below_minimum"),
        "estimated_total": round(sum(r["estimated_cost"] or 0 for r in needs if not r["on_list"]), 2),
    }


# ---------------------------------------------------------------------------
# Shopping list
# ---------------------------------------------------------------------------


def _event(db: Session, actor: Actor, reason: str) -> None:
    queue_event(db, "shopping_list_updated", {"reason": reason, "open_items": len(open_list(db)), "user": actor.name})


def open_list(db: Session) -> list[ShoppingListItem]:
    return list(db.scalars(select(ShoppingListItem).where(ShoppingListItem.status.in_(OPEN)).order_by(ShoppingListItem.id)))


def add_to_list(db: Session, actor: Actor, product_id: int, quantity: int | None, source_id: int | None, note: str | None) -> ShoppingListItem:
    product = _product(db, product_id)
    existing = db.scalar(select(ShoppingListItem).where(ShoppingListItem.product_id == product_id,
                                                        ShoppingListItem.status.in_(OPEN)))
    if existing is not None:
        raise ReplenishmentError(f"{product.name} is already on the shopping list")
    src = db.get(ProductSource, source_id) if source_id else preferred_source(db, product_id)
    if src is not None and src.product_id != product_id:
        raise ReplenishmentError("That shop isn't set up for this product")
    if quantity is None:
        prof = profile_for(db, product_id)
        quantity = suggested_quantity(product_total(db, product_id), product.min_quantity, prof.target_stock if prof else None,
                                      prof.reorder_quantity if prof else None, src.pack_size if src else 1) or 1
    item = ShoppingListItem(product_id=product_id, source_id=src.id if src else None, quantity=quantity, note=note,
                            created_by_id=actor.user_id)
    db.add(item)
    db.flush()
    audit.record(db, actor, "shopping_added", "item", _first_item_id(db, product_id) or 0, f"Added {quantity} × {product.name} to the shopping list")
    _event(db, actor, "added")
    return item


def update_entry(db: Session, actor: Actor, entry: ShoppingListItem, data: dict[str, Any]) -> ShoppingListItem:
    if "source_id" in data and data["source_id"] is not None:
        src = db.get(ProductSource, data["source_id"])
        if src is None or src.product_id != entry.product_id:
            raise ReplenishmentError("That shop isn't set up for this product")
    status = data.get("status")
    if status == "in_cart" and entry.status not in ("handed_off", "listed", "in_cart"):
        raise ReplenishmentError("Only open items can be marked as in the cart")
    if status == "deferred" and not data.get("deferred_until") and not entry.deferred_until:
        data["deferred_until"] = None
    if status == "purchased":
        raise ReplenishmentError("Use 'mark purchased' to record a purchase")
    for k, v in data.items():
        setattr(entry, k, v)
    product = db.get(Product, entry.product_id)
    if status:
        audit.record(db, actor, f"shopping_{status}", "item", _first_item_id(db, entry.product_id) or 0,
                     f"{product.name}: {status.replace('_', ' ')}" + (" (confirmed by you)" if status == "in_cart" else ""))
    _event(db, actor, status or "changed")
    return entry


def handoff(db: Session, actor: Actor, entries: list[ShoppingListItem]) -> dict[str, Any]:
    """Links to buy these items. Marks them ``handed_off`` - never ``in_cart``."""
    settings = retailer_settings(db)
    by_retailer: dict[str, list[tuple[ShoppingListItem, ProductSource | None]]] = {}
    for e in entries:
        if e.status not in OPEN:
            raise ReplenishmentError("Only open shopping-list items can be opened at a shop")
        src = db.get(ProductSource, e.source_id) if e.source_id else None
        by_retailer.setdefault(src.retailer if src else "none", []).append((e, src))
    groups = []
    now = datetime.now(UTC)
    for retailer, rows in by_retailer.items():
        prov = retailers.provider(retailer) if retailer != "none" else None
        cart_url = None
        if prov is not None and prov.capabilities.cart_handoff:
            cart_url = prov.cart_handoff_url([(s, e.quantity) for e, s in rows if s is not None], settings)
        links = []
        for e, s in rows:
            product = db.get(Product, e.product_id)
            url, kind = retailers.link_for(s, product.name, settings) if s else (None, "none")
            links.append({"entry_id": e.id, "name": product.name, "quantity": e.quantity, "url": url, "link_kind": kind,
                          "in_cart_link": bool(cart_url and s and s.retailer_product_id)})
            if url or cart_url:
                e.status = "handed_off" if e.status == "listed" else e.status
                e.handed_off_at = now
        groups.append({
            "retailer": retailer,
            "retailer_label": prov.name if prov else "No shop set",
            "cart_url": cart_url,
            "cart_note": ("Amazon will show these items and ask you to confirm adding them to your cart. "
                          "Nothing is added or ordered until you do.") if cart_url else
                         ("This shop has no way for other apps to add to your cart. Open each product and add it yourself."
                          if prov else "Add a shop to these products to get links."),
            "links": links,
        })
    _event(db, actor, "opened_at_shop")
    return {"groups": groups}


def mark_purchased(db: Session, actor: Actor, entry: ShoppingListItem, quantity: int | None, unit_price: Decimal | None,
                   add_to_stock: bool, item_id: int | None) -> ShoppingListItem:
    if entry.status in ("purchased", "cancelled"):
        raise ReplenishmentError("This entry is already closed")
    product = db.get(Product, entry.product_id)
    qty = quantity or entry.quantity
    entry.status = "purchased"
    entry.purchased_at = datetime.now(UTC)
    entry.purchased_quantity = qty
    entry.purchase_price = unit_price
    prof = profile_for(db, product.id)
    if prof is None:
        prof = ReplenishmentProfile(product_id=product.id)
        db.add(prof)
    prof.last_purchased_on = date.today()
    if unit_price is not None:
        prof.last_price = unit_price
    if add_to_stock:
        target = db.get(Item, item_id) if item_id else db.scalar(
            select(Item).where(Item.product_id == product.id, Item.deleted_at.is_(None), Item.status != ItemStatus.missing,
                               household_only()).order_by(Item.id))
        if target is None or target.product_id != product.id or target.deleted_at is not None:
            raise ReplenishmentError("Choose which holding to add the purchase to")
        adjust_quantity(db, actor, target, qty, "Bought (shopping list)")
    audit.record(db, actor, "shopping_purchased", "item", _first_item_id(db, product.id) or 0,
                 f"Bought {qty} × {product.name}" + (" and added to stock" if add_to_stock else ""))
    _event(db, actor, "purchased")
    return entry


def entry_out(db: Session, e: ShoppingListItem, settings: dict[str, str]) -> dict[str, Any]:
    product = db.get(Product, e.product_id)
    src = db.get(ProductSource, e.source_id) if e.source_id else None
    return {
        "id": e.id,
        "product_id": e.product_id,
        "item_id": _first_item_id(db, e.product_id),
        "name": product.name,
        "quantity": e.quantity,
        "status": e.status,
        "deferred_until": e.deferred_until.isoformat() if e.deferred_until else None,
        "note": e.note,
        "stock": product_total(db, e.product_id),
        "source": source_out(src, settings, product.name) if src else None,
        "estimated_cost": float(src.estimated_price * e.quantity) if src and src.estimated_price is not None else None,
        "handed_off_at": e.handed_off_at.isoformat() if e.handed_off_at else None,
        "purchased_at": e.purchased_at.isoformat() if e.purchased_at else None,
        "purchased_quantity": e.purchased_quantity,
    }


def list_out(db: Session, include_closed: bool = False) -> dict[str, Any]:
    settings = retailer_settings(db)
    stmt = select(ShoppingListItem)
    if not include_closed:
        stmt = stmt.where(ShoppingListItem.status.in_(OPEN + ("deferred",)))
    entries = [entry_out(db, e, settings) for e in db.scalars(stmt.order_by(ShoppingListItem.id.desc()).limit(500))]
    groups: dict[str, dict[str, Any]] = {}
    for e in entries:
        key = e["source"]["retailer"] if e["source"] else "none"
        label = e["source"]["retailer_label"] if e["source"] else "No shop set"
        g = groups.setdefault(key, {"retailer": key, "retailer_label": label, "entries": [], "estimated_total": 0.0,
                                    "capabilities": e["source"]["capabilities"] if e["source"] else None})
        g["entries"].append(e)
        if e["estimated_cost"] and e["status"] in OPEN:
            g["estimated_total"] = round(g["estimated_total"] + e["estimated_cost"], 2)
    return {"groups": list(groups.values()), "open_count": sum(1 for e in entries if e["status"] in OPEN),
            "providers": retailers.providers_out()}


def counts(db: Session) -> dict[str, int]:
    d = dashboard(db)
    return {"replenishment_needed": d["needs_count"], "shopping_list_open": len(open_list(db))}

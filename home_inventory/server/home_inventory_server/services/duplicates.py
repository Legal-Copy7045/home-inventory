"""Possible duplicate products, and merging two into one.

Quick photos and shelf scans make near-duplicates ("Drill" and "Cordless
drill", the same barcode added twice).  :func:`find` suggests pairs; the
user decides.  :func:`merge` moves everything from one product onto the
other - every holding, photo, shop link, restock setting and shopping-list
line - fills the kept product's blank details from the other, and then
deletes the merged product.  Holdings (items) are kept separate: they may
be in different places.

A pair the user marks "not a duplicate" is remembered and not suggested
again.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import Attachment, Item, Product, ProductSource, ReplenishmentProfile, Setting, ShoppingListItem
from .scans import _norm, similarity

DISMISSED_KEY = "duplicates_dismissed"
FILL_FIELDS = ("description", "category_id", "manufacturer", "model_number", "gtin", "barcode", "sku", "url",
               "min_quantity", "catalog_metadata")


class MergeError(ValueError):
    pass


def _dismissed(db: Session) -> set[str]:
    s = db.get(Setting, DISMISSED_KEY)
    return set(s.value or []) if s else set()


def _key(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


def dismiss(db: Session, a: int, b: int) -> None:
    s = db.get(Setting, DISMISSED_KEY)
    keys = _dismissed(db) | {_key(a, b)}
    if s is None:
        db.add(Setting(key=DISMISSED_KEY, value=sorted(keys)))
    else:
        s.value = sorted(keys)


def find(db: Session, threshold: float = 0.8, limit: int = 100) -> list[dict[str, Any]]:
    products = list(db.scalars(select(Product).where(Product.deleted_at.is_(None))))
    holdings = defaultdict(int)
    for pid, qty in db.execute(select(Item.product_id, Item.quantity).where(Item.deleted_at.is_(None))):
        holdings[pid] += qty
    # Only products someone still holds are worth merging.
    products = [p for p in products if p.id in holdings]
    skip = _dismissed(db)
    found: dict[str, dict[str, Any]] = {}

    def add(a: Product, b: Product, score: float, reason: str) -> None:
        k = _key(a.id, b.id)
        if k in skip or (k in found and found[k]["score"] >= score):
            return
        first, second = (a, b) if a.id < b.id else (b, a)
        found[k] = {"key": k, "score": round(score, 2), "reason": reason,
                    "products": [_brief(first, holdings[first.id]), _brief(second, holdings[second.id])]}

    # Same barcode / model: certain.  Name similarity: compared within
    # buckets of the same first word so this stays quick on big inventories.
    by_code: dict[str, list[Product]] = defaultdict(list)
    by_model: dict[str, list[Product]] = defaultdict(list)
    by_word: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        if p.gtin or p.barcode:
            by_code[p.gtin or p.barcode].append(p)
        if p.model_number and p.manufacturer:
            by_model[f"{_norm(p.manufacturer)}|{_norm(p.model_number)}"].append(p)
        words = _norm(p.name).split()
        for w in {words[0], words[-1]} if words else set():
            by_word[w].append(p)
    for group in by_code.values():
        for a, b in combinations(group, 2):
            add(a, b, 1.0, "Same barcode")
    for group in by_model.values():
        for a, b in combinations(group, 2):
            add(a, b, 0.99, "Same maker and model")
    for group in by_word.values():
        if len(group) > 300:
            continue  # a very common word ("set", "box"): not a useful bucket
        for a, b in combinations(group, 2):
            s = similarity(a.name, b.name)
            if s >= threshold and not (a.model_number and b.model_number and _norm(a.model_number) != _norm(b.model_number)):
                add(a, b, s, "Similar name")
    return sorted(found.values(), key=lambda d: -d["score"])[:limit]


def _brief(p: Product, qty: int) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "manufacturer": p.manufacturer, "model_number": p.model_number,
            "category": p.category.name if p.category else None, "barcode": p.barcode, "quantity": qty,
            "needs_details": p.identification_status.value == "pending"}


def merge(db: Session, actor: Actor, keep: Product, other: Product) -> dict[str, Any]:
    if keep.id == other.id:
        raise MergeError("Choose two different products")
    if keep.deleted_at is not None or other.deleted_at is not None:
        raise MergeError("Product not found")
    moved_items = list(db.scalars(select(Item.id).where(Item.product_id == other.id)))
    db.execute(update(Item).where(Item.product_id == other.id).values(product_id=keep.id))
    db.execute(update(Attachment).where(Attachment.product_id == other.id).values(product_id=keep.id))
    db.execute(update(ProductSource).where(ProductSource.product_id == other.id).values(product_id=keep.id))
    db.execute(update(ShoppingListItem).where(ShoppingListItem.product_id == other.id).values(product_id=keep.id))
    # One restock setting per product: keep the kept product's, else adopt the other's.
    theirs = db.scalar(select(ReplenishmentProfile).where(ReplenishmentProfile.product_id == other.id))
    if theirs is not None:
        if db.scalar(select(ReplenishmentProfile.id).where(ReplenishmentProfile.product_id == keep.id)) is None:
            theirs.product_id = keep.id
        else:
            db.delete(theirs)
    filled = []
    for f in FILL_FIELDS:
        if getattr(keep, f) in (None, "", []) and getattr(other, f) not in (None, "", []):
            setattr(keep, f, getattr(other, f))
            filled.append(f)
    if keep.identification_status.value == "pending" and other.identification_status.value != "pending":
        keep.identification_status = other.identification_status
        if keep.name == "Item to identify":
            keep.name = other.name
    other.deleted_at = datetime.now(UTC)
    db.flush()
    db.expire_all()
    audit.record(db, actor, "products_merged", "product", keep.id,
                 f"Merged “{other.name}” into “{keep.name}” ({len(moved_items)} holding{'s' if len(moved_items) != 1 else ''} moved)",
                 after={"merged_product_id": other.id, "items": moved_items, "filled": filled})
    return {"kept": keep.id, "merged": other.id, "items_moved": moved_items, "filled": filled}

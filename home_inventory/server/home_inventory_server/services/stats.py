"""Aggregate statistics (Home Assistant sensors and the dashboard) and the
warranty-expiry notifier."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..events import queue_event
from ..models import Container, HistoryEvent, IdentificationStatus, Item, ItemStatus, Loan, Location, Product, Setting
from .customers import household_only
from .inventory import item_out, loan_out
from .placement import Resolver


def currency(db: Session) -> str:
    s = db.get(Setting, "currency")
    return (s.value if s and s.value else None) or "USD"


def stats(db: Session, warranty_days: int = 30, expiry_days: int = 14) -> dict[str, Any]:
    today = date.today()
    # Household totals: customer equipment is left out.
    live_items = select(Item).join(Product).where(Item.deleted_at.is_(None), Product.deleted_at.is_(None), household_only())
    items = db.scalars(live_items).all()
    total_qty = sum(i.quantity for i in items)
    value = sum(((i.purchase_price or Decimal(0)) * i.quantity for i in items), Decimal(0))
    valued = sum(1 for i in items if i.purchase_price is not None)
    loans = db.scalars(select(Loan).where(Loan.returned_at.is_(None))).all()
    loans = [lo for lo in loans if lo.item.deleted_at is None]
    overdue = [lo for lo in loans if lo.expected_return and lo.expected_return < today]
    soon = today + timedelta(days=warranty_days)
    return {
        "total_items": total_qty,
        "item_records": len(items),
        "products": db.scalar(select(func.count()).select_from(Product).where(Product.deleted_at.is_(None))) or 0,
        "containers": db.scalar(select(func.count()).select_from(Container).where(Container.deleted_at.is_(None))) or 0,
        "locations": db.scalar(select(func.count()).select_from(Location).where(Location.deleted_at.is_(None))) or 0,
        "total_value": float(value),
        "valued_records": valued,
        "currency": currency(db),
        "on_loan": sum(lo.quantity for lo in loans),
        "loans_active": len(loans),
        "loans_overdue": len(overdue),
        "temporarily_relocated": sum(1 for i in items if i.status is ItemStatus.temporarily_relocated)
        + (db.scalar(select(func.count()).select_from(Container).where(Container.temporarily_moved.is_(True), Container.deleted_at.is_(None))) or 0),
        "in_use": sum(1 for i in items if i.status is ItemStatus.in_use),
        "missing": sum(1 for i in items if i.status is ItemStatus.missing),
        "warranties_expiring": sum(1 for i in items if i.warranty_expires and today <= i.warranty_expires <= soon),
        "expiring_soon": sum(1 for i in items if i.quantity > 0 and i.expires_on and today <= i.expires_on <= today + timedelta(days=expiry_days)),
        "expired": sum(1 for i in items if i.quantity > 0 and i.expires_on and i.expires_on < today),
        "expiry_window_days": expiry_days,
        "needs_details": sum(1 for i in items if i.product.identification_status is IdentificationStatus.pending),
        "unsorted_photos": sum(
            1 for i in items
            if i.container_id is None and i.location_id is None and isinstance((i.ai_metadata or {}).get("capture"), dict)
        ),
        "low_stock": db.scalar(select(func.count()).select_from(Product).where(Product.deleted_at.is_(None), Product.low_stock_notified_at.is_not(None))) or 0,
        "warranty_window_days": warranty_days,
        "last_activity": (db.scalar(select(func.max(HistoryEvent.ts))) or None),
    }


def dashboard(db: Session, warranty_days: int = 30, expiry_days: int = 14) -> dict[str, Any]:
    r = Resolver.load(db)
    today = date.today()
    st = stats(db, warranty_days, expiry_days)
    if st["last_activity"]:
        st["last_activity"] = st["last_activity"].isoformat()
    recent = db.scalars(select(HistoryEvent).order_by(HistoryEvent.ts.desc(), HistoryEvent.id.desc()).limit(12)).all()
    loans = db.scalars(
        select(Loan).where(Loan.returned_at.is_(None)).order_by(Loan.expected_return.is_(None), Loan.expected_return).limit(10)
    ).all()
    soon = today + timedelta(days=max(warranty_days, 90))
    warr = db.scalars(
        select(Item)
        .options(selectinload(Item.product), selectinload(Item.loans))
        .where(Item.deleted_at.is_(None), Item.warranty_expires.is_not(None), Item.warranty_expires >= today, Item.warranty_expires <= soon,
               household_only())
        .order_by(Item.warranty_expires)
        .limit(10)
    ).all()
    expiring = db.scalars(
        select(Item)
        .options(selectinload(Item.product), selectinload(Item.loans))
        .where(Item.deleted_at.is_(None), Item.quantity > 0, Item.expires_on.is_not(None), Item.expires_on <= today + timedelta(days=expiry_days),
               household_only())
        .order_by(Item.expires_on)
        .limit(10)
    ).all()
    low = db.scalars(
        select(Product).where(Product.deleted_at.is_(None), Product.low_stock_notified_at.is_not(None)).order_by(Product.name).limit(10)
    ).all()
    return {
        "stats": st,
        "expiring": [item_out(i, r, db) for i in expiring],
        "low_stock": [low_stock_out(db, p) for p in low],
        "recent_activity": [history_out(h) for h in recent],
        "loans": [{**loan_out(lo, today), "item": item_out(lo.item, r, db)} for lo in loans if lo.item.deleted_at is None],
        "warranties": [item_out(i, r, db) for i in warr],
    }


def history_out(h: HistoryEvent) -> dict[str, Any]:
    return {
        "id": h.id,
        "ts": h.ts.isoformat() if h.ts.tzinfo else h.ts.replace(tzinfo=UTC).isoformat(),
        "user": h.user_name,
        "action": h.action,
        "entity_type": h.entity_type,
        "entity_id": h.entity_id,
        "summary": h.summary,
        "before": h.before,
        "after": h.after,
    }


def warranty_check(db: Session, days: int) -> int:
    """Queue a ``warranty_expiring`` event once per item entering the window."""
    today = date.today()
    soon = today + timedelta(days=days)
    items = db.scalars(
        select(Item)
        .options(selectinload(Item.product))
        .where(
            Item.deleted_at.is_(None),
            Item.warranty_expires.is_not(None),
            Item.warranty_expires >= today,
            Item.warranty_expires <= soon,
            Item.warranty_notified_at.is_(None),
            household_only(),
        )
    ).all()
    for it in items:
        it.warranty_notified_at = datetime.now(UTC)
        queue_event(
            db,
            "warranty_expiring",
            {
                "item_id": it.id,
                "name": it.product.name,
                "warranty_expires": it.warranty_expires.isoformat(),
                "days_left": (it.warranty_expires - today).days,
            },
        )
    db.commit()
    return len(items)


def low_stock_out(db: Session, p: Product) -> dict[str, Any]:
    from .inventory import product_total

    first = db.scalars(select(Item.id).where(Item.product_id == p.id, Item.deleted_at.is_(None)).order_by(Item.id)).first()
    return {"product_id": p.id, "name": p.name, "quantity": product_total(db, p.id), "min_quantity": p.min_quantity, "item_id": first}


def low_stock_list(db: Session) -> list[dict[str, Any]]:
    products = db.scalars(
        select(Product).where(Product.deleted_at.is_(None), Product.min_quantity.is_not(None)).order_by(Product.name)
    ).all()
    out = [low_stock_out(db, p) for p in products]
    return sorted(out, key=lambda x: (x["quantity"] >= (x["min_quantity"] or 0), x["name"].lower()))


def expiry_check(db: Session, days: int) -> int:
    """Queue an ``item_expiring`` event once per holding entering the window
    (and once more if it is already past its date when first noticed)."""
    today = date.today()
    items = db.scalars(
        select(Item)
        .options(selectinload(Item.product))
        .where(
            Item.deleted_at.is_(None),
            Item.quantity > 0,
            Item.expires_on.is_not(None),
            Item.expires_on <= today + timedelta(days=days),
            Item.expiry_notified_at.is_(None),
            household_only(),
        )
    ).all()
    r = Resolver.load(db)
    for it in items:
        it.expiry_notified_at = datetime.now(UTC)
        queue_event(
            db,
            "item_expiring",
            {
                "item_id": it.id,
                "name": it.product.name,
                "quantity": it.quantity,
                "expires_on": it.expires_on.isoformat(),
                "days_left": (it.expires_on - today).days,
                "expired": it.expires_on < today,
                "location": r.describe_item(it)["path"],
            },
        )
    db.commit()
    return len(items)

"""Inventory verification: physically checking a place, a box or a category.

A check starts with what the records expect there.  The person confirms each
thing (or scans its barcode), corrects a quantity, marks something missing,
or records something found there that the records put elsewhere.  Only what
they confirm changes anything: things not checked are left exactly as they
were, and an old verification date is never treated as a sign something is
missing.  Each check is kept as history, and counts as the place's check-up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import (
    Category,
    Container,
    HistoryEvent,
    Item,
    ItemStatus,
    Location,
    Product,
    User,
    VerificationEntry,
    VerificationSession,
)
from ..schemas import MoveIn
from .customers import household_only
from .inventory import Conflict, NotFound, adjust_quantity, find_product_by_barcode, move_item, set_status
from .placement import Resolver


class VerifyError(ValueError):
    pass


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def changed_since(db: Session, item: Item) -> bool:
    """Has anything been recorded about it since it was last checked?  (A
    check's own corrections are recorded before the check marks it seen.)"""
    if item.verified_at is None:
        return False
    return db.scalar(select(HistoryEvent.id).where(HistoryEvent.entity_type == "item", HistoryEvent.entity_id == item.id,
                                                   HistoryEvent.ts > item.verified_at).limit(1)) is not None


def confidence(item: Item, now: datetime | None = None, db: Session | None = None) -> dict[str, Any]:
    """How recently someone saw it.  Information only: it never changes data."""
    now = now or datetime.now(UTC)
    at = _aware(item.verified_at)
    if at is None:
        return {"level": "never", "label": "Never checked", "verified_at": None, "changed_since": False}
    days = (now - at).days
    label = ("Checked today" if days < 1 else "Checked in the last 30 days" if days <= 30
             else "Checked in the last year" if days <= 365 else "Not checked in over a year")
    level = "today" if days < 1 else "recent" if days <= 30 else "year" if days <= 365 else "old"
    changed = changed_since(db, item) if db is not None else False
    return {"level": level, "label": label + (" (changed since)" if changed else ""), "verified_at": at.isoformat(),
            "changed_since": changed}


# --------------------------------------------------------------------- scope


def _scope(db: Session, r: Resolver, scope_type: str, scope_id: int) -> tuple[str, list[Item]]:
    base = select(Item).where(Item.deleted_at.is_(None), household_only())
    if scope_type == "location":
        loc = db.get(Location, scope_id)
        if loc is None or loc.deleted_at is not None:
            raise NotFound("Location not found")
        locs = r.descendant_location_ids(scope_id)
        boxes = r.containers_in_location_tree(scope_id)
        items = db.scalars(base.where(Item.location_id.in_(locs) | Item.container_id.in_(boxes))).all()
        return r.location_path(scope_id), list(items)
    if scope_type == "container":
        box = db.get(Container, scope_id)
        if box is None or box.deleted_at is not None:
            raise NotFound("Container not found")
        boxes = r.descendant_container_ids(scope_id)
        items = db.scalars(base.where(Item.container_id.in_(boxes))).all()
        return r.describe(scope_id, None)["path"], list(items)
    if scope_type == "category":
        cat = db.get(Category, scope_id)
        if cat is None:
            raise NotFound("Category not found")
        items = db.scalars(base.join(Product, Product.id == Item.product_id).where(Product.category_id == scope_id)).all()
        return cat.name, list(items)
    raise VerifyError("Choose a place, a box or a category to check")


def _place_of_scope(s: VerificationSession) -> tuple[int | None, int | None]:
    if s.scope_type == "container":
        return s.scope_id, None
    if s.scope_type == "location":
        return None, s.scope_id
    return None, None


def start(db: Session, actor: Actor, scope_type: str, scope_id: int) -> VerificationSession:
    r = Resolver.load(db)
    label, items = _scope(db, r, scope_type, scope_id)
    s = VerificationSession(scope_type=scope_type, scope_id=scope_id, scope_label=label[:300],
                            started_by_id=actor.user_id, started_by=actor.name[:128],
                            expected=[i.id for i in items])
    db.add(s)
    db.flush()
    return s


def previous(db: Session, s: VerificationSession) -> VerificationSession | None:
    return db.scalars(select(VerificationSession).where(
        VerificationSession.scope_type == s.scope_type, VerificationSession.scope_id == s.scope_id,
        VerificationSession.finished_at.is_not(None), VerificationSession.id != s.id)
        .order_by(VerificationSession.finished_at.desc())).first()


def _entry(db: Session, actor: Actor, s: VerificationSession, item: Item, result: str, **kw: Any) -> VerificationEntry:
    if s.finished_at is not None:
        raise VerifyError("This check is finished; start a new one")
    # The latest result for an item in this check replaces an earlier one.
    for old in db.scalars(select(VerificationEntry).where(VerificationEntry.session_id == s.id,
                                                          VerificationEntry.item_id == item.id)):
        db.delete(old)
    e = VerificationEntry(session_id=s.id, item_id=item.id, item_name=item.product.name[:300], result=result,
                          checked_by_id=actor.user_id, **kw)
    db.add(e)
    return e


def _seen(actor: Actor, item: Item) -> None:
    item.verified_at = datetime.now(UTC)
    item.verified_by_id = actor.user_id


def check(db: Session, actor: Actor, s: VerificationSession, item: Item, result: str, quantity: int | None = None,
          note: str | None = None) -> VerificationEntry:
    """present: it's here as recorded.  adjusted: here, with this quantity
    (the stock is corrected).  missing: not found (marked missing)."""
    if item.deleted_at is not None:
        raise NotFound("Item not found")
    before = item.quantity
    if result == "present":
        _seen(actor, item)
        if item.status is ItemStatus.missing:
            set_status(db, actor, item, ItemStatus.stored, "Found in a check")
        return _entry(db, actor, s, item, "present", quantity_before=before, quantity_after=before, note=note)
    if result == "adjusted":
        if quantity is None or quantity < 0:
            raise VerifyError("Say how many there are")
        if quantity != before:
            adjust_quantity(db, actor, item, quantity - before, f"Counted in a check of {s.scope_label}")
        _seen(actor, item)
        if item.status is ItemStatus.missing:
            set_status(db, actor, item, ItemStatus.stored, "Found in a check")
        return _entry(db, actor, s, item, "adjusted", quantity_before=before, quantity_after=item.quantity, note=note)
    if result == "missing":
        if item.status is ItemStatus.on_loan:
            raise VerifyError("It's on loan, so it isn't expected here")
        if item.status is not ItemStatus.missing:
            set_status(db, actor, item, ItemStatus.missing, f"Not found in a check of {s.scope_label}")
        return _entry(db, actor, s, item, "missing", quantity_before=before, quantity_after=before, note=note)
    raise VerifyError("Unknown result")


def found_here(db: Session, actor: Actor, s: VerificationSession, item: Item, move: bool) -> VerificationEntry:
    """Something found in this place that the records put elsewhere.  With
    ``move`` its record moves here; otherwise it's only noted."""
    if item.deleted_at is not None:
        raise NotFound("Item not found")
    r = Resolver.load(db)
    was = r.describe_item(item)["path"]
    cid, lid = _place_of_scope(s)
    if move:
        if cid is None and lid is None:
            raise VerifyError("A category check has no place to move it to")
        if item.status is ItemStatus.on_loan:
            raise VerifyError("It's on loan: mark the loan returned first")
        try:
            move_item(db, actor, item, MoveIn(container_id=cid, location_id=lid, note=f"Found in a check of {s.scope_label}"))
        except Conflict as err:
            raise VerifyError(str(err)) from err
        if item.status is ItemStatus.missing:
            set_status(db, actor, item, ItemStatus.stored, "Found in a check")
    _seen(actor, item)
    return _entry(db, actor, s, item, "found_here", quantity_before=item.quantity, quantity_after=item.quantity,
                  recorded_place=was[:500], note="moved here" if move else "left where the record says")


def scan(db: Session, s: VerificationSession, code: str, fmt: str | None = None) -> dict[str, Any]:
    """Which item a scanned barcode is: one expected here (``expected``), or
    holdings recorded elsewhere (``elsewhere``: found here, misplaced)."""
    product = find_product_by_barcode(db, code, fmt)
    if product is None:
        return {"expected": [], "elsewhere": [], "unknown": True}
    expected = set(s.expected or [])
    holdings = db.scalars(select(Item).where(Item.product_id == product.id, Item.deleted_at.is_(None),
                                             household_only()).order_by(Item.id)).all()
    return {"expected": [i.id for i in holdings if i.id in expected],
            "elsewhere": [i.id for i in holdings if i.id not in expected], "unknown": False, "name": product.name}


def finish(db: Session, actor: Actor, s: VerificationSession) -> VerificationSession:
    if s.finished_at is not None:
        return s
    entries = db.scalars(select(VerificationEntry).where(VerificationEntry.session_id == s.id)).all()
    counts = {k: sum(1 for e in entries if e.result == k) for k in ("present", "adjusted", "missing", "found_here")}
    checked = {e.item_id for e in entries}
    counts["not_checked"] = sum(1 for i in (s.expected or []) if i not in checked)
    s.finished_at = datetime.now(UTC)
    s.summary = counts
    audit.record(db, actor, "verification_finished", s.scope_type, s.scope_id,
                 f"Checked {s.scope_label}: {counts['present'] + counts['adjusted']} here, {counts['adjusted']} recounted, "
                 f"{counts['missing']} missing, {counts['found_here']} found here, {counts['not_checked']} not checked")
    return s


# --------------------------------------------------------------------- output


def session_out(db: Session, s: VerificationSession, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": s.id, "scope_type": s.scope_type, "scope_id": s.scope_id, "scope_label": s.scope_label,
        "started_by": s.started_by, "started_at": _aware(s.started_at).isoformat(),
        "finished_at": _aware(s.finished_at).isoformat() if s.finished_at else None,
        "summary": s.summary, "expected_count": len(s.expected or []),
    }
    if not detail:
        return out
    r = Resolver.load(db)
    entries = {e.item_id: e for e in db.scalars(select(VerificationEntry).where(VerificationEntry.session_id == s.id)
                                               .order_by(VerificationEntry.id))}
    prev = previous(db, s)
    rows = []
    for item_id in s.expected or []:
        it = db.get(Item, item_id)
        if it is None:
            continue
        e = entries.get(item_id)
        rows.append({"id": it.id, "name": it.product.name, "label": it.label, "quantity": it.quantity,
                     "status": it.status.value, "where": r.describe_item(it)["path"],
                     "photo_url": f"api/v1/attachments/{it.primary_photo_id}/thumb" if it.primary_photo_id else None,
                     "confidence": confidence(it, db=db), "result": e.result if e else None,
                     "counted": e.quantity_after if e else None})
    rows.sort(key=lambda x: (x["where"], x["name"].lower()))
    extra = [{"id": e.item_id, "name": e.item_name, "result": e.result, "recorded_place": e.recorded_place, "note": e.note}
             for e in entries.values() if e.item_id not in set(s.expected or [])]
    since = None
    if prev is not None:
        changed = sum(1 for x in rows if x["confidence"]["changed_since"])
        added = len(set(s.expected or []) - set(prev.expected or []))
        gone = len(set(prev.expected or []) - set(s.expected or []))
        since = {"id": prev.id, "finished_at": _aware(prev.finished_at).isoformat(), "by": prev.started_by,
                 "summary": prev.summary, "changed": changed, "new_here": added, "no_longer_here": gone}
    out.update({"items": rows, "found_here": extra, "previous": since})
    return out


def history(db: Session, limit: int = 30, scope_type: str | None = None, scope_id: int | None = None) -> list[dict[str, Any]]:
    stmt = select(VerificationSession).order_by(VerificationSession.started_at.desc()).limit(limit)
    if scope_type:
        stmt = stmt.where(VerificationSession.scope_type == scope_type, VerificationSession.scope_id == scope_id)
    return [session_out(db, s) for s in db.scalars(stmt)]


def item_history(db: Session, item: Item) -> list[dict[str, Any]]:
    out = []
    for e, s in db.execute(select(VerificationEntry, VerificationSession)
                           .join(VerificationSession, VerificationSession.id == VerificationEntry.session_id)
                           .where(VerificationEntry.item_id == item.id).order_by(VerificationEntry.checked_at.desc()).limit(20)):
        who = db.get(User, e.checked_by_id) if e.checked_by_id else None
        out.append({"session_id": s.id, "where": s.scope_label, "result": e.result, "at": _aware(e.checked_at).isoformat(),
                    "by": (who.display_name or who.username) if who else None,
                    "quantity_before": e.quantity_before, "quantity_after": e.quantity_after})
    return out

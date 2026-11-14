"""Inventory domain operations.

Every mutating function records history and queues Home Assistant events;
callers commit.  Functions raise ``NotFound``/``Conflict``/``PlacementError``
which the API maps to HTTP errors.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..auth import Actor
from ..events import queue_event
from ..models import (
    Attachment,
    AttachmentKind,
    Category,
    Container,
    IdentificationStatus,
    Item,
    ItemStatus,
    Loan,
    Location,
    Product,
    User,
)
from ..schemas import (
    ContainerIn,
    ContainerMoveIn,
    ContainerPatch,
    ItemCreate,
    ItemPatch,
    LoanIn,
    LocationIn,
    LocationPatch,
    MoveIn,
    ReturnIn,
)
from . import gtin as gtin_mod
from .placement import (
    Resolver,
    validate_container_target,
    validate_item_target,
    validate_location_parent,
)


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


QR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
PRODUCT_FIELDS = ("name", "description", "manufacturer", "model_number", "barcode", "sku", "tags", "min_quantity", "url")
PURCHASE_FIELDS = (
    "purchase_price",
    "currency",
    "purchase_date",
    "retailer",
    "warranty_start",
    "warranty_expires",
    "serial_number",
    "expires_on",
)
ITEM_AUDIT_FIELDS = (
    "quantity",
    "label",
    "notes",
    "status",
    "container_id",
    "location_id",
    "home_container_id",
    "home_location_id",
    *PURCHASE_FIELDS,
)


def now() -> datetime:
    return datetime.now(UTC)


def new_qr_code(db: Session) -> str:
    """A code unique across containers and locations, so a bare code typed
    into the scanner is never ambiguous."""
    while True:
        code = "".join(secrets.choice(QR_ALPHABET) for _ in range(8))
        if not db.scalar(select(Container.id).where(Container.qr_code == code)) and not db.scalar(
            select(Location.id).where(Location.qr_code == code)
        ):
            return code


def category_key(name: str | None) -> str:
    """What two spellings of one category share: "Kitchen & Dining" ==
    "kitchen and dining"; "Tools" == "tool"."""
    s = re.sub(r"&", " and ", (name or "").lower())
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in re.findall(r"[a-z0-9]+", s)]
    return " ".join(words)


# Categories the App relies on (maintenance is for "Equipment"): always there.
SYSTEM_CATEGORIES = ("Equipment",)


def match_category(db: Session, name: str | None) -> Category | None:
    """The existing category a name means: same name, the same words spelled
    differently, or one of its synonyms ("kitchenware" after a merge).
    None when it isn't a category; categories aren't made up on the fly."""
    if not name or not name.strip():
        return None
    name = name.strip()
    cat = db.scalar(select(Category).where(func.lower(Category.name) == name.lower()))
    if cat is not None:
        return cat
    key = category_key(name)
    cats = db.scalars(select(Category)).all()
    for c in cats:
        if category_key(c.name) == key:
            return c
    for c in cats:
        if any(category_key(syn) == key for syn in (c.synonyms or "").split(",") if syn.strip()):
            return c
    if name.lower() in (n.lower() for n in SYSTEM_CATEGORIES):
        return get_or_create_category(db, name.title())
    return None


def get_or_create_category(db: Session, name: str | None) -> Category | None:
    """Only for adding a category on purpose (Admin → Categories)."""
    if not name:
        return None
    name = name.strip()
    cat = db.scalar(select(Category).where(func.lower(Category.name) == name.lower()))
    if cat is None:
        cat = Category(name=name[:120])
        db.add(cat)
        db.flush()
    return cat


def category_or_none(db: Session, name: str | None) -> str | None:
    """A suggested category (AI, a barcode catalogue) as one of yours, or None."""
    cat = match_category(db, name)
    return cat.name if cat else None


def _get(db: Session, model, obj_id: int, include_deleted: bool = False):
    obj = db.get(model, obj_id)
    if obj is None or (not include_deleted and getattr(obj, "deleted_at", None) is not None):
        raise NotFound(f"{model.__name__} {obj_id} not found")
    return obj


def get_item(db: Session, item_id: int, include_deleted: bool = False) -> Item:
    return _get(db, Item, item_id, include_deleted)


def get_container(db: Session, container_id: int, include_deleted: bool = False) -> Container:
    return _get(db, Container, container_id, include_deleted)


def get_location(db: Session, location_id: int, include_deleted: bool = False) -> Location:
    return _get(db, Location, location_id, include_deleted)


def container_by_qr(db: Session, code: str) -> Container:
    c = db.scalar(select(Container).where(Container.qr_code == code.strip().upper()))
    if c is None or c.deleted_at is not None:
        raise NotFound("No container has this label")
    return c


def item_name(item: Item) -> str:
    return f"{item.product.name} ({item.label})" if item.label else item.product.name


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def create_location(db: Session, actor: Actor, data: LocationIn) -> Location:
    validate_location_parent(db, None, data.parent_id)
    loc = Location(qr_code=new_qr_code(db), **data.model_dump())
    db.add(loc)
    db.flush()
    audit.record(db, actor, "created", "location", loc.id, f"Created location {loc.name}", after=data.model_dump())
    return loc


def update_location(db: Session, actor: Actor, loc: Location, data: LocationPatch) -> Location:
    fields = data.model_dump(exclude_unset=True)
    if "parent_id" in fields:
        validate_location_parent(db, loc.id, fields["parent_id"])
    before = audit.snapshot(loc, fields.keys())
    for k, v in fields.items():
        setattr(loc, k, v)
    b, a = audit.diff(before, audit.snapshot(loc, fields.keys()))
    if a:
        action = "moved" if "parent_id" in a else "updated"
        audit.record(db, actor, action, "location", loc.id, f"Updated location {loc.name}", b, a)
    return loc


def delete_location(db: Session, actor: Actor, loc: Location) -> None:
    has_children = db.scalar(
        select(func.count()).select_from(Location).where(Location.parent_id == loc.id, Location.deleted_at.is_(None))
    )
    has_items = db.scalar(
        select(func.count()).select_from(Item).where(Item.location_id == loc.id, Item.deleted_at.is_(None))
    )
    has_containers = db.scalar(
        select(func.count())
        .select_from(Container)
        .where(Container.location_id == loc.id, Container.deleted_at.is_(None))
    )
    if has_children or has_items or has_containers:
        raise Conflict("Move or delete everything in this location first")
    loc.deleted_at = now()
    audit.record(db, actor, "deleted", "location", loc.id, f"Deleted location {loc.name}")


def restore_location(db: Session, actor: Actor, loc: Location) -> None:
    if loc.parent_id:
        parent = db.get(Location, loc.parent_id)
        if parent is None or parent.deleted_at is not None:
            loc.parent_id = None
    loc.deleted_at = None
    audit.record(db, actor, "restored", "location", loc.id, f"Restored location {loc.name}")


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


def create_container(db: Session, actor: Actor, data: ContainerIn) -> Container:
    validate_container_target(db, None, data.location_id, data.parent_container_id)
    c = Container(qr_code=new_qr_code(db), **data.model_dump())
    db.add(c)
    db.flush()
    audit.record(
        db,
        actor,
        "created",
        "container",
        c.id,
        f"Created container {c.name}",
        after=data.model_dump(),
        related_containers=[c.id, c.parent_container_id],
    )
    return c


def update_container(db: Session, actor: Actor, c: Container, data: ContainerPatch) -> Container:
    fields = data.model_dump(exclude_unset=True)
    before = audit.snapshot(c, fields.keys())
    for k, v in fields.items():
        setattr(c, k, v)
    b, a = audit.diff(before, audit.snapshot(c, fields.keys()))
    if a:
        audit.record(db, actor, "updated", "container", c.id, f"Updated container {c.name}", b, a, [c.id])
    return c


def move_container(db: Session, actor: Actor, c: Container, data: ContainerMoveIn) -> Container:
    validate_container_target(db, c.id, data.location_id, data.parent_container_id)
    r = Resolver.load(db)
    before_desc = r.describe(c.parent_container_id, c.location_id) if (c.parent_container_id or c.location_id) else None
    before = audit.snapshot(c, ("location_id", "parent_container_id"))
    if data.temporary:
        if not c.temporarily_moved:
            c.home_location_id, c.home_container_id = c.location_id, c.parent_container_id
        c.temporarily_moved = True
    else:
        c.home_location_id = c.home_container_id = None
        c.temporarily_moved = False
    c.location_id, c.parent_container_id = data.location_id, data.parent_container_id
    db.flush()
    r = Resolver.load(db)
    after_desc = r.describe(c.parent_container_id, c.location_id)
    summary = f"Moved container {c.name} to {after_desc['path']}" + (" (temporary)" if data.temporary else "")
    if data.note:
        summary += f" — {data.note}"
    audit.record(
        db,
        actor,
        "moved",
        "container",
        c.id,
        summary,
        before,
        audit.snapshot(c, ("location_id", "parent_container_id")),
        [c.id, before["parent_container_id"], c.parent_container_id],
    )
    queue_event(
        db,
        "container_moved",
        {
            "container_id": c.id,
            "name": c.name,
            "number": c.number,
            "from": before_desc["path"] if before_desc else None,
            "to": after_desc["path"],
            "temporary": data.temporary,
            "user": actor.name,
        },
    )
    return c


def return_container_home(db: Session, actor: Actor, c: Container) -> Container:
    if not c.temporarily_moved:
        raise Conflict("This container is already in its normal place")
    return move_container(
        db,
        actor,
        c,
        ContainerMoveIn(location_id=c.home_location_id, parent_container_id=c.home_container_id, note="Returned"),
    )


def delete_container(db: Session, actor: Actor, c: Container) -> None:
    """Soft-delete a container; its contents move to where it was."""
    items = db.scalars(select(Item).where(Item.container_id == c.id, Item.deleted_at.is_(None))).all()
    children = db.scalars(
        select(Container).where(Container.parent_container_id == c.id, Container.deleted_at.is_(None))
    ).all()
    for it in items:
        it.container_id, it.location_id = c.parent_container_id, (None if c.parent_container_id else c.location_id)
    for ch in children:
        ch.parent_container_id = c.parent_container_id
        ch.location_id = None if c.parent_container_id else c.location_id
    c.deleted_at = now()
    audit.record(
        db,
        actor,
        "deleted",
        "container",
        c.id,
        f"Deleted container {c.name}"
        + (f"; {len(items)} item(s) and {len(children)} container(s) moved out" if items or children else ""),
        before={"items": [i.id for i in items], "containers": [ch.id for ch in children]},
        related_containers=[c.id],
    )


def restore_container(db: Session, actor: Actor, c: Container) -> None:
    for attr in ("location_id", "parent_container_id"):
        target_id = getattr(c, attr)
        model = Location if attr == "location_id" else Container
        if target_id:
            t = db.get(model, target_id)
            if t is None or t.deleted_at is not None:
                setattr(c, attr, None)
    c.deleted_at = None
    audit.record(db, actor, "restored", "container", c.id, f"Restored container {c.name}", related_containers=[c.id])


# ---------------------------------------------------------------------------
# Products & items
# ---------------------------------------------------------------------------


def _apply_product_fields(db: Session, product: Product, fields: dict[str, Any]) -> None:
    if "category" in fields:
        wanted = fields.pop("category")
        cat = match_category(db, wanted)
        if wanted and wanted.strip() and cat is None:
            raise Conflict(f"\"{wanted.strip()[:60]}\" isn't one of your categories. Choose one from the list, "
                           "or ask an administrator to add it under Admin → Categories.")
        product.category_id = cat.id if cat else None
    fmt = fields.pop("barcode_format", None)
    for k in PRODUCT_FIELDS:
        if k in fields:
            setattr(product, k, fields[k])
    if "barcode" in fields:
        product.gtin = gtin_mod.normalise(product.barcode, fmt)


def find_product_by_barcode(db: Session, code: str | None, fmt: str | None = None) -> Product | None:
    if not code:
        return None
    norm = gtin_mod.normalise(code, fmt)
    stmt = select(Product).where(Product.deleted_at.is_(None))
    stmt = stmt.where(Product.gtin == norm) if norm else stmt.where(Product.barcode == code.strip())
    return db.scalars(stmt.order_by(Product.id)).first()


def _link_attachments(db: Session, item: Item, ids: list[int], kind: AttachmentKind | None) -> list[Attachment]:
    linked = []
    for att_id in ids:
        att = db.get(Attachment, att_id)
        if att is None:
            raise NotFound(f"Attachment {att_id} not found")
        if att.item_id not in (None, item.id):
            raise Conflict(f"Attachment {att_id} belongs to another item")
        if kind is AttachmentKind.photo and att.kind is not AttachmentKind.photo:
            raise Conflict("Only photos can be used as item photos")
        att.item_id = item.id
        linked.append(att)
    return linked


def create_items(db: Session, actor: Actor, data: ItemCreate) -> list[Item]:
    validate_item_target(db, data.container_id, data.location_id)
    fields = data.model_dump()
    product: Product | None = None
    if data.product_id is not None:
        product = _get(db, Product, data.product_id)
    elif data.barcode:
        # Same barcode = same product identity.  Never merge by name.
        product = find_product_by_barcode(db, data.barcode, data.barcode_format)
    created_product = product is None
    if product is None:
        product = Product(
            name=data.name or "Unnamed item",
            identification_status=data.identification_status,
            ai_metadata=data.ai_metadata,
            catalog_metadata=data.catalog_metadata,
        )
        db.add(product)
        _apply_product_fields(db, product, {k: fields[k] for k in (*PRODUCT_FIELDS, "category", "barcode_format")})
        db.flush()

    count = data.quantity if data.track_individually else 1
    per_item_qty = 1 if data.track_individually else data.quantity
    items: list[Item] = []
    for _ in range(max(count, 1)):
        item = Item(
            product_id=product.id,
            quantity=per_item_qty,
            label=data.label,
            notes=data.notes,
            container_id=data.container_id,
            location_id=data.location_id,
            ai_metadata=data.ai_metadata,
            **{k: fields[k] for k in PURCHASE_FIELDS},
        )
        db.add(item)
        db.flush()
        items.append(item)

    first = items[0]
    photos = _link_attachments(db, first, data.photo_ids, AttachmentKind.photo)
    _link_attachments(db, first, data.document_ids, None)
    for it in items:
        it.primary_photo_id = photos[0].id if photos else None

    r = Resolver.load(db)
    where = r.describe_item(first)["path"]
    for it in items:
        audit.record(
            db,
            actor,
            "created",
            "item",
            it.id,
            f"Added {it.quantity} × {product.name} to {where}",
            after={**audit.snapshot(it, ITEM_AUDIT_FIELDS), "product_id": product.id, "new_product": created_product},
            related_containers=[it.container_id],
        )
        if data.identification_status is IdentificationStatus.confirmed and data.ai_metadata:
            audit.record(db, actor, "ai_identified", "item", it.id, f"AI identification confirmed for {product.name}",
                         after={"ai": data.ai_metadata.get("summary") or data.ai_metadata.get("provider")})
        if data.barcode:
            audit.record(db, actor, "barcode_scanned", "item", it.id, f"Barcode {data.barcode} recorded",
                         after={"barcode": data.barcode})
    check_low_stock(db, product)
    queue_event(
        db,
        "item_added",
        {
            "item_ids": [i.id for i in items],
            "product_id": product.id,
            "name": product.name,
            "quantity": sum(i.quantity for i in items),
            "location": where,
            "user": actor.name,
        },
    )
    if data.container_id:
        queue_event(db, "container_inventory_updated", {"container_id": data.container_id, "user": actor.name})
    return items


def update_item(db: Session, actor: Actor, item: Item, data: ItemPatch) -> Item:
    fields = data.model_dump(exclude_unset=True)
    product = item.product
    product_fields = {k: fields.pop(k) for k in (*PRODUCT_FIELDS, "category", "barcode_format") if k in fields}
    pbefore = audit.snapshot(product, (*PRODUCT_FIELDS, "category_id", "identification_status"))
    if "identification_status" in fields:
        product.identification_status = fields.pop("identification_status")
    _apply_product_fields(db, product, product_fields)
    if "name" in product_fields and not product.name:
        raise Conflict("A name is required")

    if "primary_photo_id" in fields:
        pid = fields.pop("primary_photo_id")
        if pid is not None:
            att = db.get(Attachment, pid)
            if att is None or att.item_id != item.id or att.kind is not AttachmentKind.photo:
                raise Conflict("Primary photo must be one of this item's photos")
        item.primary_photo_id = pid

    ibefore = audit.snapshot(item, ITEM_AUDIT_FIELDS)
    for k, v in fields.items():
        setattr(item, k, v)
    if "expires_on" in fields and ibefore.get("expires_on") != audit._plain(item.expires_on):
        item.expiry_notified_at = None
    if "warranty_expires" in fields:
        item.warranty_notified_at = None
    db.flush()
    pb, pa = audit.diff(pbefore, audit.snapshot(product, (*PRODUCT_FIELDS, "category_id", "identification_status")))
    ib, ia = audit.diff(ibefore, audit.snapshot(item, ITEM_AUDIT_FIELDS))
    if "quantity" in ia:
        audit.record(
            db,
            actor,
            "quantity_changed",
            "item",
            item.id,
            f"Quantity of {product.name} changed from {ib['quantity']} to {ia['quantity']}",
            {"quantity": ib.pop("quantity")},
            {"quantity": ia.pop("quantity")},
            [item.container_id],
        )
        if item.container_id:
            queue_event(db, "container_inventory_updated", {"container_id": item.container_id, "user": actor.name})
    if "quantity" in fields or "min_quantity" in product_fields:
        check_low_stock(db, product)
    if pa or ia:
        others = (
            db.scalar(select(func.count()).select_from(Item).where(Item.product_id == product.id, Item.id != item.id, Item.deleted_at.is_(None)))
            or 0
        )
        note = f" (product details shared with {others} other record(s))" if pa and others else ""
        audit.record(db, actor, "updated", "item", item.id, f"Edited {product.name}{note}", {**pb, **ib}, {**pa, **ia},
                     [item.container_id])
    return item


def household_only():
    from .customers import household_only as _h

    return _h()


def product_total(db: Session, product_id: int) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(Item.quantity), 0)).where(
                Item.product_id == product_id, Item.deleted_at.is_(None), Item.status != ItemStatus.missing,
                household_only(),  # customer equipment isn't household stock
            )
        )
        or 0
    )


def check_low_stock(db: Session, product: Product) -> None:
    """Fire ``low_stock`` once when a tracked product drops below its minimum;
    re-arm when it is restocked."""
    if product.min_quantity is None:
        product.low_stock_notified_at = None
        return
    db.flush()
    total = product_total(db, product.id)
    if total < product.min_quantity:
        if product.low_stock_notified_at is None:
            product.low_stock_notified_at = now()
            queue_event(
                db,
                "low_stock",
                {"product_id": product.id, "name": product.name, "quantity": total, "min_quantity": product.min_quantity},
            )
    else:
        product.low_stock_notified_at = None


def adjust_quantity(db: Session, actor: Actor, item: Item, delta: int, note: str | None) -> Item:
    new_qty = item.quantity + delta
    if new_qty < 0:
        raise Conflict(f"Only {item.quantity} left")
    before = item.quantity
    item.quantity = new_qty
    verb = "Used" if delta < 0 else "Added"
    audit.record(
        db,
        actor,
        "quantity_changed",
        "item",
        item.id,
        f"{verb} {abs(delta)} × {item.product.name} ({before} → {new_qty})" + (f" — {note}" if note else ""),
        {"quantity": before},
        {"quantity": new_qty},
        [item.container_id],
    )
    if item.container_id:
        queue_event(db, "container_inventory_updated", {"container_id": item.container_id, "user": actor.name})
    check_low_stock(db, item.product)
    return item


def split_item(db: Session, item: Item, quantity: int) -> Item:
    """Split ``quantity`` units off ``item`` into a new record in the same place."""
    if quantity >= item.quantity:
        return item
    if quantity < 1:
        raise Conflict("Quantity must be at least 1")
    clone = Item(
        product_id=item.product_id,
        quantity=quantity,
        label=item.label,
        notes=item.notes,
        status=item.status,
        container_id=item.container_id,
        location_id=item.location_id,
        home_container_id=item.home_container_id,
        home_location_id=item.home_location_id,
        primary_photo_id=item.primary_photo_id,
        **{k: getattr(item, k) for k in PURCHASE_FIELDS if k != "serial_number"},
    )
    item.quantity -= quantity
    db.add(clone)
    db.flush()
    # A split-off part of a customer's item is still the customer's.
    from .customers import copy_owner

    copy_owner(db, item, clone)
    return clone


def move_item(db: Session, actor: Actor, item: Item, data: MoveIn) -> Item:
    validate_item_target(db, data.container_id, data.location_id)
    if data.quantity and data.quantity > item.quantity:
        raise Conflict(f"Only {item.quantity} available to move")
    r = Resolver.load(db)
    from_desc = r.describe_item(item)
    source_container = item.container_id
    source_location = item.location_id
    original_id = item.id
    if data.quantity and data.quantity < item.quantity:
        item = split_item(db, item, data.quantity)
    before = audit.snapshot(item, ("container_id", "location_id", "status"))
    if data.temporary:
        if item.home_container_id is None and item.home_location_id is None:
            item.home_container_id, item.home_location_id = source_container, source_location
        if item.status is ItemStatus.stored:
            item.status = ItemStatus.temporarily_relocated
    else:
        item.home_container_id = item.home_location_id = None
        if item.status is ItemStatus.temporarily_relocated:
            item.status = ItemStatus.stored
    item.container_id, item.location_id = data.container_id, data.location_id
    db.flush()
    r = Resolver.load(db)
    to_desc = r.describe_item(item)
    qty_txt = f"{item.quantity} × " if item.quantity != 1 else ""
    summary = f"Moved {qty_txt}{item_name(item)} from {from_desc['path']} to {to_desc['path']}"
    if data.temporary:
        summary += " (temporary)"
    if data.note:
        summary += f" — {data.note}"
    if item.id != original_id:
        summary += f" (split from record #{original_id})"
    audit.record(
        db,
        actor,
        "moved",
        "item",
        item.id,
        summary,
        before,
        audit.snapshot(item, ("container_id", "location_id", "status")),
        [source_container, item.container_id],
    )
    queue_event(
        db,
        "item_moved",
        {
            "item_id": item.id,
            "name": item.product.name,
            "quantity": item.quantity,
            "from": from_desc["path"],
            "to": to_desc["path"],
            "temporary": data.temporary,
            "user": actor.name,
        },
    )
    for cid in {source_container, item.container_id} - {None}:
        queue_event(db, "container_inventory_updated", {"container_id": cid, "user": actor.name})
    return item


def return_item_home(db: Session, actor: Actor, item: Item) -> Item:
    if item.home_container_id is None and item.home_location_id is None:
        raise Conflict("This item is already in its normal place")
    item = move_item(
        db, actor, item, MoveIn(container_id=item.home_container_id, location_id=item.home_location_id, note="Returned")
    )
    item.status = ItemStatus.stored
    return item


def set_status(db: Session, actor: Actor, item: Item, status: ItemStatus, note: str | None) -> Item:
    if status is ItemStatus.on_loan:
        raise Conflict("Use the loan action to lend an item")
    if item.status is ItemStatus.on_loan and status is not ItemStatus.on_loan:
        # Changing the status by hand would leave the loan open.
        raise Conflict(f"{item.product.name} is lent out; mark the loan returned instead")
    before = item.status
    item.status = status
    item.status_note = note
    audit.record(
        db,
        actor,
        "status_changed",
        "item",
        item.id,
        f"{item.product.name}: {before.value.replace('_', ' ')} → {status.value.replace('_', ' ')}" + (f" — {note}" if note else ""),
        {"status": before},
        {"status": status},
        [item.container_id],
    )
    return item


def delete_item(db: Session, actor: Actor, item: Item, reason: str | None = None) -> None:
    item.deleted_at = now()
    r = Resolver.load(db)
    where = r.describe_item(item)["path"]
    audit.record(
        db,
        actor,
        "deleted",
        "item",
        item.id,
        f"Removed {item.quantity} × {item.product.name} from {where}" + (f" — {reason}" if reason else ""),
        before=audit.snapshot(item, ITEM_AUDIT_FIELDS),
        related_containers=[item.container_id],
    )
    queue_event(
        db,
        "item_removed",
        {"item_id": item.id, "name": item.product.name, "quantity": item.quantity, "location": where, "user": actor.name},
    )
    check_low_stock(db, item.product)
    if item.container_id:
        queue_event(db, "container_inventory_updated", {"container_id": item.container_id, "user": actor.name})


def restore_item(db: Session, actor: Actor, item: Item) -> None:
    if item.container_id:
        c = db.get(Container, item.container_id)
        if c is None or c.deleted_at is not None:
            item.container_id = None
    if item.location_id:
        loc = db.get(Location, item.location_id)
        if loc is None or loc.deleted_at is not None:
            item.location_id = None
    item.deleted_at = None
    item.product.deleted_at = None
    audit.record(db, actor, "restored", "item", item.id, f"Restored {item.product.name}", related_containers=[item.container_id])


# ---------------------------------------------------------------------------
# Loans
# ---------------------------------------------------------------------------


def lend_item(db: Session, actor: Actor, item: Item, data: LoanIn) -> Loan:
    if item.status is ItemStatus.on_loan:
        raise Conflict("This item is already on loan")
    qty = data.quantity or item.quantity
    if qty > item.quantity:
        raise Conflict(f"Only {item.quantity} available")
    if qty < item.quantity:
        item = split_item(db, item, qty)
    loan = Loan(
        item_id=item.id,
        borrower=data.borrower,
        quantity=qty,
        loaned_at=data.loaned_at or date.today(),
        expected_return=data.expected_return,
        notes=data.notes,
        created_by_id=actor.user_id,
    )
    item.status = ItemStatus.on_loan
    item.status_note = f"Lent to {data.borrower}"
    db.add(loan)
    db.flush()
    due = f", due back {data.expected_return.isoformat()}" if data.expected_return else ""
    audit.record(
        db,
        actor,
        "loaned",
        "item",
        item.id,
        f"Lent {qty} × {item.product.name} to {data.borrower}{due}",
        after={"borrower": data.borrower, "quantity": qty, "expected_return": data.expected_return, "loan_id": loan.id},
        related_containers=[item.container_id],
    )
    queue_event(
        db,
        "item_loaned",
        {
            "item_id": item.id,
            "loan_id": loan.id,
            "name": item.product.name,
            "borrower": data.borrower,
            "quantity": qty,
            "expected_return": data.expected_return,
            "user": actor.name,
        },
    )
    return loan


def return_loan(db: Session, actor: Actor, loan: Loan, data: ReturnIn) -> Loan:
    if loan.returned_at is not None:
        raise Conflict("This loan has already been returned")
    loan.returned_at = data.returned_at or date.today()
    if data.notes:
        loan.notes = f"{loan.notes}\n{data.notes}" if loan.notes else data.notes
    item = loan.item
    item.status = ItemStatus.temporarily_relocated if (item.home_container_id or item.home_location_id) else ItemStatus.stored
    item.status_note = None
    audit.record(
        db,
        actor,
        "returned",
        "item",
        item.id,
        f"{loan.borrower} returned {loan.quantity} × {item.product.name}",
        {"loan_id": loan.id},
        {"returned_at": loan.returned_at},
        [item.container_id],
    )
    queue_event(
        db,
        "item_returned",
        {"item_id": item.id, "loan_id": loan.id, "name": item.product.name, "borrower": loan.borrower, "user": actor.name},
    )
    return loan


# ---------------------------------------------------------------------------
# Queries & serialisation
# ---------------------------------------------------------------------------


def attachment_out(att: Attachment | None) -> dict[str, Any] | None:
    if att is None:
        return None
    return {
        "id": att.id,
        "kind": att.kind.value,
        "mime_type": att.mime_type,
        "original_name": att.original_name,
        "size_bytes": att.size_bytes,
        "width": att.width,
        "height": att.height,
        "caption": att.caption,
        "url": f"api/v1/attachments/{att.id}/file",
        "thumb_url": f"api/v1/attachments/{att.id}/thumb" if att.thumb_path else None,
        "created_at": att.created_at.isoformat(),
    }


def product_out(p: Product) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "category": p.category.name if p.category else None,
        "manufacturer": p.manufacturer,
        "model_number": p.model_number,
        "barcode": p.barcode,
        "gtin": p.gtin,
        "sku": p.sku,
        "tags": p.tags,
        "url": p.url,
        "min_quantity": p.min_quantity,
        "is_generic": bool(p.is_generic),
        "specs": p.specs or {},
        "content_quantity": float(p.content_quantity) if p.content_quantity is not None else None,
        "content_unit": p.content_unit,
        "identification_status": p.identification_status.value,
        "catalog_metadata": p.catalog_metadata,
        "ai_metadata": p.ai_metadata,
    }


def active_loan(item: Item) -> Loan | None:
    for loan in item.loans:
        if loan.returned_at is None:
            return loan
    return None


def loan_out(loan: Loan, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    return {
        "id": loan.id,
        "item_id": loan.item_id,
        "borrower": loan.borrower,
        "quantity": loan.quantity,
        "loaned_at": loan.loaned_at.isoformat(),
        "expected_return": loan.expected_return.isoformat() if loan.expected_return else None,
        "returned_at": loan.returned_at.isoformat() if loan.returned_at else None,
        "notes": loan.notes,
        "overdue": bool(loan.returned_at is None and loan.expected_return and loan.expected_return < today),
    }


def prefetch(db: Session, items: list[Item]) -> set[int]:
    """Load what ``item_out`` needs for many items in a few queries instead of
    several per item: photos, loans, product categories and owners.  Rows end
    up in the session, so the per-item lookups are answered from memory."""
    from ..models import Customer, ItemOwner, Product

    ids = [i.id for i in items if i.id is not None]
    household: set[int] = set(ids)
    if not ids:
        return household
    by_id = {i.id: i for i in items}
    for chunk in range(0, len(ids), 500):
        part = ids[chunk:chunk + 500]
        list(db.scalars(select(Item).where(Item.id.in_(part))
                        .options(selectinload(Item.loans), selectinload(Item.product).selectinload(Product.category))))
        photo_ids = [by_id[i].primary_photo_id for i in part if by_id[i].primary_photo_id]
        if photo_ids:
            list(db.scalars(select(Attachment).where(Attachment.id.in_(photo_ids))))
        owners = list(db.scalars(select(ItemOwner).where(ItemOwner.item_id.in_(part))))
        if owners:
            list(db.scalars(select(Customer).where(Customer.id.in_({o.customer_id for o in owners}))))
        household -= {o.item_id for o in owners}
    return household


def _taker(db: Session | None, item: Item) -> str | None:
    """Who has it (Take it), by their current name; None when not taken."""
    if item.status is not ItemStatus.in_use:
        return None
    if item.taken_by_id is not None and db is not None:
        u = db.get(User, item.taken_by_id)
        if u is not None:
            return u.display_name or u.username or "someone"
    note = item.status_note or ""
    return note[len("Taken by "):] or None if note.startswith("Taken by ") else None


def items_out(items: list[Item], r: Resolver, db: Session, detail: bool = False) -> list[dict[str, Any]]:
    items = list(items)
    household = prefetch(db, items)
    return [item_out(i, r, db, detail, household=i.id in household) for i in items]


def item_out(item: Item, r: Resolver, db: Session | None = None, detail: bool = False,
             household: bool = False) -> dict[str, Any]:
    """``household=True`` (from ``items_out``) means it's known to have no customer owner."""
    photo = db.get(Attachment, item.primary_photo_id) if (db is not None and item.primary_photo_id) else None
    loan = active_loan(item)
    today = date.today()
    out: dict[str, Any] = {
        "id": item.id,
        "product_id": item.product_id,
        "name": item.product.name,
        "label": item.label,
        "display_name": item_name(item),
        "category": item.product.category.name if item.product.category else None,
        "manufacturer": item.product.manufacturer,
        "model_number": item.product.model_number,
        "barcode": item.product.barcode,
        "product_url": item.product.url,
        "quantity": item.quantity,
        "status": item.status.value,
        "status_note": item.status_note,
        "taken_by": _taker(db, item),
        "placement": r.describe_item(item),
        "home_placement": r.describe_item_home(item),
        "photo": attachment_out(photo),
        "loan": loan_out(loan, today) if loan else None,
        "warranty_expires": item.warranty_expires.isoformat() if item.warranty_expires else None,
        "warranty_active": bool(item.warranty_expires and item.warranty_expires >= today),
        "expires_on": item.expires_on.isoformat() if item.expires_on else None,
        "expired": bool(item.expires_on and item.expires_on < today),
        "days_to_expiry": (item.expires_on - today).days if item.expires_on else None,
        "min_quantity": item.product.min_quantity,
        "low_stock": item.product.low_stock_notified_at is not None,
        "purchase_price": str(item.purchase_price) if item.purchase_price is not None else None,
        "currency": item.currency,
        "identification_status": item.product.identification_status.value,
        "needs_details": item.product.identification_status is IdentificationStatus.pending,
        "capture": _capture_info(item.product),
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }
    if db is not None:
        from .customers import owner_out

        out["owner"] = None if household else owner_out(db, item.id)
    if detail and db is not None:
        out["product"] = product_out(item.product)
        out.update(
            {
                "notes": item.notes,
                "serial_number": item.serial_number,
                "purchase_date": item.purchase_date.isoformat() if item.purchase_date else None,
                "retailer": item.retailer,
                "warranty_start": item.warranty_start.isoformat() if item.warranty_start else None,
                "ai_metadata": item.ai_metadata,
                "created_at": item.created_at.isoformat(),
                "attachments": [
                    attachment_out(a)
                    for a in db.scalars(select(Attachment).where(Attachment.item_id == item.id).order_by(Attachment.id))
                ],
                "loans": [loan_out(lo, today) for lo in item.loans],
                "siblings": product_holdings(db, item.product_id, r, exclude_item_id=item.id),
            }
        )
    return out


def _capture_info(p: Product) -> dict[str, Any] | None:
    if p.identification_status is not IdentificationStatus.pending:
        return None
    meta = p.ai_metadata or {}
    status = (meta.get("capture") or {}).get("status", "none")
    return {"ai_status": status, "suggestion": meta.get("suggestion") if status == "ready" else None}


def product_holdings(db: Session, product_id: int, r: Resolver, exclude_item_id: int | None = None) -> list[dict[str, Any]]:
    items = db.scalars(
        select(Item).where(Item.product_id == product_id, Item.deleted_at.is_(None)).order_by(Item.id)
    ).all()
    return [
        {
            "id": it.id,
            "quantity": it.quantity,
            "label": it.label,
            "status": it.status.value,
            "path": r.describe_item(it)["path"],
        }
        for it in items
        if it.id != exclude_item_id
    ]


def container_out(c: Container, r: Resolver, db: Session, detail: bool = False) -> dict[str, Any]:
    place = r.describe(c.parent_container_id, c.location_id)
    item_count = db.scalar(
        select(func.coalesce(func.sum(Item.quantity), 0)).where(Item.container_id == c.id, Item.deleted_at.is_(None))
    )
    child_count = db.scalar(
        select(func.count()).select_from(Container).where(Container.parent_container_id == c.id, Container.deleted_at.is_(None))
    )
    photo = db.scalars(
        select(Attachment)
        .where(Attachment.container_id == c.id, Attachment.kind == AttachmentKind.photo)
        .order_by(Attachment.id.desc())
    ).first()
    out: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "number": c.number,
        "label": f"{c.name} #{c.number}" if c.number and c.number not in c.name else c.name,
        "description": c.description,
        "container_type": c.container_type,
        "qr_code": c.qr_code,
        "location_id": c.location_id,
        "parent_container_id": c.parent_container_id,
        "placement": place if (c.parent_container_id or c.location_id) else None,
        "path": r.describe(c.id, None)["path"],
        "temporarily_moved": c.temporarily_moved,
        "home_placement": r.describe(c.home_container_id, c.home_location_id) if c.temporarily_moved else None,
        "item_count": int(item_count or 0),
        "container_count": int(child_count or 0),
        "photo": attachment_out(photo),
        "deleted_at": c.deleted_at.isoformat() if c.deleted_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if detail:
        items = db.scalars(
            select(Item)
            .options(selectinload(Item.product).selectinload(Product.category), selectinload(Item.loans))
            .where(Item.container_id == c.id, Item.deleted_at.is_(None))
            .order_by(Item.id)
        ).all()
        children = db.scalars(
            select(Container)
            .where(Container.parent_container_id == c.id, Container.deleted_at.is_(None))
            .order_by(Container.number, Container.name)
        ).all()
        out["items"] = items_out(items, r, db)
        out["containers"] = [container_out(ch, r, db) for ch in children]
        out["photos"] = [
            attachment_out(a)
            for a in db.scalars(
                select(Attachment).where(Attachment.container_id == c.id).order_by(Attachment.id.desc())
            )
        ]
        out["maps"] = maps_for(db, container_id=c.id, location_ids=[x["id"] for x in place["location_path"]])
    return out


def _subtree_counts(db: Session, r: Resolver, location_id: int) -> dict[str, int]:
    """What's kept in a place and everything inside it: boxes, and things
    (loose or in those boxes), so a shelf's row can say "2 boxes · 7 things"."""
    ids, todo = {location_id}, [location_id]
    while todo:
        cur = todo.pop()
        for x in r.locations.values():
            if x.parent_id == cur and x.deleted_at is None and x.id not in ids:
                ids.add(x.id)
                todo.append(x.id)
    box_ids = list(db.scalars(select(Container.id).where(Container.location_id.in_(ids), Container.deleted_at.is_(None))))
    cond = Item.location_id.in_(ids)
    if box_ids:
        cond = or_(cond, Item.container_id.in_(box_ids))
    things = db.scalar(select(func.coalesce(func.sum(Item.quantity), 0)).where(Item.deleted_at.is_(None), cond)) or 0
    return {"container_count": len(box_ids), "item_count": int(things)}


def location_out(loc: Location, r: Resolver, db: Session, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": loc.id,
        "name": loc.name,
        "kind": loc.kind,
        "description": loc.description,
        "parent_id": loc.parent_id,
        "sort_order": loc.sort_order,
        "qr_code": loc.qr_code,
        "path": r.location_path(loc.id),
        "path_parts": [{"id": x.id, "name": x.name} for x in r.location_chain(loc.id)],
        "deleted_at": loc.deleted_at.isoformat() if loc.deleted_at else None,
    }
    if detail:
        children = [x for x in r.locations.values() if x.parent_id == loc.id and x.deleted_at is None]
        out["children"] = [
            {"id": ch.id, "name": ch.name, "kind": ch.kind, **_subtree_counts(db, r, ch.id)}
            for ch in sorted(children, key=lambda x: (x.sort_order, x.name.lower()))
        ]
        items = db.scalars(
            select(Item)
            .options(selectinload(Item.product).selectinload(Product.category), selectinload(Item.loans))
            .where(Item.location_id == loc.id, Item.deleted_at.is_(None))
            .order_by(Item.id)
        ).all()
        containers = db.scalars(
            select(Container).where(Container.location_id == loc.id, Container.deleted_at.is_(None)).order_by(Container.number, Container.name)
        ).all()
        out["items"] = items_out(items, r, db)
        out["containers"] = [container_out(c, r, db) for c in containers]
        out["maps"] = maps_for(db, location_ids=[loc.id] + [x.id for x in r.location_chain(loc.id)])
    return out


def maps_for(db: Session, container_id: int | None = None, location_ids: list[int] | None = None) -> list[dict[str, Any]]:
    from ..models import LocationMap, MapMarker

    conds = []
    if container_id:
        conds.append(LocationMap.id.in_(select(MapMarker.map_id).where(MapMarker.container_id == container_id)))
    if location_ids:
        conds.append(LocationMap.id.in_(select(MapMarker.map_id).where(MapMarker.location_id.in_(location_ids))))
        conds.append(LocationMap.location_id.in_(location_ids))
    if not conds:
        return []
    maps = db.scalars(select(LocationMap).where(LocationMap.deleted_at.is_(None), or_(*conds)).order_by(LocationMap.id)).all()
    return [{"id": m.id, "title": m.title, "thumb_url": f"api/v1/attachments/{m.photo_id}/thumb"} for m in maps]


def location_tree(db: Session) -> list[dict[str, Any]]:
    locs = db.scalars(select(Location).where(Location.deleted_at.is_(None))).all()
    counts_items = dict(
        db.execute(
            select(Item.location_id, func.sum(Item.quantity)).where(Item.deleted_at.is_(None), Item.location_id.is_not(None)).group_by(Item.location_id)
        ).all()
    )
    counts_cont = dict(
        db.execute(
            select(Container.location_id, func.count()).where(Container.deleted_at.is_(None), Container.location_id.is_not(None)).group_by(Container.location_id)
        ).all()
    )
    nodes = {
        loc.id: {
            "id": loc.id,
            "name": loc.name,
            "kind": loc.kind,
            "parent_id": loc.parent_id,
            "sort_order": loc.sort_order,
            "item_count": int(counts_items.get(loc.id, 0) or 0),
            "container_count": int(counts_cont.get(loc.id, 0) or 0),
            "children": [],
        }
        for loc in locs
    }
    roots = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"]) if node["parent_id"] else None
        (parent["children"] if parent else roots).append(node)

    def sort(ns):
        ns.sort(key=lambda n: (n["sort_order"], n["name"].lower()))
        for n in ns:
            sort(n["children"])

    sort(roots)
    return roots


# ---------------------------------------------------------------------------
# Category management (Admin → Categories)
# ---------------------------------------------------------------------------


def _category_items(db: Session, cat: Category) -> int:
    return db.scalar(select(func.count(Item.id)).join(Product).where(Product.category_id == cat.id, Item.deleted_at.is_(None))) or 0


def rename_category(db: Session, actor: Actor, cat: Category, name: str, synonyms: str | None) -> Category:
    name = name.strip()
    clash = next((c for c in db.scalars(select(Category).where(Category.id != cat.id))
                  if category_key(c.name) == category_key(name)), None)
    if clash:
        raise Conflict(f"There's already a category called {clash.name}. Merge into it instead.")
    before = {"name": cat.name, "synonyms": cat.synonyms}
    cat.name, cat.synonyms = name[:120], (synonyms or None)
    if before["name"] != cat.name:
        audit.record(db, actor, "renamed", "category", cat.id, f"Renamed category {before['name']} to {cat.name}",
                     before, {"name": cat.name, "synonyms": cat.synonyms})
    return cat


def merge_category(db: Session, actor: Actor, src: Category, into: Category) -> int:
    """Everything in ``src`` moves to ``into``; ``src`` goes, and its name
    becomes a synonym of ``into`` so later suggestions land there too."""
    if src.id == into.id:
        raise Conflict("Choose a different category to merge into")
    if src.name.lower() in (n.lower() for n in SYSTEM_CATEGORIES):
        raise Conflict(f"{src.name} is used by maintenance and can't be merged away")
    moved = _category_items(db, src)
    for p in db.scalars(select(Product).where(Product.category_id == src.id)):
        p.category_id = into.id
    syns = [x.strip() for x in (into.synonyms or "").split(",") if x.strip()]
    for extra in [src.name, *[x.strip() for x in (src.synonyms or "").split(",") if x.strip()]]:
        if all(category_key(extra) != category_key(x) for x in syns) and category_key(extra) != category_key(into.name):
            syns.append(extra)
    into.synonyms = ", ".join(syns)[:2000] or None
    audit.record(db, actor, "merged", "category", into.id,
                 f"Merged category {src.name} into {into.name} ({moved} item{'s' if moved != 1 else ''})",
                 {"name": src.name}, {"name": into.name})
    db.delete(src)
    db.flush()
    return moved


def delete_category(db: Session, actor: Actor, cat: Category) -> None:
    if cat.name.lower() in (n.lower() for n in SYSTEM_CATEGORIES):
        raise Conflict(f"{cat.name} is used by maintenance and can't be deleted")
    n = _category_items(db, cat)
    if n:
        raise Conflict(f"{cat.name} has {n} item{'s' if n != 1 else ''}. Merge it into another category instead.")
    for p in db.scalars(select(Product).where(Product.category_id == cat.id)):  # deleted items' products
        p.category_id = None
    audit.record(db, actor, "deleted", "category", cat.id, f"Deleted category {cat.name}", {"name": cat.name}, None)
    db.delete(cat)
    db.flush()

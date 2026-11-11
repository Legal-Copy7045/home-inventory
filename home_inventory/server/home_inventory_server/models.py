"""Database schema.

Key modelling decisions
-----------------------
* ``Product`` is an *identity* (what a thing is: name, manufacturer, model,
  barcode).  ``Item`` is a *physical holding* of that product in one place
  (quantity N of identical, untracked units, or a single serialised unit).
  Three identical extension cords in three places are one Product and three
  Items; two in the same box are one Item with quantity 2.
* An Item or Container is placed either inside a Container or directly in a
  Location (never both).  The *home* placement (normal storage) is kept
  separately from the *current* placement so temporary moves and loans can
  be undone.
* Records are soft-deleted (``deleted_at``) so accidental deletions can be
  restored from the Trash.
"""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Role(str, enum.Enum):
    admin = "admin"
    member = "member"
    read_only = "read_only"


class ItemStatus(str, enum.Enum):
    stored = "stored"
    in_use = "in_use"
    temporarily_relocated = "temporarily_relocated"
    on_loan = "on_loan"
    awaiting_return = "awaiting_return"
    missing = "missing"


class IdentificationStatus(str, enum.Enum):
    manual = "manual"  # typed in by a user
    ai_suggested = "ai_suggested"  # AI proposed, not yet confirmed
    catalog = "catalog"  # filled from a barcode catalog, confirmed
    confirmed = "confirmed"  # user reviewed and confirmed


class AttachmentKind(str, enum.Enum):
    photo = "photo"
    receipt = "receipt"
    manual = "manual"
    warranty = "warranty"
    document = "document"


class ScanStatus(str, enum.Enum):
    pending = "pending"
    analysing = "analysing"
    ready = "ready"  # suggestions available for review
    failed = "failed"
    confirmed = "confirmed"
    discarded = "discarded"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Home Assistant user id (from the Ingress X-Remote-User-Id header or the
    # companion integration).  Special ids: "system", "integration".
    ha_user_id: Mapped[str] = mapped_column(String(64), unique=True)
    username: Mapped[str | None] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(128))
    # Role explicitly assigned in the app.  NULL means "derive" (HA admin ->
    # admin, otherwise default role).
    role_override: Mapped[Role | None] = mapped_column(Enum(Role, native_enum=False, length=16))
    ha_is_admin: Mapped[bool] = mapped_column(default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # Free text synonyms used by search ("xmas, holiday").
    synonyms: Mapped[str | None] = mapped_column(Text)


class Location(TimestampMixin, Base):
    __tablename__ = "locations"
    __table_args__ = (CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_location_not_self_parent"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # property, building, room, area, shelf, drawer, cabinet, workbench, ...
    kind: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parent: Mapped[Location | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[Location]] = relationship(back_populates="parent")


class Container(TimestampMixin, Base):
    __tablename__ = "containers"
    __table_args__ = (
        CheckConstraint(
            "NOT (location_id IS NOT NULL AND parent_container_id IS NOT NULL)",
            name="ck_container_single_placement",
        ),
        CheckConstraint(
            "NOT (home_location_id IS NOT NULL AND home_container_id IS NOT NULL)",
            name="ck_container_single_home",
        ),
        CheckConstraint("parent_container_id IS NULL OR parent_container_id != id", name="ck_container_not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Permanent identifier encoded in the QR label.  Never changes.
    qr_code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    number: Mapped[str | None] = mapped_column(String(40), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    container_type: Mapped[str | None] = mapped_column(String(60))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), index=True)
    parent_container_id: Mapped[int | None] = mapped_column(
        ForeignKey("containers.id", ondelete="SET NULL"), index=True
    )
    home_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    home_container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="SET NULL"))
    temporarily_moved: Mapped[bool] = mapped_column(default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    location: Mapped[Location | None] = relationship(foreign_keys=[location_id])
    parent_container: Mapped[Container | None] = relationship(
        remote_side=[id], foreign_keys=[parent_container_id], back_populates="child_containers"
    )
    child_containers: Mapped[list[Container]] = relationship(
        back_populates="parent_container", foreign_keys=[parent_container_id]
    )


class Product(TimestampMixin, Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(200), index=True)
    model_number: Mapped[str | None] = mapped_column(String(120), index=True)
    # Normalised GTIN-14 (zero padded) for exact matching, plus the raw value
    # as scanned/entered.
    gtin: Mapped[str | None] = mapped_column(String(14), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), index=True)
    sku: Mapped[str | None] = mapped_column(String(120), index=True)
    tags: Mapped[str | None] = mapped_column(Text)
    # Product web page (retailer or manufacturer), used to import details.
    url: Mapped[str | None] = mapped_column(String(2000))
    # Consumables: warn when the total quantity across all holdings drops
    # below this number (NULL = not tracked).
    min_quantity: Mapped[int | None] = mapped_column(Integer)
    low_stock_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    catalog_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ai_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    identification_status: Mapped[IdentificationStatus] = mapped_column(
        Enum(IdentificationStatus, native_enum=False, length=16), default=IdentificationStatus.manual
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    category: Mapped[Category | None] = relationship()
    items: Mapped[list[Item]] = relationship(back_populates="product")


class Item(TimestampMixin, Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_item_quantity_non_negative"),
        CheckConstraint(
            "NOT (container_id IS NOT NULL AND location_id IS NOT NULL)", name="ck_item_single_placement"
        ),
        CheckConstraint(
            "NOT (home_container_id IS NOT NULL AND home_location_id IS NOT NULL)", name="ck_item_single_home"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # Optional per-instance label ("the red one"), notes and serial number.
    label: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    serial_number: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(ItemStatus, native_enum=False, length=32), default=ItemStatus.stored, index=True
    )
    status_note: Mapped[str | None] = mapped_column(Text)

    # Current placement
    container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="SET NULL"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), index=True)
    # Normal storage placement (differs from current while temporarily moved)
    home_container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="SET NULL"))
    home_location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))

    # Purchase & warranty (all optional)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    purchase_date: Mapped[date | None] = mapped_column(Date)
    retailer: Mapped[str | None] = mapped_column(String(200))
    warranty_start: Mapped[date | None] = mapped_column(Date)
    warranty_expires: Mapped[date | None] = mapped_column(Date, index=True)
    warranty_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Best-before / use-by / expiry date of this holding (food, medicine,
    # batteries, chemicals...).  Different lots of one product can differ.
    expires_on: Mapped[date | None] = mapped_column(Date, index=True)
    expiry_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ai_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    primary_photo_id: Mapped[int | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL", use_alter=True, name="fk_item_primary_photo")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="items")
    container: Mapped[Container | None] = relationship(foreign_keys=[container_id])
    location: Mapped[Location | None] = relationship(foreign_keys=[location_id])
    home_container: Mapped[Container | None] = relationship(foreign_keys=[home_container_id])
    home_location: Mapped[Location | None] = relationship(foreign_keys=[home_location_id])
    loans: Mapped[list[Loan]] = relationship(back_populates="item", order_by="Loan.loaned_at.desc()")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[AttachmentKind] = mapped_column(Enum(AttachmentKind, native_enum=False, length=16))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="CASCADE"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    original_name: Mapped[str | None] = mapped_column(String(255))
    # Path relative to the media directory.  Never user controlled.
    stored_path: Mapped[str] = mapped_column(String(255), unique=True)
    thumb_path: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    caption: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContainerScan(Base):
    """A photograph of an open container submitted for AI analysis.

    Suggestions live here until a user reviews and confirms them; nothing is
    written to the inventory until then.
    """

    __tablename__ = "container_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    container_id: Mapped[int] = mapped_column(ForeignKey("containers.id", ondelete="CASCADE"), index=True)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))
    status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, native_enum=False, length=16), default=ScanStatus.pending
    )
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    suggestions: Mapped[list[Any] | None] = mapped_column(JSON)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    borrower: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(default=1)
    loaned_at: Mapped[date] = mapped_column(Date)
    expected_return: Mapped[date | None] = mapped_column(Date, index=True)
    returned_at: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[Item] = relationship(back_populates="loans")


class LocationMap(TimestampMixin, Base):
    """A photograph of a storage area with manually placed markers."""

    __tablename__ = "location_maps"

    id: Mapped[int] = mapped_column(primary_key=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    photo_id: Mapped[int] = mapped_column(ForeignKey("attachments.id", ondelete="RESTRICT"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    markers: Mapped[list[MapMarker]] = relationship(
        back_populates="map", cascade="all, delete-orphan", order_by="MapMarker.id"
    )


class MapMarker(Base):
    __tablename__ = "map_markers"
    __table_args__ = (
        CheckConstraint("x >= 0 AND x <= 1 AND y >= 0 AND y <= 1", name="ck_marker_in_bounds"),
        CheckConstraint(
            "(location_id IS NOT NULL) + (container_id IS NOT NULL) = 1", name="ck_marker_single_target"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("location_maps.id", ondelete="CASCADE"), index=True)
    # Position as a fraction of the image width/height so markers survive
    # any rendering size.
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float] = mapped_column(Float)
    label: Mapped[str | None] = mapped_column(String(120))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="CASCADE"), index=True)

    map: Mapped[LocationMap] = relationship(back_populates="markers")


class HistoryEvent(Base):
    """Append-only audit trail."""

    __tablename__ = "history_events"
    __table_args__ = (Index("ix_history_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    user_name: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Secondary references so "container history" also shows items that
    # moved in or out of it.
    related_container_ids: Mapped[str | None] = mapped_column(String(200))


class BarcodeCache(Base):
    __tablename__ = "barcode_cache"
    __table_args__ = (UniqueConstraint("gtin", "provider", name="uq_barcode_cache"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    gtin: Mapped[str] = mapped_column(String(14), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    found: Mapped[bool] = mapped_column(default=False)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)

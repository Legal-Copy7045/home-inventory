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
    pending = "pending"  # quick-captured photo; details still to be added


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


class MeterKind(str, enum.Enum):
    hours = "hours"
    cycles = "cycles"


class ServiceOutcome(str, enum.Enum):
    completed = "completed"
    skipped = "skipped"
    deferred = "deferred"
    not_applicable = "not_applicable"


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
    # Permanent identifier encoded in the location's QR label.
    qr_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
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
    # Parts and consumables.  A *generic* product is a standard rather than
    # a brand ("Engine oil SAE 10W-30, API SN"): branded products that meet
    # it (a confirmed "meets" link) count as stock for it.  ``specs`` holds
    # comparable properties ({"Viscosity": "10W-30", "Standard": "API SN"}).
    # ``content_quantity``/``content_unit``: how much one holding contains
    # (a 5 L bottle -> 5, "L"); NULL means it's counted in pieces.
    is_generic: Mapped[bool] = mapped_column(default=False, server_default="0")
    specs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    content_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    content_unit: Mapped[str | None] = mapped_column(String(10))
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
    # Photos and receipts recorded with a service/maintenance entry.
    service_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("service_events.id", ondelete="SET NULL", name="fk_attachments_service_event_id"), index=True
    )
    # Photos of damage, quotes and paperwork for a repair job.
    repair_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("repair_jobs.id", ondelete="SET NULL", name="fk_attachments_repair_job_id"), index=True
    )
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
    """A photograph of an open container or a shelf submitted for AI analysis.

    Suggestions live here until a user reviews and confirms them; nothing is
    written to the inventory until then.
    """

    __tablename__ = "container_scans"
    __table_args__ = (
        CheckConstraint("(container_id IS NULL) != (location_id IS NULL)", name="ck_scan_single_target"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # A scan is of a container or of a location (a shelf, bench...), never both.
    container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="CASCADE"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
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


# ---------------------------------------------------------------------------
# Equipment maintenance
# ---------------------------------------------------------------------------


class EquipmentProfile(TimestampMixin, Base):
    """Optional maintenance details for an item (one per item).

    Manufacturer, model and serial number stay on the product/item; this
    adds what only matters for maintenance.  ``specs`` is a free list of
    ``{"label", "value", "kind": "spec"|"part", "part_number"}`` entries so
    any equipment type can record its own specifications and parts.
    """

    __tablename__ = "equipment_profiles"
    __table_args__ = (CheckConstraint("meter IN ('none', 'hours', 'cycles', 'both')", name="ck_equipment_meter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), unique=True)
    equipment_type: Mapped[str | None] = mapped_column(String(80), index=True)
    engine_manufacturer: Mapped[str | None] = mapped_column(String(120))
    engine_model: Mapped[str | None] = mapped_column(String(120))
    engine_serial: Mapped[str | None] = mapped_column(String(120))
    fuel_type: Mapped[str | None] = mapped_column(String(60))
    # Which usage meter the equipment has: none, hours, cycles or both.
    meter: Mapped[str] = mapped_column(String(10), default="hours")
    # Home Assistant entities that report the meter (read every 15 minutes).
    hours_entity_id: Mapped[str | None] = mapped_column(String(255))
    cycles_entity_id: Mapped[str | None] = mapped_column(String(255))
    specs: Mapped[list[Any] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)


class MaintenanceTask(TimestampMixin, Base):
    """A recurring (or one-off) maintenance job for an item.

    Any combination of calendar, hour and cycle intervals; whichever
    threshold comes first makes the task due.  With no interval it is a
    manual task with an optional ``next_due_on``.
    """

    __tablename__ = "maintenance_tasks"
    __table_args__ = (
        CheckConstraint("interval_days IS NULL OR interval_days > 0", name="ck_task_interval_days"),
        CheckConstraint("interval_hours IS NULL OR interval_hours > 0", name="ck_task_interval_hours"),
        CheckConstraint("interval_cycles IS NULL OR interval_cycles > 0", name="ck_task_interval_cycles"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    instructions: Mapped[str | None] = mapped_column(Text)
    interval_days: Mapped[int | None] = mapped_column(Integer)
    interval_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    interval_cycles: Mapped[int | None] = mapped_column(Integer)
    due_soon_days: Mapped[int] = mapped_column(Integer, default=14)
    due_soon_hours: Mapped[Decimal] = mapped_column(Numeric(10, 1), default=Decimal("5"))
    due_soon_cycles: Mapped[int] = mapped_column(Integer, default=2)
    last_done_on: Mapped[date | None] = mapped_column(Date)
    last_done_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    last_done_cycles: Mapped[int | None] = mapped_column(Integer)
    next_due_on: Mapped[date | None] = mapped_column(Date, index=True)
    next_due_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    next_due_cycles: Mapped[int | None] = mapped_column(Integer)
    deferred_until: Mapped[date | None] = mapped_column(Date)
    not_applicable: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeterReading(Base):
    """An operating-hours or usage-cycle reading (measured or estimated)."""

    __tablename__ = "meter_readings"
    __table_args__ = (
        Index("ix_meter_item_kind_time", "item_id", "kind", "recorded_at"),
        CheckConstraint("value >= 0", name="ck_meter_value_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    kind: Mapped[MeterKind] = mapped_column(Enum(MeterKind, native_enum=False, length=10))
    value: Mapped[Decimal] = mapped_column(Numeric(10, 1))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # manual | estimated | service | sensor | correction
    source: Mapped[str] = mapped_column(String(20), default="manual")
    note: Mapped[str | None] = mapped_column(String(300))
    service_event_id: Mapped[int | None] = mapped_column(ForeignKey("service_events.id", ondelete="SET NULL"))
    # A photo of the meter showing this reading (read by AI or by eye).
    photo_id: Mapped[int | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL", name="fk_meter_readings_photo_id")
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ServiceEvent(TimestampMixin, Base):
    """One entry in an item's service history.

    History must outlive schedule changes: ``task_id`` is cleared (not
    cascaded) when a task is removed, ``task_title`` keeps its name, and
    ``snapshot`` records the equipment as it was (name, serial, owner).
    Even permanently purging the item keeps the entry (``item_id`` NULL).
    """

    __tablename__ = "service_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("maintenance_tasks.id", ondelete="SET NULL"), index=True)
    task_title: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[ServiceOutcome] = mapped_column(
        Enum(ServiceOutcome, native_enum=False, length=20), default=ServiceOutcome.completed
    )
    performed_on: Mapped[date] = mapped_column(Date, index=True)
    hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    cycles: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    work_performed: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[str | None] = mapped_column(String(120))
    repair_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("repair_jobs.id", ondelete="SET NULL", name="fk_service_events_repair_job_id"), index=True
    )
    labor_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parts: Mapped[list[ServicePart]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="ServicePart.id"
    )


class ServicePart(Base):
    """A part or consumable used in a service entry."""

    __tablename__ = "service_parts"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_part_quantity_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    service_event_id: Mapped[int] = mapped_column(ForeignKey("service_events.id", ondelete="CASCADE"), index=True)
    # Optional link to the inventory holding the part was taken from.
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(300))
    part_number: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1"))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    event: Mapped[ServiceEvent] = relationship(back_populates="parts")


# ---------------------------------------------------------------------------
# Customers and repair jobs
# ---------------------------------------------------------------------------

REPAIR_STATUSES = (
    "received",
    "awaiting_diagnosis",
    "awaiting_approval",
    "waiting_for_parts",
    "in_progress",
    "completed",
    "returned",
    "cancelled",
)


class Customer(TimestampMixin, Base):
    """Someone whose equipment you look after (not part of the household)."""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    phone: Mapped[str | None] = mapped_column(String(60))
    email: Mapped[str | None] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerContact(Base):
    """Optional log of calls, messages and visits."""

    __tablename__ = "customer_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    contacted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    channel: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class ItemOwner(Base):
    """Marks an item as belonging to a customer rather than the household.

    A separate table (no row = household) so ownership can only change
    through the dedicated, audited endpoint - never as a side effect of an
    ordinary item edit - and so adding it needed no rebuild of ``items``.
    """

    __tablename__ = "item_owners"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    since: Mapped[date] = mapped_column(Date, default=date.today)


class RepairJob(TimestampMixin, Base):
    """A repair visit for a customer's equipment (a lightweight work order)."""

    __tablename__ = "repair_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in REPAIR_STATUSES) + ")", name="ck_repair_job_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="received", index=True)
    received_on: Mapped[date] = mapped_column(Date, default=date.today)
    condition_received: Mapped[str | None] = mapped_column(Text)
    reported_problem: Mapped[str | None] = mapped_column(Text)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    approval_notes: Mapped[str | None] = mapped_column(Text)
    estimate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    labor_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Leave empty to use the cost of parts recorded in the job's service entries.
    parts_charge: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    completed_on: Mapped[date | None] = mapped_column(Date)
    returned_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    # Customer and equipment as they were when the job was opened.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RepairJobEvent(Base):
    """Status history of a repair job."""

    __tablename__ = "repair_job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("repair_jobs.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_from: Mapped[str | None] = mapped_column(String(30))
    status_to: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)
    user_name: Mapped[str | None] = mapped_column(String(128))


# ---------------------------------------------------------------------------
# Item relationships and kits
# ---------------------------------------------------------------------------

RELATION_TYPES = ("accessory_of", "component_of", "compatible_with", "stored_with", "replacement_for", "custom")


class ItemRelation(Base):
    """A directed link between two items, shown from both sides.

    ``from`` is the accessory/component/replacement; ``to`` is the parent
    or the thing it relates to.  Deleting a relation never touches either
    item; items stay independent records with their own place and QR code.
    """

    __tablename__ = "item_relations"
    __table_args__ = (
        UniqueConstraint("from_item_id", "to_item_id", "relation_type", "custom_label", name="uq_item_relation"),
        CheckConstraint("from_item_id != to_item_id", name="ck_relation_not_self"),
        CheckConstraint(
            "relation_type IN (" + ", ".join(f"'{t}'" for t in RELATION_TYPES) + ")", name="ck_relation_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    to_item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    relation_type: Mapped[str] = mapped_column(String(30))
    # For relation_type "custom": the user's own wording, e.g. "charger for".
    custom_label: Mapped[str | None] = mapped_column(String(60))
    note: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Kit(TimestampMixin, Base):
    """A named collection (e.g. "Air compressor kit").

    Members keep their own placement; the kit's home container/location is
    where they *should* be, used to show which are stored separately.
    """

    __tablename__ = "kits"
    __table_args__ = (
        CheckConstraint("NOT (container_id IS NOT NULL AND location_id IS NOT NULL)", name="ck_kit_single_home"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[int | None] = mapped_column(ForeignKey("containers.id", ondelete="SET NULL"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    members: Mapped[list[KitMember]] = relationship(
        back_populates="kit", cascade="all, delete-orphan", order_by="KitMember.id"
    )


class KitMember(Base):
    __tablename__ = "kit_members"
    __table_args__ = (
        UniqueConstraint("kit_id", "item_id", name="uq_kit_member"),
        CheckConstraint("quantity >= 1", name="ck_kit_member_quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kit_id: Mapped[int] = mapped_column(ForeignKey("kits.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    optional: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str | None] = mapped_column(String(300))

    kit: Mapped[Kit] = relationship(back_populates="members")


# ---------------------------------------------------------------------------
# NFC tags
# ---------------------------------------------------------------------------

TAG_TARGETS = ("item", "container", "location", "kit")
TAG_STATUSES = ("active", "inactive", "lost")


class Tag(Base):
    """An NFC sticker (or any other physical tag) pointing at a record.

    ``ref`` is a random, opaque reference written to the tag inside a link
    to the app; it reveals nothing (no database ids) and grants nothing -
    opening it still needs a Home Assistant login.  ``hardware_id`` is the
    chip's serial number when the phone can read it (Android Web NFC); it is
    optional because iOS and many apps don't expose it.  The tag points at
    the record, not a place, so links keep working when things move.
    """

    __tablename__ = "tags"
    __table_args__ = (
        CheckConstraint("target_type IN ('item', 'container', 'location', 'kit')", name="ck_tag_target_type"),
        CheckConstraint("status IN ('active', 'inactive', 'lost')", name="ck_tag_status"),
        Index("ix_tags_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ref: Mapped[str] = mapped_column(String(40), unique=True)
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), default="active", index=True)
    hardware_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # What to show when scanned: view, move, adjust, service, meter, repair, return.
    default_action: Mapped[str] = mapped_column(String(20), default="view")
    replaces_tag_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id", ondelete="SET NULL"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_count: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Replenishment and shopping list
# ---------------------------------------------------------------------------

RETAILERS = ("amazon", "lowes", "tractor_supply", "other")
SHOPPING_STATUSES = ("listed", "handed_off", "in_cart", "purchased", "deferred", "cancelled")


class ReplenishmentProfile(TimestampMixin, Base):
    """Restocking settings for a product (stock is counted across holdings).

    The minimum is the product's existing ``min_quantity`` (low-stock
    threshold); this adds the level to restock up to and buying details.
    """

    __tablename__ = "replenishment_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), unique=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    target_stock: Mapped[int | None] = mapped_column(Integer)
    reorder_quantity: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[str | None] = mapped_column(String(30))
    preferred_name: Mapped[str | None] = mapped_column(String(300))
    notes: Mapped[str | None] = mapped_column(Text)
    last_purchased_on: Mapped[date | None] = mapped_column(Date)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))


class ProductSource(TimestampMixin, Base):
    """Where to buy a product (several per product; one preferred)."""

    __tablename__ = "product_sources"
    __table_args__ = (
        CheckConstraint("retailer IN ('amazon', 'lowes', 'tractor_supply', 'other')", name="ck_source_retailer"),
        CheckConstraint("pack_size >= 1", name="ck_source_pack_size"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    retailer: Mapped[str] = mapped_column(String(20))
    retailer_name: Mapped[str | None] = mapped_column(String(100))  # for "other"
    url: Mapped[str | None] = mapped_column(String(2000))
    retailer_product_id: Mapped[str | None] = mapped_column(String(60))  # ASIN, Lowe's item #, TSC SKU
    gtin: Mapped[str | None] = mapped_column(String(14))
    product_name: Mapped[str | None] = mapped_column(String(300))
    brand_model: Mapped[str | None] = mapped_column(String(200))
    pack_size: Mapped[int] = mapped_column(Integer, default=1)
    estimated_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    preferred: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ShoppingListItem(TimestampMixin, Base):
    """Something to buy.

    ``handed_off`` means the product page or a cart link was opened;
    ``in_cart`` is only ever set when the user confirms it - the app never
    claims a retailer cart was changed.
    """

    __tablename__ = "shopping_list_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('listed', 'handed_off', 'in_cart', 'purchased', 'deferred', 'cancelled')", name="ck_shopping_status"
        ),
        CheckConstraint("quantity >= 1", name="ck_shopping_quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("product_sources.id", ondelete="SET NULL"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="listed", index=True)
    deferred_until: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(500))
    handed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purchased_quantity: Mapped[int | None] = mapped_column(Integer)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------------------
# Parts: what fits what, standards, and what each service needs
# ---------------------------------------------------------------------------

LINK_STATUSES = ("suggested", "confirmed", "rejected")


class ProductLink(Base):
    """Between two products.

    * ``meets``: ``product`` meets the standard ``target`` (a generic
      product): Castrol GTX 10W-30 meets "Engine oil 10W-30, API SN".
    * ``replaced_by``: the part number ``product`` was superseded by
      ``target``.

    Only *confirmed* links count as stock for something else; suggestions
    (from a label, a manual or matching specs) wait for a person.  Links are
    never followed more than one step.
    """

    __tablename__ = "product_links"
    __table_args__ = (
        UniqueConstraint("product_id", "target_id", "kind", name="uq_product_link"),
        CheckConstraint("kind IN ('meets', 'replaced_by')", name="ck_product_link_kind"),
        CheckConstraint("status IN ('suggested', 'confirmed', 'rejected')", name="ck_product_link_status"),
        CheckConstraint("product_id != target_id", name="ck_product_link_not_self"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(10), default="suggested")
    # user | manual | label | specs
    source: Mapped[str] = mapped_column(String(12), default="user")
    note: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PartFitment(Base):
    """A part or consumable that a model of equipment takes.

    Belongs to the equipment's *product* (its model), so every machine of
    that model shares it.  ``source_ref`` says where it came from ("Owner's
    manual, page 14").
    """

    __tablename__ = "part_fitments"
    __table_args__ = (
        UniqueConstraint("equipment_product_id", "part_product_id", "role", name="uq_part_fitment"),
        CheckConstraint("status IN ('suggested', 'confirmed', 'rejected')", name="ck_fitment_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equipment_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    part_product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(80), default="")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    unit: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default="suggested")
    source: Mapped[str] = mapped_column(String(12), default="user")
    source_ref: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TaskPart(Base):
    """What one maintenance task uses each time it's done.

    ``product`` can be a specific part or a standard (generic product).
    With ``allow_equivalent`` a specific product may be replaced by anything
    that meets the same standard, or by its confirmed replacement.
    ``unit`` NULL means the product's own unit (pieces, or its content unit).
    """

    __tablename__ = "task_parts"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_task_part_quantity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("maintenance_tasks.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), default=Decimal("1"))
    unit: Mapped[str | None] = mapped_column(String(10))
    allow_equivalent: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None] = mapped_column(String(200))

    product: Mapped[Product] = relationship()


class ManualReading(Base):
    """An equipment manual (PDF or photos of pages) read by AI.

    The result is a suggestion: services with intervals and what each one
    needs, parts, consumables and capacities.  Nothing changes until a
    person reviews and applies it.
    """

    __tablename__ = "manual_readings"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'ready', 'failed', 'applied', 'discarded')", name="ck_manual_reading_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    attachment_ids: Mapped[list[Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentReading(Base):
    """What AI made of a document attached to an item: what kind of
    document it is, and values it found for the item's fields.  Suggestions
    only - a person chooses what to apply."""

    __tablename__ = "document_readings"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'ready', 'failed', 'applied', 'dismissed')", name="ck_document_reading_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attachment_id: Mapped[int] = mapped_column(ForeignKey("attachments.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

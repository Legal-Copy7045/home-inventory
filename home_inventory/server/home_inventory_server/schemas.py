"""Request validation models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import IdentificationStatus, ItemStatus, Role

Str200 = Field(default=None, max_length=200)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str) and not v.strip():
        return None
    return v


class LocationIn(_In):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None
    kind: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=5000)
    sort_order: int = 0


class LocationPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | None = None
    kind: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=5000)
    sort_order: int | None = None


class ContainerIn(_In):
    name: str = Field(min_length=1, max_length=200)
    number: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=5000)
    container_type: str | None = Field(default=None, max_length=60)
    location_id: int | None = None
    parent_container_id: int | None = None

    @model_validator(mode="after")
    def _one_place(self):
        if self.location_id is not None and self.parent_container_id is not None:
            raise ValueError("Choose either a location or a parent container")
        return self


class ContainerPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    number: str | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=5000)
    container_type: str | None = Field(default=None, max_length=60)


class ProductFields(_In):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, max_length=120)
    manufacturer: str | None = Field(default=None, max_length=200)
    model_number: str | None = Field(default=None, max_length=120)
    barcode: str | None = Field(default=None, max_length=64)
    barcode_format: str | None = Field(default=None, max_length=20)
    sku: str | None = Field(default=None, max_length=120)
    tags: str | None = Field(default=None, max_length=1000)
    min_quantity: int | None = Field(default=None, ge=0, le=100000)
    url: str | None = Field(default=None, max_length=2000)

    _blank = field_validator(
        "description", "category", "manufacturer", "model_number", "barcode", "sku", "tags", "min_quantity", "url",
        mode="before",
    )(_blank_to_none)

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str | None) -> str | None:
        if v is not None and not v.lower().startswith(("http://", "https://")):
            raise ValueError("The product link must start with http:// or https://")
        return v


class PurchaseFields(_In):
    purchase_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    purchase_date: date | None = None
    retailer: str | None = Field(default=None, max_length=200)
    warranty_start: date | None = None
    warranty_expires: date | None = None
    serial_number: str | None = Field(default=None, max_length=120)
    expires_on: date | None = None

    _blank = field_validator(
        "purchase_price", "currency", "purchase_date", "retailer", "warranty_start", "warranty_expires",
        "serial_number", "expires_on", mode="before",
    )(_blank_to_none)


class ItemCreate(ProductFields, PurchaseFields):
    product_id: int | None = None
    quantity: int = Field(default=1, ge=0, le=100000)
    # Create ``quantity`` separate records of quantity 1 (e.g. power tools
    # with serial numbers) instead of one record with quantity N.
    track_individually: bool = False
    label: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=10000)
    container_id: int | None = None
    location_id: int | None = None
    photo_ids: list[int] = Field(default_factory=list, max_length=50)
    document_ids: list[int] = Field(default_factory=list, max_length=50)
    identification_status: IdentificationStatus = IdentificationStatus.manual
    ai_metadata: dict[str, Any] | None = None
    catalog_metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.product_id is None and not self.name:
            raise ValueError("A name is required")
        if self.container_id is not None and self.location_id is not None:
            raise ValueError("Choose either a container or a location")
        if self.track_individually and self.quantity > 200:
            raise ValueError("Too many individual records; use a quantity instead")
        return self


class ItemPatch(ProductFields, PurchaseFields):
    quantity: int | None = Field(default=None, ge=0, le=100000)
    label: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=10000)
    identification_status: IdentificationStatus | None = None
    primary_photo_id: int | None = None


class PlaceItemsIn(_In):
    """Put several items in one place (sorting quick photos onto shelves)."""

    item_ids: list[int] = Field(min_length=1, max_length=500)
    container_id: int | None = None
    location_id: int | None = None

    @model_validator(mode="after")
    def _one_place(self):
        if self.container_id is not None and self.location_id is not None:
            raise ValueError("Choose either a container or a location")
        return self


class MoveIn(_In):
    container_id: int | None = None
    location_id: int | None = None
    # Move only part of a quantity; the moved part becomes its own record.
    quantity: int | None = Field(default=None, ge=1)
    # Temporary moves remember the normal storage place for a later return.
    temporary: bool = False
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _one_place(self):
        if self.container_id is not None and self.location_id is not None:
            raise ValueError("Choose either a container or a location")
        return self


class ContainerMoveIn(_In):
    location_id: int | None = None
    parent_container_id: int | None = None
    temporary: bool = False
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _one_place(self):
        if self.location_id is not None and self.parent_container_id is not None:
            raise ValueError("Choose either a location or a parent container")
        return self


class AdjustIn(_In):
    """Quick quantity change, e.g. "used one" (-1) or "bought more" (+4)."""

    delta: int = Field(ge=-100000, le=100000)
    note: str | None = Field(default=None, max_length=1000)


class StatusIn(_In):
    status: ItemStatus
    note: str | None = Field(default=None, max_length=1000)


class LoanIn(_In):
    borrower: str = Field(min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=1)
    loaned_at: date | None = None
    expected_return: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ReturnIn(_In):
    returned_at: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ScanItemIn(_In):
    """One reviewed suggestion from a container photo analysis."""

    action: Literal["add", "increase", "skip"] = "add"
    existing_item_id: int | None = None  # for "increase"
    product_id: int | None = None  # attach to an existing product identity
    name: str | None = Field(default=None, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, max_length=120)
    manufacturer: str | None = Field(default=None, max_length=200)
    model_number: str | None = Field(default=None, max_length=120)
    quantity: int = Field(default=1, ge=1, le=10000)
    confidence: float | None = None


class ScanConfirmIn(_In):
    items: list[ScanItemIn] = Field(max_length=500)
    # Optionally remove items that are no longer in the container.
    remove_item_ids: list[int] = Field(default_factory=list)


class MapIn(_In):
    title: str = Field(min_length=1, max_length=200)
    location_id: int | None = None
    photo_id: int


class MapPatch(_In):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    location_id: int | None = None


class MarkerIn(_In):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    label: str | None = Field(default=None, max_length=120)
    location_id: int | None = None
    container_id: int | None = None

    @model_validator(mode="after")
    def _one_target(self):
        if (self.location_id is None) == (self.container_id is None):
            raise ValueError("A marker must point to exactly one location or container")
        return self


class MarkersIn(_In):
    markers: list[MarkerIn] = Field(max_length=500)


class LabelRequest(_In):
    container_ids: list[int] = Field(default_factory=list, max_length=500)
    location_ids: list[int] = Field(default_factory=list, max_length=500)
    # Address the browser used to open the App (e.g.
    # https://ha.example.com/abcd1234_home_inventory); used for QR links when
    # no address is set in Settings.
    base_url: str | None = Field(default=None, max_length=500)
    template: str = "avery_l7160"
    include_contents: bool = False
    include_location: bool = True
    copies: int = Field(default=1, ge=1, le=20)
    start_position: int = Field(default=1, ge=1, le=200)

    @model_validator(mode="after")
    def _something(self):
        if not self.container_ids and not self.location_ids:
            raise ValueError("Choose at least one container or location")
        return self


class UserRoleIn(_In):
    role: Role | None


class CategoryIn(_In):
    name: str = Field(min_length=1, max_length=120)
    synonyms: str | None = Field(default=None, max_length=2000)


class SettingsIn(_In):
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    qr_base_url: str | None = Field(default=None, max_length=500)
    synonyms: dict[str, list[str]] | None = None
    # Amazon store for product links and the Add to Cart form, e.g. amazon.com
    amazon_domain: str | None = Field(default=None, max_length=30, pattern=r"^(www\.)?amazon\.[a-z.]{2,8}$")
    # Optional: your own Amazon Associates tag (only sent in links you open).
    amazon_associate_tag: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9-]*$")


class BarcodeLookupIn(_In):
    code: str = Field(min_length=1, max_length=64)
    format: str | None = Field(default=None, max_length=20)
    external: bool = True


class LinkImportIn(_In):
    url: str = Field(min_length=8, max_length=2000)
    download_image: bool = True


class SearchIn(_In):
    q: str = Field(min_length=1, max_length=500)
    use_ai: bool = True
    limit: int = Field(default=50, ge=1, le=200)


# ---------------------------------------------------------------------------
# Equipment maintenance
# ---------------------------------------------------------------------------

Hours = Decimal


class SpecEntry(_In):
    label: str = Field(min_length=1, max_length=120)
    value: str | None = Field(default=None, max_length=300)
    kind: Literal["spec", "part"] = "spec"
    part_number: str | None = Field(default=None, max_length=120)


class EquipmentIn(_In):
    preset: str | None = Field(default=None, max_length=40)
    equipment_type: str | None = Field(default=None, max_length=80)
    engine_manufacturer: str | None = Field(default=None, max_length=120)
    engine_model: str | None = Field(default=None, max_length=120)
    engine_serial: str | None = Field(default=None, max_length=120)
    fuel_type: str | None = Field(default=None, max_length=60)
    meter: Literal["none", "hours", "cycles", "both"] | None = None
    specs: list[SpecEntry] | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=10000)
    # When starting to track used equipment: schedules start from here.
    current_hours: Hours | None = Field(default=None, ge=0, le=1_000_000, max_digits=10, decimal_places=1)
    current_cycles: int | None = Field(default=None, ge=0)

    _blank = field_validator(
        "equipment_type", "engine_manufacturer", "engine_model", "engine_serial", "fuel_type", "notes", mode="before"
    )(_blank_to_none)


class TaskFields(_In):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    instructions: str | None = Field(default=None, max_length=5000)
    interval_days: int | None = Field(default=None, gt=0, le=3650)
    interval_hours: Hours | None = Field(default=None, gt=0, le=100000, max_digits=10, decimal_places=1)
    interval_cycles: int | None = Field(default=None, gt=0, le=100000)
    due_soon_days: int | None = Field(default=None, ge=0, le=365)
    due_soon_hours: Hours | None = Field(default=None, ge=0, le=10000, max_digits=10, decimal_places=1)
    due_soon_cycles: int | None = Field(default=None, ge=0, le=10000)
    last_done_on: date | None = None
    last_done_hours: Hours | None = Field(default=None, ge=0, max_digits=10, decimal_places=1)
    last_done_cycles: int | None = Field(default=None, ge=0)
    next_due_on: date | None = None
    next_due_hours: Hours | None = Field(default=None, ge=0, max_digits=10, decimal_places=1)
    next_due_cycles: int | None = Field(default=None, ge=0)

    _blank = field_validator("instructions", mode="before")(_blank_to_none)


class TaskIn(TaskFields):
    title: str = Field(min_length=1, max_length=200)
    first_due_on: date | None = None


class PartIn(_In):
    description: str = Field(min_length=1, max_length=300)
    part_number: str | None = Field(default=None, max_length=120)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=10, decimal_places=2)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    item_id: int | None = None
    # Take the quantity out of that inventory holding.
    deduct_stock: bool = False


class ServiceIn(_In):
    title: str | None = Field(default=None, max_length=200)
    performed_on: date | None = None
    hours: Hours | None = Field(default=None, ge=0, max_digits=10, decimal_places=1)
    cycles: int | None = Field(default=None, ge=0)
    work_performed: str | None = Field(default=None, max_length=10000)
    notes: str | None = Field(default=None, max_length=10000)
    performed_by: str | None = Field(default=None, max_length=120)
    labor_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    parts: list[PartIn] = Field(default_factory=list, max_length=50)
    attachment_ids: list[int] = Field(default_factory=list, max_length=30)

    _blank = field_validator("title", "work_performed", "notes", "performed_by", mode="before")(_blank_to_none)


class SkipIn(_In):
    note: str | None = Field(default=None, max_length=2000)


class DeferIn(_In):
    until: date
    note: str | None = Field(default=None, max_length=2000)


class NotApplicableIn(_In):
    value: bool = True
    note: str | None = Field(default=None, max_length=2000)


class MeterReadingIn(_In):
    kind: Literal["hours", "cycles"] = "hours"
    value: Hours = Field(ge=0, le=1_000_000, max_digits=10, decimal_places=1)
    estimated: bool = False
    correction: bool = False
    note: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Customers and repair jobs
# ---------------------------------------------------------------------------


class CustomerIn(_In):
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=200)
    address: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=10000)

    _blank = field_validator("phone", "email", "address", "notes", mode="before")(_blank_to_none)


class CustomerPatch(CustomerIn):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class ContactIn(_In):
    channel: Literal["phone", "text", "email", "in_person", "other"] | None = None
    note: str = Field(min_length=1, max_length=5000)


class OwnerIn(_In):
    customer_id: int | None
    # Required: ownership changes are deliberate, never a side effect.
    confirm: bool = False


class CustomerEquipmentIn(_In):
    name: str = Field(min_length=1, max_length=300)
    manufacturer: str | None = Field(default=None, max_length=200)
    model_number: str | None = Field(default=None, max_length=120)
    serial_number: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    category: str | None = Field(default=None, max_length=120)
    container_id: int | None = None
    location_id: int | None = None
    notes: str | None = Field(default=None, max_length=5000)


RepairStatus = Literal[
    "received", "awaiting_diagnosis", "awaiting_approval", "waiting_for_parts", "in_progress", "completed", "returned", "cancelled"
]


class RepairJobFields(_In):
    received_on: date | None = None
    condition_received: str | None = Field(default=None, max_length=10000)
    reported_problem: str | None = Field(default=None, max_length=10000)
    diagnosis: str | None = Field(default=None, max_length=10000)
    approval_notes: str | None = Field(default=None, max_length=10000)
    estimate: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    labor_charge: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    parts_charge: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    completed_on: date | None = None
    returned_on: date | None = None
    notes: str | None = Field(default=None, max_length=10000)

    _blank = field_validator("condition_received", "reported_problem", "diagnosis", "approval_notes", "notes", mode="before")(
        _blank_to_none
    )


class RepairJobIn(RepairJobFields):
    customer_id: int
    item_id: int | None = None
    new_equipment: CustomerEquipmentIn | None = None


class RepairStatusIn(_In):
    status: RepairStatus
    note: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Relationships and kits
# ---------------------------------------------------------------------------


class RelationIn(_In):
    other_item_id: int
    relation_type: Literal["accessory_of", "component_of", "compatible_with", "stored_with", "replacement_for", "custom"]
    # "this_to_other": this item is <type> the other (e.g. this hose is an
    # accessory of that compressor).  "other_to_this": the reverse.
    direction: Literal["this_to_other", "other_to_this"] = "this_to_other"
    custom_label: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=300)


class KitIn(_In):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    container_id: int | None = None
    location_id: int | None = None
    item_ids: list[int] = Field(default_factory=list, max_length=200)


class KitPatch(_In):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    container_id: int | None = None
    location_id: int | None = None


class KitMemberIn(_In):
    item_id: int
    quantity: int = Field(default=1, ge=1, le=10000)
    optional: bool = False
    note: str | None = Field(default=None, max_length=300)


class KitMemberPatch(_In):
    quantity: int | None = Field(default=None, ge=1, le=10000)
    optional: bool | None = None
    note: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# Replenishment
# ---------------------------------------------------------------------------


class RestockIn(_In):
    enabled: bool | None = None
    min_quantity: int | None = Field(default=None, ge=0, le=1_000_000)
    target_stock: int | None = Field(default=None, ge=0, le=1_000_000)
    reorder_quantity: int | None = Field(default=None, ge=1, le=1_000_000)
    unit: str | None = Field(default=None, max_length=30)
    preferred_name: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)

    _blank = field_validator("unit", "preferred_name", "notes", mode="before")(_blank_to_none)


class SourceIn(_In):
    retailer: Literal["amazon", "lowes", "tractor_supply", "other"] = "other"
    retailer_name: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=2000)
    retailer_product_id: str | None = Field(default=None, max_length=60)
    gtin: str | None = Field(default=None, max_length=14)
    product_name: str | None = Field(default=None, max_length=300)
    brand_model: str | None = Field(default=None, max_length=200)
    pack_size: int = Field(default=1, ge=1, le=10000)
    estimated_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    preferred: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)

    _blank = field_validator("retailer_name", "url", "retailer_product_id", "gtin", "product_name", "brand_model", "notes",
                             mode="before")(_blank_to_none)


class SourcePatch(SourceIn):
    retailer: Literal["amazon", "lowes", "tractor_supply", "other"] | None = None
    pack_size: int | None = Field(default=None, ge=1, le=10000)


class ShoppingAddIn(_In):
    product_id: int
    quantity: int | None = Field(default=None, ge=1, le=100000)
    source_id: int | None = None
    note: str | None = Field(default=None, max_length=500)


class ShoppingPatch(_In):
    quantity: int | None = Field(default=None, ge=1, le=100000)
    source_id: int | None = None
    status: Literal["listed", "handed_off", "in_cart", "deferred", "cancelled"] | None = None
    deferred_until: date | None = None
    note: str | None = Field(default=None, max_length=500)


class HandoffIn(_In):
    entry_ids: list[int] = Field(min_length=1, max_length=100)


class PurchasedIn(_In):
    quantity: int | None = Field(default=None, ge=1, le=100000)
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    add_to_stock: bool = True
    item_id: int | None = None

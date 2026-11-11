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

    _blank = field_validator(
        "description", "category", "manufacturer", "model_number", "barcode", "sku", "tags", "min_quantity", mode="before"
    )(_blank_to_none)


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
    container_ids: list[int] = Field(min_length=1, max_length=500)
    template: str = "avery_l7160"
    include_contents: bool = False
    include_location: bool = True
    copies: int = Field(default=1, ge=1, le=20)
    start_position: int = Field(default=1, ge=1, le=200)


class UserRoleIn(_In):
    role: Role | None


class CategoryIn(_In):
    name: str = Field(min_length=1, max_length=120)
    synonyms: str | None = Field(default=None, max_length=2000)


class SettingsIn(_In):
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    qr_base_url: str | None = Field(default=None, max_length=500)
    synonyms: dict[str, list[str]] | None = None


class BarcodeLookupIn(_In):
    code: str = Field(min_length=1, max_length=64)
    format: str | None = Field(default=None, max_length=20)
    external: bool = True


class SearchIn(_In):
    q: str = Field(min_length=1, max_length=500)
    use_ai: bool = True
    limit: int = Field(default=50, ge=1, le=200)

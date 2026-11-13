"""Inventory export and the insurance report.

One row builder feeds three outputs: the printable report (JSON, with photo
thumbnails), CSV and Excel.  Values are the recorded purchase price times
quantity - not an estimate of today's replacement cost.

Customer equipment is never included: it isn't yours to insure.
"""

from __future__ import annotations

import csv
import io
from collections import OrderedDict
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Attachment, AttachmentKind, Item, Product
from .customers import household_only
from .placement import Resolver
from .stats import currency as default_currency

COLUMNS = [
    ("place", "Place"),
    ("room", "Room / area"),
    ("name", "Item"),
    ("category", "Category"),
    ("manufacturer", "Maker"),
    ("model_number", "Model"),
    ("serial_number", "Serial number"),
    ("quantity", "Quantity"),
    ("status", "Status"),
    ("purchase_date", "Purchase date"),
    ("retailer", "Bought from"),
    ("unit_price", "Price each"),
    ("currency", "Currency"),
    ("value", "Value (price × quantity)"),
    ("estimated_value", "AI estimate (replacement × quantity)"),
    ("warranty_expires", "Warranty until"),
    ("photos", "Photos"),
    ("receipts", "Receipts"),
    ("documents", "Other documents"),
    ("item_id", "Item ID"),
]


def build(db: Session, item_ids: list[int], r: Resolver) -> dict[str, Any]:
    """Rows (in the given order), grouped by room, with totals."""
    cur = default_currency(db)
    items = {
        i.id: i
        for i in db.scalars(
            select(Item).options(selectinload(Item.product).selectinload(Product.category))
            .where(Item.id.in_(item_ids), household_only())
        )
    }
    counts: dict[tuple[int, str], int] = {}
    if items:
        for item_id, kind, n in db.execute(
            select(Attachment.item_id, Attachment.kind, func.count()).where(Attachment.item_id.in_(list(items)))
            .group_by(Attachment.item_id, Attachment.kind)
        ):
            counts[(item_id, kind.value)] = n
    rows = []
    groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    total = Decimal(0)
    estimated_total = Decimal(0)
    unvalued = 0
    for item_id in item_ids:
        it = items.get(item_id)
        if it is None:
            continue
        place = r.describe_item(it)
        # The room/area it's in (its location path, without boxes).
        room = " › ".join(loc["name"] for loc in place["location_path"]) or "Unassigned"
        value = (it.purchase_price * it.quantity) if it.purchase_price is not None else None
        if value is None:
            unvalued += 1
        else:
            total += value
        est = ((it.ai_metadata or {}).get("value_estimate") or {}).get("replacement")
        estimated = (Decimal(str(est)) * it.quantity).quantize(Decimal("0.01")) if est else None
        if value is None and estimated is not None:
            estimated_total += estimated  # only fills gaps: a price paid always wins
        photo = db.get(Attachment, it.primary_photo_id) if it.primary_photo_id else None
        row = {
            "item_id": it.id,
            "place": place["path"],
            "room": room,
            "name": it.label or it.product.name,
            "category": it.product.category.name if it.product.category else None,
            "manufacturer": it.product.manufacturer,
            "model_number": it.product.model_number,
            "serial_number": it.serial_number,
            "quantity": it.quantity,
            "status": it.status.value.replace("_", " "),
            "purchase_date": it.purchase_date.isoformat() if it.purchase_date else None,
            "retailer": it.retailer,
            "unit_price": str(it.purchase_price) if it.purchase_price is not None else None,
            "currency": it.currency or (cur if it.purchase_price is not None else None),
            "value": str(value) if value is not None else None,
            "estimated_value": str(estimated) if estimated is not None else None,
            "warranty_expires": it.warranty_expires.isoformat() if it.warranty_expires else None,
            "photos": counts.get((it.id, AttachmentKind.photo.value), 0),
            "receipts": counts.get((it.id, AttachmentKind.receipt.value), 0),
            "documents": sum(counts.get((it.id, k.value), 0) for k in (AttachmentKind.manual, AttachmentKind.warranty, AttachmentKind.document)),
            "thumb_url": (f"api/v1/attachments/{photo.id}/thumb" if photo is not None and photo.thumb_path else
                          f"api/v1/attachments/{photo.id}/file" if photo is not None else None),
        }
        rows.append(row)
        g = groups.setdefault(room, {"room": room, "count": 0, "value": Decimal(0), "items": []})
        g["count"] += 1
        g["value"] += value or 0
        g["items"].append(row)
    return {
        "currency": cur,
        "count": len(rows),
        "total_value": str(total),
        "unvalued": unvalued,
        # AI replacement estimates for items without a price (a guide, not a valuation).
        "estimated_unvalued_total": str(estimated_total),
        "rows": rows,
        "groups": [{**g, "value": str(g["value"])} for g in groups.values()],
    }


def _safe(v: Any) -> Any:
    """Stop spreadsheet formula injection from item names typed by users."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def to_csv(report: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([label for _, label in COLUMNS])
    for row in report["rows"]:
        w.writerow([_safe(row.get(k)) if row.get(k) is not None else "" for k, _ in COLUMNS])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM: Excel opens UTF-8 correctly


def to_xlsx(report: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventory"
    ws.append([label for _, label in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    numeric = {"quantity", "unit_price", "value", "photos", "receipts", "documents", "item_id"}
    for row in report["rows"]:
        out = []
        for k, _ in COLUMNS:
            v = row.get(k)
            if k in numeric and v is not None:
                v = float(v) if k in ("unit_price", "value") else int(v)
            out.append(_safe(v))
        ws.append(out)
    ws.freeze_panes = "A2"
    for col, width in zip("ABCDEFGHIJKLMNOPQRS", (34, 16, 30, 16, 16, 16, 18, 9, 12, 13, 18, 11, 9, 14, 14, 8, 9, 10, 8)):
        ws.column_dimensions[col].width = width
    summary = wb.create_sheet("By room")
    summary.append(["Room / area", "Items", f"Value ({report['currency']})"])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    for g in report["groups"]:
        summary.append([_safe(g["room"]), g["count"], float(g["value"])])
    summary.append(["Total", report["count"], float(report["total_value"])])
    summary.column_dimensions["A"].width = 30
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

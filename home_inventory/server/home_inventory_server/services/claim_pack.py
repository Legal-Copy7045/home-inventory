"""Warranty claim pack: everything a shop or maker asks for, in one ZIP.

``summary.pdf`` (the item, its make, model and serial number, where and when
it was bought, the price, the warranty dates and where it's kept), followed
by the receipts, warranty documents, manuals and other documents, and up to
six photos.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Attachment, AttachmentKind, Item
from .placement import Resolver

MAX_PHOTOS = 6
EXT = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "", name).strip()[:60] or "file"


def _rows(item: Item, r: Resolver) -> list[tuple[str, str]]:
    p = item.product
    today = date.today()
    warranty = ""
    if item.warranty_expires:
        state = "in warranty" if item.warranty_expires >= today else "expired"
        start = f"{item.warranty_start.isoformat()} to " if item.warranty_start else "until "
        warranty = f"{start}{item.warranty_expires.isoformat()} ({state})"
    price = f"{item.purchase_price} {item.currency or ''}".strip() if item.purchase_price is not None else ""
    rows = [
        ("Item", p.name), ("Make", p.manufacturer or ""), ("Model", p.model_number or ""),
        ("Serial number", item.serial_number or ""), ("Barcode", p.barcode or ""),
        ("Bought from", item.retailer or ""), ("Purchase date", item.purchase_date.isoformat() if item.purchase_date else ""),
        ("Price paid", price), ("Warranty", warranty), ("Kept at", r.describe_item(item)["path"]),
    ]
    return [(k, v) for k, v in rows if v]


def summary_pdf(item: Item, r: Resolver, photo: bytes | None, files: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 25 * mm
    c.setFont("Helvetica-Bold", 18)
    c.drawString(20 * mm, y, f"Warranty claim: {item.product.name}"[:70])
    y -= 8 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, f"Prepared {date.today().isoformat()} from Home Inventory")
    y -= 12 * mm
    for k, v in _rows(item, r):
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, k)
        c.setFont("Helvetica", 11)
        c.drawString(65 * mm, y, v[:80])
        y -= 7 * mm
    if photo:
        try:
            img = ImageReader(io.BytesIO(photo))
            iw, ih = img.getSize()
            bw = 80 * mm
            bh = min(80 * mm, bw * ih / iw)
            bw = bh * iw / ih
            y -= bh + 4 * mm
            c.drawImage(img, 20 * mm, y, bw, bh)
        except Exception:  # noqa: BLE001, S110 - a photo that can't be drawn is just left out
            pass
    if files:
        y -= 10 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, "Included documents")
        c.setFont("Helvetica", 10)
        for name in files[:20]:
            y -= 6 * mm
            c.drawString(24 * mm, y, f"• {name}"[:90])
    c.showPage()
    c.save()
    return buf.getvalue()


def build(db: Session, item: Item, media) -> tuple[str, bytes]:
    r = Resolver.load(db)
    atts = list(db.scalars(select(Attachment).where(Attachment.item_id == item.id).order_by(Attachment.id)))
    order = {AttachmentKind.receipt: 0, AttachmentKind.warranty: 1, AttachmentKind.document: 2, AttachmentKind.manual: 3}
    docs = sorted((a for a in atts if a.kind != AttachmentKind.photo), key=lambda a: (order.get(a.kind, 9), a.id))
    photos = [a for a in atts if a.kind == AttachmentKind.photo]
    if item.primary_photo_id:
        photos.sort(key=lambda a: a.id != item.primary_photo_id)
    photos = photos[:MAX_PHOTOS]

    out = io.BytesIO()
    names: list[str] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n, a in enumerate(docs, 1):
            try:
                data = media.read(a.stored_path)
            except OSError:
                continue
            stem = _safe((a.caption or a.original_name or a.kind.value).rsplit(".", 1)[0])
            name = f"{n:02d} {a.kind.value} - {stem}{EXT.get(a.mime_type, '')}"
            z.writestr(name, data)
            names.append(name)
        first_photo = None
        for n, a in enumerate(photos, 1):
            try:
                data = media.read(a.stored_path)
            except OSError:
                continue
            first_photo = first_photo or data
            name = f"photos/photo {n}{EXT.get(a.mime_type, '.jpg')}"
            z.writestr(name, data)
            names.append(name)
        z.writestr("00 summary.pdf", summary_pdf(item, r, first_photo, names))
    return f"warranty-claim-{_safe(item.product.name).replace(' ', '-').lower()}.zip", out.getvalue()

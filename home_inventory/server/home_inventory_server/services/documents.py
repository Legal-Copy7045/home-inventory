"""Recognise documents attached to items.

When a document (PDF or a photo of a receipt, warranty card, manual...) is
added to an item, AI works out what it is and which of the item's fields it
has values for.  It runs in the background, like reading a manual.

Suggestions are compared with the item *when they're shown*, so they stay
right if the item changes meanwhile:

* an empty field it can fill is ticked by default;
* a field that already has a different value is offered, unticked, with
  both values shown - nothing is ever overwritten without a person
  choosing it;
* a field that already has the same value isn't offered.

A document that's clearly about another product gets no suggestions.
"""

from __future__ import annotations

import calendar
import logging
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..ai import AIFailed, AIUnavailable
from ..auth import Actor
from ..models import Attachment, AttachmentKind, DocumentReading, Item

_LOGGER = logging.getLogger(__name__)

MAX_TEXT = 12_000  # the first pages say what a document is and hold its details
STALE_AFTER = timedelta(minutes=20)
READABLE = ("application/pdf", "image/")

KIND_FOR = {
    "receipt": AttachmentKind.receipt, "invoice": AttachmentKind.receipt, "order_confirmation": AttachmentKind.receipt,
    "warranty": AttachmentKind.warranty, "manual": AttachmentKind.manual,
}
LABELS = {
    "manufacturer": "Make", "model_number": "Model", "serial_number": "Serial number", "retailer": "Bought from",
    "purchase_date": "Purchase date", "purchase_price": "Price", "currency": "Currency",
    "warranty_start": "Warranty starts", "warranty_expires": "Warranty ends",
}


class DocumentError(ValueError):
    """User-facing problem."""


def readable(att: Attachment) -> bool:
    mime = att.mime_type or ""
    return mime == "application/pdf" or mime.startswith("image/")


def _item_text(item: Item) -> str:
    p = item.product
    return ", ".join(x for x in (p.name, p.manufacturer and f"make {p.manufacturer}",
                                p.model_number and f"model {p.model_number}") if x)


# --------------------------------------------------------------------------- running


def create(db: Session, actor: Actor | None, item: Item, att: Attachment) -> DocumentReading:
    r = DocumentReading(attachment_id=att.id, item_id=item.id, created_by_id=actor.user_id if actor else None)
    db.add(r)
    db.flush()
    return r


def start(state, reading_id: int) -> threading.Thread:
    def run() -> None:
        db = state.db.SessionLocal()
        try:
            reading = db.get(DocumentReading, reading_id)
            if reading is not None:
                analyse(db, reading, state.ai, state.media)
        except Exception:  # noqa: BLE001 - recorded on the reading
            _LOGGER.exception("Reading document %s failed", reading_id)
            db.rollback()
            reading = db.get(DocumentReading, reading_id)
            if reading is not None and reading.status == "pending":
                reading.status, reading.error = "failed", "Reading the document failed unexpectedly."
                db.commit()
        finally:
            db.close()

    t = threading.Thread(target=run, name=f"document-reading-{reading_id}", daemon=True)
    t.start()
    return t


def analyse(db: Session, reading: DocumentReading, ai, media) -> DocumentReading:
    from .manuals import ManualError, pdf_pages

    att = db.get(Attachment, reading.attachment_id)
    item = db.get(Item, reading.item_id)
    try:
        if att is None or item is None:
            raise DocumentError("The document is no longer there.")
        data = media.read(att.stored_path)
        if att.mime_type == "application/pdf":
            pages = pdf_pages(data)
            if not pages:
                raise DocumentError("This PDF has no readable text (it's probably a scan). A photo of it can be read instead.")
            text, used = [], 0
            for n, t in pages:
                if used >= MAX_TEXT:
                    break
                chunk = t[: MAX_TEXT - used]
                text.append(f"[page {n}]\n{chunk}")
                used += len(chunk)
            res = ai.read_document(_item_text(item), text="\n".join(text))
        else:
            res = ai.read_document(_item_text(item), image=data)
        reading.provider, reading.model = res.provider, res.model
        reading.result = res.data
        reading.status, reading.error = "ready", None
    except (DocumentError, ManualError, AIFailed, AIUnavailable) as err:
        reading.status, reading.error = "failed", str(err)
    db.commit()
    return reading


def expire_if_stale(r: DocumentReading) -> bool:
    created = r.created_at if r.created_at and r.created_at.tzinfo else (r.created_at or datetime.now(UTC)).replace(tzinfo=UTC)
    if r.status == "pending" and datetime.now(UTC) - created > STALE_AFTER:
        r.status, r.error = "failed", "The reading was interrupted. Try again."
        return True
    return False


# --------------------------------------------------------------------------- suggestions


def _add_months(start: date, months: int) -> date:
    y, m = divmod(start.month - 1 + months, 12)
    day = min(start.day, calendar.monthrange(start.year + y, m + 1)[1])
    return date(start.year + y, m + 1, day)


def found_values(result: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    """field -> (value, note) for what the document gives."""
    out: dict[str, tuple[str, str | None]] = {}
    for f in ("manufacturer", "model_number", "serial_number", "retailer", "purchase_date"):
        if result.get(f):
            out[f] = (result[f], None)
    if result.get("item_price"):
        out["purchase_price"] = (result["item_price"], None)
    elif result.get("total") and result.get("document_type") in ("receipt", "invoice", "order_confirmation"):
        out["purchase_price"] = (result["total"], "the total paid; the item's own line wasn't clear")
    if "purchase_price" in out and result.get("currency"):
        out["currency"] = (result["currency"], None)  # only meaningful with a price
    expires, start = result.get("warranty_expires"), result.get("purchase_date")
    if not expires and start and result.get("warranty_months"):
        expires = _add_months(date.fromisoformat(start), result["warranty_months"]).isoformat()
        out["warranty_expires"] = (expires, f"{result['warranty_months']} months from the purchase date")
    elif expires:
        out["warranty_expires"] = (expires, None)
    if expires and start:
        out["warranty_start"] = (start, None)
    return out


def _current(item: Item, field: str) -> str | None:
    obj = item.product if field in ("manufacturer", "model_number") else item
    v = getattr(obj, field)
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    return v.isoformat() if isinstance(v, date) else str(v)


def _same(a: str, b: str) -> bool:
    return "".join(a.lower().split()) == "".join(b.lower().split())


def suggestions(item: Item, att: Attachment | None, result: dict[str, Any]) -> dict[str, Any]:
    fields = []
    if result.get("matches_item", True):
        for f, (value, note) in found_values(result).items():
            cur = _current(item, f)
            if cur is not None and _same(cur, value):
                continue
            fields.append({"field": f, "label": LABELS[f], "value": value, "current": cur, "note": note,
                           "selected": cur is None})
    kind = KIND_FOR.get(result.get("document_type") or "", AttachmentKind.document)
    return {
        "fields": fields,
        "kind": kind.value if att is not None and att.kind != kind and att.kind != AttachmentKind.photo else None,
        "caption": result.get("title") if att is not None and not att.caption else None,
        "is_manual": result.get("document_type") == "manual",
    }


def reading_out(db: Session, r: DocumentReading) -> dict[str, Any]:
    out: dict[str, Any] = {"id": r.id, "attachment_id": r.attachment_id, "item_id": r.item_id, "status": r.status,
                           "error": r.error, "result": r.result,
                           "created_at": r.created_at.isoformat() if r.created_at else None}
    if r.status == "ready" and r.result:
        out["suggestions"] = suggestions(db.get(Item, r.item_id), db.get(Attachment, r.attachment_id), r.result)
    return out


def latest_for_item(db: Session, item_id: int) -> list[DocumentReading]:
    rows = db.scalars(select(DocumentReading).where(DocumentReading.item_id == item_id).order_by(DocumentReading.id.desc()))
    seen, out = set(), []
    for r in rows:
        if r.attachment_id not in seen:
            seen.add(r.attachment_id)
            out.append(r)
    return out


def running_for(db: Session, attachment_id: int) -> DocumentReading | None:
    r = db.scalar(select(DocumentReading).where(DocumentReading.attachment_id == attachment_id,
                                                DocumentReading.status == "pending"))
    if r is not None and expire_if_stale(r):
        return None
    return r


# --------------------------------------------------------------------------- applying


def apply(db: Session, actor: Actor, reading: DocumentReading, values: dict[str, Any], kind: str | None,
          caption: str | None) -> None:
    """Apply what the person chose.  ``values`` are item fields, validated by
    the normal item update (the person may have corrected them)."""
    from ..schemas import ItemPatch
    from .inventory import update_item

    if reading.status != "ready":
        raise DocumentError("This document's suggestions have already been handled")
    item = db.get(Item, reading.item_id)
    att = db.get(Attachment, reading.attachment_id)
    allowed = set(LABELS)
    unknown = set(values) - allowed
    if unknown:
        raise DocumentError(f"Can't set {', '.join(sorted(unknown))} from a document")
    if values:
        update_item(db, actor, item, ItemPatch(**values))
    if att is not None and kind:
        try:
            att.kind = AttachmentKind(kind)
        except ValueError as err:
            raise DocumentError("Unknown document type") from err
        if att.kind == AttachmentKind.photo:
            raise DocumentError("A document can't be turned into a photo")
    if att is not None and caption:
        att.caption = caption[:300]
    reading.status = "applied"
    reading.applied_at = datetime.now(UTC)
    if values or kind or caption:
        audit.record(db, actor, "document_applied", "item", item.id,
                     f"{item.product.name}: details from {(att.caption or att.original_name) if att else 'a document'}"
                     f" ({', '.join(LABELS[f] for f in values) or 'document type'})")

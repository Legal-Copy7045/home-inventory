"""Recognising a document attached to an item.

The model says what kind of document it is and copies out the values that
belong in the item's fields - purchase details from a receipt, the warranty
term from a warranty card, the model and serial number from a manual or a
registration certificate.  Only what is printed; nothing is guessed, and a
document about a different product is flagged instead of mined for values.
"""

from __future__ import annotations

from datetime import date
from typing import Any

_S = {"type": ["string", "null"]}
_N = {"type": ["number", "null"]}

DOC_TYPES = ("receipt", "invoice", "order_confirmation", "warranty", "manual", "spec_sheet", "insurance",
             "certificate", "service_record", "other")

DOCUMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": list(DOC_TYPES)},
        "title": {"type": "string"},
        "summary": _S,
        "matches_item": {"type": "boolean"},
        "product_name": _S,
        "manufacturer": _S,
        "model_number": _S,
        "serial_number": _S,
        "retailer": _S,
        "purchase_date": _S,
        "currency": _S,
        "item_price": _N,
        "total": _N,
        "warranty_months": {"type": ["integer", "null"]},
        "warranty_expires": _S,
        "confidence": {"type": "number"},
    },
    "required": ["document_type", "title", "summary", "matches_item", "product_name", "manufacturer", "model_number",
                 "serial_number", "retailer", "purchase_date", "currency", "item_price", "total", "warranty_months",
                 "warranty_expires", "confidence"],
    "additionalProperties": False,
}

DOCUMENT_PROMPT = """A document has been attached to an item in a household inventory. Say what it is and copy out
values for the item's record. Return JSON only. Use null for anything not printed in the document - NEVER guess or
complete a model or serial number, a date or a price.

"document_type": receipt (shop or till receipt), invoice, order_confirmation (online order / dispatch email),
warranty (warranty card, guarantee, extended warranty), manual (owner's / user / instruction manual), spec_sheet
(specifications, data sheet), insurance, certificate (registration, certificate of conformity, safety test),
service_record (service or repair report), or other. "title": a short name for the document, e.g.
"Lowe's receipt", "Owner's manual", "5-year warranty". "summary": one sentence.
"matches_item": false if the document is clearly about a different product than the item below.
"purchase_date" and "warranty_expires": YYYY-MM-DD. "currency": 3-letter ISO code (only when unambiguous, e.g. £ is
GBP). "item_price": the price of the line for this item (one unit); "total": the amount paid.
"warranty_months": the warranty length if stated (2 years = 24). "confidence" 0..1.
The item: {item}"""


def _s(v: Any, n: int) -> str | None:
    if v is None:
        return None
    v = " ".join(str(v).split())
    return v[:n] or None


def _day(v: Any) -> str | None:
    try:
        d = date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None
    return d.isoformat() if 1950 <= d.year <= 2100 else None


def _money(v: Any) -> str | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f"{f:.2f}" if 0 <= f < 10_000_000 else None


def clean_document(d: dict[str, Any]) -> dict[str, Any]:
    cur = str(d.get("currency") or "").strip().upper()
    try:
        months = int(d.get("warranty_months")) if d.get("warranty_months") is not None else None
    except (TypeError, ValueError):
        months = None
    try:
        conf = max(0.0, min(float(d.get("confidence")), 1.0)) if d.get("confidence") is not None else None
    except (TypeError, ValueError):
        conf = None
    return {
        "document_type": d.get("document_type") if d.get("document_type") in DOC_TYPES else "other",
        "title": _s(d.get("title"), 120) or "Document",
        "summary": _s(d.get("summary"), 300),
        "matches_item": d.get("matches_item") is not False,
        "product_name": _s(d.get("product_name"), 300),
        "manufacturer": _s(d.get("manufacturer"), 200),
        "model_number": _s(d.get("model_number"), 120),
        "serial_number": _s(d.get("serial_number"), 120),
        "retailer": _s(d.get("retailer"), 200),
        "purchase_date": _day(d.get("purchase_date")),
        "currency": cur if len(cur) == 3 and cur.isalpha() else None,
        "item_price": _money(d.get("item_price")),
        "total": _money(d.get("total")),
        "warranty_months": months if months is not None and 0 < months <= 240 else None,
        "warranty_expires": _day(d.get("warranty_expires")),
        "confidence": conf,
    }

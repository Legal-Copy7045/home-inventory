"""Provider-independent AI services.

The rest of the application only uses :class:`AIService`.  Providers turn a
prompt (+ optional image) and a JSON schema into a JSON object; the service
owns prompts, validation, retries, image down-scaling and error mapping.

AI output is never written to the inventory directly: results are returned
to the user for review (item identification) or stored as pending
suggestions (container scans).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..services.media import downscale_for_ai
from .providers import AIProviderError, Provider, build_provider

_LOGGER = logging.getLogger(__name__)


class AIUnavailable(RuntimeError):
    pass


class AIFailed(RuntimeError):
    pass


_NULLABLE_STR = {"type": ["string", "null"]}

ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": _NULLABLE_STR,
        "category": _NULLABLE_STR,
        "manufacturer": _NULLABLE_STR,
        "model_number": _NULLABLE_STR,
        "confidence": {"type": "number"},
        "notes": _NULLABLE_STR,
    },
    "required": ["name", "description", "category", "manufacturer", "model_number", "confidence", "notes"],
    "additionalProperties": False,
}

CONTAINER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": _NULLABLE_STR,
                    "category": _NULLABLE_STR,
                    "manufacturer": _NULLABLE_STR,
                    "model_number": _NULLABLE_STR,
                    "quantity": {"type": "integer"},
                    "confidence": {"type": "number"},
                    # Where in the photo, e.g. "top shelf, left" (shelf photos).
                    "position": _NULLABLE_STR,
                },
                "required": ["name", "description", "category", "manufacturer", "model_number", "quantity", "confidence", "position"],
                "additionalProperties": False,
            },
        },
        "notes": _NULLABLE_STR,
    },
    "required": ["items", "notes"],
    "additionalProperties": False,
}

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["find", "count", "loans", "warranty", "expiry", "list"]},
        "terms": {
            "type": "array",
            "description": "Each entry is a group of interchangeable words; an item must match every group.",
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "manufacturer": _NULLABLE_STR,
        "category": _NULLABLE_STR,
        "status": {
            "anyOf": [
                {"type": "string", "enum": ["stored", "in_use", "temporarily_relocated", "on_loan", "awaiting_return", "missing"]},
                {"type": "null"},
            ]
        },
    },
    "required": ["intent", "terms", "manufacturer", "category", "status"],
    "additionalProperties": False,
}

ITEM_PROMPT = """You help catalogue a household inventory. Identify the single main object in the photo.
Return JSON only. Use null for anything you cannot read or infer with reasonable confidence; never guess
model numbers. "name" is a short, searchable everyday name (e.g. "Cordless drill", "USB-C charger 65W").
"category" is a broad household category (e.g. Tools, Electronics, Kitchen, Garden, Holiday decorations,
Automotive, Sports, Toys, Clothing, Cleaning, Documents, Hardware, Camping).
"confidence" is 0..1 for the name.{hint}"""

CONTAINER_PROMPT = """You help catalogue a household inventory. The photo shows the open contents of a storage
container. List every distinct kind of object you can see, grouping identical objects with a quantity.
Return JSON only. Use null when unsure; never invent brands or model numbers. Use short everyday names.
"confidence" is 0..1 for each entry. Ignore the container itself and packing material.{hint}"""

RECEIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "retailer": _NULLABLE_STR,
        "purchase_date": _NULLABLE_STR,
        "currency": _NULLABLE_STR,
        "total": {"type": ["number", "null"]},
        "item_price": {"type": ["number", "null"]},
        "warranty_months": {"type": ["integer", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["retailer", "purchase_date", "currency", "total", "item_price", "warranty_months", "confidence"],
    "additionalProperties": False,
}

RECEIPT_PROMPT = """The photo is a shop receipt, invoice or order confirmation. Read it. Return JSON only; use null for
anything not printed on it, never guess. "retailer": the shop's name. "purchase_date": YYYY-MM-DD. "currency": the
3-letter ISO code (infer from the symbol only if unambiguous, e.g. £ is GBP). "total": the amount paid.
"item_price": the price of the line that best matches the item below (the price for one unit), or null.
"warranty_months": only if a warranty or guarantee length is printed. "confidence" 0..1 for the whole reading.{hint}"""

SHELF_PROMPT = """You help catalogue a household inventory. The photo shows a shelf, shelving unit, bench, wall
or other storage space, possibly with several shelves. List every distinct kind of object you can see, grouping
identical objects with a quantity. Count carefully; if objects are partly hidden, count only what is visible.
Return JSON only. Use null when unsure; never invent brands or model numbers. Use short everyday names, reading
labels and packaging where legible. "position" says where it is, e.g. "top shelf, left" or "second shelf, middle"
(null if unclear). "confidence" is 0..1 for each entry. Ignore the shelving itself, walls and the floor.
Closed boxes and bins: list them as one entry each named for the box (e.g. "Blue plastic storage bin").{hint}"""

SEARCH_PROMPT = """Convert a household inventory question into search filters. Return JSON only.
intent: "count" for how-many questions, "loans" for lending/borrowing questions, "warranty" for warranty
questions, "expiry" for expiry/use-by/best-before questions, "list" for "show everything in ..." questions,
otherwise "find".
terms: the object words only, each as a group of synonyms/alternate spellings and singular forms, e.g.
"where are my axle stands" -> [["axle stand","jack stand"]]; "DeWalt batteries" -> terms [["battery"]]
with manufacturer "DeWalt". Leave out filler words (my, where, the, own, find, did, lend, anyone).
Question: {query}"""


@dataclass
class AIResult:
    data: dict[str, Any]
    provider: str
    model: str
    elapsed_ms: int

    def metadata(self) -> dict[str, Any]:
        return {"provider": self.provider, "model": self.model, "elapsed_ms": self.elapsed_ms, "result": self.data}


class AIService:
    def __init__(self, settings: Settings, provider: Provider | None = None) -> None:
        self.settings = settings
        self._provider = provider

    @property
    def provider(self) -> Provider:
        if self._provider is None:
            if not self.settings.ai_enabled:
                raise AIUnavailable("AI is not configured. Choose a provider in the App configuration.")
            self._provider = build_provider(self.settings)
        return self._provider

    @property
    def enabled(self) -> bool:
        return self._provider is not None or self.settings.ai_enabled

    def describe(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        try:
            p = self.provider
        except Exception as err:  # noqa: BLE001
            return {"enabled": False, "error": str(err)}
        return {"enabled": True, "provider": p.name, "model": p.model}

    def _call(self, prompt: str, schema: dict[str, Any], image: bytes | None) -> AIResult:
        img = None
        if image is not None:
            img = downscale_for_ai(image, self.settings.ai_max_image_px)
        attempts = max(1, self.settings.ai_retries + 1)
        last: Exception | None = None
        for attempt in range(attempts):
            start = time.monotonic()
            try:
                raw = self.provider.generate_json(prompt, schema, img, timeout=self.settings.ai_timeout)
                data = raw if isinstance(raw, dict) else json.loads(raw)
                return AIResult(
                    data=data,
                    provider=self.provider.name,
                    model=self.provider.model,
                    elapsed_ms=int((time.monotonic() - start) * 1000),
                )
            except AIProviderError as err:
                last = err
                _LOGGER.warning("AI request failed (attempt %s/%s): %s", attempt + 1, attempts, err)
                if not err.retryable:
                    break
                time.sleep(min(2**attempt, 8))
            except (ValueError, TypeError) as err:
                last = err
                _LOGGER.warning("AI returned invalid JSON (attempt %s/%s): %s", attempt + 1, attempts, err)
        raise AIFailed(f"AI request failed: {last}")

    def identify_item(self, image: bytes, hint: str | None = None) -> AIResult:
        hint_txt = f"\nThe user says: {hint[:300]}" if hint else ""
        res = self._call(ITEM_PROMPT.format(hint=hint_txt), ITEM_SCHEMA, image)
        res.data = _clean_item(res.data)
        return res

    def identify_container(self, image: bytes, hint: str | None = None, shelf: bool = False) -> AIResult:
        """Everything visible in a photo of an open container, or of a shelf
        space when ``shelf`` is set.  Suggestions only - nothing is saved."""
        hint_txt = f"\nContext: {hint[:300]}" if hint else ""
        prompt = SHELF_PROMPT if shelf else CONTAINER_PROMPT
        res = self._call(prompt.format(hint=hint_txt), CONTAINER_SCHEMA, image)
        items = [_clean_item(i) for i in res.data.get("items") or [] if isinstance(i, dict) and i.get("name")]
        for i in items:
            try:
                i["quantity"] = max(1, min(int(i.get("quantity") or 1), 10000))
            except (TypeError, ValueError):
                i["quantity"] = 1
        res.data = {"items": items[:200], "notes": res.data.get("notes")}
        return res

    def read_receipt(self, image: bytes, item_name: str | None = None) -> AIResult:
        """Purchase details printed on a receipt photo (a suggestion only)."""
        hint = f"\nThe item: {item_name[:200]}" if item_name else ""
        res = self._call(RECEIPT_PROMPT.format(hint=hint), RECEIPT_SCHEMA, image)
        res.data = _clean_receipt(res.data)
        return res

    def extract_product_from_text(self, text: str, url: str) -> AIResult:
        prompt = (
            "This is the visible text of a shop or manufacturer web page. Identify the single product it is about. "
            "Return JSON only; use null for anything not stated on the page, never guess model numbers. "
            '"name" is a short everyday name including size/capacity if stated. "confidence" is 0..1.'
            f"\nURL: {url[:300]}\nPage text:\n{text[:6000]}"
        )
        res = self._call(prompt, ITEM_SCHEMA, None)
        res.data = _clean_item(res.data)
        return res

    def interpret_query(self, query: str) -> AIResult:
        res = self._call(SEARCH_PROMPT.format(query=query[:500]), SEARCH_SCHEMA, None)
        d = res.data
        terms = [[str(t).strip().lower() for t in g if str(t).strip()] for g in d.get("terms") or [] if isinstance(g, list)]
        res.data = {
            "intent": d.get("intent") if d.get("intent") in ("find", "count", "loans", "warranty", "expiry", "list") else "find",
            "terms": [g for g in terms if g][:8],
            "manufacturer": d.get("manufacturer") or None,
            "category": d.get("category") or None,
            "status": d.get("status") or None,
        }
        return res


def _clean_receipt(d: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    def money(v: Any) -> str | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f"{f:.2f}" if 0 <= f < 10_000_000 else None

    day = None
    try:
        day = date.fromisoformat(str(d.get("purchase_date") or "")[:10]).isoformat()
    except ValueError:
        pass
    cur = str(d.get("currency") or "").strip().upper()
    try:
        months = int(d.get("warranty_months")) if d.get("warranty_months") is not None else None
    except (TypeError, ValueError):
        months = None
    try:
        conf = float(d.get("confidence")) if d.get("confidence") is not None else None
    except (TypeError, ValueError):
        conf = None
    retailer = str(d.get("retailer") or "").strip()[:200] or None
    return {
        "retailer": retailer,
        "purchase_date": day,
        "currency": cur if len(cur) == 3 and cur.isalpha() else None,
        "total": money(d.get("total")),
        "item_price": money(d.get("item_price")),
        "warranty_months": months if months is not None and 0 < months <= 240 else None,
        "confidence": conf,
    }


def _clean_item(d: dict[str, Any]) -> dict[str, Any]:
    def s(key: str, n: int) -> str | None:
        v = d.get(key)
        if v is None:
            return None
        v = str(v).strip()
        return v[:n] or None

    try:
        conf = float(d.get("confidence")) if d.get("confidence") is not None else None
    except (TypeError, ValueError):
        conf = None
    if conf is not None:
        conf = max(0.0, min(conf, 1.0))
    out = {
        "name": s("name", 300) or "Unidentified item",
        "description": s("description", 2000),
        "category": s("category", 120),
        "manufacturer": s("manufacturer", 200),
        "model_number": s("model_number", 120),
        "confidence": conf,
    }
    if "quantity" in d:
        out["quantity"] = d["quantity"]
    if d.get("position"):
        out["position"] = s("position", 120)
    if d.get("notes"):
        out["notes"] = s("notes", 1000)
    return out


def confidence_label(conf: float | None) -> str:
    if conf is None:
        return "unknown"
    if conf >= 0.8:
        return "high"
    if conf >= 0.5:
        return "medium"
    return "low"

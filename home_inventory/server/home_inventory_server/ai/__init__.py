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
                    # [ymin, xmin, ymax, xmax] on a 0-1000 scale (the detection format vision
                    # models are trained on), for a cropped item photo.
                    "box_2d": {"type": ["array", "null"], "items": {"type": "number"}},
                    # False: an object is there but the model can't tell what it is.
                    "identified": {"type": "boolean"},
                },
                "required": ["name", "description", "category", "manufacturer", "model_number", "quantity", "confidence", "position", "box_2d", "identified"],
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
"confidence" is 0..1 for each entry. Ignore the container itself and packing material.
Objects you can see but can't identify are listed too, never left out: give them "identified": false and a
plain descriptive name of what is visible ("Black hard case", "Small white box", "Coiled grey cable"); everything
you can name has "identified": true.
"box_2d" is the entry's bounding box as [ymin, xmin, ymax, xmax], each 0-1000 relative to the photo's height and
width (0,0 = top-left); draw it tightly around that one entry only (null if unsure).{hint}"""

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

# Photo of a whole storage area: the layout (racks/units, shelves) plus where
# each thing is in it, and which entries are labelled boxes/bins.
_AREA_ENTRY = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": _NULLABLE_STR,
        "category": _NULLABLE_STR,
        "manufacturer": _NULLABLE_STR,
        "model_number": _NULLABLE_STR,
        "quantity": {"type": "integer"},
        "confidence": {"type": "number"},
        "position": _NULLABLE_STR,
        "box_2d": {"type": ["array", "null"], "items": {"type": "number"}},
        "kind": {"type": "string", "enum": ["item", "container"]},
        "label": _NULLABLE_STR,
        "container_type": _NULLABLE_STR,
        "unit": {"type": ["integer", "null"]},
        "shelf": {"type": ["integer", "null"]},
        "identified": {"type": "boolean"},
    },
    "required": ["name", "description", "category", "manufacturer", "model_number", "quantity", "confidence",
                 "position", "box_2d", "kind", "label", "container_type", "unit", "shelf", "identified"],
    "additionalProperties": False,
}
AREA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "shelves": {"type": "array", "items": {"type": "string"}}},
                "required": ["name", "shelves"],
                "additionalProperties": False,
            },
        },
        "items": {"type": "array", "items": _AREA_ENTRY},
        "notes": _NULLABLE_STR,
    },
    "required": ["units", "items", "notes"],
    "additionalProperties": False,
}

AREA_PROMPT = """You help catalogue a household inventory. The photo shows a storage area: shelving units,
racks, a bench, a wall or a corner, possibly several units side by side with several shelves each.

1. Describe the layout in "units": each shelving unit / rack, left to right, with a short name ("Left rack",
   "Right rack"; just one unit if there is one). For each unit, "shelves" lists its levels from the TOP down,
   with short names: "On top" (only if things sit on top of the unit), then "Shelf 1", "Shelf 2"... and
   "Bottom shelf" for the lowest. Use [] for a unit without shelves. Things on the floor in front go in a unit
   named "Floor" with no shelves.
2. List every distinct thing in "items", grouping identical objects with a quantity. Count only what is
   visible. Give each its "unit" (index into units, from 0) and "shelf" (index into that unit's shelves, from
   0; null if it has none or it's unclear), plus a short human "position" ("Left rack, shelf 2, left").
3. Storage boxes, bins, totes, tubs and crates that hold other things are "kind": "container"; read any label
   on them into "label" and use it as the name ("Party supplies"); "container_type" is what it is ("Clear
   plastic tote", "Cardboard box"). A closed box whose contents are unknown is still one container entry.
   Everything else is "kind": "item", with "label" null.
Return JSON only. Use null when unsure; never invent brands or model numbers. Short everyday names; read
packaging where legible. "confidence" is 0..1. Ignore the shelving, walls, pipes, ducts and the floor itself.
Objects you can see but can't identify are listed too, never left out: give them "identified": false and a
plain descriptive name of what is visible ("Black hard case", "Small white box", "Coiled grey cable"); everything
you can name has "identified": true.
"box_2d" is the entry's bounding box as [ymin, xmin, ymax, xmax], each 0-1000 relative to the photo's height and
width (0,0 = top-left); draw it tightly around that one entry only (null if unsure).{hint}"""

SHELF_PROMPT = """You help catalogue a household inventory. The photo shows a shelf, shelving unit, bench, wall
or other storage space, possibly with several shelves. List every distinct kind of object you can see, grouping
identical objects with a quantity. Count carefully; if objects are partly hidden, count only what is visible.
Return JSON only. Use null when unsure; never invent brands or model numbers. Use short everyday names, reading
labels and packaging where legible. "position" says where it is, e.g. "top shelf, left" or "second shelf, middle"
(null if unclear). "confidence" is 0..1 for each entry. Ignore the shelving itself, walls and the floor.
Closed boxes and bins: list them as one entry each named for the box (e.g. "Blue plastic storage bin").
Objects you can see but can't identify are listed too, never left out: give them "identified": false and a
plain descriptive name of what is visible ("Black hard case", "Small white box", "Coiled grey cable"); everything
you can name has "identified": true.
"box_2d" is the entry's bounding box as [ymin, xmin, ymax, xmax], each 0-1000 relative to the photo's height and
width (0,0 = top-left); draw it tightly around that one entry only (null if unsure).{hint}"""

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

    def _call(self, prompt: str, schema: dict[str, Any], image: bytes | None, timeout: float | None = None) -> AIResult:
        img = None
        if image is not None:
            img = downscale_for_ai(image, self.settings.ai_max_image_px)
        attempts = max(1, self.settings.ai_retries + 1)
        last: Exception | None = None
        for attempt in range(attempts):
            start = time.monotonic()
            try:
                raw = self.provider.generate_json(prompt, schema, img, timeout=timeout or self.settings.ai_timeout)
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

    def identify_area(self, image: bytes, hint: str | None = None) -> AIResult:
        """A storage area photo: its layout (units and shelves) and every item or
        labelled container in it, placed in that layout.  Suggestions only."""
        hint_txt = f"\nContext: {hint[:300]}" if hint else ""
        res = self._call(AREA_PROMPT.format(hint=hint_txt), AREA_SCHEMA, image)
        units = []
        for u in (res.data.get("units") or [])[:12]:
            if isinstance(u, dict) and str(u.get("name") or "").strip():
                shelves = [str(x).strip()[:80] for x in (u.get("shelves") or []) if str(x).strip()][:20]
                units.append({"name": str(u["name"]).strip()[:80], "shelves": shelves})
        items = []
        for raw in res.data.get("items") or []:
            if not (isinstance(raw, dict) and raw.get("name")):
                continue
            i = _clean_item(raw)
            try:
                i["quantity"] = max(1, min(int(raw.get("quantity") or 1), 10000))
            except (TypeError, ValueError):
                i["quantity"] = 1
            i["kind"] = "container" if raw.get("kind") == "container" else "item"
            label = str(raw.get("label") or "").strip()[:200] or None
            if i["kind"] == "container" and label:
                i["name"] = label[:1].upper() + label[1:].lower() if label.isupper() else label
            i["container_type"] = (str(raw.get("container_type") or "").strip()[:60] or None) if i["kind"] == "container" else None
            unit, shelf = raw.get("unit"), raw.get("shelf")
            unit = unit if isinstance(unit, int) and 0 <= unit < len(units) else None
            shelf = shelf if unit is not None and isinstance(shelf, int) and 0 <= shelf < len(units[unit]["shelves"]) else None
            i["unit"], i["shelf"] = unit, shelf
            items.append(i)
        res.data = {"units": units, "items": items[:300], "notes": res.data.get("notes")}
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

    def read_manual(self, text: str | None = None, image: bytes | None = None, equipment: str | None = None) -> AIResult:
        """Services, parts and consumables from an equipment manual: its text
        (with [page N] markers) or a photo of a page.  Suggestions only."""
        from .manual import MANUAL_PROMPT, MANUAL_SCHEMA, clean_manual

        about = f"\nThe equipment: {equipment[:200]}" if equipment else ""
        source = f"\nManual text:\n{text}" if text else "\nThe photo shows one or more pages of the manual."
        res = self._call(MANUAL_PROMPT + about + source, MANUAL_SCHEMA, image,
                         timeout=max(self.settings.ai_timeout, 180))
        res.data = clean_manual(res.data)
        return res

    def read_document(self, item: str, text: str | None = None, image: bytes | None = None) -> AIResult:
        """What a document attached to an item is, and values for the item's
        fields (from its text or a photo).  Suggestions only."""
        from .document import DOCUMENT_PROMPT, DOCUMENT_SCHEMA, clean_document

        prompt = DOCUMENT_PROMPT.replace("{item}", item[:300])
        prompt += f"\nDocument text:\n{text}" if text else "\nThe photo shows the document."
        res = self._call(prompt, DOCUMENT_SCHEMA, image, timeout=max(self.settings.ai_timeout, 120))
        res.data = clean_document(res.data)
        return res

    def read_meter(self, image: bytes, kind: str = "hours", last: float | None = None) -> AIResult:
        """The number on an hour meter or counter in a photo (a suggestion)."""
        what = "operating hours (hour meter)" if kind == "hours" else "uses/starts (counter)"
        prev = f"\nThe last recorded reading was {last}; the meter normally only goes up." if last is not None else ""
        prompt = (
            f"The photo shows the {what} display of a machine (mower, generator, tractor, compressor...). Read the number "
            "on it. Return JSON only. Mechanical hour meters show whole hours on white or black drums and tenths on a "
            "differently coloured last drum (often red, or after a decimal point): include the tenths as a decimal. "
            "If a drum is between two digits, use the lower one. Digital displays: read exactly what is shown. "
            '"value": the number, or null if you can\'t read it with confidence (never guess). "display_text": the '
            'characters exactly as shown. "confidence" 0..1.' + prev
        )
        schema = {"type": "object", "properties": {"value": {"type": ["number", "null"]},
                                                   "display_text": {"type": ["string", "null"]},
                                                   "confidence": {"type": "number"}},
                  "required": ["value", "display_text", "confidence"], "additionalProperties": False}
        res = self._call(prompt, schema, image)
        d = res.data
        try:
            value = round(float(d.get("value")), 1) if d.get("value") is not None else None
        except (TypeError, ValueError):
            value = None
        if value is not None and not 0 <= value < 1_000_000:
            value = None
        try:
            conf = max(0.0, min(float(d.get("confidence")), 1.0)) if d.get("confidence") is not None else None
        except (TypeError, ValueError):
            conf = None
        res.data = {"value": value, "display_text": (str(d.get("display_text") or "").strip()[:40] or None),
                    "confidence": conf}
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


def _box_2d(v: Any) -> list[float] | None:
    """[ymin, xmin, ymax, xmax] on 0-1000 (or 0-1) -> [x, y, w, h] fractions."""
    if not isinstance(v, list) or len(v) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = (float(n) for n in v)
    except (TypeError, ValueError):
        return None
    scale = 1.0 if max(ymin, xmin, ymax, xmax) <= 1.0 else 1000.0
    if ymax <= ymin or xmax <= xmin:
        return None
    return _clean_box([xmin / scale, ymin / scale, (xmax - xmin) / scale, (ymax - ymin) / scale])


def _clean_box(v: Any) -> list[float] | None:
    """[x, y, w, h] within the photo, or None."""
    if not isinstance(v, list) or len(v) != 4:
        return None
    try:
        x, y, w, h = (float(n) for n in v)
    except (TypeError, ValueError):
        return None
    if min(x, y) < 0 or w < 0.02 or h < 0.02 or x + w > 1.01 or y + h > 1.01:
        return None
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


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
    box = _box_2d(d.get("box_2d")) if d.get("box_2d") is not None else _clean_box(d.get("box"))
    if box and box[2] * box[3] <= 0.6:  # one entry never fills most of the photo
        out["box"] = box
    if d.get("identified") is False:
        out["identified"] = False
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

"""Read an equipment manual and turn it into a maintenance plan.

Equipment only (an item with maintenance tracking turned on).  The source
is the manual's PDF attached to the item (its text is extracted here, with
``[page N]`` markers so suggestions cite their page) or photos of its
pages (scanned manuals have no text).

The AI's answer is stored on a :class:`ManualReading` together with what
it matches in the inventory (parts by part number, standards by
specification, tasks by title).  Nothing changes until a person applies
it, choosing what to keep: then tasks are created or updated, parts and
standards created or reused, each task gets the parts *its* service needs,
and the model remembers what it takes (confirmed fitments).
"""

from __future__ import annotations

import io
import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..ai import AIFailed, AIUnavailable
from ..ai.manual import merge
from ..auth import Actor
from ..models import Attachment, EquipmentProfile, Item, MaintenanceTask, ManualReading, Product, TaskPart
from . import parts as parts_svc

_LOGGER = logging.getLogger(__name__)

MAX_TEXT = 90_000  # characters sent to the AI
MAX_PAGES = 800
MAX_PHOTOS = 8
# A scanned PDF (pictures of pages, no text) is read page by page as images.
MAX_SCANNED_PAGES = 12
MAX_CHOSEN_PAGES = 30
# A reading still "pending" after this was interrupted (the App restarted).
STALE_AFTER = timedelta(minutes=45)
_KEYWORDS = ("maintenance", "service", "schedule", "interval", "every", "hours", "oil", "spark", "filter", "plug",
             "capacity", "specification", "part", "replace", "grease", "blade", "belt", "fuel", "lubric", "inspect")


class ManualError(ValueError):
    """User-facing problem."""


def pdf_pages(data: bytes) -> list[tuple[int, str]]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        out = []
        for n, page in enumerate(reader.pages[:MAX_PAGES], start=1):
            try:
                text = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one bad page shouldn't lose the manual
                text = ""
            text = re.sub(r"[ \t]+", " ", text).strip()
            if text:
                out.append((n, text))
        return out
    except (PdfReadError, ValueError, KeyError) as err:
        raise ManualError(f"This PDF couldn't be read ({err}).") from err


def page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    try:
        return len(pdfium.PdfDocument(data))
    except Exception:  # noqa: BLE001
        return 0


def page_images(data: bytes, pages: list[int], long_edge: int = 1800) -> list[tuple[int, bytes]]:
    """(page number, JPEG) for each requested page (1-based) that exists.
    Used for scanned PDFs, whose pages are pictures with no text."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as err:  # noqa: BLE001
        raise ManualError(f"This PDF couldn't be opened ({err}).") from err
    out = []
    try:
        for n in pages:
            if not 1 <= n <= len(pdf):
                continue
            page = pdf[n - 1]
            w, h = page.get_size()
            img = page.render(scale=long_edge / max(w, h, 1)).to_pil().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85)
            out.append((n, buf.getvalue()))
    finally:
        pdf.close()
    return out


def parse_pages(spec: str | None) -> list[int] | None:
    """ "3, 18-26" -> [3, 18, ..., 26]; None when empty."""
    if not spec or not spec.strip():
        return None
    out: list[int] = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ManualError(f"Pages: “{part}” isn't a page or range like 18-26")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if b < a:
            a, b = b, a
        out.extend(range(a, b + 1))
        if len(out) > MAX_CHOSEN_PAGES:
            raise ManualError(f"Choose at most {MAX_CHOSEN_PAGES} pages")
    return sorted(set(out)) or None


def select_text(pages: list[tuple[int, str]], limit: int = MAX_TEXT) -> str:
    """The manual's text with page markers; for long manuals, the pages that
    talk about maintenance (in their original order)."""
    total = sum(len(t) + 12 for _, t in pages)
    chosen = pages
    if total > limit:
        def score(t: str) -> float:
            low = t.lower()
            return sum(low.count(k) for k in _KEYWORDS) / (1 + len(t) / 2000)

        ranked = sorted(pages, key=lambda p: score(p[1]), reverse=True)
        keep, used = set(), 0
        for n, t in ranked:
            if used + len(t) + 12 > limit:
                continue
            keep.add(n)
            used += len(t) + 12
        chosen = [p for p in pages if p[0] in keep]
    return "\n".join(f"[page {n}]\n{t}" for n, t in chosen)


def equipment_item(db: Session, item: Item) -> EquipmentProfile:
    profile = db.scalar(select(EquipmentProfile).where(EquipmentProfile.item_id == item.id))
    if profile is None:
        raise ManualError("Reading a manual is for equipment: turn on maintenance tracking for this item first.")
    return profile


def _equipment_name(item: Item, profile: EquipmentProfile) -> str:
    p = item.product
    bits = [p.manufacturer, p.name, p.model_number and f"model {p.model_number}",
            profile.engine_manufacturer and f"engine {profile.engine_manufacturer} {profile.engine_model or ''}".strip()]
    return ", ".join(b for b in bits if b)


def start(state, reading_id: int) -> threading.Thread:
    """Read in a background thread with its own database session.  The
    request that started it returns at once: reading a long manual can take
    minutes, longer than phones and proxies keep a request open."""

    def run() -> None:
        db = state.db.SessionLocal()
        try:
            reading = db.get(ManualReading, reading_id)
            item = db.get(Item, reading.item_id) if reading else None
            if reading is None or item is None:
                return
            analyse(db, reading, state.ai, state.media, item)
        except Exception:  # noqa: BLE001 - recorded on the reading, never lost silently
            _LOGGER.exception("Reading manual %s failed", reading_id)
            db.rollback()
            reading = db.get(ManualReading, reading_id)
            if reading is not None and reading.status == "pending":
                reading.status, reading.error = "failed", "Reading the manual failed unexpectedly. Try again."
                db.commit()
        finally:
            db.close()

    t = threading.Thread(target=run, name=f"manual-reading-{reading_id}", daemon=True)
    t.start()
    return t


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def running_for(db: Session, item_id: int) -> ManualReading | None:
    r = db.scalar(select(ManualReading).where(ManualReading.item_id == item_id, ManualReading.status == "pending")
                  .order_by(ManualReading.id.desc()))
    if r is not None and expire_if_stale(r):
        db.commit()
        return None
    return r


def expire_if_stale(r: ManualReading) -> bool:
    if r.status == "pending" and r.created_at and datetime.now(UTC) - _aware(r.created_at) > STALE_AFTER:
        r.status, r.error = "failed", "The reading was interrupted (the App restarted or the AI didn't answer). Try again."
        return True
    return False


def analyse(db: Session, reading: ManualReading, ai, media, item: Item) -> ManualReading:
    """Ask the AI (runs in the background thread started by :func:`start`)."""
    profile = equipment_item(db, item)
    hint = _equipment_name(item, profile)
    results = []
    try:
        atts = [db.get(Attachment, a) for a in reading.attachment_ids]
        pdfs = [a for a in atts if a is not None and a.mime_type == "application/pdf"]
        photos = [a for a in atts if a is not None and (a.mime_type or "").startswith("image/")]
        if not pdfs and not photos:
            raise ManualError("Choose the manual (a PDF) or photos of its pages.")
        chosen = (reading.result or {}).get("pages")  # set when the reading was started
        notes = []
        for att in pdfs:
            data = media.read(att.stored_path)
            pages = pdf_pages(data)
            if chosen:
                pages = [p for p in pages if p[0] in chosen]
            if pages:
                res = ai.read_manual(text=select_text(pages), equipment=hint)
                results.append(res.data)
                continue
            # No text: a scanned manual.  Read its pages as pictures.
            total = page_count(data)
            wanted = chosen or list(range(1, min(total, MAX_SCANNED_PAGES) + 1))
            images = page_images(data, wanted)
            if not images:
                raise ManualError(f"{att.original_name or 'This PDF'} has no pages to read.")
            for _n, img in images:
                res = ai.read_manual(image=img, equipment=hint)
                results.append(res.data)
            if not chosen and total > MAX_SCANNED_PAGES:
                notes.append(f"{att.original_name or 'The manual'} is scanned: pages 1-{MAX_SCANNED_PAGES} of {total} "
                             "were read. If the maintenance schedule is further in, read again and choose its pages.")
        for att in photos[:MAX_PHOTOS]:
            res = ai.read_manual(image=media.read(att.stored_path), equipment=hint)
            results.append(res.data)
        reading.provider, reading.model = res.provider, res.model
        data = merge(results)
        if not data["services"] and not data["parts"]:
            raise ManualError("No maintenance schedule or parts were found in this manual.")
        reading.result = annotate(db, item, data)
        if notes:
            reading.result["notes"] = notes
        reading.status = "ready"
        reading.error = None
    except (ManualError, AIFailed, AIUnavailable) as err:
        reading.status = "failed"
        reading.error = str(err)
    except Exception as err:  # noqa: BLE001
        _LOGGER.exception("Reading manual %s failed", reading.id)
        reading.status = "failed"
        reading.error = f"Reading the manual failed: {err}"
    db.commit()
    return reading


# ---------------------------------------------------------------------------
# Matching against what's already there
# ---------------------------------------------------------------------------

_VISCOSITY = re.compile(r"\b(\d{1,2})\s*w\s*-?\s*(\d{2})\b", re.I)


def proposed_specs(spec: str | None) -> dict[str, str]:
    """Comparable properties from a printed specification.  Only the oil
    viscosity grade is recognised automatically; other standards are
    confirmed by a person."""
    m = _VISCOSITY.search(spec or "")
    return {"Viscosity": f"{m.group(1)}W-{m.group(2)}"} if m else {}


def _match_standard(db: Session, spec: str | None, specs: dict[str, str]) -> Product | None:
    key = parts_svc._spec_key(spec or "")
    if not key:
        return None
    for std in db.scalars(select(Product).where(Product.deleted_at.is_(None), Product.is_generic.is_(True))):
        if std.specs and all(parts_svc._spec_key(v) in key for v in std.specs.values()):
            return std
        if specs and std.specs and parts_svc.specs_satisfy(specs, std.specs):
            return std
    return None


def annotate(db: Session, item: Item, data: dict[str, Any]) -> dict[str, Any]:
    tasks = db.scalars(select(MaintenanceTask).where(MaintenanceTask.item_id == item.id,
                                                     MaintenanceTask.deleted_at.is_(None))).all()
    for p in data["parts"]:
        match = None
        for number in [p.get("part_number"), *p.get("alternatives", [])]:
            match = parts_svc.find_by_part_number(db, number)
            if match:
                break
        p["proposed_specs"] = proposed_specs(p.get("specification"))
        # A consumable given only by its grade is a standard, not a brand.
        p["proposed_generic"] = p["kind"] == "consumable" and not p.get("part_number")
        if match is None and p["proposed_generic"]:
            match = _match_standard(db, p.get("specification"), p["proposed_specs"])
        p["match"] = parts_svc._brief(match) if match else None
    for s in data["services"]:
        best, score = None, 0.0
        for t in tasks:
            r = SequenceMatcher(None, (s["title"] or "").lower(), t.title.lower()).ratio()
            if r > score:
                best, score = t, r
        s["task_match"] = {"id": best.id, "title": best.title} if best and score >= 0.75 else None
    return data


def reading_out(r: ManualReading) -> dict[str, Any]:
    return {"id": r.id, "item_id": r.item_id, "attachment_ids": r.attachment_ids, "status": r.status,
            "provider": r.provider, "model": r.model, "result": r.result, "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "applied_at": r.applied_at.isoformat() if r.applied_at else None}


# ---------------------------------------------------------------------------
# Applying what the person chose
# ---------------------------------------------------------------------------


def _dec(v: Any) -> Decimal | None:
    return Decimal(str(v)) if v not in (None, "") else None


def apply(db: Session, actor: Actor, item: Item, reading: ManualReading, choice: dict[str, Any]) -> dict[str, Any]:
    from . import maintenance as mnt

    if reading.status not in ("ready",):
        raise ManualError("This reading has already been handled" if reading.status in ("applied", "discarded")
                          else "This reading has no result to apply")
    profile = equipment_item(db, item)
    ref = "Owner's manual"
    products: dict[str, tuple[Product, dict[str, Any]]] = {}
    made = {"parts_created": 0, "standards_created": 0, "parts_linked": 0, "tasks_created": 0, "tasks_updated": 0}
    for p in choice.get("parts") or []:
        if not p.get("include", True):
            continue
        key = str(p.get("key") or "")
        if p.get("product_id"):
            product = parts_svc._get_product(db, int(p["product_id"]))
        else:
            name = (p.get("name") or "").strip()[:300]
            if not name:
                raise ManualError("Every part needs a name")
            generic = bool(p.get("is_generic"))
            product = Product(
                name=name, manufacturer=(p.get("manufacturer") or None) if not generic else None,
                model_number=(p.get("part_number") or None) if not generic else None,
                description=p.get("specification") or None, is_generic=generic,
                specs=parts_svc.clean_specs(p.get("specs")),
            )
            db.add(product)
            db.flush()
            parts_svc.suggest_meets_links(db, product)
            made["standards_created" if generic else "parts_created"] += 1
        page = p.get("page")
        source_ref = f"{ref}, page {page}" if page else ref
        parts_svc.save_fitment(db, actor, item.product, product, p.get("role") or product.name, _dec(p.get("quantity")),
                               p.get("unit"), status="confirmed", source="manual", source_ref=source_ref)
        made["parts_linked"] += 1
        products[key] = (product, p)

    meters = mnt.meters_for(db, item.id)
    for s in choice.get("services") or []:
        if not s.get("include", True):
            continue
        title = (s.get("title") or "").strip()[:200]
        if not title:
            raise ManualError("Every service needs a title")
        fields = {"interval_hours": _dec(s.get("interval_hours")), "interval_days": s.get("interval_days") or None,
                  "interval_cycles": s.get("interval_cycles") or None}
        if s.get("instructions"):
            fields["instructions"] = str(s["instructions"])[:5000]
        task = db.get(MaintenanceTask, int(s["task_id"])) if s.get("task_id") else None
        if task is not None and (task.item_id != item.id or task.deleted_at is not None):
            raise ManualError("That task belongs to something else")
        if task is None:
            task = mnt.create_task(db, actor, item, {"title": title, **{k: v for k, v in fields.items() if v is not None}})
            first = _dec(s.get("first_at_hours"))
            if first is not None and (meters.hours is None or meters.hours < first):
                task.next_due_hours = first
            made["tasks_created"] += 1
        else:
            changes = {k: v for k, v in fields.items() if v is not None}
            if changes:
                mnt.update_task(db, actor, task, changes)
            made["tasks_updated"] += 1
        # This service's own needs, merged into what the task already lists.
        rows = {tp.product_id: {"product_id": tp.product_id, "quantity": tp.quantity, "unit": tp.unit,
                                "allow_equivalent": tp.allow_equivalent, "note": tp.note}
                for tp in db.scalars(select(TaskPart).where(TaskPart.task_id == task.id))}
        for need in s.get("needs") or []:
            hit = products.get(str(need.get("key") or ""))
            if hit is None:
                continue
            product, part = hit
            qty = need.get("quantity") or part.get("quantity") or 1
            unit = need.get("unit") if need.get("quantity") else part.get("unit") if part.get("quantity") else None
            rows[product.id] = {"product_id": product.id, "quantity": qty, "unit": unit,
                                "allow_equivalent": part.get("allow_equivalent", True), "note": part.get("role"),
                                "role": part.get("role"), "source": "manual",
                                "source_ref": f"{ref}, page {s['page']}" if s.get("page") else ref}
        parts_svc.set_task_parts(db, actor, task, list(rows.values()))

    specs_added = 0
    if choice.get("specs"):
        current = list(profile.specs or [])
        labels = {str(x.get("label", "")).lower() for x in current}
        for sp in choice["specs"]:
            if sp.get("include", True) and sp.get("label") and str(sp["label"]).lower() not in labels:
                current.append({"label": str(sp["label"])[:80], "value": str(sp.get("value") or "")[:200], "kind": "spec"})
                labels.add(str(sp["label"]).lower())
                specs_added += 1
        profile.specs = current
    reading.status = "applied"
    reading.applied_at = datetime.now(UTC)
    db.flush()
    audit.record(db, actor, "manual_applied", "item", item.id,
                 f"{item.product.name}: maintenance plan from the manual ({made['tasks_created']} new tasks, "
                 f"{made['parts_linked']} parts)")
    return {**made, "specs_added": specs_added}

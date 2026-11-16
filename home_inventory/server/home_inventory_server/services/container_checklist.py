"""A printed contents checklist for a box, read back from photos.

1. **Print** (``pdf``): every item in the box (and boxes inside it), one row
   each, numbered, with its photo, name, record number and quantity, and
   boxes to tick: *Back here*, *Move to ___*, *Dispose*, *Missing*,
   *Qty now ___*.
2. **Read** (``read_page``): a photo or scan of a completed page goes to the
   AI, which reads the ticks and handwriting row by row.  Rows are matched
   by the printed record number, never by name.  Nothing changes.
3. **Review and apply** (``apply``): the person checks every row and
   confirms; moves, disposals, missing items and new counts are applied
   together and recorded as a check of the box.

Only items in the box can be changed this way.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor
from ..models import Attachment, Container, Item, ItemStatus
from ..schemas import MoveIn
from . import inventory as inv
from . import verification as ver
from .customers import household_only
from .placement import Resolver

ACTIONS = ("back", "move", "dispose", "missing", "none")
ROWS_PER_PAGE = 9


class ChecklistError(ValueError):
    pass


def box(db: Session, container_id: int) -> Container:
    c = db.get(Container, container_id)
    if c is None or c.deleted_at is not None:
        raise inv.NotFound("Container not found")
    return c


def rows(db: Session, c: Container) -> list[dict[str, Any]]:
    """What's in the box (and boxes inside it), in a stable order, numbered."""
    r = Resolver.load(db)
    ids = r.descendant_container_ids(c.id)
    items = db.scalars(select(Item).where(Item.deleted_at.is_(None), Item.container_id.in_(ids), household_only())).all()
    here = r.describe(c.id, None)["path"]
    out = []
    for it in sorted(items, key=lambda i: (i.container_id != c.id, r.describe(i.container_id, None)["path"],
                                           i.product.name.lower(), i.id)):
        inner = r.describe(it.container_id, None)["path"]
        out.append({"item_id": it.id, "name": it.product.name, "label": it.label, "quantity": it.quantity,
                    "status": it.status.value, "inside": inner[len(here):].lstrip(" ›") if inner != here else None,
                    "photo_id": it.primary_photo_id})
    for n, row in enumerate(out, start=1):
        row["row"] = n
    return out


def pdf(db: Session, media, c: Container, today: date | None = None) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    today = today or date.today()
    r = Resolver.load(db)
    path = r.describe(c.id, None)["path"]
    data = rows(db, c)
    ss = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=ss["Normal"], fontSize=8, leading=10)
    cell = ParagraphStyle("cell", parent=ss["Normal"], fontSize=10, leading=12)
    opt = ParagraphStyle("opt", parent=ss["Normal"], fontSize=10, leading=12)

    def tick_boxes() -> Table:
        """Empty squares to tick, drawn as bordered cells (fonts often have
        no empty-box glyph and print a solid square)."""
        b = 4.2 * mm
        gap = [""] * 6
        t = Table([["", Paragraph("Back here", opt), "", Paragraph("Dispose", opt), "", Paragraph("Missing", opt)], gap,
                   ["", Paragraph("Move to ____________________", opt), "", "", "", ""], gap,
                   [Paragraph("Qty now ______", opt), "", "", "", "", ""]],
                  colWidths=[b, 20 * mm, b, 17 * mm, b, 17 * mm], rowHeights=[b, 3 * mm, b, 3 * mm, 5 * mm])
        t.setStyle(TableStyle([
            ("SPAN", (1, 2), (5, 2)), ("SPAN", (0, 4), (5, 4)),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1.5 * mm), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            *[("BOX", (c, r), (c, r), 0.9, colors.black) for c, r in ((0, 0), (2, 0), (4, 0), (0, 2))],
        ]))
        return t
    title = f"Contents check: {c.number + ' · ' if c.number else ''}{c.name}"
    pages = max(1, -(-len(data) // ROWS_PER_PAGE))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=title)

    def photo(row: dict[str, Any]):
        att = db.get(Attachment, row["photo_id"]) if row["photo_id"] else None
        if att is None:
            return ""
        try:
            raw = media.read(att.thumb_path or att.stored_path)
            img = Image(io.BytesIO(raw))
            scale = min(22 * mm / img.imageWidth, 22 * mm / img.imageHeight)
            img.drawWidth, img.drawHeight = img.imageWidth * scale, img.imageHeight * scale
            return img
        except Exception:  # noqa: BLE001 - a missing photo never stops the print
            return ""

    story: list[Any] = []
    for page in range(pages):
        chunk = data[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
        story += [Paragraph(escape(title), ss["Title"]),
                  Paragraph(escape(f"{path} · printed {today.isoformat()} · checklist C{c.id} · page {page + 1} of {pages}"),
                            small),
                  Paragraph("Tick <b>one</b> box per row. For <b>Move to</b>, write where it went. Write the count in "
                            "<b>Qty now</b> only if it's different. Then photograph or scan every page and choose "
                            "<b>Read completed checklist</b> on the box in Home Inventory.", small),
                  Spacer(1, 3 * mm)]
        table = [["#", "", "Item", "What happened"]]
        for row in chunk:
            name = escape(row["name"]) + (f" ({escape(row['label'])})" if row["label"] else "")
            meta = f"Record {row['item_id']} · qty {row['quantity']}" + (f" · in {escape(row['inside'])}" if row["inside"] else "")
            if row["status"] != "stored":
                meta += f" · {row['status'].replace('_', ' ')}"
            table.append([Paragraph(f"<b>{row['row']}</b>", cell), photo(row),
                          Paragraph(f"<b>{name}</b><br/><font size=8>{meta}</font>", cell),
                          tick_boxes()])
        if not chunk:
            table.append(["", "", Paragraph("This box is empty in Home Inventory.", cell), ""])
        t = Table(table, colWidths=[9 * mm, 25 * mm, 72 * mm, 80 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("ROWHEIGHT", (0, 1), (-1, -1), 25 * mm),
        ]))
        story.append(t)
        if page == pages - 1:
            story += [Spacer(1, 4 * mm), Paragraph("<b>Also found in this box</b> (not on the list):", cell)]
            story += [Paragraph("_" * 85, cell) for _ in range(4)]
        else:
            story.append(PageBreak())
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Reading a completed page
# ---------------------------------------------------------------------------


def page_images(data: bytes, mime: str | None) -> list[bytes]:
    """A photo, or every page of a scanned PDF, as images to read."""
    from .manuals import page_count
    from .manuals import page_images as pdf_pages

    if data[:4] == b"%PDF" or mime == "application/pdf":
        n = page_count(data)
        if n < 1:
            raise ChecklistError("That PDF has no pages")
        return [img for _n, img in pdf_pages(data, list(range(1, min(n, 20) + 1)))]
    from PIL import Image, UnidentifiedImageError

    try:
        Image.open(io.BytesIO(data)).verify()
    except (UnidentifiedImageError, OSError) as err:
        raise ChecklistError("That isn't a photo or a PDF") from err
    return [data]


def interpret(db: Session, c: Container, read: list[dict[str, Any]], extras: list[str]) -> dict[str, Any]:
    """Match what the AI read to the box's records (by printed record number,
    then row number) and suggest a place for each "move to"."""
    from .places import resolve

    expected = {r["item_id"]: r for r in rows(db, c)}
    by_row = {r["row"]: r for r in expected.values()}
    out: dict[int, dict[str, Any]] = {}
    unmatched = []
    for x in read:
        target = expected.get(x.get("item_id")) or by_row.get(x.get("row"))
        if target is None:
            unmatched.append(x)
            continue
        action = x.get("action") if x.get("action") in ACTIONS else "none"
        qty = x.get("quantity")
        place = None
        if action == "move" and x.get("move_to"):
            cands = resolve(db, str(x["move_to"]))["candidates"]
            place = cands[0] if cands else None
        out[target["item_id"]] = {**target, "action": action, "quantity_now": qty if isinstance(qty, int) and qty >= 0 else None,
                                  "move_to": x.get("move_to"), "place": place, "note": x.get("note"),
                                  "confidence": x.get("confidence")}
    return {"rows": list(out.values()), "unmatched": unmatched, "extras": [e for e in extras if e]}


# ---------------------------------------------------------------------------
# Applying what the person confirmed
# ---------------------------------------------------------------------------


def apply(db: Session, actor: Actor, c: Container, actions: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply confirmed rows.  Every item must be in this box; everything is
    checked before anything changes."""
    expected = {r["item_id"] for r in rows(db, c)}
    plan = []
    for a in actions:
        item = db.get(Item, int(a["item_id"]))
        if item is None or item.deleted_at is not None or item.id not in expected:
            raise ChecklistError("Only things in this box can be changed from its checklist")
        action = a.get("action") or "none"
        if action not in ACTIONS:
            raise ChecklistError("Unknown action")
        qty = a.get("quantity")
        if qty is not None and (not isinstance(qty, int) or qty < 0):
            raise ChecklistError("A count must be a whole number")
        if action == "move":
            if (a.get("container_id") is None) == (a.get("location_id") is None):
                raise ChecklistError(f"Choose where {item.product.name} went")
            inv.validate_item_target(db, a.get("container_id"), a.get("location_id"))
        if action == "missing" and item.status is ItemStatus.on_loan:
            raise ChecklistError(f"{item.product.name} is on loan, so it isn't expected here")
        plan.append((item, action, qty, a))
    s = ver.start(db, actor, "container", c.id)
    done = {"back": 0, "move": 0, "dispose": 0, "missing": 0, "counted": 0}
    for item, action, qty, a in plan:
        if action == "dispose":
            inv.delete_item(db, actor, item, f"Disposed of (checklist of {s.scope_label})")
            done["dispose"] += 1
            continue
        if action == "missing":
            ver.check(db, actor, s, item, "missing", note=a.get("note"))
            done["missing"] += 1
            continue
        if action in ("back", "move") or qty is not None:
            if qty is not None and qty != item.quantity:
                ver.check(db, actor, s, item, "adjusted", quantity=qty, note=a.get("note"))
                done["counted"] += 1
            else:
                ver.check(db, actor, s, item, "present", note=a.get("note"))
            if action == "back":
                done["back"] += 1
        if action == "move":
            inv.move_item(db, actor, item, MoveIn(container_id=a.get("container_id"), location_id=a.get("location_id"),
                                                  note=f"From the checklist of {s.scope_label}"))
            done["move"] += 1
    ver.finish(db, actor, s)
    return {"check_id": s.id, **done}

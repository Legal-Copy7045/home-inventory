"""Bulk-create the location hierarchy from a spreadsheet.

Format (sheet "Locations", first row = headers):

    Level 1  | Level 2 | Level 3             | Level 4      | ... | Kind  | Description
    Property | Shed    | Rear Right Shelving | Middle Shelf |     | Shelf | Hoses and reels

Each row describes one place by its full path; parents are created as
needed.  Rows may also be written in outline style: leading blank level
cells mean "same as the row above", so

    Property |        |
             | House  |
             |        | Garage

creates Property › House › Garage.  Locations are matched by name
(case-insensitive) under the same parent, so importing the same sheet twice
creates nothing new.  ``Kind`` and ``Description`` apply to the last named
place in the row and only fill empty values on existing locations.

CSV files with the same columns are accepted too.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import Location
from .inventory import new_qr_code
from .placement import Resolver

MAX_ROWS = 5000
MAX_LEVELS = 12
TEMPLATE_LEVELS = 6
KINDS = ["Property", "Building", "Room", "Area", "Rack", "Shelf", "Drawer", "Cabinet", "Closet", "Workbench", "Tool rack", "Wall"]
_LEVEL_RE = re.compile(r"^\s*level\s*(\d+)\s*$", re.I)


class ImportFileError(ValueError):
    """The file can't be read as a location sheet."""


@dataclass
class Row:
    number: int  # spreadsheet row number, for messages
    path: list[str]
    kind: str | None = None
    description: str | None = None


@dataclass
class Plan:
    rows: list[Row] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    to_create: list[str] = field(default_factory=list)  # display paths, parents first
    existing: int = 0
    updates: int = 0


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _table_from_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook  # uses defusedxml when installed

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as err:  # noqa: BLE001 - any parse failure is a bad file
        raise ImportFileError("This file isn't a readable Excel workbook (.xlsx)") from err
    try:
        ws = next((wb[n] for n in wb.sheetnames if n.strip().lower() == "locations"), None) or wb.worksheets[0]
        table: list[list[str]] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > MAX_ROWS + 1:
                raise ImportFileError(f"The sheet has more than {MAX_ROWS} rows; split it into smaller files")
            table.append([_cell(v) for v in row[: MAX_LEVELS + 6]])
        return table
    finally:
        wb.close()


def _table_from_csv(data: bytes) -> list[list[str]]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if len(rows) > MAX_ROWS + 1:
        raise ImportFileError(f"The file has more than {MAX_ROWS} rows; split it into smaller files")
    return [[_cell(v) for v in r[: MAX_LEVELS + 6]] for r in rows]


def read_rows(data: bytes, filename: str | None) -> tuple[list[Row], list[dict[str, Any]]]:
    if not data:
        raise ImportFileError("The file is empty")
    is_xlsx = data[:2] == b"PK"
    if not is_xlsx and filename and filename.lower().endswith((".xlsx", ".xlsm")):
        raise ImportFileError("This file isn't a readable Excel workbook (.xlsx)")
    if not is_xlsx and filename and filename.lower().endswith(".xls"):
        raise ImportFileError("Old .xls files aren't supported; save it as .xlsx or .csv")
    table = _table_from_xlsx(data) if is_xlsx else _table_from_csv(data)

    # Find the header row (first row containing "Level 1").
    header_idx, level_cols, kind_col, desc_col = None, [], None, None
    for idx, row in enumerate(table[:20]):
        levels = {int(m.group(1)): c for c, v in enumerate(row) if (m := _LEVEL_RE.match(v))}
        if 1 in levels:
            header_idx = idx
            level_cols = [levels[k] for k in sorted(levels) if k <= MAX_LEVELS]
            for c, v in enumerate(row):
                low = v.strip().lower()
                if low in ("kind", "type"):
                    kind_col = c
                elif low in ("description", "notes"):
                    desc_col = c
            break
    if header_idx is None:
        raise ImportFileError('No header row found. The first row needs columns named "Level 1", "Level 2", …')

    rows: list[Row] = []
    errors: list[dict[str, Any]] = []
    previous: list[str] = []
    for idx in range(header_idx + 1, len(table)):
        raw = table[idx]
        number = idx + 1
        cells = [raw[c] if c < len(raw) else "" for c in level_cols]
        if not any(cells):
            continue
        filled = [i for i, v in enumerate(cells) if v]
        first, last = filled[0], filled[-1]
        if any(not cells[i] for i in range(first, last + 1)):
            errors.append({"row": number, "message": "Levels must be filled left to right without gaps"})
            continue
        if first > len(previous):
            errors.append({"row": number, "message": f"Level {first + 1} has no parent: fill in the levels to its left"})
            continue
        path = previous[:first] + cells[first : last + 1]
        if any(len(p) > 200 for p in path):
            errors.append({"row": number, "message": "A location name is longer than 200 characters"})
            continue
        kind = raw[kind_col] if kind_col is not None and kind_col < len(raw) else ""
        desc = raw[desc_col] if desc_col is not None and desc_col < len(raw) else ""
        rows.append(Row(number, path, kind[:40] or None, desc[:5000] or None))
        previous = path
    if not rows and not errors:
        raise ImportFileError("The sheet has no locations in it")
    return rows, errors


# ---------------------------------------------------------------------------
# Planning and applying
# ---------------------------------------------------------------------------


class _Tree:
    """Existing (non-deleted) locations keyed by (parent id, lower-case name)."""

    def __init__(self, db: Session) -> None:
        self.by_key: dict[tuple[int | None, str], Location] = {}
        for loc in db.scalars(select(Location).where(Location.deleted_at.is_(None)).order_by(Location.id)):
            self.by_key.setdefault((loc.parent_id, loc.name.strip().lower()), loc)


def plan(db: Session, rows: list[Row], errors: list[dict[str, Any]]) -> Plan:
    tree = _Tree(db)
    p = Plan(rows=rows, errors=list(errors))
    seen_new: set[tuple[str, ...]] = set()
    seen_existing: set[int] = set()
    for row in rows:
        parent_id: int | None = None
        new_from = None
        shown: list[str] = []  # existing names as stored, new names as typed
        for depth, name in enumerate(row.path):
            loc = tree.by_key.get((parent_id, name.lower())) if new_from is None else None
            shown.append(loc.name if loc is not None else name)
            if loc is None:
                new_from = depth if new_from is None else new_from
                key = tuple(x.lower() for x in row.path[: depth + 1])
                if key not in seen_new:
                    seen_new.add(key)
                    p.to_create.append(" › ".join(shown))
            else:
                parent_id = loc.id
                if loc.id not in seen_existing:
                    seen_existing.add(loc.id)
                if depth == len(row.path) - 1 and ((row.kind and not loc.kind) or (row.description and not loc.description)):
                    p.updates += 1
    p.existing = len(seen_existing)
    return p


def apply(db: Session, actor: Actor, rows: list[Row]) -> dict[str, Any]:
    tree = _Tree(db)
    created: list[int] = []
    updated: set[int] = set()
    for row in rows:
        parent_id: int | None = None
        for depth, name in enumerate(row.path):
            key = (parent_id, name.lower())
            loc = tree.by_key.get(key)
            last = depth == len(row.path) - 1
            if loc is None:
                loc = Location(
                    name=name,
                    parent_id=parent_id,
                    qr_code=new_qr_code(db),
                    kind=row.kind if last else None,
                    description=row.description if last else None,
                )
                db.add(loc)
                db.flush()
                tree.by_key[key] = loc
                created.append(loc.id)
            elif last:
                changed = False
                if row.kind and not loc.kind:
                    loc.kind, changed = row.kind, True
                if row.description and not loc.description:
                    loc.description, changed = row.description, True
                if changed:
                    updated.add(loc.id)
            parent_id = loc.id
    if created or updated:
        audit.record(
            db,
            actor,
            "imported",
            "location",
            created[0] if created else next(iter(updated)),
            f"Imported locations from a spreadsheet: {len(created)} created, {len(updated)} updated",
            after={"created": created, "updated": sorted(updated)},
        )
    return {"created": len(created), "updated": len(updated)}


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

EXAMPLE_ROWS = [
    ["Property", "House", "Garage", "Upper shelving", "", "", "Rack", ""],
    ["Property", "House", "Garage", "Workbench", "Top drawer", "", "Drawer", "Small hand tools"],
    ["Property", "House", "Basement", "Utility room", "Storage rack", "Shelf 2", "Shelf", ""],
    ["Property", "Shed", "Right side", "Ground level", "", "", "Area", ""],
    ["Property", "Shed", "Rear Right Shelving", "Middle Shelf", "", "", "Shelf", "Hoses and reels"],
]


def build_template(db: Session, include_existing: bool) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Locations"
    # Each entry: (path, kind, description)
    entries: list[tuple[list[str], str, str]] = []
    if include_existing:
        r = Resolver.load(db)
        live = [loc for loc in r.locations.values() if loc.deleted_at is None]
        has_child = {loc.parent_id for loc in live if loc.parent_id}
        for loc in sorted(live, key=lambda x: r.location_path(x.id).lower()):
            if loc.id in has_child and not (loc.kind or loc.description):
                continue  # implied by its children's rows
            entries.append(([x.name for x in r.location_chain(loc.id)], loc.kind or "", loc.description or ""))
    if not entries:
        entries = [([c for c in ex[:6] if c], ex[6], ex[7]) for ex in EXAMPLE_ROWS]
    levels = max([TEMPLATE_LEVELS, *(len(p) for p, _, _ in entries)])
    ws.append([f"Level {i}" for i in range(1, levels + 1)] + ["Kind", "Description"])
    for path, kind, desc in entries:
        ws.append(path + [""] * (levels - len(path)) + [kind, desc])

    head_fill = PatternFill("solid", fgColor="0288D1")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(vertical="center")
    for i in range(1, levels + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 22
    ws.column_dimensions[ws.cell(row=1, column=levels + 1).column_letter].width = 14
    ws.column_dimensions[ws.cell(row=1, column=levels + 2).column_letter].width = 40
    ws.freeze_panes = "A2"
    kind_col = ws.cell(row=1, column=levels + 1).column_letter
    dv = DataValidation(type="list", formula1='"' + ",".join(KINDS) + '"', allow_blank=True, showErrorMessage=False)
    dv.add(f"{kind_col}2:{kind_col}{MAX_ROWS}")
    ws.add_data_validation(dv)

    help_ws = wb.create_sheet("How to use")
    lines = [
        ("Home Inventory – location import", True),
        ("", False),
        ("One row per place. Write its full path from the top level down, one level per column:", False),
        ("    Property | Shed | Rear Right Shelving | Middle Shelf", False),
        ("Parents are created automatically, so you don't need separate rows for Property or Shed.", False),
        ("", False),
        ("Outline style also works: leave the leading level cells blank to mean 'same as the row above'.", False),
        ("Add more 'Level 7', 'Level 8' … columns if you need deeper nesting.", False),
        ("", False),
        ("Kind (optional): Property, Building, Room, Area, Rack, Shelf, Drawer, Cabinet, Closet, Workbench, Tool rack, Wall – or your own word.", False),
        ("Description (optional): notes for the last place in the row.", False),
        ("", False),
        ("Importing is safe to repeat: places that already exist (same name under the same parent) are reused, not duplicated.", False),
        ("You will see a preview before anything is created.", False),
        ("", False),
        ("Upload this file in Home Inventory: Locations → Import from Excel.", True),
    ]
    for text, bold in lines:
        help_ws.append([text])
        if bold:
            help_ws.cell(row=help_ws.max_row, column=1).font = Font(bold=True, size=13)
    help_ws.column_dimensions["A"].width = 120

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

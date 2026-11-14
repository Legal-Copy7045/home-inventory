"""REST API (``/api/v1``).

Every route depends on :func:`current_actor`, which authenticates the caller
(Ingress identity or integration token).  Mutations additionally require the
member role; administration requires admin.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..auth import Actor, authenticated_actor, current_actor, get_db, require_admin, require_member
from ..models import (
    Attachment,
    AttachmentKind,
    Category,
    Container,
    IdentificationStatus,
    Item,
    ItemStatus,
    Loan,
    Location,
    Product,
    Role,
    Setting,
    User,
)
from ..schemas import (
    BULK_MAX,
    AdjustIn,
    BulkDeleteIn,
    BulkEditIn,
    BulkItemsIn,
    BulkLendIn,
    CategoryIn,
    ContainerIn,
    ContainerMoveIn,
    ContainerPatch,
    ItemCreate,
    ItemPatch,
    LoanIn,
    LocationIn,
    LocationPatch,
    MoveIn,
    PlaceItemsIn,
    ReturnIn,
    StatusIn,
)
from ..services import capture as capture_svc
from ..services import customers as customers_svc
from ..services import duplicates as dup_svc
from ..services import export as export_svc
from ..services import inventory as inv
from ..services import labels as labels_svc
from ..services import location_import
from ..services import maintenance as maintenance_svc
from ..services import scans as scans_svc
from ..services import stats as stats_svc
from ..services.media import MediaError
from ..services.placement import PlacementError, Resolver, validate_item_target

_LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _state(request: Request):
    return request.app.state


def _limit(request: Request, actor: Actor, bucket: str) -> None:
    """Per-user rate limit for operations that cost money or reach the internet."""
    _state(request).rate_limiter.check(actor.user_id, bucket)


def _commit(db: Session) -> None:
    db.commit()


def _err(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except inv.NotFound as err:
        raise HTTPException(404, str(err)) from err
    except (inv.Conflict, PlacementError) as err:
        raise HTTPException(409, str(err)) from err


def _resolver(db: Session) -> Resolver:
    return Resolver.load(db)


def _get_or_404(db: Session, model, obj_id: int, include_deleted: bool = False):
    return _err(inv._get, db, model, obj_id, include_deleted)


# ---------------------------------------------------------------------------
# identity, dashboard, stats
# ---------------------------------------------------------------------------


class AdminClaim(BaseModel):
    code: str = Field(min_length=4, max_length=40)


@router.post("/setup/claim-admin")
def claim_admin(data: AdminClaim, request: Request, actor: Actor = Depends(authenticated_actor),
                db: Session = Depends(get_db)):
    """Become the first administrator using the setup code from the App log."""
    st = _state(request)
    if actor.channel != "ingress":
        raise HTTPException(403, "Only available in the app")
    user = db.get(User, actor.user_id)
    st.admin_bootstrap.claim(db, st.settings, st.admin_directory, user, data.code)
    audit.record(db, actor, "admin_claimed", "user", user.id, f"{actor.name} became administrator using the setup code")
    db.commit()
    return {"role": "admin"}


@router.get("/me")
def me(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    st = _state(request)
    return {
        "id": actor.user_id,
        "ha_user_id": actor.ha_user_id,
        "name": actor.name,
        "role": actor.role.value,
        "channel": actor.channel,
        "can_edit": actor.can(Role.member),
        "is_admin": actor.can(Role.admin),
        # True only while nobody can administer the app; the UI then offers
        # to enter the setup code from the App log.
        "admin_setup": actor.channel == "ingress" and st.admin_bootstrap.available(db, st.settings, st.admin_directory),
        "app": {
            "version": st.version,
            "ai": st.ai.describe(),
            "currency": stats_svc.currency(db),
            "qr_base_url": _qr_base(db, st),
            "label_templates": [{"key": t.key, "name": t.name} for t in labels_svc.TEMPLATES.values()],
        },
    }


@router.get("/dashboard")
def dashboard(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    s = _state(request).settings
    return stats_svc.dashboard(db, s.warranty_notice_days, s.expiry_notice_days)


@router.get("/checkups")
def checkups_due(limit: int = 10, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Places that most need a fresh photo inventory, never-checked first."""
    from ..services import checkups

    return {"places": checkups.due(db, max(1, min(limit, 50)))}


@router.get("/stats")
def stats(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    s = _state(request).settings
    st = stats_svc.stats(db, s.warranty_notice_days, s.expiry_notice_days)
    st.update(maintenance_svc.counts(db))
    st.update(customers_svc.counts(db))
    from ..services import replenishment as repl_svc

    st.update(repl_svc.counts(db))
    if st["last_activity"]:
        st["last_activity"] = st["last_activity"].isoformat()
    return st


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------


@router.get("/locations/tree")
def locations_tree(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return inv.location_tree(db)


@router.get("/locations")
def locations_flat(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    locs = [loc for loc in r.locations.values() if loc.deleted_at is None]
    out = [{"id": loc.id, "name": loc.name, "kind": loc.kind, "parent_id": loc.parent_id, "qr_code": loc.qr_code,
            "path": r.location_path(loc.id)} for loc in locs]
    return sorted(out, key=lambda x: x["path"].lower())


@router.get("/locations/template.xlsx")
def locations_template(existing: bool = False, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Excel template for bulk location import (optionally pre-filled with
    the current hierarchy)."""
    data = location_import.build_template(db, include_existing=existing)
    name = "home-inventory-locations.xlsx" if existing else "home-inventory-locations-template.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/locations/import")
async def locations_import(
    request: Request,
    file: UploadFile = File(...),
    confirm: bool = Form(False),
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Preview (``confirm`` false) or apply a location spreadsheet."""
    limit = 5 * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "The file is larger than 5 MB")
    try:
        rows, errors = await asyncio.to_thread(location_import.read_rows, data, file.filename)
    except location_import.ImportFileError as err:
        raise HTTPException(422, str(err)) from err
    plan = location_import.plan(db, rows, errors)
    summary = {
        "rows": len(rows),
        "errors": plan.errors,
        "to_create": plan.to_create,
        "create_count": len(plan.to_create),
        "existing_count": plan.existing,
        "update_count": plan.updates,
    }
    if not confirm:
        return {**summary, "applied": False}
    result = location_import.apply(db, actor, rows)
    db.commit()
    return {**summary, "applied": True, "created": result["created"], "updated": result["updated"]}


@router.post("/locations", status_code=201)
def create_location(data: LocationIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    loc = _err(inv.create_location, db, actor, data)
    db.commit()
    return inv.location_out(loc, _resolver(db), db, detail=True)


@router.get("/locations/{location_id}")
def get_location(location_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    loc = _get_or_404(db, Location, location_id)
    return inv.location_out(loc, _resolver(db), db, detail=True)


@router.patch("/locations/{location_id}")
def patch_location(location_id: int, data: LocationPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    loc = _get_or_404(db, Location, location_id)
    _err(inv.update_location, db, actor, loc, data)
    db.commit()
    return inv.location_out(loc, _resolver(db), db, detail=True)


@router.delete("/locations/{location_id}", status_code=204)
def delete_location(location_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    loc = _get_or_404(db, Location, location_id)
    _err(inv.delete_location, db, actor, loc)
    db.commit()


@router.post("/locations/{location_id}/restore")
def restore_location(location_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    loc = _get_or_404(db, Location, location_id, include_deleted=True)
    inv.restore_location(db, actor, loc)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------


@router.get("/containers")
def list_containers(
    q: str | None = None,
    location_id: int | None = None,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    from ..services.textmatch import matches_words

    r = _resolver(db)
    containers = db.scalars(select(Container).where(Container.deleted_at.is_(None))).all()
    if q and q.strip():
        # Words in any order, matched across the name, number, type,
        # description and where it is ("tote shed front right").
        code = q.strip().upper()
        containers = [
            c for c in containers
            if c.qr_code == code or matches_words(q, " ".join(filter(None, (
                c.name, c.number, f"#{c.number}" if c.number else None, c.container_type, c.description,
                r.describe(c.parent_container_id, c.location_id)["path"]))))
        ]
    if location_id:
        within = r.containers_in_location_tree(location_id)
        containers = [c for c in containers if c.id in within]
    out = [inv.container_out(c, r, db) for c in containers]
    return sorted(out, key=lambda c: (_natural(c["number"]), c["name"].lower()))


def _natural(s: str | None) -> tuple:
    import re

    if not s:
        return (1, ())
    return (0, tuple(int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s) if p))


@router.post("/containers", status_code=201)
def create_container(data: ContainerIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _err(inv.create_container, db, actor, data)
    db.commit()
    return inv.container_out(c, _resolver(db), db, detail=True)


@router.get("/containers/numbers")
def container_numbers(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """The next free container number (one more than the highest number in
    use) and the numbers already taken, so a form can suggest one and warn
    about duplicates."""
    used: dict[str, dict] = {}
    highest = 0
    for c in db.scalars(select(Container).where(Container.deleted_at.is_(None), Container.number.is_not(None))):
        key = c.number.strip().lstrip("#").lower()
        used.setdefault(key, {"id": c.id, "name": c.name, "number": c.number})
        if key.isdigit():
            highest = max(highest, int(key))
    return {"next": str(highest + 1), "used": used}


@router.get("/containers/lookup")
def lookup_container(ref: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Find a container by QR payload/code, number or exact name (used by Home Assistant services)."""
    ref = ref.strip()
    code = labels_svc.parse_qr(ref)
    c = None
    if code:
        c = db.scalar(select(Container).where(Container.qr_code == code, Container.deleted_at.is_(None)))
    if c is None:
        num = ref.lstrip("#").strip()
        c = db.scalars(
            select(Container).where(Container.deleted_at.is_(None), or_(Container.number == num, func.lower(Container.name) == ref.lower()))
        ).first()
    if c is None:
        raise HTTPException(404, "No matching container")
    return inv.container_out(c, _resolver(db), db, detail=True)


@router.post("/qr/resolve")
def resolve_qr(body: dict[str, str], actor: Actor = Depends(authenticated_actor), db: Session = Depends(get_db)):
    """Turn any label payload (link, HINV:… code or bare code) into a record."""
    from ..services import tags as tag_svc

    if tag_svc.REF_RE.search((body.get("text") or "").strip()):
        try:
            tag = tag_svc.resolve(db, body["text"])
        except tag_svc.TagNotFound as err:
            raise HTTPException(404, str(err)) from err
        from ..events import queue_event

        queue_event(db, "tag_scanned", {"tag_id": tag.id, "target_type": tag.target_type, "target_id": tag.target_id,
                                        "default_action": tag.default_action, "user": actor.name})
        db.commit()
        return {"type": tag.target_type, "id": tag.target_id, "tag_id": tag.id, "default_action": tag.default_action}
    ref = labels_svc.parse_qr_ref(body.get("text", ""))
    if not ref:
        raise HTTPException(404, "Not a Home Inventory label")
    kind, code = ref
    if kind in (None, "C"):
        c = db.scalar(select(Container).where(Container.qr_code == code))
        if c is not None:
            if c.deleted_at is not None:
                raise HTTPException(410, "This container was deleted (see Trash)")
            return {"type": "container", "id": c.id, "container_id": c.id, "qr_code": code}
    if kind in (None, "L"):
        loc = db.scalar(select(Location).where(Location.qr_code == code))
        if loc is not None:
            if loc.deleted_at is not None:
                raise HTTPException(410, "This location was deleted (see Trash)")
            return {"type": "location", "id": loc.id, "location_id": loc.id, "qr_code": code}
    raise HTTPException(404, "Nothing has this label")


@router.get("/containers/{container_id}")
def get_container(container_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id)
    return inv.container_out(c, _resolver(db), db, detail=True)


@router.patch("/containers/{container_id}")
def patch_container(container_id: int, data: ContainerPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id)
    inv.update_container(db, actor, c, data)
    db.commit()
    return inv.container_out(c, _resolver(db), db, detail=True)


@router.post("/containers/{container_id}/move")
def move_container(container_id: int, data: ContainerMoveIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id)
    _err(inv.move_container, db, actor, c, data)
    db.commit()
    return inv.container_out(c, _resolver(db), db, detail=True)


@router.post("/containers/{container_id}/return")
def return_container(container_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id)
    _err(inv.return_container_home, db, actor, c)
    db.commit()
    return inv.container_out(c, _resolver(db), db, detail=True)


@router.delete("/containers/{container_id}", status_code=204)
def delete_container(container_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id)
    inv.delete_container(db, actor, c)
    db.commit()


@router.post("/containers/{container_id}/restore")
def restore_container(container_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id, include_deleted=True)
    inv.restore_container(db, actor, c)
    db.commit()
    return {"ok": True}


def _qr_base(db: Session, st, from_browser: str | None = None) -> str:
    """Address used in QR links: Settings, else App config, else the address
    the browser used to open the App."""
    s = db.get(Setting, "qr_base_url")
    return (
        (s.value if s and s.value else None)
        or st.settings.qr_base_url
        or labels_svc.clean_base_url(from_browser)
        or ""
    )


@router.get("/containers/{container_id}/qr.svg")
def container_qr(container_id: int, request: Request, base: str | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    c = _get_or_404(db, Container, container_id)
    payload = labels_svc.qr_payload(c.qr_code, _qr_base(db, _state(request), base), "C")
    return Response(labels_svc.qr_svg(payload), media_type="image/svg+xml", headers={"Cache-Control": "private, no-cache"})


@router.get("/locations/{location_id}/qr.svg")
def location_qr(location_id: int, request: Request, base: str | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    loc = _get_or_404(db, Location, location_id)
    if not loc.qr_code:
        loc.qr_code = inv.new_qr_code(db)
        db.commit()
    payload = labels_svc.qr_payload(loc.qr_code, _qr_base(db, _state(request), base), "L")
    return Response(labels_svc.qr_svg(payload), media_type="image/svg+xml", headers={"Cache-Control": "private, no-cache"})


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------


def _item_filters(
    r: Resolver, q: str | None, status: ItemStatus | None, category: str | None, container_id: int | None,
    location_id: int | None, loose: bool, needs_details: bool,
):
    """WHERE conditions shared by the Items list and "select all matching"."""
    conds = [Item.deleted_at.is_(None)]
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(
            Product.name.ilike(like), Product.manufacturer.ilike(like), Product.model_number.ilike(like),
            Product.barcode.ilike(like), Product.description.ilike(like), Item.label.ilike(like),
            Item.serial_number.ilike(like), Category.name.ilike(like),
        ))
    if status:
        conds.append(Item.status == status)
    if category:
        conds.append(func.lower(Category.name) == category.lower())
    if container_id:
        conds.append(Item.container_id.in_(r.descendant_container_ids(container_id)))
    if location_id:
        locs, conts = r.descendant_location_ids(location_id), r.containers_in_location_tree(location_id)
        conds.append(or_(Item.location_id.in_(locs), Item.container_id.in_(conts)))
    if loose:
        conds.extend([Item.container_id.is_(None), Item.location_id.is_not(None)])
    if needs_details:
        conds.append(Product.identification_status == IdentificationStatus.pending)
    return conds


def _item_base(*cols):
    return select(*cols).join(Product, Item.product_id == Product.id).outerjoin(Category, Product.category_id == Category.id)


_ITEM_ORDER = {
    "name": [func.lower(Product.name), Item.id],
    "recent": [Item.updated_at.desc(), Item.id.desc()],
    "created": [Item.created_at.desc(), Item.id.desc()],
    "oldest": [Item.id],
    "value": [Item.purchase_price.desc().nulls_last(), Item.id],
    "expiry": [Item.expires_on.asc().nulls_last(), Item.id],
    "warranty": [Item.warranty_expires.asc().nulls_last(), Item.id],
}


def _ordered_item_ids(db: Session, r: Resolver, conds, sort: str) -> list[int]:
    if sort == "location":
        # Grouped by where things are: place path, then name; unplaced last.
        rows = db.execute(_item_base(Item.id, Item.container_id, Item.location_id, Product.name).where(*conds)).all()

        def where(row) -> tuple:
            unplaced = row.container_id is None and row.location_id is None
            path = "" if unplaced else r.describe(row.container_id, row.location_id)["path"].lower()
            return (unplaced, path, (row.name or "").lower(), row.id)

        return [row.id for row in sorted(rows, key=where)]
    order = _ITEM_ORDER.get(sort, _ITEM_ORDER["name"])
    return list(db.scalars(_item_base(Item.id).where(*conds).order_by(*order)))


@router.get("/items")
def list_items(
    q: str | None = None,
    status: ItemStatus | None = None,
    category: str | None = None,
    container_id: int | None = None,
    location_id: int | None = None,
    loose: bool = False,
    needs_details: bool = False,
    sort: str = "name",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    r = _resolver(db)
    conds = _item_filters(r, q, status, category, container_id, location_id, loose, needs_details)
    if sort == "location":
        ids = _ordered_item_ids(db, r, conds, sort)
        total, page_ids = len(ids), ids[offset : offset + limit]
    else:
        total = db.scalar(_item_base(func.count(Item.id)).where(*conds)) or 0
        order = _ITEM_ORDER.get(sort, _ITEM_ORDER["name"])
        page_ids = list(db.scalars(_item_base(Item.id).where(*conds).order_by(*order).limit(limit).offset(offset)))
    loaded = {
        i.id: i
        for i in db.scalars(
            select(Item).options(selectinload(Item.product).selectinload(Product.category), selectinload(Item.loans))
            .where(Item.id.in_(page_ids))
        )
    }
    page = [loaded[i] for i in page_ids if i in loaded]
    return {"total": total, "offset": offset, "items": inv.items_out(page, r, db)}


def _report(db: Session, r: Resolver, q, status, category, container_id, location_id):
    conds = _item_filters(r, q, status, category, container_id, location_id, False, False)
    return export_svc.build(db, _ordered_item_ids(db, r, conds, "location"), r)


@router.get("/reports/inventory")
def inventory_report(
    q: str | None = None,
    status: ItemStatus | None = None,
    category: str | None = None,
    container_id: int | None = None,
    location_id: int | None = None,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    """Insurance / inventory report: every matching household item grouped by
    room, with recorded values, serial numbers and paperwork counts."""
    return _report(db, _resolver(db), q, status, category, container_id, location_id)


@router.get("/reports/inventory.{fmt}")
def inventory_report_file(
    fmt: str,
    q: str | None = None,
    status: ItemStatus | None = None,
    category: str | None = None,
    container_id: int | None = None,
    location_id: int | None = None,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    """The same report as a CSV or Excel download."""
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(404, "Use .csv or .xlsx")
    report = _report(db, _resolver(db), q, status, category, container_id, location_id)
    stamp = date.today().isoformat()
    if fmt == "csv":
        body, media = export_svc.to_csv(report), "text/csv; charset=utf-8"
    else:
        body, media = export_svc.to_xlsx(report), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="home-inventory-{stamp}.{fmt}"'})


@router.get("/items/ids")
def list_item_ids(
    q: str | None = None,
    status: ItemStatus | None = None,
    category: str | None = None,
    container_id: int | None = None,
    location_id: int | None = None,
    loose: bool = False,
    needs_details: bool = False,
    sort: str = "name",
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    """Every item id matching the Items filters, for "select all N"."""
    r = _resolver(db)
    ids = _ordered_item_ids(db, r, _item_filters(r, q, status, category, container_id, location_id, loose, needs_details), sort)
    return {"total": len(ids), "ids": ids[:BULK_MAX]}


@router.post("/items", status_code=201)
def create_item(data: ItemCreate, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    items = _err(inv.create_items, db, actor, data)
    db.commit()
    r = _resolver(db)
    return {"items": inv.items_out(items, r, db, detail=True)}


@router.get("/items/{item_id}")
def get_item(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id, include_deleted=True)
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.patch("/items/{item_id}")
def patch_item(item_id: int, data: ItemPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    _err(inv.update_item, db, actor, item, data)
    db.commit()
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.post("/items/{item_id}/move")
def move_item(item_id: int, data: MoveIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    moved = _err(inv.move_item, db, actor, item, data)
    db.commit()
    return inv.item_out(moved, _resolver(db), db, detail=True)


@router.post("/items/{item_id}/return")
def return_item(item_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    item = _err(inv.return_item_home, db, actor, item)
    db.commit()
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.post("/items/{item_id}/adjust")
def adjust_item(item_id: int, data: AdjustIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    _err(inv.adjust_quantity, db, actor, item, data.delta, data.note)
    db.commit()
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.get("/expiring")
def expiring(
    days: int = Query(30, ge=0, le=3650),
    include_expired: bool = True,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    r = _resolver(db)
    today = date.today()
    stmt = select(Item).options(selectinload(Item.product), selectinload(Item.loans)).where(
        Item.deleted_at.is_(None), Item.quantity > 0, Item.expires_on.is_not(None), Item.expires_on <= today + timedelta(days=days)
    )
    if not include_expired:
        stmt = stmt.where(Item.expires_on >= today)
    return inv.items_out(db.scalars(stmt.order_by(Item.expires_on)), r, db)


@router.get("/low-stock")
def low_stock(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return stats_svc.low_stock_list(db)


@router.post("/items/{item_id}/status")
def set_status(item_id: int, data: StatusIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    _err(inv.set_status, db, actor, item, data.status, data.note)
    db.commit()
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.post("/items/{item_id}/loan", status_code=201)
def lend_item(item_id: int, data: LoanIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    loan = _err(inv.lend_item, db, actor, item, data)
    db.commit()
    return {"loan": inv.loan_out(loan), "item": inv.item_out(loan.item, _resolver(db), db, detail=True)}


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, reason: str | None = None, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    inv.delete_item(db, actor, item, reason)
    db.commit()


@router.post("/items/{item_id}/restore")
def restore_item(item_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id, include_deleted=True)
    inv.restore_item(db, actor, item)
    db.commit()
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.post("/capture", status_code=201)
async def quick_capture(
    request: Request,
    file: UploadFile = File(...),
    container_id: int | None = Form(None),
    location_id: int | None = Form(None),
    batch: str | None = Form(None, max_length=64),
    group: int | None = Form(None, ge=1, le=1000),
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Quick photo: store the photo as a new item, flagged as needing details,
    and queue an AI suggestion in the background.  The place is optional:
    photos taken without one are sorted onto shelves later (``/capture/place``)."""
    st = _state(request)
    _err(validate_item_target, db, container_id, location_id)  # before storing the file
    data = await file.read(st.media.max_bytes + 1)
    if len(data) > st.media.max_bytes:
        raise HTTPException(413, "File too large")
    try:
        stored = await asyncio.to_thread(st.media.save, data, False)
    except MediaError as err:
        raise HTTPException(415, str(err)) from err
    photo = Attachment(kind=AttachmentKind.photo, original_name=file.filename, created_by_id=actor.user_id, **stored.__dict__)
    db.add(photo)
    db.flush()
    item = _err(capture_svc.capture, db, actor, photo, container_id, location_id, st.ai.enabled, batch, group)
    db.commit()
    if st.ai.enabled:
        st.capture_queue.put_nowait(item.id)
    return inv.item_out(item, _resolver(db), db)


@router.get("/capture/unsorted")
def capture_unsorted(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Quick photos taken without a place, grouped as they were taken."""
    r = _resolver(db)
    items = capture_svc.unsorted_items(db)
    return {
        "total": len(items),
        "groups": [{**g, "items": inv.items_out(g["items"], r, db)} for g in capture_svc.unsorted_groups(items)],
    }


@router.post("/capture/place")
@router.post("/items/bulk-move")
def capture_place(data: PlaceItemsIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Put several items in one place at once (a whole shelf's photos, or a
    selection on the Items page).  All or nothing."""
    if data.container_id is None and data.location_id is None:
        raise HTTPException(422, "Choose a container or location")
    _err(validate_item_target, db, data.container_id, data.location_id)
    items = _bulk_items(db, data.item_ids)
    move = MoveIn(container_id=data.container_id, location_id=data.location_id)
    moved = [_err(inv.move_item, db, actor, item, move) for item in items]
    db.commit()
    r = _resolver(db)
    return {"items": inv.items_out(moved, r, db)}


def _bulk_items(db: Session, ids: list[int]) -> list[Item]:
    return [_get_or_404(db, Item, i) for i in dict.fromkeys(ids)]


@router.post("/items/bulk-edit")
def bulk_edit_items(data: BulkEditIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Set the same details on several items.  All or nothing.  Category and
    maker belong to the product, so items of the same product share them."""
    items = _bulk_items(db, data.item_ids)
    fields = data.model_dump(exclude_unset=True, exclude={"item_ids", "status"})
    for item in items:
        if fields:
            _err(inv.update_item, db, actor, item, ItemPatch(**fields))
        if "status" in data.model_fields_set and data.status is not None and item.status is not data.status:
            _err(inv.set_status, db, actor, item, data.status, None)
    db.commit()
    r = _resolver(db)
    return {"items": inv.items_out(items, r, db)}


@router.post("/items/bulk-lend")
def bulk_lend_items(data: BulkLendIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Lend several items to the same person (whole quantities).  All or nothing."""
    items = _bulk_items(db, data.item_ids)
    loan = LoanIn(borrower=data.borrower, expected_return=data.expected_return, notes=data.notes)
    loans = [_err(inv.lend_item, db, actor, item, loan) for item in items]
    db.commit()
    r = _resolver(db)
    return {"items": [inv.item_out(lo.item, r, db) for lo in loans]}


@router.get("/deleted-items")
def deleted_items(limit: int = Query(200, ge=1, le=500), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Recently deleted items, newest first, for undo / restore."""
    r = _resolver(db)
    items = db.scalars(
        select(Item).options(selectinload(Item.product), selectinload(Item.loans))
        .where(Item.deleted_at.is_not(None)).order_by(Item.deleted_at.desc(), Item.id.desc()).limit(limit)
    ).all()
    return {"items": inv.items_out(items, r, db)}


@router.post("/items/bulk-restore")
def bulk_restore_items(data: BulkItemsIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Undo a (bulk) delete.  Items that are not deleted are left alone."""
    items = [_get_or_404(db, Item, i, include_deleted=True) for i in dict.fromkeys(data.item_ids)]
    restored = []
    for item in items:
        if item.deleted_at is not None:
            inv.restore_item(db, actor, item)
            restored.append(item.id)
    db.commit()
    return {"restored": restored}


@router.post("/items/bulk-delete")
def bulk_delete_items(data: BulkDeleteIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Delete several items (each can be restored from its page or history)."""
    items = _bulk_items(db, data.item_ids)
    for item in items:
        inv.delete_item(db, actor, item, data.reason)
    db.commit()
    return {"deleted": [i.id for i in items]}


@router.get("/products/duplicates")
def product_duplicates(request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Pairs of products that look like the same thing (barcode, model, name
    or photo), most likely first."""
    st = _state(request)
    return dup_svc.find(db, media=st.media, data_dir=st.settings.data_dir)


class MergeIn(BaseModel):
    other_id: int


@router.post("/products/{product_id}/merge")
def merge_products(product_id: int, data: MergeIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Merge ``other_id`` into this product (all holdings, photos, links and
    restock settings move here; the other product is deleted)."""
    keep, other = db.get(Product, product_id), db.get(Product, data.other_id)
    if keep is None or other is None:
        raise HTTPException(404, "Product not found")
    try:
        res = dup_svc.merge(db, actor, keep, other)
    except dup_svc.MergeError as err:
        raise HTTPException(409, str(err)) from err
    db.commit()
    return res


class DismissIn(BaseModel):
    product_ids: list[int] = Field(min_length=2, max_length=2)


@router.post("/products/duplicates/dismiss")
def dismiss_duplicate(data: DismissIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Not a duplicate: stop suggesting this pair."""
    dup_svc.dismiss(db, *data.product_ids)
    db.commit()
    return {"ok": True}


@router.get("/products/similar")
def similar_products(name: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Existing products with a similar name, so the user can choose to add
    another instance instead of creating a near-duplicate product."""
    r = _resolver(db)
    out = []
    for p in db.scalars(select(Product).where(Product.deleted_at.is_(None))):
        s = scans_svc.similarity(name, p.name)
        if s >= 0.6:
            holdings = inv.product_holdings(db, p.id, r)
            if holdings:
                out.append({"product": inv.product_out(p), "similarity": round(s, 2), "holdings": holdings})
    out.sort(key=lambda x: -x["similarity"])
    return out[:8]


# ---------------------------------------------------------------------------
# loans, temporary, warranties
# ---------------------------------------------------------------------------


@router.get("/loans")
def list_loans(active: bool = True, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    stmt = select(Loan).join(Item).where(Item.deleted_at.is_(None))
    if active:
        stmt = stmt.where(Loan.returned_at.is_(None))
    loans = db.scalars(stmt.order_by(Loan.returned_at.is_not(None), Loan.expected_return.is_(None), Loan.expected_return, Loan.loaned_at.desc()).limit(500)).all()
    return [{**inv.loan_out(lo), "item": inv.item_out(lo.item, r, db)} for lo in loans]


@router.post("/loans/{loan_id}/return")
def return_loan(loan_id: int, data: ReturnIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(404, "Loan not found")
    _err(inv.return_loan, db, actor, loan, data)
    db.commit()
    return inv.loan_out(loan)


@router.get("/temporary")
def temporary(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    items = db.scalars(
        select(Item)
        .options(selectinload(Item.product), selectinload(Item.loans))
        .where(
            Item.deleted_at.is_(None),
            or_(Item.status.in_([ItemStatus.temporarily_relocated, ItemStatus.in_use, ItemStatus.missing, ItemStatus.awaiting_return]),
                Item.home_container_id.is_not(None), Item.home_location_id.is_not(None)),
        )
    ).all()
    containers = db.scalars(select(Container).where(Container.deleted_at.is_(None), Container.temporarily_moved.is_(True))).all()
    return {"items": inv.items_out(items, r, db), "containers": [inv.container_out(c, r, db) for c in containers]}


@router.get("/warranties")
def warranties(days: int = Query(90, ge=0, le=3650), include_expired: bool = False, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    today = date.today()
    stmt = select(Item).options(selectinload(Item.product), selectinload(Item.loans)).where(Item.deleted_at.is_(None), Item.warranty_expires.is_not(None))
    if not include_expired:
        stmt = stmt.where(Item.warranty_expires >= today)
    stmt = stmt.where(Item.warranty_expires <= today + timedelta(days=days))
    return inv.items_out(db.scalars(stmt.order_by(Item.warranty_expires)), r, db)


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------


@router.get("/categories")
def categories(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    counts = dict(
        db.execute(
            select(Product.category_id, func.count(Item.id)).join(Item).where(Item.deleted_at.is_(None)).group_by(Product.category_id)
        ).all()
    )
    return [{"id": c.id, "name": c.name, "synonyms": c.synonyms, "item_count": counts.get(c.id, 0)} for c in db.scalars(select(Category).order_by(func.lower(Category.name)))]


@router.post("/categories", status_code=201)
def create_category(data: CategoryIn, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    """Categories are a fixed list that administrators keep; items choose from it."""
    near = inv.match_category(db, data.name)
    if near is not None:
        raise HTTPException(409, f"That's already a category: {near.name}")
    cat = inv.get_or_create_category(db, data.name)
    cat.synonyms = data.synonyms or None
    audit.record(db, actor, "created", "category", cat.id, f"Added category {cat.name}", None, {"name": cat.name})
    db.commit()
    return {"id": cat.id, "name": cat.name, "synonyms": cat.synonyms, "item_count": 0}


def _category_or_404(db: Session, category_id: int) -> Category:
    cat = db.get(Category, category_id)
    if cat is None:
        raise HTTPException(404, "Category not found")
    return cat


@router.patch("/categories/{category_id}")
def patch_category(category_id: int, data: CategoryIn, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    cat = _err(inv.rename_category, db, actor, _category_or_404(db, category_id), data.name, data.synonyms)
    db.commit()
    return {"id": cat.id, "name": cat.name, "synonyms": cat.synonyms}


class CategoryMergeIn(BaseModel):
    into_id: int


@router.post("/categories/{category_id}/merge")
def merge_category(category_id: int, data: CategoryMergeIn, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    src, into = _category_or_404(db, category_id), _category_or_404(db, data.into_id)
    moved = _err(inv.merge_category, db, actor, src, into)
    db.commit()
    return {"moved": moved, "into": {"id": into.id, "name": into.name, "synonyms": into.synonyms}}


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    _err(inv.delete_category, db, actor, _category_or_404(db, category_id))
    db.commit()

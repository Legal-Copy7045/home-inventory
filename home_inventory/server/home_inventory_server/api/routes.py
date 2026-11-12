"""REST API (``/api/v1``).

Every route depends on :func:`current_actor`, which authenticates the caller
(Ingress identity or integration token).  Mutations additionally require the
member role; administration requires admin.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask

from .. import audit
from ..ai import AIFailed, AIUnavailable, confidence_label
from ..auth import Actor, authenticated_actor, current_actor, effective_role, get_db, require_admin, require_member
from ..models import (
    Attachment,
    AttachmentKind,
    Category,
    Container,
    ContainerScan,
    HistoryEvent,
    IdentificationStatus,
    Item,
    ItemStatus,
    Loan,
    Location,
    LocationMap,
    MapMarker,
    Product,
    Role,
    ScanStatus,
    Setting,
    User,
)
from ..schemas import (
    AdjustIn,
    BarcodeLookupIn,
    CategoryIn,
    ContainerIn,
    ContainerMoveIn,
    ContainerPatch,
    ItemCreate,
    ItemPatch,
    LabelRequest,
    LinkImportIn,
    LoanIn,
    LocationIn,
    LocationPatch,
    MapIn,
    MapPatch,
    MarkersIn,
    MoveIn,
    ReturnIn,
    ScanConfirmIn,
    SettingsIn,
    StatusIn,
    UserRoleIn,
)
from ..services import backup as backup_svc
from ..services import barcode as barcode_svc
from ..services import capture as capture_svc
from ..services import customers as customers_svc
from ..services import inventory as inv
from ..services import labels as labels_svc
from ..services import location_import, weblink
from ..services import maintenance as maintenance_svc
from ..services import scans as scans_svc
from ..services import search as search_svc
from ..services import stats as stats_svc
from ..services.media import MediaError
from ..services.placement import PlacementError, Resolver

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


@router.get("/stats")
def stats(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    s = _state(request).settings
    st = stats_svc.stats(db, s.warranty_notice_days, s.expiry_notice_days)
    st.update(maintenance_svc.counts(db))
    st.update(customers_svc.counts(db))
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
    r = _resolver(db)
    stmt = select(Container).where(Container.deleted_at.is_(None))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Container.name.ilike(like), Container.number.ilike(like), Container.qr_code == q.strip().upper(), Container.description.ilike(like)))
    containers = db.scalars(stmt).all()
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
def resolve_qr(body: dict[str, str], actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Turn any label payload (link, HINV:… code or bare code) into a record."""
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
    stmt = (
        select(Item)
        .options(selectinload(Item.product).selectinload(Product.category), selectinload(Item.loans))
        .join(Product)
        .outerjoin(Category, Product.category_id == Category.id)
        .where(Item.deleted_at.is_(None))
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Product.name.ilike(like),
                Product.manufacturer.ilike(like),
                Product.model_number.ilike(like),
                Product.barcode.ilike(like),
                Product.description.ilike(like),
                Item.label.ilike(like),
                Item.serial_number.ilike(like),
                Category.name.ilike(like),
            )
        )
    if status:
        stmt = stmt.where(Item.status == status)
    if category:
        stmt = stmt.where(func.lower(Category.name) == category.lower())
    if container_id:
        within = r.descendant_container_ids(container_id)
        stmt = stmt.where(Item.container_id.in_(within))
    if loose:
        stmt = stmt.where(Item.container_id.is_(None), Item.location_id.is_not(None))
    if needs_details:
        stmt = stmt.where(Product.identification_status == IdentificationStatus.pending)
    order = {
        "name": [func.lower(Product.name), Item.id],
        "recent": [Item.updated_at.desc()],
        "created": [Item.created_at.desc()],
        "oldest": [Item.id],
        "value": [Item.purchase_price.desc().nulls_last()],
        "expiry": [Item.expires_on.asc().nulls_last()],
        "warranty": [Item.warranty_expires.asc().nulls_last()],
    }.get(sort, [func.lower(Product.name)])
    items = db.scalars(stmt.order_by(*order)).all()
    if location_id:
        locs = r.descendant_location_ids(location_id)
        conts = r.containers_in_location_tree(location_id)
        items = [i for i in items if i.location_id in locs or i.container_id in conts]
    total = len(items)
    page = items[offset : offset + limit]
    return {"total": total, "items": [inv.item_out(i, r, db) for i in page]}


@router.post("/items", status_code=201)
def create_item(data: ItemCreate, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    items = _err(inv.create_items, db, actor, data)
    db.commit()
    r = _resolver(db)
    return {"items": [inv.item_out(i, r, db, detail=True) for i in items]}


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
    return [inv.item_out(i, r, db) for i in db.scalars(stmt.order_by(Item.expires_on))]


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
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Quick photo: store the photo as a new item in the given place, flagged
    as needing details, and queue an AI suggestion in the background."""
    st = _state(request)
    if container_id is None and location_id is None:
        raise HTTPException(422, "Choose a container or location for quick photos")
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
    item = _err(capture_svc.capture, db, actor, photo, container_id, location_id, st.ai.enabled)
    db.commit()
    if st.ai.enabled:
        st.capture_queue.put_nowait(item.id)
    return inv.item_out(item, _resolver(db), db)


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
    return {"items": [inv.item_out(i, r, db) for i in items], "containers": [inv.container_out(c, r, db) for c in containers]}


@router.get("/warranties")
def warranties(days: int = Query(90, ge=0, le=3650), include_expired: bool = False, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    today = date.today()
    stmt = select(Item).options(selectinload(Item.product), selectinload(Item.loans)).where(Item.deleted_at.is_(None), Item.warranty_expires.is_not(None))
    if not include_expired:
        stmt = stmt.where(Item.warranty_expires >= today)
    stmt = stmt.where(Item.warranty_expires <= today + timedelta(days=days))
    return [inv.item_out(i, r, db) for i in db.scalars(stmt.order_by(Item.warranty_expires))]


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
def create_category(data: CategoryIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    cat = inv.get_or_create_category(db, data.name)
    cat.synonyms = data.synonyms
    db.commit()
    return {"id": cat.id, "name": cat.name, "synonyms": cat.synonyms}


@router.patch("/categories/{category_id}")
def patch_category(category_id: int, data: CategoryIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    cat = db.get(Category, category_id)
    if cat is None:
        raise HTTPException(404, "Category not found")
    clash = db.scalar(select(Category).where(func.lower(Category.name) == data.name.lower(), Category.id != cat.id))
    if clash:
        raise HTTPException(409, "A category with that name already exists")
    cat.name, cat.synonyms = data.name, data.synonyms
    db.commit()
    return {"id": cat.id, "name": cat.name, "synonyms": cat.synonyms}


# ---------------------------------------------------------------------------
# attachments
# ---------------------------------------------------------------------------


@router.post("/attachments", status_code=201)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    kind: AttachmentKind = Form(AttachmentKind.photo),
    item_id: int | None = Form(None),
    container_id: int | None = Form(None),
    location_id: int | None = Form(None),
    repair_job_id: int | None = Form(None),
    caption: str | None = Form(None),
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    st = _state(request)
    limit = st.media.max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {limit // (1024 * 1024)} MB")
    owners = [x for x in (item_id, container_id, location_id) if x is not None]
    if len(owners) > 1:
        raise HTTPException(422, "Attach to at most one record")
    for model, oid in ((Item, item_id), (Container, container_id), (Location, location_id)):
        if oid is not None:
            _get_or_404(db, model, oid)
    job = None
    if repair_job_id is not None:
        from ..models import RepairJob

        job = db.get(RepairJob, repair_job_id)
        if job is None or job.deleted_at is not None:
            raise HTTPException(404, "Repair job not found")
        if owners:
            raise HTTPException(422, "A repair job photo is attached to the job's equipment automatically")
        item_id = job.item_id
    try:
        stored = await asyncio.to_thread(st.media.save, data, kind is not AttachmentKind.photo)
    except MediaError as err:
        raise HTTPException(415, str(err)) from err
    att = Attachment(
        kind=kind,
        item_id=item_id,
        container_id=container_id,
        location_id=location_id,
        original_name=(file.filename or "")[:255] or None,
        caption=(caption or "")[:300] or None,
        created_by_id=actor.user_id,
        repair_job_id=job.id if job else None,
        **stored.__dict__,
    )
    db.add(att)
    db.flush()
    if job is not None:
        audit.record(db, actor, "repair_document", "item", item_id or 0, f"{job.number}: added {kind.value}")
    elif item_id and kind is AttachmentKind.photo:
        item = db.get(Item, item_id)
        if item.primary_photo_id is None:
            item.primary_photo_id = att.id
        audit.record(db, actor, "photo_added", "item", item_id, f"Added a photo to {item.product.name}")
    elif item_id:
        item = db.get(Item, item_id)
        audit.record(db, actor, "document_added", "item", item_id, f"Added {kind.value} to {item.product.name}")
    elif container_id:
        audit.record(db, actor, "photo_added", "container", container_id, "Added a photo", related_containers=[container_id])
    db.commit()
    return inv.attachment_out(att)


def _attachment_file(request: Request, att: Attachment, thumb: bool) -> FileResponse:
    st = _state(request)
    rel = att.thumb_path if thumb and att.thumb_path else att.stored_path
    path = st.media.path(rel)
    if not path.exists():
        raise HTTPException(404, "File missing")
    mime = "image/jpeg" if thumb and att.thumb_path else att.mime_type
    headers = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}
    disposition = "inline"
    if att.mime_type == "application/pdf":
        headers["Content-Security-Policy"] = "sandbox"
    name = att.original_name or f"attachment-{att.id}"
    return FileResponse(path, media_type=mime, headers=headers, filename=name, content_disposition_type=disposition)


@router.get("/attachments/{attachment_id}/file")
def attachment_file(attachment_id: int, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Not found")
    return _attachment_file(request, att, thumb=False)


@router.get("/attachments/{attachment_id}/thumb")
def attachment_thumb(attachment_id: int, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Not found")
    return _attachment_file(request, att, thumb=True)


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: int, request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise HTTPException(404, "Not found")
    if db.scalar(select(LocationMap.id).where(LocationMap.photo_id == att.id)):
        raise HTTPException(409, "This photo is used by a visual map")
    for it in db.scalars(select(Item).where(Item.primary_photo_id == att.id)):
        it.primary_photo_id = None
    for scan in db.scalars(select(ContainerScan).where(ContainerScan.photo_id == att.id)):
        scan.photo_id = None
    if att.item_id:
        audit.record(db, actor, "attachment_removed", "item", att.item_id, f"Removed {att.kind.value} {att.original_name or ''}".strip())
    st = _state(request)
    paths = (att.stored_path, att.thumb_path)
    db.delete(att)
    db.commit()
    for p in paths:
        st.media.delete(p)


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------


@router.get("/ai/status")
def ai_status(request: Request, actor: Actor = Depends(current_actor)):
    return _state(request).ai.describe()


@router.post("/ai/identify-item")
async def ai_identify_item(body: dict[str, Any], request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    st = _state(request)
    att = db.get(Attachment, int(body.get("attachment_id") or 0))
    if att is None or att.kind is not AttachmentKind.photo:
        raise HTTPException(404, "Photo not found")
    _limit(request, actor, "ai")
    data = st.media.read(att.stored_path)
    try:
        res = await asyncio.to_thread(st.ai.identify_item, data, (body.get("hint") or None))
    except AIUnavailable as err:
        raise HTTPException(503, str(err)) from err
    except AIFailed as err:
        raise HTTPException(502, str(err)) from err
    suggestion = res.data
    suggestion["confidence_label"] = confidence_label(suggestion.get("confidence"))
    similar = similar_products(suggestion["name"], actor, db) if suggestion.get("name") else []
    return {"suggestion": suggestion, "metadata": res.metadata(), "similar": similar}


@router.post("/containers/{container_id}/scans", status_code=201)
async def create_scan(
    container_id: int,
    request: Request,
    file: UploadFile | None = File(None),
    attachment_id: int | None = Form(None),
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    st = _state(request)
    c = _get_or_404(db, Container, container_id)
    if st.ai.enabled:
        _limit(request, actor, "ai")
    if file is not None:
        raw = await file.read(st.media.max_bytes + 1)
        if len(raw) > st.media.max_bytes:
            raise HTTPException(413, "File too large")
        try:
            stored = await asyncio.to_thread(st.media.save, raw, False)
        except MediaError as err:
            raise HTTPException(415, str(err)) from err
        att = Attachment(kind=AttachmentKind.photo, container_id=c.id, original_name=file.filename, caption="Contents photo", created_by_id=actor.user_id, **stored.__dict__)
        db.add(att)
        db.flush()
    elif attachment_id:
        att = db.get(Attachment, attachment_id)
        if att is None:
            raise HTTPException(404, "Photo not found")
        if att.container_id is None and att.item_id is None:
            att.container_id = c.id
    else:
        raise HTTPException(422, "A photo is required")
    scan = ContainerScan(container_id=c.id, photo_id=att.id, created_by_id=actor.user_id)
    db.add(scan)
    db.commit()
    image = st.media.read(att.stored_path)
    if st.ai.enabled:
        await asyncio.to_thread(scans_svc.analyse, db, scan, st.ai, image)
    else:
        scan.status = ScanStatus.failed
        scan.error = "AI is not configured; add items manually."
        db.commit()
    return scans_svc.scan_out(scan, db)


@router.get("/containers/{container_id}/scans")
def list_scans(container_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    scans = db.scalars(select(ContainerScan).where(ContainerScan.container_id == container_id).order_by(ContainerScan.id.desc()).limit(20)).all()
    return [scans_svc.scan_out(s, db) for s in scans]


@router.get("/scans/{scan_id}")
def get_scan(scan_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    scan = db.get(ContainerScan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    return scans_svc.scan_out(scan, db)


@router.post("/scans/{scan_id}/retry")
async def retry_scan(scan_id: int, request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    st = _state(request)
    scan = db.get(ContainerScan, scan_id)
    if scan is None or scan.photo_id is None:
        raise HTTPException(404, "Scan not found")
    if scan.status in (ScanStatus.confirmed, ScanStatus.discarded):
        raise HTTPException(409, "This scan has already been handled")
    _limit(request, actor, "ai")
    att = db.get(Attachment, scan.photo_id)
    await asyncio.to_thread(scans_svc.analyse, db, scan, st.ai, st.media.read(att.stored_path))
    return scans_svc.scan_out(scan, db)


@router.post("/scans/{scan_id}/confirm")
def confirm_scan(scan_id: int, data: ScanConfirmIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    scan = db.get(ContainerScan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    result = _err(scans_svc.confirm, db, actor, scan, data)
    db.commit()
    return result


@router.post("/scans/{scan_id}/discard")
def discard_scan(scan_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    scan = db.get(ContainerScan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    scan.status = ScanStatus.discarded
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# barcode, search
# ---------------------------------------------------------------------------


@router.post("/link/import")
async def link_import(data: LinkImportIn, request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Read product details (and the main photo) from a web page for review.

    Nothing is saved to the inventory; the photo is stored as an unattached
    upload (removed automatically if never used).
    """
    st = _state(request)
    _limit(request, actor, "weblink")
    ai = st.ai if st.ai.enabled else None
    try:
        page = await asyncio.to_thread(weblink.import_from_url, data.url, ai)
    except weblink.LinkError as err:
        raise HTTPException(422, str(err)) from err
    photo = None
    image_error = None
    if data.download_image and page.image_url:
        try:
            raw = await asyncio.to_thread(weblink.download_image, page.image_url)
            stored = await asyncio.to_thread(st.media.save, raw, False)
            att = Attachment(kind=AttachmentKind.photo, original_name="web-image", caption="From product page",
                             created_by_id=actor.user_id, **stored.__dict__)
            db.add(att)
            db.commit()
            photo = inv.attachment_out(att)
        except (weblink.LinkError, MediaError) as err:
            # Details go to the App log; the browser gets a fixed message.
            _LOGGER.info("Product photo from %s not added: %s", page.image_url, err)
            image_error = "The product photo could not be downloaded or is not a supported image."
    similar = similar_products(page.name, actor, db) if page.name else []
    existing = inv.find_product_by_barcode(db, page.barcode) if page.barcode else None
    return {
        "details": page.as_dict(),
        "photo": photo,
        "image_error": image_error,
        "similar": similar,
        "existing_product": inv.product_out(existing) if existing else None,
    }


@router.post("/barcode/lookup")
def barcode_lookup(data: BarcodeLookupIn, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    st = _state(request)
    if data.external:
        _limit(request, actor, "barcode")
    return barcode_svc.identify(db, st.settings, data.code, data.format, data.external, st.barcode_providers)


@router.get("/search")
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    ai: bool = True,
    limit: int = Query(50, ge=1, le=200),
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    st = _state(request)
    use_ai = st.ai if (ai and st.ai.enabled and _looks_like_question(q)) else None
    if use_ai is not None:
        try:
            _limit(request, actor, "ai")
        except HTTPException:
            use_ai = None  # over the AI limit: still answer, with keyword search
    out = search_svc.search(db, q, ai=use_ai, limit=limit)
    # Show each result's parent equipment / accessories so a search for
    # "air compressor" also surfaces its hose, inflator and fittings.
    from ..services import relations as rel_svc

    related = rel_svc.relations_for(db, [r["id"] for r in out["results"][:30]])
    for r in out["results"]:
        r["related"] = related.get(r["id"], [])[:6]
    return out


def _looks_like_question(q: str) -> bool:
    # Short keyword searches are answered instantly without AI.
    return len(q.split()) >= 3


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


@router.get("/labels/templates")
def label_templates(actor: Actor = Depends(current_actor)):
    return [{"key": t.key, "name": t.name, "per_page": t.cols * t.rows} for t in labels_svc.TEMPLATES.values()]


@router.post("/labels/pdf")
def labels_pdf(data: LabelRequest, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    base = _qr_base(db, _state(request), data.base_url)
    out = []
    for cid in data.container_ids:
        c = _get_or_404(db, Container, cid)
        contents: list[str] = []
        if data.include_contents:
            for it in db.scalars(select(Item).where(Item.container_id == c.id, Item.deleted_at.is_(None)).order_by(Item.id)):
                contents.append(f"{it.quantity} × {it.product.name}" if it.quantity != 1 else it.product.name)
        place = r.describe(c.parent_container_id, c.location_id)
        out.append(
            labels_svc.LabelData(
                code=c.qr_code,
                payload=labels_svc.qr_payload(c.qr_code, base, "C"),
                title=c.name,
                number=c.number,
                location=place["path"] if data.include_location and (c.location_id or c.parent_container_id) else None,
                contents=contents,
            )
        )
    for lid in data.location_ids:
        loc = _get_or_404(db, Location, lid)
        if not loc.qr_code:
            loc.qr_code = inv.new_qr_code(db)
            db.commit()
        contents = []
        if data.include_contents:
            for c in db.scalars(select(Container).where(Container.location_id == loc.id, Container.deleted_at.is_(None)).order_by(Container.number, Container.name)):
                contents.append(f"#{c.number} {c.name}" if c.number else c.name)
            for it in db.scalars(select(Item).where(Item.location_id == loc.id, Item.deleted_at.is_(None)).order_by(Item.id)):
                contents.append(f"{it.quantity} × {it.product.name}" if it.quantity != 1 else it.product.name)
        parent = r.location_path(loc.parent_id) if loc.parent_id else None
        out.append(
            labels_svc.LabelData(
                code=loc.qr_code,
                payload=labels_svc.qr_payload(loc.qr_code, base, "L"),
                title=loc.name,
                number=None,
                location=parent if data.include_location else None,
                contents=contents,
            )
        )
    pdf = labels_svc.render_pdf(out, data.template, data.copies, data.start_position)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="labels.pdf"'})


# ---------------------------------------------------------------------------
# visual maps
# ---------------------------------------------------------------------------


def _map_out(m: LocationMap, r: Resolver, db: Session, detail: bool = False) -> dict[str, Any]:
    att = db.get(Attachment, m.photo_id)
    out: dict[str, Any] = {
        "id": m.id,
        "title": m.title,
        "location_id": m.location_id,
        "location_path": r.location_path(m.location_id) if m.location_id else None,
        "photo": inv.attachment_out(att),
        "marker_count": len(m.markers),
    }
    if detail:
        markers = []
        for mk in m.markers:
            if mk.container_id:
                c = r.containers.get(mk.container_id)
                target = {"type": "container", "id": mk.container_id, "name": c.name if c else "?", "number": c.number if c else None,
                          "deleted": bool(c is None or c.deleted_at)}
            else:
                loc = r.locations.get(mk.location_id)
                target = {"type": "location", "id": mk.location_id, "name": loc.name if loc else "?", "deleted": bool(loc is None or loc.deleted_at)}
            markers.append({"id": mk.id, "x": mk.x, "y": mk.y, "label": mk.label, "target": target})
        out["markers"] = markers
    return out


@router.get("/maps")
def list_maps(location_id: int | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    stmt = select(LocationMap).options(selectinload(LocationMap.markers)).where(LocationMap.deleted_at.is_(None))
    if location_id:
        stmt = stmt.where(LocationMap.location_id.in_(r.descendant_location_ids(location_id)))
    return [_map_out(m, r, db) for m in db.scalars(stmt.order_by(LocationMap.title))]


@router.post("/maps", status_code=201)
def create_map(data: MapIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    att = db.get(Attachment, data.photo_id)
    if att is None or att.kind is not AttachmentKind.photo:
        raise HTTPException(404, "Photo not found")
    if data.location_id:
        _get_or_404(db, Location, data.location_id)
        if att.location_id is None and att.item_id is None and att.container_id is None:
            att.location_id = data.location_id
    m = LocationMap(title=data.title, location_id=data.location_id, photo_id=data.photo_id)
    db.add(m)
    db.flush()
    audit.record(db, actor, "created", "map", m.id, f"Created visual map {m.title}")
    db.commit()
    return _map_out(m, _resolver(db), db, detail=True)


@router.get("/maps/{map_id}")
def get_map(map_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    m = _get_or_404(db, LocationMap, map_id)
    return _map_out(m, _resolver(db), db, detail=True)


@router.patch("/maps/{map_id}")
def patch_map(map_id: int, data: MapPatch, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    m = _get_or_404(db, LocationMap, map_id)
    fields = data.model_dump(exclude_unset=True)
    if fields.get("location_id"):
        _get_or_404(db, Location, fields["location_id"])
    for k, v in fields.items():
        setattr(m, k, v)
    db.commit()
    return _map_out(m, _resolver(db), db, detail=True)


@router.put("/maps/{map_id}/markers")
def put_markers(map_id: int, data: MarkersIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    m = _get_or_404(db, LocationMap, map_id)
    for mk in data.markers:
        if mk.container_id:
            _get_or_404(db, Container, mk.container_id)
        if mk.location_id:
            _get_or_404(db, Location, mk.location_id)
    m.markers.clear()
    db.flush()
    for mk in data.markers:
        m.markers.append(MapMarker(**mk.model_dump()))
    audit.record(db, actor, "updated", "map", m.id, f"Updated {len(data.markers)} marker(s) on {m.title}")
    db.commit()
    return _map_out(m, _resolver(db), db, detail=True)


@router.delete("/maps/{map_id}", status_code=204)
def delete_map(map_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    m = _get_or_404(db, LocationMap, map_id)
    m.deleted_at = inv.now()
    audit.record(db, actor, "deleted", "map", m.id, f"Deleted visual map {m.title}")
    db.commit()


# ---------------------------------------------------------------------------
# history & trash
# ---------------------------------------------------------------------------


@router.get("/history")
def history(
    entity_type: str | None = None,
    entity_id: int | None = None,
    container_id: int | None = None,
    action: str | None = None,
    user: str | None = None,
    before_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    stmt = select(HistoryEvent)
    if not actor.can(Role.member):
        # Entries that name customers are for members and administrators only.
        stmt = stmt.where(HistoryEvent.entity_type != "customer",
                          ~HistoryEvent.action.like("repair\\_%", escape="\\"),
                          ~HistoryEvent.action.like("owner\\_%", escape="\\"))
    if container_id:
        stmt = stmt.where(
            or_(
                (HistoryEvent.entity_type == "container") & (HistoryEvent.entity_id == container_id),
                HistoryEvent.related_container_ids.like(f"%,{container_id},%"),
            )
        )
    else:
        if entity_type:
            stmt = stmt.where(HistoryEvent.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(HistoryEvent.entity_id == entity_id)
    if action:
        stmt = stmt.where(HistoryEvent.action == action)
    if user:
        stmt = stmt.where(HistoryEvent.user_name == user)
    if before_id:
        stmt = stmt.where(HistoryEvent.id < before_id)
    rows = db.scalars(stmt.order_by(HistoryEvent.id.desc()).limit(limit)).all()
    return {"events": [stats_svc.history_out(h) for h in rows], "next_before_id": rows[-1].id if len(rows) == limit else None}


@router.get("/trash")
def trash(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    r = _resolver(db)
    items = db.scalars(select(Item).options(selectinload(Item.product), selectinload(Item.loans)).where(Item.deleted_at.is_not(None)).order_by(Item.deleted_at.desc()).limit(200)).all()
    containers = db.scalars(select(Container).where(Container.deleted_at.is_not(None)).order_by(Container.deleted_at.desc()).limit(200)).all()
    locations = db.scalars(select(Location).where(Location.deleted_at.is_not(None)).order_by(Location.deleted_at.desc()).limit(200)).all()
    return {
        "items": [inv.item_out(i, r, db) for i in items],
        "containers": [inv.container_out(c, r, db) for c in containers],
        "locations": [inv.location_out(loc, r, db) for loc in locations],
    }


@router.delete("/trash/items/{item_id}", status_code=204)
def purge_item(item_id: int, request: Request, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    item = db.get(Item, item_id)
    if item is None or item.deleted_at is None:
        raise HTTPException(404, "Item is not in the trash")
    st = _state(request)
    files = []
    for att in db.scalars(select(Attachment).where(Attachment.item_id == item.id)):
        if not db.scalar(select(LocationMap.id).where(LocationMap.photo_id == att.id)):
            files += [att.stored_path, att.thumb_path]
    product = item.product
    audit.record(db, actor, "purged", "item", item.id, f"Permanently deleted {product.name}")
    for it in db.scalars(select(Item).where(Item.primary_photo_id.in_(select(Attachment.id).where(Attachment.item_id == item.id)))):
        it.primary_photo_id = None
    db.delete(item)
    db.flush()
    if not db.scalar(select(func.count()).select_from(Item).where(Item.product_id == product.id)):
        db.delete(product)
    db.commit()
    for f in files:
        st.media.delete(f)


# ---------------------------------------------------------------------------
# administration
# ---------------------------------------------------------------------------


@router.get("/users")
def users(request: Request, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    st = _state(request)
    out = []
    for u in db.scalars(select(User).where(User.ha_user_id.not_in(["system", "integration"])).order_by(User.display_name)):
        out.append(
            {
                "id": u.id,
                "name": u.display_name or u.username or u.ha_user_id,
                "username": u.username,
                "ha_is_admin": u.ha_is_admin,
                "role": effective_role(u, st.settings).value,
                "role_override": u.role_override.value if u.role_override else None,
                "last_seen": u.last_seen.isoformat(),
            }
        )
    return out


@router.patch("/users/{user_id}")
def set_user_role(user_id: int, data: UserRoleIn, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.id == actor.user_id and data.role not in (None, Role.admin):
        raise HTTPException(409, "You cannot remove your own administrator role")
    before = u.role_override.value if u.role_override else None
    u.role_override = data.role
    audit.record(db, actor, "role_changed", "user", u.id, f"Role of {u.display_name or u.username} set to {data.role.value if data.role else 'automatic'}",
                 {"role": before}, {"role": data.role.value if data.role else None})
    db.commit()
    return {"ok": True}


@router.get("/settings")
def get_settings(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    st = _state(request)
    syn = db.get(Setting, "synonyms")
    return {
        "currency": stats_svc.currency(db),
        "qr_base_url": _qr_base(db, st),
        "synonyms": syn.value if syn else {},
        "ai": st.ai.describe(),
        "barcode_providers": [name for name, _ in (st.barcode_providers if st.barcode_providers is not None else barcode_svc.providers(st.settings))],
        "warranty_notice_days": st.settings.warranty_notice_days,
        "mode": st.settings.mode,
        "version": st.version,
    }


@router.patch("/settings")
def patch_settings(data: SettingsIn, request: Request, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    for key, value in data.model_dump(exclude_unset=True).items():
        if key == "currency" and value:
            value = value.upper()
        s = db.get(Setting, key) or Setting(key=key)
        s.value = value
        db.merge(s)
    audit.record(db, actor, "settings_changed", "settings", 0, "Updated settings", after=data.model_dump(exclude_unset=True))
    db.commit()
    return get_settings(request, actor, db)


@router.get("/backup/export")
def backup_export(request: Request, actor: Actor = Depends(require_admin)):
    st = _state(request)
    tmp = Path(tempfile.mkdtemp(dir=st.settings.data_dir)) / "home-inventory-backup.zip"
    backup_svc.export_archive(st.db.engine, st.settings.media_dir, tmp)

    def cleanup() -> None:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()

    stamp = date.today().isoformat()
    return FileResponse(tmp, media_type="application/zip", filename=f"home-inventory-{stamp}.zip", background=BackgroundTask(cleanup))


@router.post("/backup/restore")
async def backup_restore(request: Request, file: UploadFile = File(...), actor: Actor = Depends(require_admin)):
    st = _state(request)
    limits = backup_svc.Limits.for_upload(st.settings.backup_max_mb)
    tmpdir = Path(tempfile.mkdtemp(prefix=".restore-upload-", dir=st.settings.data_dir))
    target = tmpdir / "upload.zip"
    try:
        size = 0
        with target.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limits.max_upload:
                    raise HTTPException(413, f"Backup is larger than the {st.settings.backup_max_mb} MB limit")
                fh.write(chunk)
        try:
            await asyncio.to_thread(backup_svc.validate_archive, target, limits)
            async with st.write_lock:
                result = await asyncio.to_thread(
                    backup_svc.restore_archive, target, st.settings.db_path, st.settings.media_dir,
                    st.settings.backup_dir, st.db.engine, st.reopen_database, limits,
                )
        except backup_svc.RestoreError as err:
            raise HTTPException(422, str(err)) from err
        # The restored database has its own users, so this goes to the App log.
        _LOGGER.warning("%s restored a backup made %s; previous data saved as %s",
                        actor.name, result["restored"].get("created_at", "at an unknown date"), result["safety_backup"])
        return result
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# events for the Home Assistant integration
# ---------------------------------------------------------------------------


@router.get("/events/stream")
async def event_stream(request: Request, actor: Actor = Depends(current_actor)):
    st = _state(request)
    if actor.channel != "api":
        raise HTTPException(403, "The event stream is for the Home Assistant integration")
    queue = st.bus.subscribe()

    async def gen():
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"id: {msg['id']}\nevent: {msg['type']}\ndata: {json.dumps(msg)}\n\n"
        finally:
            st.bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

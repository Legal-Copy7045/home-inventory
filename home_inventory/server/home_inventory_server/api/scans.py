"""REST API: AI identification and photo inventories (scans)."""

from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import AIFailed, AIUnavailable, confidence_label
from ..auth import Actor, current_actor, get_db, require_member
from ..models import (
    Attachment,
    AttachmentKind,
    Container,
    ContainerScan,
    Item,
    Location,
    ScanStatus,
)
from ..schemas import (
    ScanConfirmIn,
)
from ..services import inventory as inv
from ..services import scans as scans_svc
from ..services.media import MediaError, crop_box
from .routes import _err, _get_or_404, _limit, _state, similar_products

_LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")



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


class ReceiptIn(BaseModel):
    attachment_id: int


@router.post("/items/{item_id}/read-receipt")
async def read_receipt(item_id: int, data: ReceiptIn, request: Request, actor: Actor = Depends(require_member),
                       db: Session = Depends(get_db)):
    """AI reads a receipt photo attached to this item and suggests the
    purchase date, price, shop and warranty end.  Nothing is saved."""
    st = _state(request)
    item = _get_or_404(db, Item, item_id)
    att = db.get(Attachment, data.attachment_id)
    if att is None or att.item_id != item.id:
        raise HTTPException(404, "Receipt not found on this item")
    if not (att.mime_type or "").startswith("image/"):
        raise HTTPException(422, "AI can read receipt photos, not PDFs")
    _limit(request, actor, "ai")
    try:
        res = await asyncio.to_thread(st.ai.read_receipt, st.media.read(att.stored_path), item.product.name)
    except AIUnavailable as err:
        raise HTTPException(503, str(err)) from err
    except AIFailed as err:
        raise HTTPException(502, str(err)) from err
    sug = res.data
    warranty_expires = None
    if sug["purchase_date"] and sug["warranty_months"]:
        start = date.fromisoformat(sug["purchase_date"])
        y, m = divmod(start.month - 1 + sug["warranty_months"], 12)
        day = min(start.day, calendar.monthrange(start.year + y, m + 1)[1])
        warranty_expires = date(start.year + y, m + 1, day).isoformat()
    sug["warranty_expires"] = warranty_expires
    sug["confidence_label"] = confidence_label(sug.get("confidence"))
    return {"suggestion": sug, "metadata": res.metadata()}


async def _new_scan(
    request: Request, actor: Actor, db: Session, file: UploadFile | None, attachment_id: int | None,
    container: Container | None = None, location: Location | None = None,
) -> dict:
    """Photo inventory of a container's contents or of a shelf/location."""
    st = _state(request)
    if st.ai.enabled:
        _limit(request, actor, "ai")
    owner = {"container_id": container.id} if container is not None else {"location_id": location.id}
    if file is not None:
        raw = await file.read(st.media.max_bytes + 1)
        if len(raw) > st.media.max_bytes:
            raise HTTPException(413, "File too large")
        try:
            stored = await asyncio.to_thread(st.media.save, raw, False)
        except MediaError as err:
            raise HTTPException(415, str(err)) from err
        caption = "Contents photo" if container is not None else "Shelf photo"
        att = Attachment(kind=AttachmentKind.photo, original_name=file.filename, caption=caption, created_by_id=actor.user_id,
                         **owner, **stored.__dict__)
        db.add(att)
        db.flush()
    elif attachment_id:
        att = db.get(Attachment, attachment_id)
        if att is None:
            raise HTTPException(404, "Photo not found")
        if att.container_id is None and att.item_id is None and att.location_id is None:
            for k, v in owner.items():
                setattr(att, k, v)
    else:
        raise HTTPException(422, "A photo is required")
    scan = ContainerScan(photo_id=att.id, created_by_id=actor.user_id, **owner)
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


@router.post("/containers/{container_id}/scans", status_code=201)
async def create_scan(
    container_id: int,
    request: Request,
    file: UploadFile | None = File(None),
    attachment_id: int | None = Form(None),
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    c = _get_or_404(db, Container, container_id)
    return await _new_scan(request, actor, db, file, attachment_id, container=c)


@router.get("/containers/{container_id}/scans")
def list_scans(container_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    scans = db.scalars(select(ContainerScan).where(ContainerScan.container_id == container_id).order_by(ContainerScan.id.desc()).limit(20)).all()
    return [scans_svc.scan_out(s, db) for s in scans]


@router.post("/locations/{location_id}/scans", status_code=201)
async def create_location_scan(
    location_id: int,
    request: Request,
    file: UploadFile | None = File(None),
    attachment_id: int | None = Form(None),
    actor: Actor = Depends(require_member),
    db: Session = Depends(get_db),
):
    """Photo inventory of a shelf, bench, wall...: AI lists what it sees for
    review; confirmed items become loose items in this location."""
    loc = _get_or_404(db, Location, location_id)
    return await _new_scan(request, actor, db, file, attachment_id, location=loc)


@router.get("/locations/{location_id}/scans")
def list_location_scans(location_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    scans = db.scalars(select(ContainerScan).where(ContainerScan.location_id == location_id).order_by(ContainerScan.id.desc()).limit(20)).all()
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


def _cropper(st, db: Session, actor: Actor, scan: ContainerScan):
    """``crop(box)``: a new photo of just that part of the scan photo, or None."""
    photo = db.get(Attachment, scan.photo_id) if scan.photo_id else None
    source: list[bytes] = []

    def crop(box: list[float]) -> Attachment | None:
        if photo is None:
            return None
        try:
            if not source:
                source.append(st.media.read(photo.stored_path))
            part = crop_box(source[0], box)
            if part is None:
                return None
            stored = st.media.save(part, False)
        except Exception:  # noqa: BLE001 - fall back to the whole photo
            return None
        att = Attachment(kind=AttachmentKind.photo, caption=scans_svc.CUT_CAPTION, created_by_id=actor.user_id,
                         **stored.__dict__)
        db.add(att)
        db.flush()
        return att

    return crop


async def _recrop(request: Request, actor: Actor, db: Session, scan: ContainerScan) -> dict:
    st = _state(request)
    if not st.ai.enabled:
        raise HTTPException(422, "AI is not configured. Choose a provider in the App configuration.")
    photo = db.get(Attachment, scan.photo_id) if scan.photo_id else None
    if photo is None:
        raise HTTPException(404, "The photo of this photo inventory is no longer there")
    _limit(request, actor, "ai")
    crop = _cropper(st, db, actor, scan)
    image = st.media.read(photo.stored_path)
    try:
        out = await asyncio.to_thread(scans_svc.recrop, db, actor, scan, st.ai, image, crop, st.media)
    except (AIUnavailable, AIFailed) as err:
        db.rollback()
        raise HTTPException(502, str(err)) from err
    except inv.Conflict as err:
        db.rollback()
        raise HTTPException(409, str(err)) from err
    db.commit()
    return out


@router.post("/scans/{scan_id}/recrop")
async def recrop_scan(scan_id: int, request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Redo the cut-out photos of a confirmed photo inventory."""
    scan = db.get(ContainerScan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    return await _recrop(request, actor, db, scan)


@router.post("/items/{item_id}/redo-cutouts")
async def redo_item_cutouts(item_id: int, request: Request, actor: Actor = Depends(require_member),
                            db: Session = Depends(get_db)):
    """Redo the cut-outs of the photo inventory this item came from (all of its items and boxes)."""
    item = _get_or_404(db, Item, item_id)
    scan_id = (item.ai_metadata or {}).get("scan_id")
    scan = db.get(ContainerScan, scan_id) if scan_id else None
    if scan is None:
        raise HTTPException(404, "This item didn't come from a photo inventory")
    return await _recrop(request, actor, db, scan)


@router.post("/scans/{scan_id}/confirm")
def confirm_scan(scan_id: int, data: ScanConfirmIn, request: Request, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    scan = db.get(ContainerScan, scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found")
    crop = _cropper(_state(request), db, actor, scan)
    result = _err(scans_svc.confirm, db, actor, scan, data, crop)
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

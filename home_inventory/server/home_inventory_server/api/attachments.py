"""REST API: Photos and documents: upload, files, delete."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor, current_actor, get_db, require_member
from ..models import (
    Attachment,
    AttachmentKind,
    Container,
    ContainerScan,
    Item,
    Location,
    LocationMap,
)
from ..services import inventory as inv
from ..services.media import MediaError
from .routes import _get_or_404, _state

_LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")



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
    recognise: bool = Form(True),
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
    out = inv.attachment_out(att)
    if att.mime_type.startswith("image/"):
        from ..services.media import taken_on

        out["taken_on"] = taken_on(data)
    if recognise and item_id and job is None and kind is not AttachmentKind.photo:
        # A document on an item: AI works out what it is and what it can fill in.
        from .parts import start_document_reading

        reading = start_document_reading(request, db, actor, db.get(Item, item_id), att, quiet=True)
        out["reading_id"] = reading.id if reading else None
    return out


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

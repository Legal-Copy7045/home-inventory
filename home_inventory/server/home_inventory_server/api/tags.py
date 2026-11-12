"""NFC tags: assign, manage and resolve."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, authenticated_actor, current_actor, get_db, require_member
from ..events import queue_event
from ..models import Tag
from ..services import labels as labels_svc
from ..services import tags as tag_svc

router = APIRouter(prefix="/api/v1")

Target = Literal["item", "container", "location", "kit"]


class TagIn(BaseModel):
    model_config = {"extra": "forbid"}
    target_type: Target
    target_id: int
    label: str | None = Field(default=None, max_length=120)
    hardware_id: str | None = Field(default=None, max_length=64)
    default_action: str | None = Field(default=None, max_length=20)


class TagPatch(BaseModel):
    model_config = {"extra": "forbid"}
    target_type: Target | None = None
    target_id: int | None = None
    label: str | None = Field(default=None, max_length=120)
    status: Literal["active", "inactive", "lost"] | None = None
    hardware_id: str | None = Field(default=None, max_length=64)
    default_action: str | None = Field(default=None, max_length=20)


class ReplaceIn(BaseModel):
    model_config = {"extra": "forbid"}
    hardware_id: str | None = Field(default=None, max_length=64)


class ResolveIn(BaseModel):
    model_config = {"extra": "forbid"}
    text: str = Field(default="", max_length=2000)
    hardware_id: str | None = Field(default=None, max_length=64)


def _base(request: Request, db: Session, base: str | None = None) -> str:
    from .routes import _qr_base

    return _qr_base(db, request.app.state, base)


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tag_svc.TagError as err:
        raise HTTPException(422, str(err)) from err


def _tag(db: Session, tag_id: int) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(404, "Tag not found")
    return tag


@router.get("/tags")
def list_tags(request: Request, status: str | None = Query(None, max_length=10), target_type: Target | None = None,
              target_id: int | None = None, base: str | None = Query(None, max_length=500),
              actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    stmt = select(Tag)
    if status:
        stmt = stmt.where(Tag.status == status)
    if target_type:
        stmt = stmt.where(Tag.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(Tag.target_id == target_id)
    b = _base(request, db, base)
    return [tag_svc.tag_out(db, t, b) for t in db.scalars(stmt.order_by(Tag.status, Tag.id.desc()).limit(1000))]


@router.get("/tags/check")
def check_hardware(hardware_id: str = Query(..., max_length=64), request: Request = None, actor: Actor = Depends(current_actor),
                   db: Session = Depends(get_db)):
    """Is a chip (by serial number) already assigned?"""
    dup = tag_svc.duplicate_of(db, hardware_id)
    return {"duplicate": tag_svc.tag_out(db, dup, _base(request, db)) if dup else None}


@router.post("/tags", status_code=201)
def create_tag(data: TagIn, request: Request, base: str | None = Query(None, max_length=500),
               actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    tag = _run(tag_svc.create, db, actor, data.target_type, data.target_id, data.label, data.hardware_id, data.default_action)
    db.commit()
    return tag_svc.tag_out(db, tag, _base(request, db, base))


@router.patch("/tags/{tag_id}")
def change_tag(tag_id: int, data: TagPatch, request: Request, actor: Actor = Depends(require_member),
               db: Session = Depends(get_db)):
    tag = _tag(db, tag_id)
    fields = data.model_dump(exclude_unset=True)
    for k in ("target_type", "target_id", "status"):
        if fields.get(k) is None:
            fields.pop(k, None)
    _run(tag_svc.update, db, actor, tag, fields)
    db.commit()
    return tag_svc.tag_out(db, tag, _base(request, db))


@router.post("/tags/{tag_id}/replace", status_code=201)
def replace_tag(tag_id: int, data: ReplaceIn, request: Request, actor: Actor = Depends(require_member),
                db: Session = Depends(get_db)):
    new = _run(tag_svc.replace, db, actor, _tag(db, tag_id), data.hardware_id)
    db.commit()
    return tag_svc.tag_out(db, new, _base(request, db))


@router.delete("/tags/{tag_id}", status_code=204)
def deactivate_tag(tag_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    _run(tag_svc.update, db, actor, _tag(db, tag_id), {"status": "inactive"})
    db.commit()


@router.post("/tags/resolve")
def resolve_tag(data: ResolveIn, request: Request, actor: Actor = Depends(authenticated_actor), db: Session = Depends(get_db)):
    """Look up a scanned tag. Requires a normal login; the tag itself grants nothing."""
    try:
        tag = tag_svc.resolve(db, data.text, data.hardware_id)
    except tag_svc.TagNotFound as err:
        raise HTTPException(404, str(err)) from err
    queue_event(db, "tag_scanned", {"tag_id": tag.id, "target_type": tag.target_type, "target_id": tag.target_id,
                                    "default_action": tag.default_action, "user": actor.name})
    db.commit()
    return tag_svc.tag_out(db, tag, _base(request, db))


@router.get("/tags/{tag_id}/qr.svg")
def tag_qr(tag_id: int, request: Request, base: str | None = Query(None, max_length=500),
           actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """QR code of the same link, as a printed fallback for the NFC tag."""
    tag = _tag(db, tag_id)
    return Response(labels_svc.qr_svg(tag_svc.link_for(tag, _base(request, db, base))), media_type="image/svg+xml",
                    headers={"Cache-Control": "private, no-cache"})

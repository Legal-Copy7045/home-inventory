"""Printed contents checklists for boxes: print, read back, apply."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..services import container_checklist as cl
from ..services import inventory as inv

router = APIRouter(prefix="/api/v1")


def _box(db: Session, container_id: int):
    try:
        return cl.box(db, container_id)
    except inv.NotFound as err:
        raise HTTPException(404, str(err)) from err


@router.get("/containers/{container_id}/checklist")
def checklist_rows(container_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """The rows the printed checklist has (to review a completed one by hand)."""
    return {"rows": cl.rows(db, _box(db, container_id))}


@router.get("/containers/{container_id}/checklist.pdf")
def checklist_pdf(container_id: int, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    c = _box(db, container_id)
    data = cl.pdf(db, request.app.state.media, c)
    name = f"checklist-{c.number or c.id}.pdf".replace(" ", "-")
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.post("/containers/{container_id}/checklist/read")
async def read_checklist(container_id: int, request: Request, file: UploadFile = File(...),
                         actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Read the marks on a photo (or scanned PDF) of completed pages.  A
    suggestion only: nothing changes until the rows are confirmed."""
    from ..ai import AIFailed, AIUnavailable

    st = request.app.state
    c = _box(db, container_id)
    if not st.ai.enabled:
        raise HTTPException(503, "AI isn't set up, so the checklist can't be read. Fill it in by hand instead.")
    data = await file.read(st.settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > st.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "That file is too big")
    try:
        images = cl.page_images(data, file.content_type)
    except cl.ChecklistError as err:
        raise HTTPException(422, str(err)) from err
    st.rate_limiter.check(actor.user_id, "ai")
    expected = cl.rows(db, c)
    read, extras = [], []
    try:
        for img in images:
            res = await asyncio.to_thread(st.ai.read_checklist, img, expected)
            read += res.data["rows"]
            extras += res.data["extras"]
    except AIUnavailable as err:
        raise HTTPException(503, str(err)) from err
    except AIFailed as err:
        raise HTTPException(502, str(err)) from err
    return {"pages": len(images), **cl.interpret(db, c, read, extras)}


class ChecklistAction(BaseModel):
    item_id: int
    action: Literal["back", "move", "dispose", "missing", "none"] = "none"
    quantity: int | None = Field(default=None, ge=0, le=100000)
    container_id: int | None = None
    location_id: int | None = None
    note: str | None = Field(default=None, max_length=300)


class ChecklistApplyIn(BaseModel):
    actions: list[ChecklistAction] = Field(min_length=1, max_length=1000)


@router.post("/containers/{container_id}/checklist/apply")
def apply_checklist(container_id: int, data: ChecklistApplyIn, actor: Actor = Depends(require_member),
                    db: Session = Depends(get_db)):
    c = _box(db, container_id)
    try:
        out = cl.apply(db, actor, c, [a.model_dump() for a in data.actions])
    except (cl.ChecklistError, ValueError) as err:
        db.rollback()
        raise HTTPException(422, str(err)) from err
    except inv.Conflict as err:
        db.rollback()
        raise HTTPException(409, str(err)) from err
    db.commit()
    return out

"""Take it / put away, and identifying something from a photo to find it."""

from __future__ import annotations

import asyncio
import io
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..ai import AIFailed, AIUnavailable, confidence_label
from ..auth import Actor, current_actor, get_db, require_admin, require_member
from ..models import Item
from ..services import inventory as inv
from ..services import search as search_svc
from ..services import takeaway as svc
from .routes import _err, _get_or_404, _limit, _resolver, _state

router = APIRouter(prefix="/api/v1")


@router.post("/items/{item_id}/take")
def take_item(item_id: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    _err(svc.take, db, actor, item)
    db.commit()
    return inv.item_out(item, _resolver(db), db, detail=True)


@router.get("/taken")
def list_taken(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Everything taken and not yet put away; ``mine`` marks what you took."""
    items = svc.taken_items(db)
    out = inv.items_out(items, _resolver(db), db)
    mine = svc.taken_note(actor)
    for o, it in zip(out, items):
        o["mine"] = it.status_note == mine
    return {"items": out}


class PutAwayIn(BaseModel):
    container_id: int | None = None
    location_id: int | None = None
    mode: Literal["auto", "move", "temporary"] = "auto"


@router.post("/items/{item_id}/put-away")
def put_away(item_id: int, data: PutAwayIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    res = _err(svc.put_away, db, actor, item, data.container_id, data.location_id, data.mode)
    db.commit()
    r = _resolver(db)
    return {"result": res["result"], "item": inv.item_out(res["item"], r, db, detail=True),
            "target": r.describe(data.container_id, data.location_id)}


@router.get("/places/resolve")
def resolve_place(q: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Which location or container spoken words mean ("container number 5",
    "small engine repair"), best first; ``clear`` when it's safe to act."""
    from ..services import places

    return places.resolve(db, q[:200])


@router.post("/ai/identify-to-find")
async def identify_to_find(request: Request, file: UploadFile = File(...), hint: str | None = Form(None),
                           actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """What's in this photo, and which of your items it could be.
    The photo isn't stored."""
    st = _state(request)
    if not st.ai.enabled:
        raise HTTPException(503, "AI isn't set up")
    limit = st.media.max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File is larger than {limit // (1024 * 1024)} MB")
    try:
        Image.open(io.BytesIO(data)).verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as err:
        raise HTTPException(415, "That isn't a photo") from err
    _limit(request, actor, "ai")
    try:
        res = await asyncio.to_thread(st.ai.identify_item, data, (hint or "")[:300] or None)
    except AIUnavailable as err:
        raise HTTPException(503, str(err)) from err
    except AIFailed as err:
        raise HTTPException(502, str(err)) from err
    s = res.data
    s["confidence_label"] = confidence_label(s.get("confidence"))
    queries = [s.get("name") or ""]
    if s.get("manufacturer") or s.get("model_number"):
        queries.append(" ".join(x for x in (s.get("manufacturer"), s.get("model_number"), s.get("name")) if x))
    seen: set[int] = set()
    matches = []
    for q in queries:
        if not q.strip():
            continue
        for row in search_svc.search(db, q[:200], ai=None, limit=10)["results"]:
            if row["id"] not in seen:
                seen.add(row["id"])
                matches.append(row)
    return {"suggestion": s, "matches": matches[:10]}


# ---------------------------------------------------------------------------
# Voice history (Admin)
# ---------------------------------------------------------------------------


class VoiceEntryIn(BaseModel):
    source: str | None = None
    device: str | None = None
    intent: str | None = None
    slots: dict[str, str | None] = {}
    text: str | None = None
    answer: str | None = None
    question: bool = False


@router.post("/voice/history", status_code=201)
def add_voice_entry(data: VoiceEntryIn, request: Request, actor: Actor = Depends(current_actor)):
    """Written by the Home Assistant integration after a voice answer."""
    from ..services import voicelog

    _limit(request, actor, "voice_log")
    return voicelog.add(_state(request).settings.data_dir, data.model_dump(), actor.name)


@router.get("/voice/history")
def voice_history(request: Request, limit: int = 200, actor: Actor = Depends(require_admin)):
    from ..services import voicelog

    return {"entries": voicelog.recent(_state(request).settings.data_dir, max(1, min(limit, 500)))}


@router.delete("/voice/history", status_code=204)
def clear_voice_history(request: Request, actor: Actor = Depends(require_admin)):
    from ..services import voicelog

    voicelog.clear(_state(request).settings.data_dir)

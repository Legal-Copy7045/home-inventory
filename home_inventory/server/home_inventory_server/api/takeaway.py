"""Take it / put away, and identifying something from a photo to find it."""

from __future__ import annotations

import asyncio
import io
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import AIFailed, AIUnavailable, confidence_label
from ..auth import Actor, current_actor, get_db, require_admin, require_member
from ..models import Container, Item, Location
from ..services import inventory as inv
from ..services import scans as scans_svc
from ..services import search as search_svc
from ..services import takeaway as svc
from ..services.placement import PlacementError
from .routes import _err, _get_or_404, _limit, _resolver, _state

router = APIRouter(prefix="/api/v1")


@router.post("/items/{item_id}/take")
def take_item(item_id: int, transfer: bool = False, actor: Actor = Depends(require_member),
              db: Session = Depends(get_db)):
    """Take it.  Already taken by someone else: 409, unless ``transfer`` says
    you have it now (taking it over is recorded)."""
    item = _get_or_404(db, Item, item_id)
    try:
        svc.take(db, actor, item, transfer=transfer)
    except svc.AlreadyTaken as err:
        raise HTTPException(409, {"code": "already_taken", "message": str(err),
                                  "taken_by": svc.taker_name(db, item)}) from err
    except inv.Conflict as err:
        raise HTTPException(409, str(err)) from err
    db.commit()
    return _with_taker(db, actor, [item], inv.item_out(item, _resolver(db), db, detail=True))


def _with_taker(db: Session, actor: Actor, items: list[Item], out):
    """``mine`` and ``taken_by`` on item responses (by user id, not by name)."""
    one = not isinstance(out, list)
    rows = [out] if one else out
    for o, it in zip(rows, items):
        o["mine"] = svc.taken_by_me(it, actor)
        o["taken_by"] = svc.taker_name(db, it) if svc.is_taken(it) else None
    return rows[0] if one else rows


@router.get("/taken")
def list_taken(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Everything taken and not yet put away; ``mine`` marks what you took."""
    items = svc.taken_items(db)
    return {"items": _with_taker(db, actor, items, inv.items_out(items, _resolver(db), db))}


@router.get("/taken/route")
def taken_route(mine: bool = True, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Put-away route: what you've taken (or everyone's, ``mine=false``),
    grouped by where each thing goes, in the order to walk round."""
    items = svc.taken_items(db)
    if mine:
        items = [it for it in items if svc.taken_by_me(it, actor)]
    r = _resolver(db)
    stops = svc.route(r, items)
    for s in stops:
        s["items"] = _with_taker(db, actor, s["items"], inv.items_out(s["items"], r, db))
    return {"stops": stops, "count": len(items), "order": "room, then place within the room (alphabetical)"}


class PutAwayBatchIn(BaseModel):
    item_ids: list[int] = Field(min_length=1, max_length=200)
    container_id: int | None = None
    location_id: int | None = None
    # auto: back in its own place / usual home, otherwise "needs a decision"
    mode: Literal["auto", "move", "temporary"] = "auto"


@router.post("/put-away/batch")
def put_away_batch(data: PutAwayBatchIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Put several things away in one place.  Each item is its own small
    transaction, and every one gets a result: ``done`` (with how), ``choose``
    (somewhere new: needs "move it here or just this once?") or ``failed``
    with the reason.  Nothing is reported as done unless it was saved."""
    results = []
    for item_id in dict.fromkeys(data.item_ids):
        item = db.get(Item, item_id)
        if item is None or item.deleted_at is not None:
            results.append({"item_id": item_id, "status": "failed", "error": "Item not found"})
            continue
        name = item.product.name
        try:
            with db.begin_nested():
                res = svc.put_away(db, actor, item, data.container_id, data.location_id, data.mode)
            db.commit()
        except (inv.Conflict, inv.NotFound, PlacementError) as err:
            db.rollback()
            results.append({"item_id": item_id, "name": name, "status": "failed", "error": str(err)})
            continue
        status = "choose" if res["result"] == "choose" else "done"
        results.append({"item_id": item_id, "name": name, "status": status, "result": res["result"]})
    summary = {k: sum(1 for r in results if r["status"] == k) for k in ("done", "choose", "failed")}
    return {"results": results, **summary}


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


@router.post("/ai/which-place")
async def which_place(request: Request, file: UploadFile = File(...), actor: Actor = Depends(require_member),
                      db: Session = Depends(get_db)):
    """ "Which shelf is this?": the AI lists what's in the photo; the places
    where those things are recorded are ranked. The photo isn't stored."""
    st = _state(request)
    if not st.ai.enabled:
        raise HTTPException(503, "AI isn't set up")
    data = await file.read(st.media.max_bytes + 1)
    if len(data) > st.media.max_bytes:
        raise HTTPException(413, "File too large")
    try:
        Image.open(io.BytesIO(data)).verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as err:
        raise HTTPException(415, "That isn't a photo") from err
    _limit(request, actor, "ai")
    hint = "A photo of one shelf, drawer, cupboard or open box. List every separate thing you can see."
    try:
        res = await asyncio.to_thread(st.ai.identify_container, data, hint)
    except AIUnavailable as err:
        raise HTTPException(503, str(err)) from err
    except AIFailed as err:
        raise HTTPException(502, str(err)) from err
    seen = [e["name"] for e in res.data.get("items") or [] if e.get("name")][:40]
    r = _resolver(db)
    votes: dict[tuple[str, int], dict] = {}
    for name in seen:
        hits = search_svc.search(db, name[:200], ai=None, limit=5)["results"]
        for rank, row in enumerate(hits[:3]):
            p = row["placement"]
            key = ("container", p["container"]["id"]) if p.get("container") else (("location", p["location"]["id"]) if p.get("location") else None)
            if key is None:
                continue
            v = votes.setdefault(key, {"type": key[0], "id": key[1], "path": p.get("path"), "score": 0.0, "matched": []})
            v["score"] += 1.0 / (rank + 1)
            if row["display_name"] not in v["matched"]:
                v["matched"].append(row["display_name"])
    places = sorted(votes.values(), key=lambda v: -v["score"])[:3]
    for v in places:
        detail = (inv.container_out(db.get(Container, v["id"]), r, db, detail=True) if v["type"] == "container"
                  else inv.location_out(db.get(Location, v["id"]), r, db, detail=True))
        recorded = [i["display_name"] for i in detail.get("items") or []]
        v["recorded"] = recorded[:60]
        v["not_seen"] = [n for n in recorded if n not in v["matched"]][:30]
        v["score"] = round(v["score"], 2)
    return {"seen": seen, "places": places}


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


UNANSWERED = ("I couldn't find",)
NAME_INTENTS = {"HomeInventoryWhereIs", "HomeInventoryWhereDoesItGo", "HomeInventoryMove", "HomeInventoryChoose"}


@router.get("/voice/suggestions")
def voice_suggestions(request: Request, actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    """Names said by voice that weren't found, with the items they probably
    meant ("strimmer" → String trimmer), to add as another name."""
    from difflib import SequenceMatcher

    from ..models import Product
    from ..services import voicelog
    from ..services.textmatch import words

    said: dict[str, dict] = {}
    for e in voicelog.recent(_state(request).settings.data_dir, 500):
        if e.get("intent") not in NAME_INTENTS or not str(e.get("answer") or "").startswith(UNANSWERED):
            continue
        phrase = (e.get("slots") or {}).get("item") or (e.get("slots") or {}).get("choice")
        key = " ".join(words(phrase or ""))
        if not key:
            continue
        s = said.setdefault(key, {"phrase": phrase, "count": 0, "last": e.get("at")})
        s["count"] += 1
    products = list(db.scalars(select(Product).where(Product.deleted_at.is_(None))))
    out = []
    for key, s in sorted(said.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        compact = key.replace(" ", "")
        scored = []
        for p in products:
            names = [p.name] + [t.strip() for t in (p.tags or "").split(",") if t.strip()]
            if any(" ".join(words(n)) == key for n in names):
                scored = []  # already a name or another name of something: nothing to suggest
                break
            name_key = " ".join(words(p.name))
            score = max(scans_svc.similarity(key, name_key),
                        SequenceMatcher(None, compact, name_key.replace(" ", "")).ratio())
            if score >= 0.5:
                scored.append((score, p))
        else:
            scored.sort(key=lambda x: -x[0])
            out.append({**s, "suggestions": [{"product_id": p.id, "name": p.name, "score": round(sc, 2)} for sc, p in scored[:3]]})
    return {"phrases": out[:50]}


class AliasIn(BaseModel):
    alias: str


@router.post("/products/{product_id}/aliases")
def add_alias(product_id: int, data: AliasIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Another name for a product (kept with its tags): search and voice find it by that name too."""
    from .. import audit
    from ..models import Product

    p = _get_or_404(db, Product, product_id)
    alias = " ".join(data.alias.split())[:80]
    if not alias:
        raise HTTPException(422, "Say the other name")
    tags = [t.strip() for t in (p.tags or "").split(",") if t.strip()]
    if alias.lower() not in (t.lower() for t in tags) and alias.lower() != p.name.lower():
        tags.append(alias)
        p.tags = ", ".join(tags)[:1000]
        audit.record(db, actor, "updated", "product", p.id, f"Added another name for {p.name}: {alias}")
        db.commit()
    return {"product_id": p.id, "name": p.name, "tags": p.tags}

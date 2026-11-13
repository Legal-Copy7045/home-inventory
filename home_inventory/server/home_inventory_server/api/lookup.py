"""REST API: Barcode lookup, links, search and labels."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import (
    Attachment,
    AttachmentKind,
    Container,
    Item,
    Location,
)
from ..schemas import (
    BarcodeLookupIn,
    LabelRequest,
    LinkImportIn,
)
from ..services import barcode as barcode_svc
from ..services import inventory as inv
from ..services import labels as labels_svc
from ..services import search as search_svc
from ..services import weblink
from ..services.media import MediaError
from .routes import _err, _get_or_404, _limit, _qr_base, _resolver, _state, similar_products

_LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")



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


class UseOneIn(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    format: str | None = Field(default=None, max_length=30)
    item_id: int | None = None
    container_id: int | None = None


@router.post("/barcode/use-one")
def barcode_use_one(data: UseOneIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    """Scan a consumable's barcode to take one out of stock."""
    res = _err(barcode_svc.use_one, db, actor, data.code, data.format, data.item_id, data.container_id)
    db.commit()
    return res


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
                description=c.description,
                location=place["path"] if data.include_location and (c.location_id or c.parent_container_id) else None,
                contents=contents,
                arrow=data.arrows.get(f"C{c.id}"),
            )
        )
    for lid in data.location_ids:
        loc = _get_or_404(db, Location, lid)
        if not loc.qr_code:
            loc.qr_code = inv.new_qr_code(db)
            db.commit()
        contents = []
        if data.include_contents:
            # What belongs here: shelves within it, boxes, then loose items.
            for child in db.scalars(select(Location).where(Location.parent_id == loc.id, Location.deleted_at.is_(None)).order_by(Location.name)):
                contents.append(f"› {child.name}")
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
                description=loc.description,
                location=parent if data.include_location else None,
                contents=contents,
                arrow=data.arrows.get(f"L{loc.id}"),
            )
        )
    pdf = labels_svc.render_pdf(out, data.template, data.copies, data.start_position)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="labels.pdf"'})

"""REST API: Visual maps."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..auth import Actor, current_actor, get_db, require_member
from ..models import (
    Attachment,
    AttachmentKind,
    Container,
    Location,
    LocationMap,
    MapMarker,
)
from ..schemas import (
    MapIn,
    MapPatch,
    MarkersIn,
)
from ..services import inventory as inv
from ..services.placement import Resolver
from .routes import _get_or_404, _resolver

_LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")



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

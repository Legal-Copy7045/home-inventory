"""Inventory verification and the Needs Attention list."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from ..auth import Actor, current_actor, get_db, require_member
from ..models import Item, VerificationSession
from ..services import verification as svc
from ..services.inventory import NotFound
from .routes import _get_or_404

router = APIRouter(prefix="/api/v1")


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except NotFound as err:
        raise HTTPException(404, str(err)) from err
    except svc.VerifyError as err:
        raise HTTPException(422, str(err)) from err


class StartIn(BaseModel):
    location_id: int | None = None
    container_id: int | None = None
    category_id: int | None = None

    @model_validator(mode="after")
    def _one(self):
        if sum(x is not None for x in (self.location_id, self.container_id, self.category_id)) != 1:
            raise ValueError("Choose one place, box or category")
        return self


class CheckIn(BaseModel):
    result: Literal["present", "adjusted", "missing"]
    quantity: int | None = Field(default=None, ge=0, le=1_000_000)
    note: str | None = Field(default=None, max_length=300)


class FoundIn(BaseModel):
    item_id: int
    move: bool = False


class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    format: str | None = Field(default=None, max_length=20)


def _session(db: Session, sid: int) -> VerificationSession:
    s = db.get(VerificationSession, sid)
    if s is None:
        raise HTTPException(404, "Check not found")
    return s


@router.post("/verifications", status_code=201)
def start_check(data: StartIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    kind, sid = next((k, v) for k, v in (("location", data.location_id), ("container", data.container_id),
                                         ("category", data.category_id)) if v is not None)
    s = _run(svc.start, db, actor, kind, sid)
    db.commit()
    return svc.session_out(db, s, detail=True)


@router.get("/verifications")
def list_checks(limit: int = Query(30, ge=1, le=200), scope_type: Literal["location", "container", "category"] | None = None,
                scope_id: int | None = None, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return {"checks": svc.history(db, limit, scope_type, scope_id)}


@router.get("/verifications/{sid}")
def get_check(sid: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    return svc.session_out(db, _session(db, sid), detail=True)


@router.post("/verifications/{sid}/items/{item_id}")
def check_item(sid: int, item_id: int, data: CheckIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    s = _session(db, sid)
    item = _get_or_404(db, Item, item_id)
    if item_id not in (s.expected or []):
        raise HTTPException(422, "That isn't expected here: use Found here instead")
    _run(svc.check, db, actor, s, item, data.result, data.quantity, data.note)
    db.commit()
    return svc.session_out(db, s, detail=True)


@router.post("/verifications/{sid}/found-here")
def found_here(sid: int, data: FoundIn, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    s = _session(db, sid)
    item = _get_or_404(db, Item, data.item_id)
    _run(svc.found_here, db, actor, s, item, data.move)
    db.commit()
    return svc.session_out(db, s, detail=True)


@router.post("/verifications/{sid}/scan")
def scan_in_check(sid: int, data: ScanIn, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    from ..services import inventory as inv
    from .routes import _resolver

    s = _session(db, sid)
    res = svc.scan(db, s, data.code, data.format)
    r = _resolver(db)
    for key in ("expected", "elsewhere"):
        res[key] = inv.items_out([db.get(Item, i) for i in res[key]], r, db)
    return res


@router.post("/verifications/{sid}/finish")
def finish_check(sid: int, actor: Actor = Depends(require_member), db: Session = Depends(get_db)):
    s = _session(db, sid)
    _run(svc.finish, db, actor, s)
    db.commit()
    return svc.session_out(db, s, detail=True)


@router.get("/items/{item_id}/verification")
def item_verification(item_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    item = _get_or_404(db, Item, item_id)
    return {"confidence": svc.confidence(item, db=db), "history": svc.item_history(db, item)}


@router.get("/attention")
def needs_attention(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    """Everything that needs taking care of, most urgent first."""
    from ..services import attention

    return attention.collect(db, bool(db.info.get("can_view_customers", False)))

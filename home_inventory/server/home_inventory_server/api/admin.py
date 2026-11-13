"""REST API: History, trash, administration, backup and the event stream."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.background import BackgroundTask

from .. import audit
from ..auth import Actor, current_actor, effective_role, get_db, require_admin
from ..models import (
    Attachment,
    Container,
    HistoryEvent,
    Item,
    Location,
    LocationMap,
    Role,
    Setting,
    User,
)
from ..schemas import (
    SettingsIn,
    UserRoleIn,
)
from ..services import backup as backup_svc
from ..services import barcode as barcode_svc
from ..services import inventory as inv
from ..services import maintenance_notices
from ..services import stats as stats_svc
from .routes import _qr_base, _resolver, _state

_LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")



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
        "items": inv.items_out(items, r, db),
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
        **{k: (db.get(Setting, k).value if db.get(Setting, k) else None) for k in ("amazon_domain", "amazon_associate_tag")},
        "ai": st.ai.describe(),
        "barcode_providers": [name for name, _ in (st.barcode_providers if st.barcode_providers is not None else barcode_svc.providers(st.settings))],
        "warranty_notice_days": st.settings.warranty_notice_days,
        "maintenance_auto_shopping": maintenance_notices.auto_shopping(db),
        "hemisphere": (db.get(Setting, "hemisphere").value if db.get(Setting, "hemisphere") else None) or "north",
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

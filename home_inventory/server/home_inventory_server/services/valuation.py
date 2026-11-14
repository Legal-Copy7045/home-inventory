"""AI value estimates, for one item or many.

An estimate is a rough replacement price and a second-hand range, kept with
the item (``ai_metadata.value_estimate``), dated and marked as AI.  A price
the household entered always comes first in reports.

Estimating many items runs as one background job at a time (its progress is
kept in the ``value_estimate_job`` setting, so it survives page reloads):

* ``unpriced``: items without a purchase price;
* ``category``: items in one category;
* ``all``: the whole household inventory.

Items that share a product (the same thing kept in two places) are
estimated once.  Items estimated recently are skipped unless asked to
re-estimate.  Customer equipment is never included.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..ai import AIFailed, AIUnavailable
from ..auth import Actor
from ..models import Attachment, Category, Item, Product, Setting
from . import jobs
from .customers import household_only

_LOGGER = logging.getLogger(__name__)

JOB_KEY = "value_estimate_job"
# Identifies this run of the App: a job started before a restart was interrupted.
BOOT = uuid.uuid4().hex
SCOPES = ("unpriced", "category", "all")
RECENT_DAYS = 30
MAX_ITEMS = 2000


class ValuationError(ValueError):
    """User-facing problem."""


class AlreadyRunning(ValuationError):
    """Another run of value estimates hasn't finished."""


def describe(item: Item) -> str:
    p = item.product
    bits = [p.name, p.manufacturer and f"make {p.manufacturer}", p.model_number and f"model {p.model_number}",
            p.category and f"category {p.category.name}", item.purchase_date and f"bought {item.purchase_date.isoformat()}",
            item.purchase_price is not None and f"paid {item.purchase_price} {item.currency or ''}".strip(),
            p.description]
    return ", ".join(str(b) for b in bits if b)


def estimate(db: Session, ai, media, item: Item, currency: str) -> dict[str, Any]:
    """Ask AI for this item's value (raises AIFailed / AIUnavailable)."""
    photo = db.get(Attachment, item.primary_photo_id) if item.primary_photo_id else None
    image = None
    if photo is not None:
        try:
            image = media.read(photo.stored_path)
        except OSError:
            image = None
    res = ai.estimate_value(describe(item), currency, image)
    return {**res.data, "estimated_on": datetime.now(UTC).date().isoformat(), "provider": res.provider, "model": res.model}


def save(db: Session, actor: Actor | None, item: Item, est: dict[str, Any]) -> None:
    item.ai_metadata = {**(item.ai_metadata or {}), "value_estimate": est}
    if actor is not None:
        audit.record(db, actor, "value_estimated", "item", item.id, f"{item.product.name}: AI value estimate")


# --------------------------------------------------------------------------- choosing items


def _recent(item: Item, today: date) -> bool:
    est = (item.ai_metadata or {}).get("value_estimate") or {}
    try:
        return today - date.fromisoformat(est.get("estimated_on", "")) < timedelta(days=RECENT_DAYS)
    except ValueError:
        return False


def select_items(db: Session, scope: str, category_id: int | None = None, redo_recent: bool = False,
                 today: date | None = None) -> list[Item]:
    if scope not in SCOPES:
        raise ValuationError("Choose items without a price, a category, or everything")
    today = today or date.today()
    stmt = (select(Item).join(Product, Product.id == Item.product_id)
            .options(selectinload(Item.product).selectinload(Product.category))
            .where(Item.deleted_at.is_(None), Product.deleted_at.is_(None), household_only()).order_by(Item.id))
    if scope == "unpriced":
        stmt = stmt.where(Item.purchase_price.is_(None))
    elif scope == "category":
        if category_id is None or db.get(Category, category_id) is None:
            raise ValuationError("Choose a category")
        stmt = stmt.where(Product.category_id == category_id)
    items = list(db.scalars(stmt))
    if not redo_recent:
        items = [i for i in items if not _recent(i, today)]
    return items


def preview(db: Session, scope: str, category_id: int | None = None, redo_recent: bool = False,
            limits: dict[str, Any] | None = None) -> dict[str, Any]:
    items = select_items(db, scope, category_id, redo_recent)
    requests = len({i.product_id for i in items})
    out = {"items": len(items), "requests": requests, "too_many": len(items) > MAX_ITEMS, "max": MAX_ITEMS,
           "minutes": None, "days": None}
    if limits:
        lim = limits.get("limits") or {}
        if lim.get("per_minute"):
            out["minutes"] = -(-requests // lim["per_minute"])
        if lim.get("per_day"):
            left = max(0, lim["per_day"] - limits.get("requests_last_24h", 0))
            out["days"] = 1 if requests <= left else 1 + -(-(requests - left) // lim["per_day"])
    return out


# --------------------------------------------------------------------------- the background job


def job(db: Session) -> dict[str, Any] | None:
    s = db.get(Setting, JOB_KEY)
    return dict(s.value) if s is not None and s.value else None


def _put(db: Session, data: dict[str, Any]) -> None:
    s = db.get(Setting, JOB_KEY) or Setting(key=JOB_KEY)
    s.value = data
    db.merge(s)
    db.commit()


def running(db: Session) -> bool:
    return is_live(job(db))


def is_live(j: dict[str, Any] | None) -> bool:
    """Whether this job snapshot is still in progress.  Judged from the one
    snapshot: reading the job again could see it finish in between and make a
    finished job look interrupted."""
    if not j or j.get("status") not in ("pending", "running"):
        return False
    # A job from before the App restarted never finishes: let a new one start.
    # (A running job may wait hours for the AI limits, so time isn't a sign.)
    return j.get("boot") == BOOT


def start(state, actor: Actor, scope: str, category_id: int | None, redo_recent: bool) -> dict[str, Any]:
    db = state.db.SessionLocal()
    try:
        if running(db):
            raise AlreadyRunning("Values are already being estimated. Wait for that to finish, or stop it.")
        items = select_items(db, scope, category_id, redo_recent)
        if not items:
            raise ValuationError("There's nothing to estimate: every chosen item already has a recent estimate.")
        if len(items) > MAX_ITEMS:
            raise ValuationError(f"That's {len(items)} items; estimate at most {MAX_ITEMS} at a time (choose a category).")
        cat = db.get(Category, category_id) if category_id else None
        now = datetime.now(UTC).isoformat()
        data = {"status": "pending", "scope": scope, "category": cat.name if cat else None,
                "total": len(items), "done": 0, "failed": 0, "skipped": 0, "estimated_total": 0.0,
                "currency": None, "error": None, "started_at": now, "heartbeat": now, "finished_at": None,
                "started_by": actor.name, "cancel": False, "boot": BOOT}
        _put(db, data)
        ids = [i.id for i in items]
    finally:
        db.close()
    jobs.submit(lambda: _run(state, actor, ids), "value-estimates")
    return data


def cancel(db: Session) -> dict[str, Any] | None:
    j = job(db)
    if j and j.get("status") in ("pending", "running"):
        j["cancel"] = True
        _put(db, j)
    return j


def _run(state, actor: Actor, ids: list[int]) -> None:
    from . import stats as stats_svc

    db = state.db.SessionLocal()
    try:
        data = job(db) or {}
        data["status"] = "running"
        currency = stats_svc.currency(db)
        data["currency"] = currency
        _put(db, data)
        by_product: dict[int, dict[str, Any]] = {}
        unavailable = 0
        for item_id in ids:
            data = job(db) or data
            if data.get("cancel"):
                data["status"] = "cancelled"
                break
            item = db.get(Item, item_id)
            if item is None or item.deleted_at is not None:
                data["skipped"] += 1
            else:
                est = by_product.get(item.product_id)
                try:
                    if est is None:
                        # Waits for its turn within the AI rate limits (background work).
                        est = estimate(db, state.ai, state.media, item, currency)
                        by_product[item.product_id] = est
                    save(db, actor, item, est)
                    data["done"] += 1
                    if est.get("replacement"):
                        data["estimated_total"] = round(data["estimated_total"] + est["replacement"] * item.quantity, 2)
                    unavailable = 0
                except AIUnavailable as err:
                    data["failed"] += 1
                    unavailable += 1
                    if unavailable >= 3:  # the provider is down: stop rather than fail every item
                        data["status"], data["error"] = "failed", str(err)
                        break
                except AIFailed as err:
                    data["failed"] += 1
                    data["error"] = str(err)
            data["heartbeat"] = datetime.now(UTC).isoformat()
            _put(db, data)
        else:
            data["status"] = "finished"
        data["finished_at"] = datetime.now(UTC).isoformat()
        if data["done"]:
            audit.record(db, actor, "values_estimated", "settings", 0,
                         f"AI value estimates: {data['done']} item{'s' if data['done'] != 1 else ''}"
                         + (f" in {data['category']}" if data.get("category") else ""))
        _put(db, data)
    except Exception:  # noqa: BLE001 - recorded on the job
        _LOGGER.exception("Value estimates failed")
        db.rollback()
        data = job(db) or {}
        data.update({"status": "failed", "error": "Estimating stopped unexpectedly.",
                     "finished_at": datetime.now(UTC).isoformat()})
        _put(db, data)
    finally:
        db.close()

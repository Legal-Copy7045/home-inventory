"""Barcode identification.

Order of resolution:

1. The household's own inventory (exact GTIN match, then raw value).
2. Cached catalog lookups (positive results 90 days, misses 7 days).
3. External catalogs, enabled individually in the App configuration:

   * Open Food Facts / Open Products Facts / Open Beauty Facts – free, no
     key, ODbL-licensed data, ~100 product reads/min per IP.  Best coverage
     for groceries and household consumables; patchy for tools/electronics.
   * UPCitemdb trial endpoint – free, no key, 100 lookups/day; good coverage
     of North American retail goods (tools, electronics).
   * Barcode Lookup (barcodelookup.com) – commercial, API key required.

A barcode without any catalog result is normal; the user enters details
manually.  Catalog data is only ever *suggested*.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import BarcodeCache, Item, Product
from . import gtin as gtin_mod
from .inventory import product_out
from .placement import Resolver

_LOGGER = logging.getLogger(__name__)
USER_AGENT = "HomeInventory-HomeAssistantApp/0.1 (household inventory; https://github.com/Legal-Copy7045/home-inventory)"
HIT_TTL = timedelta(days=90)
MISS_TTL = timedelta(days=7)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in d.items() if v not in (None, "", [])}


def _open_facts(host: str, source: str) -> Callable[[str, float], dict[str, Any] | None]:
    def lookup(code: str, timeout: float) -> dict[str, Any] | None:
        r = httpx.get(
            f"https://{host}/api/v2/product/{code}.json",
            params={"fields": "product_name,generic_name,brands,categories,image_url,quantity,code"},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        body = r.json()
        if body.get("status") != 1 or not body.get("product"):
            return None
        p = body["product"]
        name = p.get("product_name") or p.get("generic_name")
        if not name:
            return None
        cats = [c.strip() for c in (p.get("categories") or "").split(",") if c.strip()]
        return _clean(
            {
                "name": name,
                "description": p.get("generic_name") if p.get("generic_name") != name else None,
                "manufacturer": (p.get("brands") or "").split(",")[0] or None,
                "category": cats[-1] if cats else None,
                "image_url": p.get("image_url"),
                "size": p.get("quantity"),
                "source": source,
                "license": "ODbL (Open Food Facts contributors)",
                "url": f"https://{host}/product/{code}",
            }
        )

    return lookup


def _upcitemdb(code: str, timeout: float) -> dict[str, Any] | None:
    r = httpx.get(
        "https://api.upcitemdb.com/prod/trial/lookup",
        params={"upc": code},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=timeout,
    )
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise RateLimited("UPCitemdb daily limit reached")
    r.raise_for_status()
    items = r.json().get("items") or []
    if not items:
        return None
    it = items[0]
    return _clean(
        {
            "name": it.get("title"),
            "description": it.get("description"),
            "manufacturer": it.get("brand") or it.get("manufacturer"),
            "model_number": it.get("model"),
            "category": (it.get("category") or "").split(">")[-1].strip() or None,
            "image_url": (it.get("images") or [None])[0],
            "source": "UPCitemdb",
        }
    )


def _barcodelookup(api_key: str) -> Callable[[str, float], dict[str, Any] | None]:
    def lookup(code: str, timeout: float) -> dict[str, Any] | None:
        r = httpx.get(
            "https://api.barcodelookup.com/v3/products",
            params={"barcode": code, "formatted": "y", "key": api_key},
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            raise RateLimited("Barcode Lookup rate limit reached")
        r.raise_for_status()
        products = r.json().get("products") or []
        if not products:
            return None
        p = products[0]
        return _clean(
            {
                "name": p.get("title"),
                "description": p.get("description"),
                "manufacturer": p.get("brand") or p.get("manufacturer"),
                "model_number": p.get("model") or p.get("mpn"),
                "category": (p.get("category") or "").split(">")[-1].strip() or None,
                "image_url": (p.get("images") or [None])[0],
                "source": "Barcode Lookup",
            }
        )

    return lookup


class RateLimited(RuntimeError):
    pass


def providers(settings: Settings) -> list[tuple[str, Callable[[str, float], dict[str, Any] | None]]]:
    out: list[tuple[str, Callable[[str, float], dict[str, Any] | None]]] = []
    if settings.barcode_open_facts:
        out += [
            ("openfoodfacts", _open_facts("world.openfoodfacts.org", "Open Food Facts")),
            ("openproductsfacts", _open_facts("world.openproductsfacts.org", "Open Products Facts")),
            ("openbeautyfacts", _open_facts("world.openbeautyfacts.org", "Open Beauty Facts")),
        ]
    if settings.barcode_upcitemdb:
        out.append(("upcitemdb", _upcitemdb))
    if settings.barcode_lookup_api_key:
        out.append(("barcodelookup", _barcodelookup(settings.barcode_lookup_api_key)))
    return out


def lookup_catalog(db: Session, settings: Settings, bc: gtin_mod.Barcode, provider_list=None) -> dict[str, Any] | None:
    key = bc.gtin14 or bc.raw
    now = datetime.now(UTC)
    provider_list = provider_list if provider_list is not None else providers(settings)
    for name, fn in provider_list:
        cached = db.scalar(select(BarcodeCache).where(BarcodeCache.gtin == key, BarcodeCache.provider == name))
        if cached is not None:
            fetched = cached.fetched_at if cached.fetched_at.tzinfo else cached.fetched_at.replace(tzinfo=UTC)
            if now - fetched < (HIT_TTL if cached.found else MISS_TTL):
                if cached.found:
                    return cached.data
                continue
        result = None
        try:
            for code in bc.lookup_codes:
                result = fn(code, settings.barcode_timeout)
                if result:
                    break
        except RateLimited as err:
            _LOGGER.info("%s", err)
            continue
        except (httpx.HTTPError, ValueError) as err:
            _LOGGER.info("Barcode lookup via %s failed: %s", name, err)
            continue  # transient: don't cache
        if cached is None:
            cached = BarcodeCache(gtin=key, provider=name)
            db.add(cached)
        cached.found = bool(result)
        cached.data = result
        cached.fetched_at = now
        db.commit()
        if result:
            return result
    return None


def identify(db: Session, settings: Settings, code: str, fmt: str | None, external: bool, provider_list=None) -> dict[str, Any]:
    bc = gtin_mod.parse(code, fmt)
    r = Resolver.load(db)
    stmt = select(Product).where(Product.deleted_at.is_(None))
    stmt = stmt.where(Product.gtin == bc.gtin14) if bc.gtin14 else stmt.where(Product.barcode == bc.raw)
    products = db.scalars(stmt).all()
    matches = []
    total = 0
    for p in products:
        holdings = []
        for it in db.scalars(select(Item).where(Item.product_id == p.id, Item.deleted_at.is_(None)).order_by(Item.id)):
            holdings.append(
                {
                    "item_id": it.id,
                    "quantity": it.quantity,
                    "status": it.status.value,
                    "label": it.label,
                    "path": r.describe_item(it)["path"],
                    "container": r.describe_item(it)["container"],
                }
            )
            total += it.quantity
        matches.append({"product": product_out(p), "holdings": holdings})

    catalog = None
    if external and not products:
        catalog = lookup_catalog(db, settings, bc, provider_list)
    elif products:
        catalog = products[0].catalog_metadata

    message = None
    if matches:
        lines = [f"{h['quantity']} in {h['path']}" for m in matches for h in m["holdings"]]
        name = matches[0]["product"]["name"]
        message = f"I found {name} in your inventory. You own {total}." + (
            "\n" + "\n".join(f"• {line}" for line in lines) if lines else ""
        )
    return {
        "barcode": {
            "raw": bc.raw,
            "display": bc.display,
            "gtin": bc.gtin14,
            "symbology": bc.symbology,
            "valid": bc.valid,
        },
        "inventory_matches": matches,
        "owned_quantity": total,
        "catalog": catalog,
        "message": message,
    }


def use_one(db: Session, actor, code: str, fmt: str | None, item_id: int | None = None,
            container_id: int | None = None) -> dict[str, Any]:
    """"Take one out" by barcode: decrement one unit of the matching product.

    Picks the holding automatically when it is unambiguous: the one chosen
    (``item_id``), else the one in ``container_id`` (scan mode's current
    container), else the only household holding with stock.  Otherwise the
    caller gets the choices back and nothing changes.
    """
    from ..services import inventory as inv
    from ..services.customers import household_only

    bc = gtin_mod.parse(code, fmt)
    stmt = select(Product).where(Product.deleted_at.is_(None))
    stmt = stmt.where(Product.gtin == bc.gtin14) if bc.gtin14 else stmt.where(Product.barcode == bc.raw)
    product_ids = [p.id for p in db.scalars(stmt)]
    if not product_ids:
        raise inv.NotFound("That barcode isn't in your inventory yet")
    r = Resolver.load(db)
    holdings = list(db.scalars(
        select(Item).where(Item.product_id.in_(product_ids), Item.deleted_at.is_(None), Item.quantity > 0, household_only())
        .order_by(Item.id)
    ))
    pick = None
    if item_id is not None:
        pick = next((h for h in holdings if h.id == item_id), None)
        if pick is None:
            raise inv.Conflict("That holding has none left or doesn't match this barcode")
    elif container_id is not None:
        pick = next((h for h in holdings if h.container_id == container_id), None)
    if pick is None and item_id is None and len(holdings) == 1:
        pick = holdings[0]
    if pick is None:
        if not holdings:
            raise inv.Conflict("None left to take out")
        return {"used": None, "choices": [
            {"item_id": h.id, "name": h.product.name, "quantity": h.quantity, "path": r.describe_item(h)["path"]}
            for h in holdings
        ]}
    inv.adjust_quantity(db, actor, pick, -1, "Barcode: took one out")
    return {"used": {"item_id": pick.id, "name": pick.product.name, "remaining": pick.quantity,
                     "path": r.describe_item(pick)["path"], "low_stock": pick.product.low_stock_notified_at is not None},
            "choices": []}

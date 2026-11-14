"""What to buy, from everything that needs stock: one line per part.

Demand comes from three places and is added up per product, so a plug used
by the mower, the generator and the snowblower is one line:

* **maintenance**: parts that tasks due within ``days`` need, beyond what's
  available (stock reserved for repair jobs isn't counted as available);
* **minimum stock**: products below their low-stock alert;
* **reserved**: repair reservations bigger than what's on the shelf.

Each line says what's needed, what's available, what's reserved, what's
short, and what to buy (in whole packs from the preferred source), grouped
by retailer.  Nothing is bought or added to the list automatically.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Item, Product, StockReservation
from . import parts as parts_svc
from . import replenishment as repl
from .customers import household_only
from .inventory import product_total


def demand(db: Session, days: int = 30) -> dict[str, Any]:
    lines: dict[int, dict[str, Any]] = {}

    def line(product: Product) -> dict[str, Any]:
        if product.id not in lines:
            lines[product.id] = {"product_id": product.id, "name": product.name, "reasons": [], "short": 0.0,
                                 "unit": None, "buy_quantity": 0}
        return lines[product.id]

    for ln in parts_svc.readiness(db, within_days=days)["lines"]:
        if ln["status"] not in ("missing", "partial") or not ln.get("buy"):
            continue
        product = db.get(Product, ln["buy"]["product_id"])
        if product is None:
            continue
        x = line(product)
        machines = sorted({t["item"]["name"] for t in ln.get("tasks", [])})
        x["reasons"].append({"kind": "maintenance", "text": f"maintenance due on {', '.join(machines)}",
                             "need": ln["quantity"], "unit": ln["unit"], "have": ln["have"], "reserved": ln.get("reserved", 0)})
        x["short"] += ln["short"]
        x["unit"] = ln["unit"]
        x["buy_quantity"] = max(x["buy_quantity"], int(ln["buy"]["quantity"]))
    settings = repl.retailer_settings(db)
    for row in repl.dashboard(db)["below_minimum"]:
        product = db.get(Product, row["product_id"])
        if product is None:
            continue
        x = line(product)
        x["reasons"].append({"kind": "minimum", "text": f"below its minimum of {row['minimum']}", "need": row["minimum"],
                             "unit": row["unit"], "have": row["stock"], "reserved": 0})
        x["buy_quantity"] = max(x["buy_quantity"], int(row["suggested_quantity"] or 0))
    over = db.execute(select(StockReservation.item_id, func.sum(StockReservation.quantity))
                      .where(StockReservation.released_at.is_(None), StockReservation.consumed_at.is_(None))
                      .group_by(StockReservation.item_id)).all()
    for item_id, qty in over:
        item = db.get(Item, item_id)
        if item is None or item.deleted_at is not None or qty <= item.quantity:
            continue
        x = line(item.product)
        x["reasons"].append({"kind": "reserved", "text": f"{qty} reserved for repairs, {item.quantity} on the shelf",
                             "need": int(qty), "unit": None, "have": item.quantity, "reserved": int(qty)})
        x["buy_quantity"] = max(x["buy_quantity"], int(qty) - item.quantity)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in lines.values():
        src = repl.preferred_source(db, x["product_id"])
        source = repl.source_out(src, settings, x["name"]) if src else None
        x["in_stock"] = product_total(db, x["product_id"])
        x["holdings"] = db.scalar(select(func.count()).select_from(Item).where(
            Item.product_id == x["product_id"], Item.deleted_at.is_(None), household_only())) or 0
        x["source"] = source
        x["buy_quantity"] = max(1, x["buy_quantity"])
        groups[(source or {}).get("retailer_label") or "No preferred shop"].append(x)
    out = [{"retailer": k, "lines": sorted(v, key=lambda x: x["name"].lower())} for k, v in groups.items()]
    out.sort(key=lambda g: (g["retailer"] == "No preferred shop", g["retailer"].lower()))
    return {"days": days, "groups": out, "count": len(lines)}

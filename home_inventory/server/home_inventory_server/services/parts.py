"""Parts and consumables: standards, what fits what, and readiness.

A part is a :class:`Product`.  There is no separate catalog, so stock is
only ever the product's household holdings (no double counting).

* **Standards** are generic products ("Engine oil SAE 10W-30, API SN").
  A branded product *meets* a standard through a confirmed link; matching
  ``specs`` only *suggests* the link.
* **Fitments** say what a model of equipment takes (owned by the
  equipment's product, so every machine of that model shares them).
* **Task parts** say what one maintenance task uses each time.

Readiness: for each part a task needs, what you have that can be used:

1. the product itself;
2. if it's a standard: anything that meets it;
3. if it's specific and equivalents are allowed: its standard(s) and
   anything meeting them, and its confirmed replacement part.

Links are followed one step only.  Suggested links count as "uncertain",
never as stock.  Demand for the same standard is added up across tasks
(the mower's "Honda 10W-30" and the generator's "10W-30" become one line).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..auth import Actor
from ..models import (
    Item,
    ItemStatus,
    MaintenanceTask,
    PartFitment,
    Product,
    ProductLink,
    TaskPart,
)
from .customers import household_only


class PartsError(ValueError):
    """User-facing validation problem."""


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

_UNITS: dict[str, tuple[str, Decimal]] = {
    "ml": ("volume", Decimal(1)), "cl": ("volume", Decimal(10)), "l": ("volume", Decimal(1000)),
    "litre": ("volume", Decimal(1000)), "liter": ("volume", Decimal(1000)),
    "g": ("mass", Decimal(1)), "kg": ("mass", Decimal(1000)),
}
_COUNT = {"", "each", "pc", "pcs", "piece", "pieces", "x"}


def norm_unit(unit: str | None) -> str | None:
    """Canonical spelling ("Litres" -> "L", "pcs" -> None meaning pieces)."""
    u = (unit or "").strip().lower().rstrip(".")
    if u.endswith("s") and u[:-1] in _UNITS:
        u = u[:-1]
    if u in _COUNT:
        return None
    if u in ("l", "litre", "liter"):
        return "L"
    return u[:10] if u in _UNITS else (unit or "").strip()[:10] or None


def _base(qty: Decimal, unit: str | None) -> tuple[str, Decimal]:
    """(dimension, amount in base unit).  Unknown units are their own dimension."""
    u = (unit or "").lower()
    if not u:
        return "count", qty
    if u in _UNITS:
        dim, factor = _UNITS[u]
        return dim, qty * factor
    return f"unit:{u}", qty


def _from_base(amount: Decimal, unit: str | None) -> Decimal:
    u = (unit or "").lower()
    return amount / _UNITS[u][1] if u in _UNITS else amount


def _num(v: Decimal | None) -> float | None:
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# Specs and standards
# ---------------------------------------------------------------------------


def _spec_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def clean_specs(specs: dict[str, Any] | None) -> dict[str, str] | None:
    if not specs:
        return None
    out: dict[str, str] = {}
    for k, v in list(specs.items())[:30]:
        k, v = str(k).strip()[:60], str(v if v is not None else "").strip()[:120]
        if k and v:
            out[k] = v
    return out or None


def specs_satisfy(product_specs: dict[str, Any] | None, standard_specs: dict[str, Any] | None) -> bool:
    """Every property of the standard is present, with the same value, on the
    product ("10W-30" == "10w30"; keys compared the same way)."""
    if not standard_specs or not product_specs:
        return False
    have = {_spec_key(k): _spec_key(v) for k, v in product_specs.items()}
    return all(have.get(_spec_key(k)) == _spec_key(v) for k, v in standard_specs.items())


def part_number_key(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _get_product(db: Session, product_id: int) -> Product:
    p = db.get(Product, product_id)
    if p is None or p.deleted_at is not None:
        raise PartsError("Product not found")
    return p


def save_details(db: Session, actor: Actor, product: Product, data: dict[str, Any]) -> Product:
    if "is_generic" in data and data["is_generic"] is not None:
        product.is_generic = bool(data["is_generic"])
    if "specs" in data:
        product.specs = clean_specs(data["specs"])
    if "content_quantity" in data:
        q = data["content_quantity"]
        product.content_quantity = Decimal(str(q)) if q not in (None, "") else None
    if "content_unit" in data:
        product.content_unit = norm_unit(data["content_unit"])
    if product.content_quantity is not None and product.content_quantity <= 0:
        raise PartsError("The amount in one pack must be more than 0")
    db.flush()
    suggest_meets_links(db, product)
    audit.record(db, actor, "product_changed", "product", product.id, f"{product.name}: updated part details")
    return product


def create_standard(db: Session, actor: Actor, name: str, specs: dict[str, Any] | None, unit: str | None,
                    category_id: int | None = None) -> Product:
    name = (name or "").strip()[:300]
    if not name:
        raise PartsError("Give the standard a name, e.g. Engine oil SAE 10W-30")
    p = Product(name=name, is_generic=True, specs=clean_specs(specs), content_unit=norm_unit(unit), category_id=category_id)
    db.add(p)
    db.flush()
    suggest_meets_links(db, p)
    audit.record(db, actor, "standard_added", "product", p.id, f"Added standard {p.name}")
    return p


def _link(db: Session, product_id: int, target_id: int, kind: str) -> ProductLink | None:
    return db.scalar(select(ProductLink).where(ProductLink.product_id == product_id, ProductLink.target_id == target_id,
                                               ProductLink.kind == kind))


def suggest_meets_links(db: Session, product: Product) -> int:
    """Suggest "meets" links where specs match.  Never confirms, never revives
    a rejected link."""
    made = 0
    if product.is_generic:
        if not product.specs:
            return 0
        others = db.scalars(select(Product).where(Product.deleted_at.is_(None), Product.is_generic.is_(False),
                                                  Product.specs.is_not(None)))
        pairs = [(o, product) for o in others]
    else:
        if not product.specs:
            return 0
        stds = db.scalars(select(Product).where(Product.deleted_at.is_(None), Product.is_generic.is_(True),
                                                Product.specs.is_not(None)))
        pairs = [(product, s) for s in stds]
    for prod, std in pairs:
        if prod.id != std.id and specs_satisfy(prod.specs, std.specs) and _link(db, prod.id, std.id, "meets") is None:
            db.add(ProductLink(product_id=prod.id, target_id=std.id, kind="meets", status="suggested", source="specs",
                               note="Specifications match"))
            made += 1
    if made:
        db.flush()
    return made


def set_link(db: Session, actor: Actor, product: Product, target: Product, kind: str, status: str,
             source: str = "user", note: str | None = None) -> ProductLink:
    if kind not in ("meets", "replaced_by"):
        raise PartsError("Unknown link")
    if status not in ("suggested", "confirmed", "rejected"):
        raise PartsError("Unknown status")
    if product.id == target.id:
        raise PartsError("A product can't be linked to itself")
    if kind == "meets" and not target.is_generic:
        raise PartsError(f"{target.name} isn't a standard. Mark it as a standard (generic) first.")
    if kind == "meets" and product.is_generic:
        raise PartsError("A standard can't meet another standard; link the branded products to it instead")
    if kind == "replaced_by" and _link(db, target.id, product.id, "replaced_by") is not None:
        raise PartsError("These two already replace each other the other way round")
    link = _link(db, product.id, target.id, kind)
    if link is None:
        link = ProductLink(product_id=product.id, target_id=target.id, kind=kind, source=source, created_by_id=actor.user_id)
        db.add(link)
    link.status = status
    if note is not None:
        link.note = note[:300] or None
    db.flush()
    what = {"meets": "meets", "replaced_by": "is replaced by"}[kind]
    audit.record(db, actor, "product_link_" + status, "product", product.id, f"{product.name} {what} {target.name}: {status}")
    return link


def link_out(link: ProductLink, db: Session) -> dict[str, Any]:
    p, t = db.get(Product, link.product_id), db.get(Product, link.target_id)
    return {"id": link.id, "kind": link.kind, "status": link.status, "source": link.source, "note": link.note,
            "product": _brief(p), "target": _brief(t)}


def _brief(p: Product | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"id": p.id, "name": p.name, "manufacturer": p.manufacturer, "model_number": p.model_number,
            "is_generic": bool(p.is_generic), "specs": p.specs or {},
            "content_quantity": _num(p.content_quantity), "content_unit": p.content_unit}


def standards(db: Session, q: str | None = None) -> list[dict[str, Any]]:
    stmt = select(Product).where(Product.deleted_at.is_(None), Product.is_generic.is_(True)).order_by(Product.name)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q.strip()}%"))
    return [_brief(p) for p in db.scalars(stmt.limit(100))]


def find_by_part_number(db: Session, number: str | None) -> Product | None:
    key = part_number_key(number)
    if len(key) < 3:
        return None
    # Compared in SQLite (hinv_pnkey, registered in db.py) rather than
    # loading every product.
    return db.scalars(select(Product).where(
        Product.deleted_at.is_(None),
        or_(func.hinv_pnkey(Product.model_number) == key, func.hinv_pnkey(Product.sku) == key),
    ).order_by(Product.id).limit(1)).first()


def details_out(db: Session, product: Product) -> dict[str, Any]:
    out_links = db.scalars(select(ProductLink).where(ProductLink.product_id == product.id, ProductLink.status != "rejected"))
    in_links = db.scalars(select(ProductLink).where(ProductLink.target_id == product.id, ProductLink.status != "rejected"))
    fits = db.scalars(select(PartFitment).where(PartFitment.part_product_id == product.id, PartFitment.status != "rejected"))
    takes = db.scalars(select(PartFitment).where(PartFitment.equipment_product_id == product.id,
                                                 PartFitment.status != "rejected"))
    return {
        **_brief(product),
        "stock": stock_amount(db, product),
        "links": [link_out(x, db) for x in out_links],
        "linked_from": [link_out(x, db) for x in in_links],
        "fits": [fitment_out(db, f) for f in fits],
        "takes": [fitment_out(db, f) for f in takes],
    }


# ---------------------------------------------------------------------------
# Fitments and task parts
# ---------------------------------------------------------------------------


def fitment_out(db: Session, f: PartFitment) -> dict[str, Any]:
    return {"id": f.id, "role": f.role, "quantity": _num(f.quantity), "unit": f.unit, "status": f.status,
            "source": f.source, "source_ref": f.source_ref, "note": f.note,
            "equipment": _brief(db.get(Product, f.equipment_product_id)), "part": _brief(db.get(Product, f.part_product_id))}


def save_fitment(db: Session, actor: Actor | None, equipment: Product, part: Product, role: str | None,
                 quantity: Decimal | None, unit: str | None, status: str = "confirmed", source: str = "user",
                 source_ref: str | None = None) -> PartFitment:
    role = (role or "").strip()[:80]
    f = db.scalar(select(PartFitment).where(PartFitment.equipment_product_id == equipment.id,
                                            PartFitment.part_product_id == part.id, PartFitment.role == role))
    if f is None:
        f = PartFitment(equipment_product_id=equipment.id, part_product_id=part.id, role=role, source=source,
                        created_by_id=actor.user_id if actor else None)
        db.add(f)
    elif f.status == "confirmed" and status == "suggested":
        return f  # never downgrade what a person confirmed
    f.status = status
    if quantity is not None:
        f.quantity = quantity
        f.unit = norm_unit(unit)
    if source_ref:
        f.source_ref = source_ref[:200]
    db.flush()
    return f


def task_parts(db: Session, task_id: int) -> list[TaskPart]:
    return list(db.scalars(select(TaskPart).where(TaskPart.task_id == task_id).order_by(TaskPart.id)))


def set_task_parts(db: Session, actor: Actor, task: MaintenanceTask, rows: list[dict[str, Any]]) -> list[TaskPart]:
    """Replace a task's parts.  Each row names a ``product_id``; the part is
    also remembered for the equipment's model (a confirmed fitment)."""
    item = db.get(Item, task.item_id)
    for old in task_parts(db, task.id):
        db.delete(old)
    db.flush()
    out = []
    for r in rows[:50]:
        part = _get_product(db, int(r["product_id"]))
        qty = Decimal(str(r.get("quantity") or 1))
        if qty <= 0:
            raise PartsError("Quantities must be more than 0")
        tp = TaskPart(task_id=task.id, product_id=part.id, quantity=qty, unit=norm_unit(r.get("unit")),
                      allow_equivalent=bool(r.get("allow_equivalent", True)), note=(r.get("note") or None))
        db.add(tp)
        out.append(tp)
        if part.id != item.product_id:
            save_fitment(db, actor, item.product, part, r.get("role") or r.get("note") or "", qty, r.get("unit"),
                         status="confirmed", source=r.get("source") or "user", source_ref=r.get("source_ref"))
    db.flush()
    audit.record(db, actor, "maintenance_task_changed", "item", item.id,
                 f"{item.product.name}: parts for “{task.title}” set ({len(out)})")
    return out


# ---------------------------------------------------------------------------
# Stock and readiness
# ---------------------------------------------------------------------------


def _holdings(db: Session, product_id: int) -> list[Item]:
    return list(db.scalars(select(Item).where(Item.product_id == product_id, Item.deleted_at.is_(None),
                                              Item.status != ItemStatus.missing, household_only()).order_by(Item.id)))


def stock_amount(db: Session, product: Product) -> dict[str, Any]:
    count = sum(i.quantity for i in _holdings(db, product.id))
    out: dict[str, Any] = {"count": count}
    if product.content_quantity is not None:
        out["amount"] = float(product.content_quantity * count)
        out["unit"] = product.content_unit
    return out


def _amount(product: Product, count: int, dim: str) -> Decimal | None:
    """How much ``count`` holdings of ``product`` are in dimension ``dim``
    (base units), or None if they can't be compared."""
    if dim == "count":
        return Decimal(count)
    if product.content_quantity is None:
        return None
    pdim, amount = _base(product.content_quantity * count, product.content_unit)
    return amount if pdim == dim else None


@dataclass
class _Candidate:
    product: Product
    sure: bool
    why: str


def candidates(db: Session, product: Product, allow_equivalent: bool) -> list[_Candidate]:
    """What can be used for ``product`` (see module doc); one link step only."""
    out: dict[int, _Candidate] = {product.id: _Candidate(product, True, "exact")}

    def add(p: Product | None, sure: bool, why: str) -> None:
        if p is None or p.deleted_at is not None:
            return
        cur = out.get(p.id)
        if cur is None or (sure and not cur.sure):
            out[p.id] = _Candidate(p, sure, why)

    def meeting(std: Product, sure_std: bool) -> None:
        for link in db.scalars(select(ProductLink).where(ProductLink.target_id == std.id, ProductLink.kind == "meets",
                                                         ProductLink.status.in_(("confirmed", "suggested")))):
            add(db.get(Product, link.product_id), sure_std and link.status == "confirmed", f"meets {std.name}")

    if product.is_generic:
        meeting(product, True)
    elif allow_equivalent:
        for link in db.scalars(select(ProductLink).where(ProductLink.product_id == product.id,
                                                         ProductLink.status.in_(("confirmed", "suggested")))):
            target = db.get(Product, link.target_id)
            sure = link.status == "confirmed"
            if link.kind == "replaced_by":
                add(target, sure, f"replaces {product.name}")
            elif link.kind == "meets" and target is not None:
                add(target, sure, "the standard it meets")
                meeting(target, sure)
    return list(out.values())


def demand_key(db: Session, product: Product, allow_equivalent: bool) -> int:
    """Requirements with the same key share stock: a specific product whose
    equivalents are allowed is pooled under its one confirmed standard."""
    if product.is_generic or not allow_equivalent:
        return product.id
    stds = db.scalars(select(ProductLink.target_id).where(ProductLink.product_id == product.id, ProductLink.kind == "meets",
                                                          ProductLink.status == "confirmed")).all()
    return stds[0] if len(stds) == 1 else product.id


@dataclass
class _Need:
    key: int
    product: Product
    unit: str | None
    dim: str
    amount: Decimal  # base units
    allow_equivalent: bool
    tasks: list[dict[str, Any]] = field(default_factory=list)


def _need_for(db: Session, tp: TaskPart) -> _Need:
    product = db.get(Product, tp.product_id)
    unit = tp.unit
    dim, amount = _base(tp.quantity, unit)
    return _Need(demand_key(db, product, tp.allow_equivalent), product, unit, dim, amount, tp.allow_equivalent)


def evaluate(db: Session, needs: list[_Need]) -> dict[str, Any]:
    """Status for one line: needs sharing a key (same dimension assumed;
    mixed dimensions make the line uncertain)."""
    first = needs[0]
    dims = {n.dim for n in needs}
    total = sum((n.amount for n in needs), Decimal(0))
    pool: dict[int, _Candidate] = {}
    for n in needs:
        for c in candidates(db, n.product, n.allow_equivalent):
            if c.product.id not in pool or (c.sure and not pool[c.product.id].sure):
                pool[c.product.id] = c
    sure, maybe, unknown = Decimal(0), Decimal(0), False
    sources = []
    for c in pool.values():
        hold = _holdings(db, c.product.id)
        count = sum(i.quantity for i in hold)
        if not count:
            continue
        amt = _amount(c.product, count, first.dim) if len(dims) == 1 else None
        if amt is None:
            unknown = True
        elif c.sure:
            sure += amt
        else:
            maybe += amt
        sources.append({
            "product": _brief(c.product), "sure": c.sure, "why": c.why, "count": count,
            "amount": float(_from_base(amt, first.unit)) if amt is not None else None,
            "items": [{"id": i.id, "quantity": i.quantity, "label": i.label} for i in hold],
        })
    sources.sort(key=lambda s: (not s["sure"], s["product"]["id"] != first.product.id))
    if len(dims) > 1:
        status = "uncertain"
    elif sure >= total:
        status = "in_stock"
    elif sure + maybe >= total or (unknown and sure < total):
        status = "uncertain"
    elif sure > 0:
        status = "partial"
    else:
        status = "missing"
    short = max(total - sure, Decimal(0))
    key_product = db.get(Product, first.key)
    buy = _buy(db, needs, short, first.dim) if status in ("partial", "missing", "uncertain") and short > 0 else None
    return {
        "key": first.key,
        "product": _brief(key_product),
        "quantity": float(_from_base(total, first.unit)),
        "unit": first.unit,
        "status": status,
        "have": float(_from_base(sure, first.unit)),
        "have_uncertain": float(_from_base(maybe, first.unit)),
        "short": float(_from_base(short, first.unit)),
        "sources": sources,
        "buy": buy,
    }


def _buy(db: Session, needs: list[_Need], short: Decimal, dim: str) -> dict[str, Any] | None:
    """What to put on the shopping list: a named product if a task names one
    (else the standard itself), in whole packs."""
    named = next((n.product for n in needs if not n.product.is_generic), None)
    product = named or needs[0].product
    if dim == "count":
        qty = math.ceil(short)
    else:
        per = _amount(product, 1, dim)
        qty = math.ceil(short / per) if per else 1
    return {"product_id": product.id, "name": product.name, "quantity": max(1, int(qty))}


def task_readiness(db: Session, task: MaintenanceTask) -> list[dict[str, Any]]:
    rows = []
    for tp in task_parts(db, task.id):
        line = evaluate(db, [_need_for(db, tp)])
        product = db.get(Product, tp.product_id)
        line.update({"task_part_id": tp.id, "product": _brief(product), "allow_equivalent": tp.allow_equivalent,
                     "note": tp.note})
        rows.append(line)
    return rows


def readiness(db: Session, within_days: int = 30, item_filter=None) -> dict[str, Any]:
    """Parts for every task that is overdue or due within ``within_days``,
    added up across equipment."""
    from . import maintenance as mnt

    dash = mnt.dashboard(db, within_days=within_days, item_filter=item_filter)
    groups: dict[int, list[_Need]] = {}
    tasks_without_parts = []
    for t in dash["overdue"] + dash["due"]:
        tps = task_parts(db, t["id"])
        if not tps:
            tasks_without_parts.append({"id": t["id"], "title": t["title"], "item": t["item"], "status": t["status"]})
        for tp in tps:
            need = _need_for(db, tp)
            need.tasks.append({"id": t["id"], "title": t["title"], "item": t["item"], "status": t["status"],
                               "quantity": float(tp.quantity), "unit": tp.unit})
            groups.setdefault(need.key, []).append(need)
    lines = []
    for needs in groups.values():
        line = evaluate(db, needs)
        line["tasks"] = [t for n in needs for t in n.tasks]
        lines.append(line)
    order = {"missing": 0, "partial": 1, "uncertain": 2, "in_stock": 3}
    lines.sort(key=lambda x: (order[x["status"]], x["product"]["name"].lower()))
    return {"within_days": within_days, "lines": lines, "tasks_without_parts": tasks_without_parts,
            "short_count": sum(1 for x in lines if x["status"] in ("missing", "partial"))}


def item_parts(db: Session, item: Item) -> dict[str, Any]:
    """The item page's Maintenance & Parts section: what its model takes,
    and each task's parts with readiness."""
    takes = db.scalars(select(PartFitment).where(PartFitment.equipment_product_id == item.product_id,
                                                 PartFitment.status != "rejected").order_by(PartFitment.role, PartFitment.id))
    tasks = db.scalars(select(MaintenanceTask).where(MaintenanceTask.item_id == item.id, MaintenanceTask.deleted_at.is_(None))
                       .order_by(MaintenanceTask.sort_order, MaintenanceTask.id))
    return {
        "model": _brief(item.product),
        "fitments": [fitment_out(db, f) for f in takes],
        "tasks": {t.id: task_readiness(db, t) for t in tasks},
    }

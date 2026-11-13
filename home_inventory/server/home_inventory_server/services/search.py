"""Inventory search.

Natural-language questions are turned into *filters* (term groups, intent,
manufacturer, status) either by a built-in rule-based parser or, when
configured, by an AI provider.  Results and the answer sentence are always
built from database rows: the AI never supplies locations or quantities.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.orm import Session, selectinload

from ..models import Category, Item, ItemStatus, Product, Setting
from . import gtin as gtin_mod
from .inventory import active_loan, items_out
from .placement import Resolver
from .textmatch import stem

_LOGGER = logging.getLogger(__name__)

STOPWORDS = {
    "a", "about", "all", "am", "an", "and", "any", "anyone", "anybody", "are", "at", "borrowed", "can", "could",
    "did", "do", "does", "find", "for", "from", "get", "got", "has", "have", "how", "i", "in", "is", "it", "keep",
    "kept", "lend", "lent", "loan", "loaned", "locate", "many", "me", "much", "my", "of", "on", "or", "our", "own",
    "please", "put", "show", "so", "some", "someone", "somebody", "stored", "that", "the", "their", "them", "there",
    "these", "this", "those", "to", "us", "we", "what", "whats", "where", "wheres", "which", "who", "whom", "with",
    "you", "your", "list", "everything", "items", "stuff", "things", "owned", "borrow",
}

# Built-in synonym groups; administrators can add more in Settings.
DEFAULT_SYNONYMS: list[set[str]] = [
    {"christmas", "xmas", "holiday", "festive"},
    {"decoration", "decorations", "ornament", "ornaments", "decor"},
    {"axle stand", "jack stand", "axle stands", "jack stands"},
    {"usb-c", "usbc", "usb c", "type-c", "type c"},
    {"charger", "power adapter", "power supply", "adapter"},
    {"tv", "television"},
    {"extension cord", "extension lead", "extension cable", "power strip"},
    {"hoover", "vacuum", "vacuum cleaner"},
    {"spanner", "wrench"},
    {"torch", "flashlight"},
    {"impact wrench", "impact gun"},
    {"drill", "drill driver"},
    {"battery", "batteries", "battery pack"},
    {"screwdriver", "screw driver"},
    {"cable", "cord", "lead"},
    {"tape measure", "measuring tape"},
    {"halloween", "spooky"},
]

TERM_ALIASES = {"dewalt": "dewalt", "de walt": "dewalt"}


def singular(word: str) -> str:
    """The singular of a term (its last word), by the shared rule in ``textmatch.stem``."""
    head, _, last = word.rpartition(" ")
    return f"{head} {stem(last)}" if head else stem(last)


def normalise_text(text: str | None) -> str:
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"[^\w\s\-\.]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Query:
    raw: str
    intent: str = "find"
    terms: list[list[str]] = field(default_factory=list)  # AND of OR-groups
    manufacturer: str | None = None
    category: str | None = None
    status: str | None = None
    barcode: str | None = None
    interpreted_by: str = "rules"


class SynonymIndex:
    def __init__(self, groups: list[set[str]]) -> None:
        self.lookup: dict[str, set[str]] = defaultdict(set)
        for g in groups:
            norm = {normalise_text(x) for x in g} | {singular(normalise_text(x)) for x in g}
            for w in norm:
                self.lookup[w] |= norm

    def expand(self, term: str) -> list[str]:
        t = normalise_text(term)
        alts = {t, singular(t)} | self.lookup.get(t, set()) | self.lookup.get(singular(t), set())
        return sorted(a for a in alts if a)


def load_synonyms(db: Session) -> SynonymIndex:
    groups = [set(g) for g in DEFAULT_SYNONYMS]
    custom = db.get(Setting, "synonyms")
    if custom and isinstance(custom.value, dict):
        for k, vs in custom.value.items():
            groups.append({k, *(vs or [])})
    for cat in db.scalars(select(Category).where(Category.synonyms.is_not(None))):
        groups.append({cat.name, *[s.strip() for s in (cat.synonyms or "").split(",") if s.strip()]})
    return SynonymIndex(groups)


def parse_rules(raw: str, manufacturers: set[str], syn: SynonymIndex) -> Query:
    q = Query(raw=raw)
    text = normalise_text(raw)
    if re.search(r"\bhow (many|much)\b|\bcount\b|\bnumber of\b", text):
        q.intent = "count"
    elif re.search(r"\b(lend|lent|loan|loaned|borrow|borrowed|who has)\b", text):
        q.intent = "loans"
    elif re.search(r"\bwarrant(y|ies)\b", text):
        q.intent = "warranty"
    elif re.search(r"\b(expir\w*|use by|best before|out of date|go(es|ing)? off)\b", text):
        q.intent = "expiry"
    if re.search(r"\bmissing\b|\blost\b", text):
        q.status = ItemStatus.missing.value
    digits = re.sub(r"\s", "", raw)
    if re.fullmatch(r"\d{8,14}", digits):
        q.barcode = digits
        return q

    words = [w for w in text.split() if w]
    # Detect manufacturers (multi-word aware)
    for m in sorted(manufacturers, key=len, reverse=True):
        mn = normalise_text(m)
        if mn and re.search(rf"\b{re.escape(mn)}\b", " ".join(words)):
            q.manufacturer = m
            words = " ".join(words).replace(mn, " ").split()
            break
    for alias, canonical in TERM_ALIASES.items():
        if alias in " ".join(words) and not q.manufacturer:
            q.manufacturer = canonical
    noise = {"warranty", "warranties", "missing", "lost", "expire", "expires", "expired", "expiring", "expiry", "soon",
             "date", "dates", "off", "going", "goes", "go", "use", "best", "before", "out", "next", "week", "month"}
    content = [w for w in words if w not in STOPWORDS and w.strip("-.") and w not in noise]
    # Greedily join adjacent words into known multi-word synonyms ("axle stands").
    i = 0
    while i < len(content):
        if i + 1 < len(content):
            pair = f"{content[i]} {content[i + 1]}"
            if pair in syn.lookup or singular(pair) in syn.lookup or f"{content[i]} {singular(content[i + 1])}" in syn.lookup:
                key = pair if pair in syn.lookup else (singular(pair) if singular(pair) in syn.lookup else f"{content[i]} {singular(content[i + 1])}")
                q.terms.append(syn.expand(key))
                i += 2
                continue
        q.terms.append(syn.expand(content[i]))
        i += 1
    return q


@dataclass
class Doc:
    item: Item
    name: str
    category: str
    manufacturer: str
    exact: set[str]
    body: str
    place: str
    words: set[str]


def _build_doc(item: Item, r: Resolver) -> Doc:
    p: Product = item.product
    name = normalise_text(f"{p.name} {item.label or ''}")
    exact = {normalise_text(x) for x in (p.model_number, p.barcode, p.sku, item.serial_number, p.gtin) if x}
    body = normalise_text(" ".join(filter(None, [p.description, p.tags, item.notes, item.status_note])))
    place = normalise_text(r.describe_item(item)["path"])
    cat = normalise_text(p.category.name if p.category else "")
    manu = normalise_text(p.manufacturer)
    words = set(f"{name} {cat} {manu} {body}".split())
    return Doc(item, name, cat, manu, exact, body, place, words)


def _contains(hay: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?<![\w]){re.escape(needle)}", hay) is not None


def edit_distance(a: str, b: str, limit: int = 2) -> int:
    """Optimal-string-alignment distance (counts adjacent transpositions as
    one edit, so "axel" -> "axle" is 1)."""
    if a == b:
        return 0
    prev2: list[int] | None = None
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _term_score(doc: Doc, alts: list[str]) -> float:
    best = 0.0
    for a in alts:
        if a in doc.exact:
            best = max(best, 10)
        if _contains(doc.name, a):
            best = max(best, 5 if re.search(rf"\b{re.escape(a)}\b", doc.name) else 3.5)
        if _contains(doc.category, a):
            best = max(best, 3)
        if _contains(doc.manufacturer, a):
            best = max(best, 3)
        if _contains(doc.body, a):
            best = max(best, 1.5)
        if _contains(doc.place, a):
            best = max(best, 1)
        if any(x.startswith(a) for x in doc.exact) and len(a) >= 4:
            best = max(best, 4)
    if best == 0:
        # Fuzzy fallback for typos ("screwdiver")
        for a in alts:
            if len(a) < 4:
                continue
            allowed = 1 if len(a) <= 6 else 2
            for w in doc.words:
                if abs(len(w) - len(a)) <= allowed and edit_distance(a, w, allowed) <= allowed:
                    best = max(best, 2)
                    break
    return best


def run_search(db: Session, query: Query, limit: int = 50) -> dict[str, Any]:
    r = Resolver.load(db)
    stmt = (
        select(Item)
        .options(selectinload(Item.product).selectinload(Product.category), selectinload(Item.loans))
        .join(Product)
        .where(Item.deleted_at.is_(None), Product.deleted_at.is_(None))
    )
    if query.barcode:
        norm = gtin_mod.normalise(query.barcode)
        stmt = stmt.where((Product.gtin == norm) if norm else (Product.barcode == query.barcode))
    # Cheap filters in SQL; words are matched below (they also match places
    # and allow typos, which SQL can't).
    if query.status:
        try:
            stmt = stmt.where(Item.status == ItemStatus(query.status))
        except ValueError:
            stmt = stmt.where(false())  # not a status: nothing matches
    if query.intent == "loans" and not query.terms:
        stmt = stmt.where(Item.status.in_([ItemStatus.on_loan, ItemStatus.awaiting_return]))
    if query.intent == "warranty":
        stmt = stmt.where(Item.warranty_expires.is_not(None))
    if query.intent == "expiry":
        stmt = stmt.where(Item.expires_on.is_not(None))
    items = db.scalars(stmt).all()

    scored: list[tuple[float, Item]] = []
    for it in items:
        if query.status and it.status.value != query.status:
            continue
        if query.intent == "loans" and it.status not in (ItemStatus.on_loan, ItemStatus.awaiting_return) and not query.terms:
            continue
        if query.manufacturer and normalise_text(query.manufacturer) not in normalise_text(it.product.manufacturer):
            continue
        if query.category and normalise_text(query.category) not in normalise_text(it.product.category.name if it.product.category else ""):
            # Category from AI is a hint; keep item if terms match strongly.
            if not query.terms:
                continue
        if query.intent == "warranty" and not it.warranty_expires:
            continue
        if query.intent == "expiry" and not it.expires_on:
            continue
        doc = _build_doc(it, r)
        total = 0.0
        ok = True
        for group in query.terms:
            s = _term_score(doc, group)
            if s == 0:
                ok = False
                break
            total += s
        if not ok:
            continue
        if not query.terms and not (query.manufacturer or query.status or query.barcode or query.intent in ("loans", "warranty", "expiry")):
            continue
        scored.append((total, it))

    scored.sort(key=lambda x: (-x[0], x[1].product.name.lower(), x[1].id))
    results = items_out([it for _, it in scored[:limit]], r, db)
    for res, (_, it) in zip(results, scored[:limit]):
        from .inventory import maps_for

        res["maps"] = maps_for(
            db,
            container_id=it.container_id,
            location_ids=[x["id"] for x in res["placement"]["location_path"]],
        )
        res["score"] = round(_, 2)
    answer = build_answer(query, [it for _, it in scored], r)
    return {
        "query": query.raw,
        "interpretation": {
            "intent": query.intent,
            "terms": query.terms,
            "manufacturer": query.manufacturer,
            "category": query.category,
            "status": query.status,
            "barcode": query.barcode,
            "by": query.interpreted_by,
        },
        "answer": answer,
        "total_matches": len(scored),
        "total_quantity": sum(it.quantity for _, it in scored),
        "results": results,
    }


def _what(query: Query) -> str:
    parts = []
    if query.manufacturer:
        parts.append(query.manufacturer if query.manufacturer != "dewalt" else "DeWalt")
    parts.extend(g[0] if len(g) == 1 else next((x for x in g if " " not in x), g[0]) for g in query.terms)
    return " ".join(parts) or "matching items"


def build_answer(query: Query, items: list[Item], r: Resolver) -> str:
    what = _what(query)
    if not items:
        if query.intent == "loans":
            return f"No {what} are recorded as lent out." if query.terms else "Nothing is currently on loan."
        return f"I couldn't find any {what} in your inventory."
    total = sum(i.quantity for i in items)
    places: dict[str, int] = defaultdict(int)
    for it in items:
        places[r.describe_item(it)["path"]] += it.quantity
    place_lines = "; ".join(f"{q} in {p}" for p, q in sorted(places.items(), key=lambda x: -x[1])[:6])
    if query.intent == "loans":
        loaned = [(it, active_loan(it)) for it in items]
        loaned = [(it, lo) for it, lo in loaned if lo is not None]
        if not loaned:
            return f"No — none of your {what} are on loan. You have {total} ({place_lines})."
        bits = []
        today = date.today()
        for it, lo in loaned[:5]:
            due = ""
            if lo.expected_return:
                due = f", overdue since {lo.expected_return:%d %b}" if lo.expected_return < today else f", due back {lo.expected_return:%d %b}"
            bits.append(f"{it.product.name} to {lo.borrower} on {lo.loaned_at:%d %b %Y}{due}")
        return "Yes — you lent " + "; ".join(bits) + "."
    if query.intent == "count":
        return f"You own {total} {what} across {len(places)} place{'s' if len(places) != 1 else ''}: {place_lines}."
    if query.intent == "expiry":
        today = date.today()
        soon = sorted(items, key=lambda i: i.expires_on or date.max)
        bits = []
        for i in soon[:6]:
            when = f"expired {i.expires_on:%d %b %Y}" if i.expires_on < today else f"expires {i.expires_on:%d %b %Y}"
            bits.append(f"{i.quantity} × {i.product.name} ({when}, {r.describe_item(i)['path']})")
        return "Expiry dates: " + "; ".join(bits) + "."
    if query.intent == "warranty":
        soon = sorted(items, key=lambda i: i.warranty_expires or date.max)
        return "Warranties: " + "; ".join(
            f"{i.product.name} until {i.warranty_expires:%d %b %Y}" for i in soon[:6] if i.warranty_expires
        ) + "."
    products = {i.product_id for i in items}
    if len(products) == 1:
        p = items[0].product
        return f"I found {p.name} in your inventory. You own {total}: {place_lines}."
    n = len(places)
    return f"Found {total} item{'s' if total != 1 else ''} matching \"{what}\" in {n} place{'s' if n != 1 else ''}: {place_lines}."


def known_manufacturers(db: Session) -> set[str]:
    return {
        m
        for m in db.scalars(select(Product.manufacturer).where(Product.manufacturer.is_not(None), Product.deleted_at.is_(None)).distinct())
        if m and len(m) >= 2
    }


def search(db: Session, raw: str, ai=None, limit: int = 50) -> dict[str, Any]:
    syn = load_synonyms(db)
    query = parse_rules(raw, known_manufacturers(db), syn)
    ai_error = None
    if ai is not None and not query.barcode:
        try:
            res = ai.interpret_query(raw)
            d = res.data
            q2 = Query(
                raw=raw,
                intent=d["intent"],
                terms=[sorted(set(sum((syn.expand(t) for t in g), []))) for g in d["terms"]],
                manufacturer=d["manufacturer"],
                category=d["category"],
                status=d["status"],
                interpreted_by=f"ai:{res.provider}",
            )
            result = run_search(db, q2, limit)
            if result["total_matches"] or not query.terms:
                return result
            # AI filters were too strict; fall back to rule-based parsing.
        except Exception as err:  # noqa: BLE001 - AI failures must never break search
            # Details go to the App log; the browser gets a fixed message.
            _LOGGER.warning("AI could not interpret search %r: %s", raw[:100], err)
            ai_error = "AI couldn't interpret the question, so these are keyword results."
    result = run_search(db, query, limit)
    if not result["total_matches"] and not query.barcode:
        # Voice assistants write numbers as digits ("0 turn mower") and people
        # name things in words ("Zero-turn mower"), or the other way round.
        alt = swap_numbers(raw)
        if alt != raw:
            alt_result = run_search(db, parse_rules(alt, known_manufacturers(db), syn), limit)
            if alt_result["total_matches"]:
                alt_result["query"] = raw
                result = alt_result
    if ai_error:
        result["ai_error"] = ai_error
    return result


_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                 "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
                 "nineteen", "twenty"]


def swap_numbers(text: str) -> str:
    """Standalone small numbers the other way round: digits to words if there
    are any digits ("0 turn" → "zero turn"), else words to digits
    ("two stroke" → "2 stroke")."""
    if re.search(r"\b(?:[0-9]|1[0-9]|20)\b", text):
        return re.sub(r"\b([0-9]|1[0-9]|20)\b", lambda m: _NUMBER_WORDS[int(m.group(1))], text)
    words = {w: str(n) for n, w in enumerate(_NUMBER_WORDS)}
    return re.sub(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", lambda m: words[m.group(1).lower()], text, flags=re.I)

"""Find a place (a location or a container) from spoken words.

"Small engine repair", "the garage", "container number 5", "box five",
"top shelf in the shed". Used by voice ("I've moved the engine oil to…").

Each candidate has a score from 0 to 1. ``clear`` is set when the best one
is a confident match and well ahead of the rest, so voice can move without
asking; otherwise it asks which one (several) or "did you mean…?" (one).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Container, Location
from .placement import Resolver
from .search import swap_numbers
from .textmatch import matches_words, words

_LEAD = re.compile(r"^(?:(?:to|into|in|on|onto|at|inside|the|my|a)\s+)+", re.I)
_NUMBER = re.compile(r"\b(?:container|box|tote|bin|crate|tub|case|drawer|number|no|#)\s*(?:number\s*|no\.?\s*|#\s*)?([0-9]+[a-z]?)\b", re.I)
CLEAR = 0.88   # best score needed to act without asking
MARGIN = 0.12  # ...and how far ahead of the next one


def _norm(text: str) -> str:
    return " ".join(words(text))


def _compact(text: str) -> str:
    return "".join(words(text))


def _score(query: str, name: str, path: str) -> float:
    qn, nn = _norm(query), _norm(name)
    if not qn or not nn:
        return 0.0
    if qn == nn:
        return 1.0
    if _compact(query) == _compact(name):
        return 0.97
    best = 0.0
    if matches_words(query, name):
        best = 0.9 if len(words(query)) >= len(words(name)) - 1 else 0.8
    elif matches_words(query, f"{name} {path}"):
        best = 0.75
    ratio = SequenceMatcher(None, _compact(query), _compact(name)).ratio()
    if ratio >= 0.7:
        best = max(best, round(ratio * 0.92, 3))
    return best


def resolve(db: Session, text: str, limit: int = 6) -> dict[str, Any]:
    raw = _LEAD.sub("", (text or "").strip().strip("?.!")).strip()
    if not raw:
        return {"query": text, "candidates": [], "clear": False}
    r = Resolver.load(db)
    locations = db.scalars(select(Location).where(Location.deleted_at.is_(None))).all()
    containers = db.scalars(select(Container).where(Container.deleted_at.is_(None))).all()
    variants = {raw, swap_numbers(raw)}
    found: dict[tuple[str, int], dict[str, Any]] = {}

    def add(kind: str, obj: Any, score: float) -> None:
        if score <= 0:
            return
        if kind == "location":
            path = r.describe(None, obj.id)["path"]
            parent = path.rsplit(" › ", 1)[0] if " › " in path else ""
            spoken = obj.name
        else:
            path = r.describe(obj.id, None)["path"]
            parent = r.describe(obj.parent_container_id, obj.location_id)["path"]
            spoken = f"{obj.container_type or 'Container'} number {obj.number}, {obj.name}" if obj.number else obj.name
        key = (kind, obj.id)
        if key not in found or found[key]["score"] < score:
            found[key] = {"type": kind, "id": obj.id, "name": obj.name, "spoken": spoken,
                          "where": parent.replace(" › ", ", "), "path": path, "score": round(score, 3)}

    for v in variants:
        num = _NUMBER.search(v)
        if num:
            wanted = num.group(1).lower()
            rest = _norm(_NUMBER.sub(" ", v))
            for c in containers:
                if (c.number or "").lower() == wanted:
                    # "blue tote number 5": the other words should fit too
                    fits = not rest or matches_words(rest, " ".join(filter(None, (c.name, c.container_type, c.description))))
                    add("container", c, 1.0 if fits else 0.85)
        for loc in locations:
            add("location", loc, _score(v, loc.name, r.describe(None, loc.id)["path"]))
        for c in containers:
            label = " ".join(filter(None, (c.container_type, c.name)))
            add("container", c, max(_score(v, c.name, r.describe(c.id, None)["path"]), _score(v, label, "")))

    cands = sorted((c for c in found.values() if c["score"] >= 0.5), key=lambda c: (-c["score"], c["path"]))[:limit]
    clear = bool(cands) and cands[0]["score"] >= CLEAR and (len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= MARGIN)
    return {"query": raw, "candidates": cands, "clear": clear}

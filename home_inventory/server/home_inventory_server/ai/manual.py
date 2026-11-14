"""Reading an equipment manual: what to service, when, and with what.

The model returns:

* ``parts``: every part or consumable the manual names, once each, with a
  short ``key`` ("engine_oil", "spark_plug"), its part number or
  specification exactly as printed, the amount per service and the page;
* ``services``: each maintenance job with its interval(s) (hours, days,
  cycles - "every 50 hours or yearly" is both), an optional first/break-in
  point ("first oil change after 5 hours"), and ``needs``: which parts
  (by key) that particular service uses and how much;
* ``specs``: other figures worth keeping (oil capacity, plug gap...).

Different intervals often need different things (the 50-hour service
changes the oil; the 100-hour service also replaces the plug and air
filter), so needs are per service, never pooled.
"""

from __future__ import annotations

import re
from typing import Any

# When a job is done (the checklist groups) and what kind of job it is.
WHEN = ("before_use", "after_use", "periodic", "hours", "seasonal", "annual", "storage", "one_time")
KINDS = ("inspect", "replace", "service", "clean", "lubricate", "adjust")

_S = {"type": ["string", "null"]}
_N = {"type": ["number", "null"]}
_I = {"type": ["integer", "null"]}

MANUAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "equipment": {
            "type": "object",
            "properties": {"manufacturer": _S, "model": _S, "engine": _S,
                           "models_covered": {"type": "array", "items": {"type": "string"}},
                           "schedule_varies_by_model": {"type": "boolean"}},
            "required": ["manufacturer", "model", "engine", "models_covered", "schedule_varies_by_model"],
            "additionalProperties": False,
        },
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "role": {"type": "string"},
                    "kind": {"type": "string", "enum": ["consumable", "part"]},
                    "name": {"type": "string"},
                    "manufacturer": _S,
                    "part_number": _S,
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "specification": _S,
                    "quantity": _N,
                    "unit": _S,
                    "page": _I,
                },
                "required": ["key", "role", "kind", "name", "manufacturer", "part_number", "alternatives",
                             "specification", "quantity", "unit", "page"],
                "additionalProperties": False,
            },
        },
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "interval_hours": _N,
                    "interval_days": _I,
                    "interval_cycles": _I,
                    "first_at_hours": _N,
                    "when": {"type": "string", "enum": list(WHEN)},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "section": _S,
                    "instructions": _S,
                    "needs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"key": {"type": "string"}, "quantity": _N, "unit": _S},
                            "required": ["key", "quantity", "unit"],
                            "additionalProperties": False,
                        },
                    },
                    "page": _I,
                },
                "required": ["title", "interval_hours", "interval_days", "interval_cycles", "first_at_hours",
                             "when", "kind", "section", "instructions", "needs", "page"],
                "additionalProperties": False,
            },
        },
        "specs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "value": {"type": "string"}, "page": _I},
                "required": ["label", "value", "page"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["equipment", "parts", "services", "specs"],
    "additionalProperties": False,
}

MANUAL_PROMPT = """You read equipment manuals (mowers, generators, pressure washers, vehicles, appliances...) for a
household maintenance log. Extract ONLY what the manual states. Return JSON only.

"parts": every replaceable part and consumable the manual names for maintenance (engine oil, spark plug,
air filter, fuel filter, blades, belts, grease...), each ONCE with a short snake_case "key". "role" says what it
is for ("Engine oil", "Spark plug"). "kind" is "consumable" for fluids/grease, else "part". "part_number" and
"alternatives" (other part numbers or brand equivalents the manual lists) exactly as printed - NEVER invent or
complete a part number; null if not printed. "specification" is the standard or grade printed
(e.g. "SAE 10W-30, API SJ or later", "NGK BPR6ES equivalent, gap 0.7-0.8 mm"). "quantity"/"unit" is the amount
used at one service (oil capacity 0.55 L -> 0.55, "L"; 1 spark plug -> 1, null). "page" from the [page N] markers.

"services": every scheduled maintenance job, one entry per job and interval, e.g. "Change engine oil" every 50
hours or 12 months -> interval_hours 50, interval_days 365. A first / break-in service ("first oil change after
5 hours") goes in "first_at_hours" of the same job. Months -> days (1 month = 30, 1 year = 365). Inspections and
cleaning count as jobs. "needs" lists the parts (by key) THAT job uses and how much: jobs at different intervals
often need different things - never copy parts into a job that doesn't use them. A job that only inspects or
cleans needs nothing.

"when" says when the job is done, as the manual's schedule table groups it: "before_use" (before each use / daily
checks), "after_use" (after each use), "hours" (every N hours or uses), "periodic" (every N days/weeks/months),
"annual" (once a year), "seasonal" (start of a season), "storage" (before storing / end of season / winterising),
"one_time" (a single job, e.g. the break-in oil change). "kind": inspect (check, look at), replace, service (change
oil, sharpen), clean, lubricate (grease, oil), adjust. "section" is the heading the job appears under, as printed
(e.g. "Maintenance Schedule", "Storage"); null if none.

"equipment": the manufacturer and model this manual is for. "models_covered" lists every model number the manual
says it covers, exactly as printed (empty if it names none). "schedule_varies_by_model" is true only when the
maintenance schedule differs between those models (e.g. a different oil capacity or interval for one variant).

"specs": other useful figures (fuel type, tyre pressure, blade bolt torque...).

Use null where the manual says nothing. If it isn't an equipment manual, return empty lists."""

_KEY = re.compile(r"[^a-z0-9_]+")


def _s(v: Any, n: int) -> str | None:
    if v is None:
        return None
    v = " ".join(str(v).split())
    return v[:n] or None


def _num(v: Any, lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 3) if lo < f <= hi else None


def _int(v: Any, lo: int, hi: int) -> int | None:
    f = _num(v, lo, hi)
    return int(round(f)) if f is not None else None


def _key(v: Any) -> str:
    return _KEY.sub("_", str(v or "").strip().lower()).strip("_")[:40]


def clean_manual(d: dict[str, Any]) -> dict[str, Any]:
    eq = d.get("equipment") if isinstance(d.get("equipment"), dict) else {}
    parts, seen = [], set()
    for p in d.get("parts") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        key = _key(p.get("key") or p.get("name"))
        if not key or key in seen:
            continue
        seen.add(key)
        parts.append({
            "key": key,
            "role": _s(p.get("role"), 80) or _s(p.get("name"), 80),
            "kind": "consumable" if p.get("kind") == "consumable" else "part",
            "name": _s(p.get("name"), 300),
            "manufacturer": _s(p.get("manufacturer"), 200),
            "part_number": _s(p.get("part_number"), 120),
            "alternatives": [a for a in (_s(x, 120) for x in (p.get("alternatives") or [])[:8]) if a],
            "specification": _s(p.get("specification"), 300),
            "quantity": _num(p.get("quantity"), 0, 100000),
            "unit": _s(p.get("unit"), 10),
            "page": _int(p.get("page"), 0, 5000),
        })
        if len(parts) >= 60:
            break
    services = []
    for sv in d.get("services") or []:
        if not isinstance(sv, dict) or not sv.get("title"):
            continue
        needs, nkeys = [], set()
        for n in sv.get("needs") or []:
            k = _key(n.get("key")) if isinstance(n, dict) else ""
            if k in seen and k not in nkeys:
                nkeys.add(k)
                needs.append({"key": k, "quantity": _num(n.get("quantity"), 0, 100000), "unit": _s(n.get("unit"), 10)})
        services.append({
            "title": _s(sv.get("title"), 200),
            "when": sv.get("when") if sv.get("when") in WHEN else None,
            "kind": sv.get("kind") if sv.get("kind") in KINDS else None,
            "section": _s(sv.get("section"), 120),
            "interval_hours": _num(sv.get("interval_hours"), 0, 100000),
            "interval_days": _int(sv.get("interval_days"), 0, 36500),
            "interval_cycles": _int(sv.get("interval_cycles"), 0, 10_000_000),
            "first_at_hours": _num(sv.get("first_at_hours"), 0, 100000),
            "instructions": _s(sv.get("instructions"), 2000),
            "needs": needs,
            "page": _int(sv.get("page"), 0, 5000),
        })
        if len(services) >= 40:
            break
    specs = [{"label": _s(x.get("label"), 80), "value": _s(x.get("value"), 200), "page": _int(x.get("page"), 0, 5000)}
             for x in (d.get("specs") or [])[:40] if isinstance(x, dict) and x.get("label") and x.get("value")]
    return {
        "equipment": {**{k: _s(eq.get(k), 200) for k in ("manufacturer", "model", "engine")},
                      "models_covered": [m for m in (_s(x, 80) for x in (eq.get("models_covered") or [])[:30]) if m],
                      "schedule_varies_by_model": bool(eq.get("schedule_varies_by_model"))},
        "parts": parts,
        "services": services,
        "specs": specs,
    }


def merge(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Several readings (one per photographed page) as one."""
    out: dict[str, Any] = {"equipment": {"manufacturer": None, "model": None, "engine": None, "models_covered": [],
                                         "schedule_varies_by_model": False},
                           "parts": [], "services": [], "specs": []}
    keys: set[str] = set()
    jobs: set[tuple] = set()
    for r in results:
        for k, v in (r.get("equipment") or {}).items():
            if k == "models_covered":
                out["equipment"][k] = list(dict.fromkeys([*out["equipment"][k], *(v or [])]))
            elif k == "schedule_varies_by_model":
                out["equipment"][k] = out["equipment"][k] or bool(v)
            else:
                out["equipment"][k] = out["equipment"].get(k) or v
        for p in r.get("parts") or []:
            if p["key"] not in keys:
                keys.add(p["key"])
                out["parts"].append(p)
        for s in r.get("services") or []:
            sig = ((s["title"] or "").lower(), s["interval_hours"], s["interval_days"], s["interval_cycles"])
            if sig not in jobs:
                jobs.add(sig)
                out["services"].append(s)
        out["specs"].extend(r.get("specs") or [])
    return out

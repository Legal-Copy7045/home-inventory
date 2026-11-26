"""Feature catalogue: which checks cover which feature.

``regression/catalogue/*.yaml`` lists features by area, each with regular
expressions matched (``re.search``, case-insensitive) against every check's
``stage:ID key`` string, e.g. ``tests:T0042 server:tests/test_items.py::test_move``.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .results import Check


def load(cat_dir: Path) -> list[dict]:
    feats = []
    for p in sorted(cat_dir.glob("*.yaml")):
        d = yaml.safe_load(p.read_text()) or {}
        for f in d.get("features", []):
            feats.append({"area": d.get("area", p.stem), "name": f["name"], "description": f.get("description", ""),
                          "match": [re.compile(m, re.I) for m in f.get("match", [])]})
    return feats


def coverage(features: list[dict], checks: list[Check]) -> list[dict]:
    rows = []
    for f in features:
        hit = [c for c in checks if any(m.search(c.full) for m in f["match"])]
        t = {s: sum(c.status == s for c in hit) for s in ("pass", "fail", "skip", "pending")}
        stages = sorted({c.stage for c in hit})
        rows.append({"area": f["area"], "name": f["name"], "description": f["description"], "total": len(hit), **t,
                     "stages": stages, "failing": [c.id for c in hit if c.status == "fail"]})
    return rows


def uncovered(features: list[dict], checks: list[Check]) -> list[Check]:
    """Checks no feature claims; shown so the catalogue can be kept up to date."""
    return [c for c in checks if c.stage in ("tests", "browser")
            and not any(m.search(c.full) for f in features for m in f["match"])]

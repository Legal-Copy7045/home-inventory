"""Feature catalogue: which checks cover which feature (the project standard's format).

``regression/catalogue/<area>.yaml`` is a list of features::

    - feature: Roles and access control
      match: ['^unit:server:tests/test_auth\\.py', '^page:api:']   # regexes over "<stage>:<check key>"
      manual: [ha-sidebar-panel]                                    # optional: manual checks for this feature

Patterns are matched with ``re.search``, case-insensitive.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .results import Check


def load(cat_dir: Path) -> list[dict]:
    feats = []
    for p in sorted(cat_dir.glob("*.yaml")):
        for f in yaml.safe_load(p.read_text()) or []:
            pats = list(f.get("match", [])) + [f"^manual:{re.escape(k)}$" for k in f.get("manual", [])]
            feats.append({"area": p.stem.replace("-", " ").capitalize(), "name": f["feature"],
                          "description": f.get("description", ""), "match": [re.compile(m, re.I) for m in pats]})
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
    return [c for c in checks if c.stage in ("unit", "page")
            and not any(m.search(c.full) for f in features for m in f["match"])]

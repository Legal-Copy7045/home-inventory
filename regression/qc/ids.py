"""Stable check IDs.

Every check has a readable key (``ruff:server``, ``page:member:/items``...).  The
registry in ``regression/ids/<stage>.yaml`` gives each key a short ID (``T0042``)
the first time it is seen.  IDs are never renumbered and never reused: a check
that disappears is marked ``retired`` and keeps its ID; if the same key comes
back it gets the same ID again.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

PREFIX = {"quality": "Q", "tests": "T", "browser": "B", "live": "L", "manual": "M", "walkthrough": "U",
          "findings": "F", "ux-findings": "W"}


class Registry:
    def __init__(self, path: Path, stage: str):
        self.path = path
        self.stage = stage
        if path.exists():
            d = yaml.safe_load(path.read_text()) or {}
        else:
            d = {}
        self.prefix: str = d.get("prefix") or PREFIX.get(stage, stage[:1].upper())
        self.next: int = int(d.get("next", 1))
        self.ids: dict[str, dict] = d.get("ids") or {}
        self._by_key = {v["key"]: k for k, v in self.ids.items()}
        self.changed = False

    def id_for(self, key: str, today: str | None = None) -> str:
        if key in self._by_key:
            cid = self._by_key[key]
            if self.ids[cid].pop("retired", None):
                self.changed = True
            return cid
        cid = f"{self.prefix}{self.next:04d}"
        while cid in self.ids:  # hand-edited file: never reuse
            self.next += 1
            cid = f"{self.prefix}{self.next:04d}"
        self.next += 1
        self.ids[cid] = {"key": key, "added": today or dt.date.today().isoformat()}
        self._by_key[key] = cid
        self.changed = True
        return cid

    def retire_missing(self, seen: set[str], today: str | None = None, scopes: list[str] | None = None) -> list[str]:
        """Mark keys not seen this run as retired; only keys under ``scopes`` when given."""
        gone = []
        for cid, v in self.ids.items():
            if scopes and not any(v["key"].startswith(s) for s in scopes):
                continue
            if v["key"] not in seen and "retired" not in v:
                v["retired"] = today or dt.date.today().isoformat()
                gone.append(cid)
        if gone:
            self.changed = True
        return gone

    def save(self) -> None:
        if not self.changed and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        head = (f"# Stable IDs for the {self.stage} checks. Written by regression/run.py.\n"
                "# Never renumber or reuse an ID; a check that goes away is marked retired.\n")
        body = yaml.safe_dump({"prefix": self.prefix, "next": self.next, "ids": self.ids},
                              sort_keys=False, allow_unicode=True, width=200)
        self.path.write_text(head + body)


def assign(res, ids_dir: Path, today: str | None = None) -> list[str]:
    """Fill in ``Check.id`` for every check of a StageResult; returns retired IDs."""
    reg = Registry(ids_dir / f"{res.stage}.yaml", res.stage)
    for c in res.checks:
        c.id = reg.id_for(c.key, today)
    retired = reg.retire_missing({c.key for c in res.checks}, today, res.scopes) if res.complete and res.checks else []
    reg.save()
    return retired

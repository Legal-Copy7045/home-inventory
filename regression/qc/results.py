"""One check result, and the files a stage writes."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

STATUSES = ("pass", "fail", "skip", "pending")


@dataclass
class Check:
    stage: str
    key: str            # natural, readable key: what the check is ("ruff:server", "page:admin:/items")
    status: str         # pass | fail | skip | pending (manual checks nobody has done yet)
    detail: str = ""
    id: str = ""        # stable ID from regression/ids/<stage>.yaml, filled in by the registry
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"bad status {self.status!r} for {self.key}")

    @property
    def full(self) -> str:
        """What catalogue patterns match: ``stage:ID key``."""
        return f"{self.stage}:{self.id} {self.key}"


@dataclass
class StageResult:
    stage: str
    checks: list[Check]
    started: str = ""
    finished: str = ""
    note: str = ""          # e.g. why the whole stage was skipped
    complete: bool = True   # False when the stage ran only partly; then no IDs are retired
    scopes: list[str] = field(default_factory=list)  # key prefixes that ran in full (empty: all of them)

    def totals(self) -> dict[str, int]:
        t = {s: 0 for s in STATUSES}
        for c in self.checks:
            t[c.status] += 1
        return t


def save_stage(res: StageResult, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{res.stage}.json"
    body = asdict(res)
    p.write_text(json.dumps(body, indent=1))
    return p


def load_stage(p: Path) -> StageResult:
    d = json.loads(p.read_text())
    d["checks"] = [Check(**c) for c in d["checks"]]
    return StageResult(**d)


def load_run(results_dir: Path) -> list[StageResult]:
    return [load_stage(p) for p in sorted(results_dir.glob("*.json"))]

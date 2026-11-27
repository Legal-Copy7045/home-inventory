"""One check result, and the files a stage writes."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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
        """What catalogue patterns match (the standard's ``<stage>:<key>``)."""
        return f"{self.stage}:{self.key}"


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


# Files that sit beside the stage results in regression/out/ but are not stage results.
NOT_STAGES = {"brief.json", "meta.json", "walkthrough.json"}


def result_file(stage: str) -> str:
    """The standard's names: out/<stage>.json; the walkthrough's checks go to walkthrough-checks.json."""
    return "walkthrough-checks.json" if stage == "walkthrough" else f"{stage}.json"


def save_stage(res: StageResult, out: Path) -> Path:
    """Write the stage in the project standard's result format (status "manual" for a manual check not yet done),
    keeping this runner's extras (note, complete, scopes, per-check data) as additional fields."""
    from .ids import PREFIX
    out.mkdir(parents=True, exist_ok=True)
    p = out / result_file(res.stage)
    body = {"stage": res.stage, "prefix": PREFIX.get(res.stage, res.stage[:1].upper()), "started": res.started,
            "finished": res.finished, "note": res.note, "complete": res.complete, "scopes": res.scopes,
            "results": [{"id": c.id, "key": c.key, "status": "manual" if c.status == "pending" else c.status,
                         "detail": c.detail, "evidence": "", **({"data": c.data} if c.data else {})}
                        for c in res.checks]}
    p.write_text(json.dumps(body, indent=1, ensure_ascii=False))
    return p


def load_stage(p: Path) -> StageResult:
    d = json.loads(p.read_text())
    checks = [Check(d["stage"], r["key"], "pending" if r["status"] == "manual" else r["status"], r.get("detail", ""),
                    r.get("id", ""), r.get("data") or {}) for r in d["results"]]
    return StageResult(d["stage"], checks, d.get("started", ""), d.get("finished", ""), d.get("note", ""),
                       d.get("complete", True), d.get("scopes") or [])


def load_run(out: Path) -> list[StageResult]:
    """Every regression stage result in ``out`` (not the walkthrough's)."""
    stages = []
    for p in sorted(out.glob("*.json")):
        if p.name in NOT_STAGES or p.name == "walkthrough-checks.json":
            continue
        try:
            d = json.loads(p.read_text())
        except ValueError:
            continue
        if isinstance(d, dict) and "results" in d and "stage" in d:
            stages.append(load_stage(p))
    return stages

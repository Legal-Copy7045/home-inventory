#!/usr/bin/env python3
"""Home Inventory regression runner (the project standard's commands; see regression/README.md and profile.yaml).

    python3 regression/run.py check                 ci + unit, quiet: run before marking a PR ready
    python3 regression/run.py                       every stage but walkthrough: ci, unit, page, live, manual
    python3 regression/run.py --with-walkthrough    every stage, and the walkthrough beside them
    python3 regression/run.py --stage unit          one stage (repeatable; also --stage walkthrough)
    python3 regression/run.py report [--walkthrough]   out/report.html | out/walkthrough.html (this project's own)
    python3 regression/run.py brief|log|summary|manual|schema|walkthrough-due ...   (standard.py)

Everything goes to regression/out/ (not committed), in the standard's layout: <stage>.json, walkthrough.json,
walkthrough/ (screenshots), walkthrough-checks.json, findings/<reviewer>.json, briefs/, logs/, report.html and
walkthrough.html; plus meta.json and work/ (the throwaway Apps). It never changes product code.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import standard  # noqa: E402
from qc import QC_DIR, ROOT, render  # noqa: E402
from qc import catalogue as cat  # noqa: E402
from qc.ids import assign  # noqa: E402
from qc.results import Check, StageResult, load_run, load_stage, result_file, save_stage  # noqa: E402
from qc.stages import (  # noqa: E402
    ALL_STAGES,
    REGRESSION_STAGES,
    STAGE_ALIASES,
    STAGE_FUNCS,
    Ctx,
    app_version,
    build_frontend,
)

OUT = QC_DIR / "out"
IDS = QC_DIR / "ids"
WALK_PORTS = (18299, 18300, 18301, 18302)
TOOLING = {"brief", "log", "summary", "manual", "schema", "walkthrough-due"}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def select_stages(stages: list[str] | None, with_walkthrough: bool) -> tuple[list[str], bool]:
    """Which regression stages run, and whether the walkthrough runs."""
    chosen = list(dict.fromkeys(STAGE_ALIASES.get(s, s) for s in (stages or [])))
    for s in chosen:
        if s not in ALL_STAGES:
            raise SystemExit(f"unknown stage {s!r}; choose from {', '.join(ALL_STAGES)}")
    walk = with_walkthrough or "walkthrough" in chosen
    reg = [s for s in chosen if s != "walkthrough"]
    if not chosen:
        reg = list(REGRESSION_STAGES)
    elif with_walkthrough and not reg:
        reg = list(REGRESSION_STAGES)
    return [s for s in REGRESSION_STAGES if s in reg], walk


def meta() -> dict:
    p = OUT / "meta.json"
    m = json.loads(p.read_text()) if p.exists() else {}
    b = OUT / "brief.json"
    if b.exists():
        brief = json.loads(b.read_text())
        m.update(audit=brief.get("audit"), audit_why=brief.get("why"))
    return m


# ---------------------------------------------------------------- reports (this project's own: see standard.yaml)

def render_regression() -> Path | None:
    stages = load_run(OUT)
    if not stages:
        return None
    order = {s: i for i, s in enumerate(REGRESSION_STAGES)}
    stages.sort(key=lambda s: order.get(s.stage, 99))
    checks = [c for s in stages for c in s.checks]
    feats = cat.load(QC_DIR / "catalogue")
    findings, reviewers = render.load_findings(OUT / "findings", render.SECURITY_REVIEWERS)
    render.assign_finding_ids(findings, IDS, "security")
    html = render.regression_report(stages, meta(), cat.coverage(feats, checks), cat.uncovered(feats, checks),
                                    findings, reviewers)
    out = OUT / "report.html"
    out.write_text(html)
    return out


def render_walkthrough() -> Path | None:
    inv_p = OUT / "walkthrough.json"
    if not inv_p.exists():
        return None
    res_p = OUT / result_file("walkthrough")
    ux = load_stage(res_p) if res_p.exists() else None
    findings, reviewers = render.load_findings(OUT / "findings", render.WALK_REVIEWERS)
    render.assign_finding_ids(findings, IDS, "ux")
    html = render.walkthrough_report(json.loads(inv_p.read_text()), ux, meta(), findings, reviewers, OUT / "walkthrough")
    out = OUT / "walkthrough.html"
    out.write_text(html)
    return out


# ---------------------------------------------------------------- stages

def stage_line(res: StageResult) -> list[str]:
    t = res.totals()
    lines = [f"{res.stage:<12} pass {t['pass']:>4}  fail {t['fail']:>3}  skip {t['skip']:>3}  manual {t['pending']:>3}"
             + (f"  ({res.note})" if res.note else "")]
    for c in res.checks:
        if c.status == "fail":
            last = c.detail.strip().splitlines()[-1][:160] if c.detail.strip() else ""
            lines.append(f"  FAIL {c.id} {c.key}: {last}")
    return lines


def run_track(stages: list[str], ctx: Ctx, built, today: str, results: list[StageResult], say) -> None:
    for st in stages:
        ctx.log(f"== {st}")
        fn = STAGE_FUNCS[st]
        try:
            res = fn(ctx, built) if st in ("page", "walkthrough") else fn(ctx)
        except Exception as e:  # noqa: BLE001 - one broken stage must not lose the others' results
            res = StageResult(st, [Check(st, "RUN", "fail", f"stage crashed: {e!r}")], complete=False)
        res.finished = res.finished or standard.now()
        assign(res, IDS, today)
        save_stage(res, ctx.out)
        results.append(res)
        for line in stage_line(res):
            say(line)
        if st == "walkthrough":
            say("walkthrough done")  # /full-check waits for this line before briefing the walkthrough reviewers


def clear(reg: list[str], walk: bool, full: bool) -> None:
    """Drop the previous run's files this run replaces, so a report never mixes two runs."""
    for st in reg:
        (OUT / result_file(st)).unlink(missing_ok=True)
    if full:
        for n in ("report.html", "brief.json", *(f"findings/{r}.json" for r in render.SECURITY_REVIEWERS),
                  *(f"briefs/{r}.md" for r in render.SECURITY_REVIEWERS)):
            (OUT / n).unlink(missing_ok=True)
    if walk:
        for n in ("walkthrough.json", result_file("walkthrough"), "walkthrough.html", "briefs/desktop.md",
                  "briefs/phone.md", *(f"findings/{r}.json" for r in render.WALK_REVIEWERS)):
            (OUT / n).unlink(missing_ok=True)
        shutil.rmtree(OUT / "walkthrough", ignore_errors=True)


def run(reg: list[str], walk: bool, quiet: bool) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "findings").mkdir(exist_ok=True)
    (OUT / "logs").mkdir(exist_ok=True)
    clear(reg, walk, full=reg == list(REGRESSION_STAGES))
    m = {"date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "commit": git("rev-parse", "--short", "HEAD"),
         "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "version": app_version(),
         "dirty": bool(git("status", "--porcelain", "--untracked-files=no")), "stages": reg, "walkthrough": walk,
         "previous_commit": standard.last_commit(QC_DIR / "RUNS.md", standard.RUNS_HEADER) or None}
    (OUT / "meta.json").write_text(json.dumps(m, indent=1))
    lock = threading.Lock()
    logfile = (OUT / "logs" / "run.log").open("w")

    def logger(prefix):
        def log(msg):
            with lock:
                logfile.write(f"[{prefix}] {msg}\n")
                logfile.flush()
                if not quiet:
                    print(f"[{prefix}] {msg}", flush=True)
        return log

    def say(msg):
        with lock:
            print(msg, flush=True)

    if not quiet:
        print(f"output: {OUT} (log: {OUT / 'logs/run.log'})", flush=True)
    built = build_frontend(logger("prepare")) if ({"page"} & set(reg) or walk) else None
    today = dt.date.today().isoformat()
    results: list[StageResult] = []
    threads = []
    if reg:
        ctx = Ctx(OUT, log=logger("regression"))
        threads.append(threading.Thread(target=run_track, args=(reg, ctx, built, today, results, say)))
    if walk:
        wctx = Ctx(OUT, ports=WALK_PORTS, log=logger("walkthrough"), name="walkthrough")
        threads.append(threading.Thread(target=run_track, args=(["walkthrough"], wctx, built, today, results, say)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    logfile.close()
    if not quiet:
        for p in (render_regression() if reg else None, render_walkthrough() if walk else None):
            if p:
                print(f"report: {p}")
    return 1 if any(c.status == "fail" for r in results for c in r.checks) else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in TOOLING:
        return standard.main(argv)
    if argv and argv[0] == "report":
        ap = argparse.ArgumentParser(prog="run.py report")
        ap.add_argument("--walkthrough", action="store_true", help="the walkthrough report instead")
        a = ap.parse_args(argv[1:])
        p = render_walkthrough() if a.walkthrough else render_regression()
        print(p or f"nothing to report: no {'walkthrough' if a.walkthrough else 'stage results'} in {OUT}")
        return 0 if p else 1
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", nargs="?", choices=["check"], help="ci + unit, quiet: before a PR is marked ready")
    ap.add_argument("--stage", action="append", help=f"run only this stage ({', '.join(ALL_STAGES)}); repeatable")
    ap.add_argument("--with-walkthrough", action="store_true", help="also run the walkthrough, in parallel")
    ap.add_argument("--quiet", action="store_true", help="one line per stage and the failing checks only")
    a = ap.parse_args(argv)
    if a.mode == "check":
        return run(["ci", "unit"], False, quiet=True)
    reg, walk = select_stages(a.stage, a.with_walkthrough)
    return run(reg, walk, a.quiet)


if __name__ == "__main__":
    raise SystemExit(main())

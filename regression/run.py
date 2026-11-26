#!/usr/bin/env python3
"""Home Inventory quality runner.

    python3 regression/run.py                       # regression stages: quality, tests, browser, live, manual
    python3 regression/run.py --stage tests         # one stage (repeat --stage for several)
    python3 regression/run.py --stage walkthrough   # the walkthrough crawl only
    python3 regression/run.py --with-walkthrough    # regression stages and walkthrough in parallel
    python3 regression/run.py report RUN_DIR        # re-render the reports (after reviewer findings are added)
    python3 regression/run.py log RUN_DIR           # add the RUNS.md / WALKTHROUGHS.md entries
    python3 regression/run.py summary RUN_DIR       # one plain-text summary of a run
    python3 regression/run.py manual KEY pass|fail [--note TEXT]

A run writes regression/out/<stamp>/ (not committed):
    regression/results/<stage>.json, regression/report.html, findings/security-*.json
    walkthrough/results/walkthrough.json, walkthrough/inventory.json, walkthrough/screens/*.png,
    walkthrough/report.html, findings/walkthrough-*.json
It never changes product code.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from qc import (  # noqa: E402
    QC_DIR,
    ROOT,
    render,  # noqa: E402
)
from qc import catalogue as cat  # noqa: E402
from qc.ids import assign  # noqa: E402
from qc.results import load_run, load_stage, save_stage  # noqa: E402
from qc.stages import (  # noqa: E402
    ALL_STAGES,
    REGRESSION_STAGES,
    STAGE_FUNCS,
    Ctx,
    app_version,
    build_frontend,
    record_manual,
)

IDS = QC_DIR / "ids"
PUBLISHED = QC_DIR / "published.yaml"
WALK_PORTS = (18299, 18300, 18301, 18302)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def select_stages(stages: list[str] | None, with_walkthrough: bool) -> tuple[list[str], bool]:
    """Which regression stages run, and whether the walkthrough runs."""
    chosen = list(dict.fromkeys(stages or []))
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


def meta_for(run: Path) -> dict:
    p = run / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def published() -> dict:
    return (yaml.safe_load(PUBLISHED.read_text()) or {}) if PUBLISHED.exists() else {}


def render_regression(run: Path) -> Path | None:
    res_dir = run / "regression/results"
    if not res_dir.exists():
        return None
    stages = load_run(res_dir)
    order = {s: i for i, s in enumerate(REGRESSION_STAGES)}
    stages.sort(key=lambda s: order.get(s.stage, 99))
    checks = [c for s in stages for c in s.checks]
    feats = cat.load(QC_DIR / "catalogue")
    findings, reviewers = render.load_findings(run / "findings", "security")
    render.assign_finding_ids(findings, IDS, "security")
    html = render.regression_report(stages, meta_for(run), cat.coverage(feats, checks), cat.uncovered(feats, checks),
                                    findings, reviewers)
    out = run / "regression/report.html"
    out.write_text(html)
    (run / "regression/findings.json").write_text(json.dumps(findings, indent=1))
    return out


def render_walkthrough(run: Path) -> Path | None:
    wdir = run / "walkthrough"
    inv_p = wdir / "inventory.json"
    if not inv_p.exists():
        return None
    res_p = wdir / "results/walkthrough.json"
    ux = load_stage(res_p) if res_p.exists() else None
    findings, reviewers = render.load_findings(run / "findings", "walkthrough")
    render.assign_finding_ids(findings, IDS, "ux")
    html = render.walkthrough_report(json.loads(inv_p.read_text()), ux, meta_for(run), findings, reviewers, wdir / "screens")
    out = wdir / "report.html"
    out.write_text(html)
    (wdir / "findings.json").write_text(json.dumps(findings, indent=1))
    return out


def _clip(ids: list[str], n: int = 25) -> str:
    return (", ".join(ids[:n]) + (f" … +{len(ids) - n}" if len(ids) > n else "")) or "–"


def log_run(run: Path) -> list[str]:
    """Append this run's lines to RUNS.md and WALKTHROUGHS.md."""
    meta, pub, done = meta_for(run), published(), []
    date, commit = meta.get("date", "?")[:16].replace("T", " "), meta.get("commit", "?")
    reg = run / "regression/results"
    if reg.exists():
        checks = [c for s in load_run(reg) for c in s.checks]
        t = {k: sum(c.status == k for c in checks) for k in ("pass", "fail", "skip", "pending")}
        f = json.loads((run / "regression/findings.json").read_text()) if (run / "regression/findings.json").exists() else []
        fids = [f"{x['id']} ({x.get('severity', '?')})" for x in sorted(f, key=render.sev_rank)]
        link = f"[report]({pub['regression']})" if pub.get("regression") else "not published"
        line = (f"| {date} | `{commit}` | {meta.get('version', '?')} | {t['pass']} | {t['fail']} | {t['skip']} | {t['pending']} | "
                f"{_clip([c.id for c in checks if c.status == 'fail'])} | {_clip(fids, 15)} | {link} |")
        _append(QC_DIR / "RUNS.md", line)
        done.append("RUNS.md")
    wres = run / "walkthrough/results/walkthrough.json"
    if wres.exists():
        ux = load_stage(wres)
        inv = json.loads((run / "walkthrough/inventory.json").read_text())
        screens = sum(len(w["pages"]) for r in inv["roles"].values() for w in r.values())
        f = json.loads((run / "walkthrough/findings.json").read_text()) if (run / "walkthrough/findings.json").exists() else []
        groups = ", ".join(f"{g} {n}" for g in render.GROUPS if (n := sum(x.get("group") == g for x in f))) or "–"
        t = ux.totals()
        link = f"[report]({pub['walkthrough']})" if pub.get("walkthrough") else "not published"
        line = (f"| {date} | `{commit}` | {meta.get('version', '?')} | {screens} | {t['pass']} | {t['fail']} | "
                f"{_clip([c.id for c in ux.checks if c.status == 'fail'])} | {groups} | {link} |")
        _append(QC_DIR / "WALKTHROUGHS.md", line)
        done.append("WALKTHROUGHS.md")
    return done


def _append(path: Path, line: str) -> None:
    text = path.read_text().rstrip("\n") + "\n" + line + "\n"
    path.write_text(text)


def summary(run: Path) -> str:
    meta = meta_for(run)
    lines = [f"Run {run.name}: version {meta.get('version')} at {meta.get('commit')}"]
    reg = run / "regression/results"
    if reg.exists():
        stages = load_run(reg)
        for s in stages:
            t = s.totals()
            lines.append(f"  {s.stage:<10} pass {t['pass']:>4}  fail {t['fail']:>3}  skip {t['skip']:>3}  manual {t['pending']:>2}"
                         + (f"  ({s.note})" if s.note else ""))
        fails = [c for s in stages for c in s.checks if c.status == "fail"]
        for c in fails[:40]:
            lines.append(f"    FAIL {c.id} {c.key}")
        fp = run / "regression/findings.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            by = {s: sum((x.get("severity") or "").lower() == s for x in f) for s in render.SEVERITIES}
            lines.append("  security findings: " + ", ".join(f"{k} {v}" for k, v in by.items() if v) if f else "  security findings: none")
    wres = run / "walkthrough/results/walkthrough.json"
    if wres.exists():
        t = load_stage(wres).totals()
        lines.append(f"  walkthrough UX checks: pass {t['pass']}, fail {t['fail']}")
        fp = run / "walkthrough/findings.json"
        if fp.exists():
            f = json.loads(fp.read_text())
            lines.append("  walkthrough findings: " + (", ".join(f"{g} {n}" for g in render.GROUPS
                                                                if (n := sum(x.get('group') == g for x in f))) or "none"))
    pub = published()
    for k in ("regression", "walkthrough"):
        if pub.get(k):
            lines.append(f"  {k} report: {pub[k]}")
    return "\n".join(lines)


def run_track(name: str, stages: list[str], ctx: Ctx, built, ids_today: str) -> None:
    for st in stages:
        ctx.log(f"== {st}")
        fn = STAGE_FUNCS[st]
        try:
            res = fn(ctx, built) if st in ("browser", "walkthrough") else fn(ctx)
        except Exception as e:  # noqa: BLE001 - one broken stage must not lose the others' results
            from qc.results import Check, StageResult
            res = StageResult(st, [Check(st, f"{st}:(stage)", "fail", f"stage crashed: {e!r}")], complete=False)
        assign(res, IDS, ids_today)
        save_stage(res, ctx.out / "results")
        t = res.totals()
        ctx.log(f"== {st}: pass {t['pass']} fail {t['fail']} skip {t['skip']} manual {t['pending']}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("report", "log", "summary", "manual"):
        cmd = argv[0]
        if cmd == "manual":
            ap = argparse.ArgumentParser(prog="run.py manual")
            ap.add_argument("key")
            ap.add_argument("status", choices=("pass", "fail"))
            ap.add_argument("--note", default="")
            a = ap.parse_args(argv[1:])
            record_manual(a.key, a.status, a.note)
            print(f"recorded {a.key}: {a.status}")
            return 0
        run = Path(argv[1]).resolve() if len(argv) > 1 else latest_run()
        if cmd == "report":
            for p in (render_regression(run), render_walkthrough(run)):
                if p:
                    print(p)
        elif cmd == "log":
            print("updated", ", ".join(log_run(run)) or "nothing")
        else:
            print(summary(run))
        return 0

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", action="append", help=f"run only this stage ({', '.join(ALL_STAGES)}); repeatable")
    ap.add_argument("--with-walkthrough", action="store_true", help="also run the walkthrough, in parallel")
    ap.add_argument("--out", type=Path, help="run folder (default regression/out/<stamp>)")
    a = ap.parse_args(argv)
    reg, walk = select_stages(a.stage, a.with_walkthrough)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run = (a.out or QC_DIR / "out" / f"{stamp}-{git('rev-parse', '--short', 'HEAD')}").resolve()
    run.mkdir(parents=True, exist_ok=True)
    (run / "findings").mkdir(exist_ok=True)
    meta = {"date": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "commit": git("rev-parse", "--short", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"), "version": app_version(),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no")), "stages": reg, "walkthrough": walk,
            "previous_commit": previous_commit()}
    (run / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"run folder: {run}", flush=True)

    lock = threading.Lock()

    def logger(prefix):
        def log(msg):
            with lock:
                print(f"[{prefix}] {msg}", flush=True)
        return log

    built = build_frontend(logger("prepare")) if ({"browser"} & set(reg) or walk) else None
    today = dt.date.today().isoformat()
    threads = []
    if reg:
        ctx = Ctx(run / "regression", log=logger("regression"))
        threads.append(threading.Thread(target=run_track, args=("regression", reg, ctx, built, today)))
    if walk:
        wctx = Ctx(run / "walkthrough", ports=WALK_PORTS, log=logger("walkthrough"))
        threads.append(threading.Thread(target=run_track, args=("walkthrough", ["walkthrough"], wctx, built, today)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for p in (render_regression(run), render_walkthrough(run)):
        if p:
            print(f"report: {p}")
    print(summary(run))
    return 0


def previous_commit() -> str | None:
    """Commit of the last logged regression run, for "what changed since last time"."""
    p = QC_DIR / "RUNS.md"
    if not p.exists():
        return None
    for line in reversed(p.read_text().splitlines()):
        cells = [c.strip(" `") for c in line.split("|")]
        if len(cells) > 3 and cells[2] and cells[2] not in ("Commit", "---") and not set(cells[2]) <= {"-"}:
            return cells[2]
    return None


def latest_run() -> Path:
    runs = sorted((QC_DIR / "out").glob("*/meta.json"))
    if not runs:
        raise SystemExit("no runs in regression/out yet")
    return runs[-1].parent


if __name__ == "__main__":
    raise SystemExit(main())

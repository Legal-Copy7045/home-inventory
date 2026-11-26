"""HTML reports for a regression run and a walkthrough run.

Both are single self-contained pages (published as private artifacts): no
scripts, colours from tokens with a dark theme, readable on a phone.
"""
from __future__ import annotations

import base64
import io
import json
import re
from html import escape
from pathlib import Path

from .results import Check, StageResult

SEVERITIES = ("critical", "high", "medium", "low", "info")
GROUPS = ("merge", "rename", "move", "remove", "flow", "mobile", "keep-separate")
GROUP_TITLES = {"merge": "Merge", "rename": "Rename", "move": "Move", "remove": "Remove", "flow": "Flow",
                "mobile": "Phone", "keep-separate": "Keep separate"}

CSS = """
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>
/* Layout: one reading column; summary first, then what needs a decision, then evidence. */
:root {
  --bg: #f3f6f8; --surface: #ffffff; --ink: #18232c; --muted: #5a6875; --line: #d9e1e8;
  --accent: #0277bd; --accent-soft: #e1f1fb;
  --pass: #2e7d32; --pass-soft: #e6f4e7; --fail: #c62828; --fail-soft: #fdeaea;
  --warn: #a86300; --warn-soft: #fff3df; --skip: #6b7782; --skip-soft: #eef1f4;
  --font: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f1418; --surface: #182027; --ink: #e5ecf1; --muted: #98a6b2; --line: #2b3640;
  --accent: #4fb3ec; --accent-soft: #11303f; --pass: #66bb6a; --pass-soft: #15301a; --fail: #ef6461; --fail-soft: #3a1b1b;
  --warn: #f0a63a; --warn-soft: #3a2a10; --skip: #9aa6b0; --skip-soft: #232c34; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0f1418; --surface: #182027; --ink: #e5ecf1; --muted: #98a6b2; --line: #2b3640;
  --accent: #4fb3ec; --accent-soft: #11303f; --pass: #66bb6a; --pass-soft: #15301a; --fail: #ef6461; --fail-soft: #3a1b1b;
  --warn: #f0a63a; --warn-soft: #3a2a10; --skip: #9aa6b0; --skip-soft: #232c34; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--font); }
.wrap { max-width: 1100px; margin: 0 auto; padding-block: 28px 64px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 28px; }
h1 { font-size: 1.7rem; margin: 0; letter-spacing: -0.01em; text-wrap: balance; }
h2 { font-size: 1.2rem; margin: 0 0 10px; text-wrap: balance; }
h3 { font-size: 1rem; margin: 14px 0 6px; }
.meta { color: var(--muted); display: flex; flex-wrap: wrap; gap: 4px 18px; font-size: .9rem; }
.meta b { color: var(--ink); font-weight: 600; }
code, .id { font-family: var(--mono); font-size: .86em; }
.id { white-space: nowrap; color: var(--accent); }
section { min-width: 0; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.tile { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.tile .n { font-size: 1.6rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.tile .l { color: var(--muted); font-size: .82rem; text-transform: uppercase; letter-spacing: .05em; }
.tile.bad .n { color: var(--fail); } .tile.good .n { color: var(--pass); }
.scroll { overflow-x: auto; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; background: var(--surface); }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
pre { margin: 4px 0 0; white-space: pre-wrap; word-break: break-word; font: .8rem/1.45 var(--mono); color: var(--muted); max-height: 18em; overflow: auto; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: .76rem; font-weight: 600; white-space: nowrap; }
.pass { background: var(--pass-soft); color: var(--pass); } .fail { background: var(--fail-soft); color: var(--fail); }
.skip { background: var(--skip-soft); color: var(--skip); } .pending { background: var(--warn-soft); color: var(--warn); }
.critical, .high { background: var(--fail-soft); color: var(--fail); } .medium { background: var(--warn-soft); color: var(--warn); }
.low, .info { background: var(--skip-soft); color: var(--skip); }
.bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--skip-soft); min-width: 80px; }
.bar i { display: block; } .bar .p { background: var(--pass); } .bar .f { background: var(--fail); } .bar .s { background: var(--skip); } .bar .w { background: var(--warn); }
details { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; }
details + details { margin-top: 8px; }
summary { cursor: pointer; font-weight: 600; }
summary:focus-visible, a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
a { color: var(--accent); }
.note { color: var(--muted); font-size: .9rem; margin: 0 0 10px; max-width: 70ch; }
.empty { color: var(--muted); font-style: italic; }
.cards { display: grid; gap: 10px; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; display: grid; gap: 6px; min-width: 0; }
.card .head { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; }
.card .head b { font-size: 1rem; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; font-size: .9rem; }
.kv dt { color: var(--muted); } .kv dd { margin: 0; min-width: 0; }
.thumbs { display: flex; flex-wrap: wrap; gap: 8px; }
.thumbs figure { margin: 0; font-size: .75rem; color: var(--muted); max-width: 180px; }
.thumbs img { display: block; max-width: 100%; border: 1px solid var(--line); border-radius: 6px; }
.menus { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px; }
.menus ul { margin: 2px 0 8px; padding-left: 18px; }
@media (prefers-reduced-motion: no-preference) { details[open] > *:not(summary) { animation: fade .15s ease-out; } }
@keyframes fade { from { opacity: .4; } to { opacity: 1; } }
</style>
"""


VERIFIED = '<span class="pill pass">verified</span>'
UNVERIFIED = '<span class="pill skip">unverified</span>'
NO_CHECKS = '<span class="pill fail">no checks</span>'


def pill(status: str) -> str:
    return f'<span class="pill {escape(status)}">{escape(status)}</span>'


def bar(t: dict) -> str:
    total = sum(t.values()) or 1
    seg = "".join(f'<i class="{c}" style="width:{t.get(k, 0) * 100 / total:.2f}%"></i>'
                  for k, c in (("pass", "p"), ("fail", "f"), ("pending", "w"), ("skip", "s")))
    return f'<div class="bar" role="img" aria-label="{t.get("pass", 0)} pass, {t.get("fail", 0)} fail">{seg}</div>'


def page(title: str, body: str) -> str:
    return f"<title>{escape(title)}</title>\n{CSS}\n<div class=\"wrap\">\n{body}\n</div>\n"


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


def _checks_table(checks: list[Check], with_detail=True) -> str:
    if not checks:
        return '<p class="empty">None.</p>'
    rows = "".join(
        f'<tr><td class="id">{escape(c.id)}</td><td>{pill(c.status)}</td><td><code>{escape(c.key)}</code>'
        + (f"<pre>{escape(c.detail)}</pre>" if with_detail and c.detail and c.status != "pass" else "") + "</td></tr>"
        for c in checks)
    return f'<div class="scroll"><table><thead><tr><th>ID</th><th>Result</th><th>Check</th></tr></thead><tbody>{rows}</tbody></table></div>'


# --------------------------------------------------------------------------- findings

def load_findings(fdir: Path, prefix: str) -> tuple[list[dict], list[dict]]:
    """All ``<prefix>-*.json`` files: (findings, reviewers)."""
    findings, reviewers = [], []
    for p in sorted(fdir.glob(f"{prefix}-*.json")):
        d = json.loads(p.read_text())
        name = d.get("reviewer") or p.stem.removeprefix(prefix + "-")
        reviewers.append({"name": name, "scope": d.get("scope", ""), "status": d.get("status", "completed"),
                          "proposed_menus": d.get("proposed_menus") or {}})
        for f in d.get("findings", []):
            findings.append({**f, "reviewer": f.get("reviewer") or name})
    return findings, reviewers


def finding_key(f: dict, kind: str) -> str:
    """Registry key for a finding.  Security keys are hashed: the registry is committed to a
    public repository and must not describe unfixed problems."""
    if kind == "security":
        import hashlib
        raw = f"{f.get('file') or '-'}:{slug(f.get('title', ''))}"
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"{f.get('group', 'flow')}:{slug(f.get('title', ''))}"


def assign_finding_ids(findings: list[dict], ids_dir: Path, kind: str) -> None:
    from .ids import Registry
    reg = Registry(ids_dir / ("findings.yaml" if kind == "security" else "ux-findings.yaml"),
                   "findings" if kind == "security" else "ux-findings")
    for f in findings:
        f["id"] = reg.id_for(finding_key(f, kind))
    reg.save()


def sev_rank(f: dict) -> int:
    s = (f.get("severity") or "info").lower()
    return SEVERITIES.index(s) if s in SEVERITIES else len(SEVERITIES)


# --------------------------------------------------------------------------- regression report

def regression_report(stages: list[StageResult], meta: dict, coverage: list[dict], uncovered: list[Check],
                      findings: list[dict], reviewers: list[dict]) -> str:
    allc = [c for s in stages for c in s.checks]
    tot = {k: sum(c.status == k for c in allc) for k in ("pass", "fail", "skip", "pending")}
    fails = [c for c in allc if c.status == "fail"]
    out = [f'<header><h1>Home Inventory regression</h1><div class="meta">'
           f'<span>Version <b>{escape(meta.get("version", "?"))}</b></span>'
           f'<span>Commit <b><code>{escape(meta.get("commit", "?"))}</code></b></span>'
           f'<span>Run <b>{escape(meta.get("date", "?"))}</b></span>'
           f'<span>Stages <b>{escape(", ".join(s.stage for s in stages))}</b></span></div></header>']
    by_sev = {s: sum((f.get("severity") or "").lower() == s for f in findings) for s in SEVERITIES}
    tiles = [("Checks", sum(tot.values()), ""), ("Passed", tot["pass"], "good"), ("Failed", tot["fail"], "bad" if tot["fail"] else "good"),
             ("Skipped", tot["skip"], ""), ("Manual to do", tot["pending"], ""),
             ("Security findings", len(findings), "bad" if by_sev["critical"] + by_sev["high"] else "")]
    out.append('<section class="tiles">' + "".join(
        f'<div class="tile {c}"><div class="n">{n}</div><div class="l">{escape(lab)}</div></div>' for lab, n, c in tiles) + "</section>")

    rows = ""
    for s in stages:
        t = s.totals()
        rows += (f'<tr><td><b>{escape(s.stage)}</b>{"<br><span class=note>" + escape(s.note) + "</span>" if s.note else ""}</td>'
                 + "".join(f'<td class="num">{t[k]}</td>' for k in ("pass", "fail", "skip", "pending")) + f"<td>{bar(t)}</td></tr>")
    out.append('<section><h2>Stages</h2><div class="scroll"><table><thead><tr><th>Stage</th><th class="num">Pass</th>'
               '<th class="num">Fail</th><th class="num">Skip</th><th class="num">Manual</th><th>Share</th></tr></thead>'
               f"<tbody>{rows}</tbody></table></div></section>")

    # security audit
    sec = ['<section><h2>Security audit</h2><p class="note">Independent read-only reviewers. A regression run fixes '
           'nothing: each finding waits for a decision. “Verified” means the reviewer confirmed it against the code '
           'or a running App, not only by reading.</p>']
    if reviewers:
        sec.append('<p class="note">' + "; ".join(f'<b>{escape(r["name"])}</b>: {escape(r["status"])}'
                                                  + (f' ({escape(r["scope"])})' if r["scope"] else "") for r in reviewers) + "</p>")
    if findings:
        cards = []
        for f in sorted(findings, key=lambda x: (sev_rank(x), x.get("id", ""))):
            sev = (f.get("severity") or "info").lower()
            where = f.get("file") or ""
            if f.get("line"):
                where += f":{f['line']}"
            cards.append(
                f'<details><summary><span class="id">{escape(f.get("id", ""))}</span> {pill(sev)} '
                f'{VERIFIED if f.get("verified") else UNVERIFIED} '
                f'{escape(f.get("title", ""))}</summary><dl class="kv">'
                f'<dt>Where</dt><dd><code>{escape(where)}</code></dd><dt>Reviewer</dt><dd>{escape(f.get("reviewer", ""))}</dd>'
                f'<dt>What</dt><dd>{escape(f.get("description", ""))}</dd>'
                + (f'<dt>Evidence</dt><dd><pre>{escape(f["evidence"])}</pre></dd>' if f.get("evidence") else "")
                + (f'<dt>Suggested fix</dt><dd>{escape(f["recommendation"])}</dd>' if f.get("recommendation") else "")
                + "</dl></details>")
        sec.append("".join(cards))
    else:
        sec.append('<p class="empty">' + ("No findings." if reviewers else "No reviewer results recorded for this run.") + "</p>")
    out.append("".join(sec) + "</section>")

    out.append(f"<section><h2>Failing checks ({len(fails)})</h2>{_checks_table(fails)}</section>")

    cov_rows = "".join(
        f'<tr><td>{escape(r["area"])}</td><td><b>{escape(r["name"])}</b></td><td class="num">{r["total"]}</td>'
        f'<td class="num">{r["pass"]}</td><td class="num">{r["fail"]}</td><td class="num">{r["skip"] + r["pending"]}</td>'
        f'<td>{escape(", ".join(r["stages"])) if r["total"] else NO_CHECKS}</td>'
        f'<td>{" ".join(f"<span class=id>{escape(i)}</span>" for i in r["failing"][:8])}</td></tr>'
        for r in coverage)
    out.append('<section><h2>Feature coverage</h2><p class="note">Each feature in regression/catalogue and the checks '
               'that cover it, from every stage.</p><div class="scroll"><table><thead><tr><th>Area</th><th>Feature</th>'
               '<th class="num">Checks</th><th class="num">Pass</th><th class="num">Fail</th><th class="num">Other</th>'
               f'<th>Stages</th><th>Failing</th></tr></thead><tbody>{cov_rows}</tbody></table></div>'
               + (f'<details><summary>{len(uncovered)} test and browser checks no feature claims</summary>'
                  f'<pre>{escape(chr(10).join(c.id + " " + c.key for c in uncovered[:400]))}</pre></details>' if uncovered else "")
               + "</section>")

    manual = [c for c in allc if c.stage == "manual"]
    if manual:
        mrows = "".join(f'<tr><td class="id">{escape(c.id)}</td><td>{pill(c.status)}</td><td><code>{escape(c.key)}</code>'
                        f'<pre>{escape(c.detail)}</pre></td></tr>' for c in manual)
        out.append('<section><h2>Manual checklist</h2><p class="note">Things only a person can verify. Record a result '
                   'with <code>python3 regression/run.py manual KEY pass|fail --note "…"</code>.</p>'
                   f'<div class="scroll"><table><thead><tr><th>ID</th><th>Result</th><th>Check</th></tr></thead>'
                   f"<tbody>{mrows}</tbody></table></div></section>")

    skipped = [c for c in allc if c.status == "skip"]
    det = [f'<details><summary>Skipped ({len(skipped)})</summary>{_checks_table(skipped)}</details>' if skipped else ""]
    for s in stages:
        det.append(f'<details><summary>All {escape(s.stage)} checks ({len(s.checks)})</summary>{_checks_table(s.checks, False)}</details>')
    out.append("<section><h2>Every check</h2>" + "".join(det) + "</section>")
    return page("Home Inventory regression", "\n".join(out))


# --------------------------------------------------------------------------- walkthrough report

def thumbnail(png: Path, width: int = 180) -> str | None:
    try:
        from PIL import Image
        with Image.open(png) as im:
            im = im.convert("RGB")
            h = min(int(im.height * width / im.width), 360)
            crop = im.crop((0, 0, im.width, int(h * im.width / width)))
            crop = crop.resize((width, h))
            buf = io.BytesIO()
            crop.save(buf, "JPEG", quality=55)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a missing screenshot just shows no thumbnail
        return None


def walkthrough_report(inv: dict, ux: StageResult | None, meta: dict, findings: list[dict], reviewers: list[dict],
                       shots: Path, max_thumbs: int = 60) -> str:
    roles = inv.get("roles", {})
    screens = sum(len(w["pages"]) for r in roles.values() for w in r.values())
    checks = ux.checks if ux else []
    fails = [c for c in checks if c.status == "fail"]
    thumbs_left = [max_thumbs]
    shot_of: dict[str, str] = {}
    for role, widths in roles.items():
        for width, data in widths.items():
            for href, p in data["pages"].items():
                if p.get("shot"):
                    shot_of[p["shot"]] = f"{role} · {width} · {p.get('pattern', href)}"

    def thumbs(names: list[str]) -> str:
        figs = []
        for n in names:
            if thumbs_left[0] <= 0 or n not in shot_of:
                continue
            src = thumbnail(shots / n)
            if src:
                thumbs_left[0] -= 1
                figs.append(f'<figure><img src="{src}" alt="Screenshot: {escape(shot_of[n])}" loading="lazy">'
                            f"<figcaption>{escape(shot_of[n])}</figcaption></figure>")
        return f'<div class="thumbs">{"".join(figs)}</div>' if figs else ""

    out = [f'<header><h1>Home Inventory walkthrough</h1><div class="meta">'
           f'<span>Version <b>{escape(meta.get("version", "?"))}</b></span>'
           f'<span>Commit <b><code>{escape(meta.get("commit", "?"))}</code></b></span>'
           f'<span>Run <b>{escape(meta.get("date", "?"))}</b></span>'
           f'<span>Roles <b>{escape(", ".join(roles))}</b></span><span>Widths <b>desktop 1280×900, phone 390×844</b></span></div></header>']
    by_group = {g: [f for f in findings if f.get("group") == g] for g in GROUPS}
    tiles = [("Screens recorded", screens, ""), ("UX checks", len(checks), ""),
             ("UX failing", len(fails), "bad" if fails else "good"), ("Reviewer findings", len(findings), "")]
    out.append('<section class="tiles">' + "".join(
        f'<div class="tile {c}"><div class="n">{n}</div><div class="l">{escape(lab)}</div></div>' for lab, n, c in tiles) + "</section>")

    fsec = ['<section><h2>Proposals</h2><p class="note">From two independent reviewers (desktop and phone) who saw the '
            'screens and the code. Nothing has been changed: these wait for a decision.</p>']
    if reviewers:
        fsec.append('<p class="note">' + "; ".join(f'<b>{escape(r["name"])}</b>: {escape(r["status"])}' for r in reviewers) + "</p>")
    if not findings:
        fsec.append('<p class="empty">No reviewer findings recorded for this run.</p>')
    for g in GROUPS:
        items = by_group[g]
        if not items:
            continue
        cards = []
        for f in sorted(items, key=lambda x: x.get("id", "")):
            cards.append(
                f'<div class="card"><div class="head"><span class="id">{escape(f.get("id", ""))}</span>'
                f'<b>{escape(f.get("title", ""))}</b><span class="pill skip">effort {escape(str(f.get("effort", "?")))}</span>'
                f'<span class="pill {"high" if str(f.get("risk", "")).lower().startswith("h") else "medium" if str(f.get("risk", "")).lower().startswith("m") else "low"}">risk {escape(str(f.get("risk", "?")))}</span>'
                f'<span class="note">{escape(f.get("reviewer", ""))}</span></div><dl class="kv">'
                f'<dt>Now</dt><dd>{escape(f.get("now", ""))}</dd><dt>Proposal</dt><dd>{escape(f.get("proposal", ""))}</dd>'
                f'<dt>Affects</dt><dd>{escape(", ".join(f.get("affects", [])) if isinstance(f.get("affects"), list) else str(f.get("affects", "")))}</dd>'
                + (f'<dt>Why separate</dt><dd>{escape(f["why"])}</dd>' if f.get("why") else "")
                + "</dl>" + thumbs(f.get("evidence", [])) + "</div>")
        fsec.append(f'<h3>{GROUP_TITLES[g]} ({len(items)})</h3><div class="cards">{"".join(cards)}</div>')
    out.append("".join(fsec) + "</section>")

    menus = {}
    for r in reviewers:
        for role, m in (r.get("proposed_menus") or {}).items():
            menus.setdefault(role, []).append((r["name"], m))
    if menus:
        blocks = []
        for role, lst in menus.items():
            for who, m in lst:
                groups = "".join(f'<b>{escape(g.get("group") or "")}</b><ul>' + "".join(f"<li>{escape(str(i))}</li>" for i in g.get("items", []))
                                 + "</ul>" for g in m)
                blocks.append(f'<div class="card"><div class="head"><b>{escape(role)}</b><span class="note">{escape(who)}</span></div>{groups}</div>')
        out.append(f'<section><h2>Proposed menus</h2><div class="menus">{"".join(blocks)}</div></section>')

    if fails:
        frows = "".join(f'<tr><td class="id">{escape(c.id)}</td><td><code>{escape(c.key)}</code><pre>{escape(c.detail)}</pre></td></tr>'
                        for c in fails)
        out.append(f'<section><h2>Automated UX checks failing ({len(fails)})</h2><div class="scroll"><table><thead><tr>'
                   f"<th>ID</th><th>Check</th></tr></thead><tbody>{frows}</tbody></table></div>"
                   + "</section>")
        failing_shots = []
        for c in fails:
            _, width, pat = (c.key.split(":", 2) + ["", ""])[:3]
            for role in roles:
                n = f"{role}-{width}-{slug_route(pat)}.png"
                if n in shot_of and n not in failing_shots:
                    failing_shots.append(n)
                    break
        if failing_shots:
            out.append(f'<section><h2>Failing screens</h2>{thumbs(failing_shots)}</section>')

    cur = []
    for role, widths in roles.items():
        for width, data in widths.items():
            groups = "".join(f'<b>{escape(g["group"])}</b><ul>' + "".join(
                f'<li>{escape(i["label"])} <code>{escape(i["href"])}</code></li>' for i in g["items"]) + "</ul>" for g in data["menu"])
            cur.append(f'<div class="card"><div class="head"><b>{escape(role)}</b><span class="note">{escape(width)}</span></div>{groups}</div>')
    out.append(f'<section><h2>Menus today</h2><div class="menus">{"".join(cur)}</div></section>')

    inv_blocks = []
    for role, widths in roles.items():
        for width, data in widths.items():
            rows = "".join(
                f'<tr><td><code>{escape(p.get("pattern", href))}</code></td><td>{escape(p.get("title", ""))}</td>'
                f'<td>{escape(" · ".join(p.get("headings", [])[:5]))}</td><td>{escape(" · ".join(p.get("primary", [])))}</td>'
                f'<td class="num">{p.get("height", "")}</td></tr>' for href, p in data["pages"].items())
            inv_blocks.append(f'<details><summary>{escape(role)} · {escape(width)} ({len(data["pages"])} screens)</summary>'
                              f'<div class="scroll"><table><thead><tr><th>Screen</th><th>Title</th><th>Headings</th>'
                              f'<th>Main buttons</th><th class="num">Height px</th></tr></thead><tbody>{rows}</tbody></table></div></details>')
    out.append("<section><h2>Screen inventory</h2>" + "".join(inv_blocks) + "</section>")
    return page("Home Inventory walkthrough", "\n".join(out))


def slug_route(pattern: str) -> str:
    """Same file naming as crawl.mjs."""
    s = re.sub(r"[^a-z0-9]+", "-", pattern.lstrip("/"), flags=re.I).strip("-")
    return s or "dashboard"

"""The regression stages and the walkthrough stage.

Each stage returns a StageResult of checks with readable keys; run.py gives
them stable IDs.  Nothing here changes product code or data outside the run's
own output folder.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import QC_DIR, ROOT
from .results import Check, StageResult
from .server import ROLES, AppServer, _open

SERVER = ROOT / "home_inventory/server"
FRONTEND = ROOT / "home_inventory/frontend"
TOOLS = Path(os.environ.get("HINV_QC_TOOLS", Path.home() / ".cache/hinv-qc"))
GITLEAKS_VERSION = "8.24.3"  # same as CI
REGRESSION_STAGES = ("quality", "tests", "browser", "live", "manual")
ALL_STAGES = REGRESSION_STAGES + ("walkthrough",)


@dataclass
class Ctx:
    out: Path               # this run's folder
    ports: tuple[int, int, int, int] = (18199, 18200, 18201, 18202)  # seeded ingress/api, smoke ingress/api
    log: callable = print

    @property
    def work(self) -> Path:
        p = self.out / "work"
        p.mkdir(parents=True, exist_ok=True)
        return p


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd: list[str] | str, cwd: Path = ROOT, timeout: int = 1800, env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})}, shell=isinstance(cmd, str))
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError as e:
        return 127, str(e)
    except subprocess.TimeoutExpired as e:
        return 124, f"timed out after {timeout}s\n{(e.stdout or '')[-2000:] if isinstance(e.stdout, str) else ''}"


def tail(text: str, n: int = 40) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


# --------------------------------------------------------------------------- quality

def _tool_venv() -> Path | None:
    """A cached venv for scanners the container doesn't ship (bandit)."""
    venv = TOOLS / "venv"
    py = venv / "bin/python"
    if not py.exists():
        rc, _ = sh([sys.executable, "-m", "venv", str(venv)], timeout=300)
        if rc:
            return None
    rc, _ = sh([str(py), "-m", "pip", "install", "-q", "bandit[toml]"], timeout=600)
    return venv if rc == 0 else None


def _gitleaks() -> Path | None:
    exe = shutil.which("gitleaks")
    if exe:
        return Path(exe)
    exe = TOOLS / "bin/gitleaks"
    if exe.exists():
        return exe
    url = (f"https://github.com/gitleaks/gitleaks/releases/download/v{GITLEAKS_VERSION}/"
           f"gitleaks_{GITLEAKS_VERSION}_linux_x64.tar.gz")
    try:
        exe.parent.mkdir(parents=True, exist_ok=True)
        tgz = exe.parent / "gitleaks.tgz"
        with urllib.request.urlopen(url, timeout=120) as r:
            tgz.write_bytes(r.read())
        with tarfile.open(tgz) as t:
            t.extract("gitleaks", exe.parent, filter="data")
        tgz.unlink()
        exe.chmod(0o755)
        return exe
    except Exception:  # noqa: BLE001 - offline: the check is skipped and says why
        return None


def run_quality(ctx: Ctx) -> StageResult:
    res = StageResult("quality", [], started=now())
    add = res.checks.append

    def cmd_check(key, cmd, cwd=ROOT, timeout=900, ok_codes=(0,)):
        rc, out = sh(cmd, cwd, timeout)
        add(Check("quality", key, "pass" if rc in ok_codes else "fail", tail(out)))
        return rc, out

    ctx.log("quality: ruff")
    cmd_check("ruff:server", ["ruff", "check", "home_inventory_server", "tests"], SERVER)
    cmd_check("ruff:integration+tools", ["ruff", "check", "custom_components", "tests", "regression", "tools"])

    ctx.log("quality: typecheck")
    if not (FRONTEND / "node_modules").exists():
        sh(["npm", "ci"], FRONTEND, 900)
    cmd_check("typecheck:frontend", ["npm", "run", "-s", "typecheck"], FRONTEND)

    ctx.log("quality: versions")
    cmd_check("versions:consistent", [sys.executable, ".github/scripts/check_versions.py"])

    ctx.log("quality: bandit")
    venv = _tool_venv()
    for key, target in (("bandit:server", SERVER / "home_inventory_server"), ("bandit:integration", ROOT / "custom_components")):
        if not venv:
            add(Check("quality", key, "skip", "could not install bandit (no package index?)"))
            continue
        # medium or high severity, medium or high confidence
        rc, out = sh([str(venv / "bin/bandit"), "-r", str(target), "-f", "json", "-q", "-ll", "-ii"], timeout=600)
        try:
            found = json.loads(out[out.index("{"):])["results"]
        except (ValueError, KeyError):
            add(Check("quality", key, "fail", "bandit output unreadable:\n" + tail(out)))
            continue
        lines = [f"{r['issue_severity']}/{r['issue_confidence']} {r['test_id']} {Path(r['filename']).relative_to(ROOT)}:"
                 f"{r['line_number']} {r['issue_text']}" for r in found]
        add(Check("quality", key, "fail" if found else "pass", "\n".join(lines[:30]) or "no medium/high findings",
                  data={"findings": len(found)}))

    ctx.log("quality: dependency audit")
    rc, out = sh(["pip-audit", "--progress-spinner", "off", "-f", "json", str(SERVER)], timeout=900)
    try:
        deps = json.loads(out[out.index("{"):])["dependencies"]
        vulns = [f"{d['name']} {d['version']}: " + ", ".join(v["id"] for v in d["vulns"]) for d in deps if d.get("vulns")]
        add(Check("quality", "audit:python", "fail" if vulns else "pass", "\n".join(vulns) or f"{len(deps)} packages, no known vulnerabilities"))
    except (ValueError, KeyError):
        add(Check("quality", "audit:python", "skip" if rc in (124, 127) else "fail", "pip-audit did not finish:\n" + tail(out)))
    rc, out = sh(["npm", "audit", "--omit=dev", "--audit-level=high", "--json"], FRONTEND, 600)
    try:
        meta = json.loads(out[out.index("{"):])["metadata"]["vulnerabilities"]
        bad = meta.get("high", 0) + meta.get("critical", 0)
        add(Check("quality", "audit:javascript", "fail" if bad else "pass", json.dumps(meta)))
    except (ValueError, KeyError):
        add(Check("quality", "audit:javascript", "skip", "npm audit did not finish:\n" + tail(out)))

    ctx.log("quality: secrets")
    rc, out = sh([sys.executable, ".github/scripts/privacy_guard.py"], timeout=600)
    note = "" if os.environ.get("PRIVACY_DENYLIST") else " (private-terms list not set here: credentials and identities checked, names not)"
    add(Check("quality", "secrets:privacy-guard", "pass" if rc == 0 else "fail", tail(out) + note))
    gl = _gitleaks()
    if gl:
        rc, out = sh([str(gl), "git", str(ROOT), "--redact", "--no-banner", "-c", str(ROOT / ".gitleaks.toml")], timeout=900)
        add(Check("quality", "secrets:gitleaks-history", "pass" if rc == 0 else "fail", tail(out)))
    else:
        add(Check("quality", "secrets:gitleaks-history", "skip", "gitleaks not available and could not be downloaded"))
    res.finished = now()
    return res


# --------------------------------------------------------------------------- tests

def parse_junit(path: Path, stage: str, prefix: str, file_of=lambda cls: cls) -> list[Check]:
    checks = []
    if not path.exists():
        return checks
    for tc in ET.parse(path).getroot().iter("testcase"):
        cls, name = tc.get("classname", ""), tc.get("name", "")
        key = f"{prefix}:{file_of(cls)}::{name}"
        status, detail = "pass", ""
        for tag in ("failure", "error"):
            el = tc.find(tag)
            if el is not None:
                status, detail = "fail", tail((el.get("message") or "") + "\n" + (el.text or ""), 25)
        if tc.find("skipped") is not None:
            status, detail = "skip", tc.find("skipped").get("message") or ""
        checks.append(Check(stage, key, status, detail))
    return checks


def _py_file(cls: str) -> str:
    """``tests.test_auth.TestX`` -> ``tests/test_auth.py``."""
    parts = cls.split(".")
    for i in range(len(parts), 0, -1):
        if parts[i - 1].startswith("test_"):
            return "/".join(parts[:i]) + ".py" + ("::" + ".".join(parts[i:]) if parts[i:] else "")
    return cls


def ha_python() -> str | None:
    for cand in (os.environ.get("HINV_HA_PYTHON"), "/tmp/claude-0/havenv/bin/python", str(ROOT / ".havenv/bin/python")):
        if cand and Path(cand).exists():
            rc, _ = sh([cand, "-c", "import pytest_homeassistant_custom_component, home_inventory_server"], timeout=60)
            if rc == 0:
                return cand
    return None


def run_tests(ctx: Ctx) -> StageResult:
    res = StageResult("tests", [], started=now(), scopes=[])
    w = ctx.work

    def suite(prefix, cmd, cwd, xml, file_of=_py_file, timeout=3600, env=None):
        ctx.log(f"tests: {prefix}")
        rc, out = sh(cmd, cwd, timeout, env)
        found = parse_junit(xml, "tests", prefix, file_of)
        if not found:
            res.checks.append(Check("tests", f"{prefix}:(suite)", "fail", "no results:\n" + tail(out)))
        else:
            res.checks.extend(found)
            res.scopes.append(prefix + ":")

    suite("server", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={w / 'server.xml'}"],
          SERVER, w / "server.xml")
    suite("frontend", ["npx", "vitest", "run", "--reporter=junit", f"--outputFile={w / 'frontend.xml'}"], FRONTEND,
          w / "frontend.xml", file_of=lambda c: c)
    suite("qc", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(QC_DIR / "tests/pytest.ini"),
                 f"--junitxml={w / 'qc.xml'}", str(QC_DIR / "tests")], ROOT, w / "qc.xml",
          file_of=lambda c: "regression/tests/" + _py_file(c))
    py = ha_python()
    if py:
        suite("integration", [py, "-m", "pytest", "-q", "-p", "no:logging", "-p", "no:cacheprovider",
                              f"--junitxml={w / 'integration.xml'}"], ROOT, w / "integration.xml")
    else:
        res.checks.append(Check("tests", "integration:(suite)", "skip",
                                "Home Assistant test packages not installed here; set HINV_HA_PYTHON (see docs/development.md)"))
    res.finished = now()
    return res


# --------------------------------------------------------------------------- browser

ROUTE_PARAM = {  # App route -> which seeded record fills its parameter
    "/items/:id": "item", "/equipment/:id": "mower", "/containers/:id": "container",
    "/containers/:id/checklist": "container", "/locations/:id": "location", "/kits/:id": "kit",
    "/customers/:id": "customer", "/repairs/:id": "repair", "/repairs/:id/print": "repair",
    "/maps/:id": "map", "/verify/:id": "verification", "/tag/:ref": "tag",
}


def app_routes() -> list[str]:
    src = (FRONTEND / "src/App.tsx").read_text()
    return [p for p in re.findall(r'<Route path="([^"]+)"', src) if p != "*"]


def concrete_routes(ids: dict) -> list[dict]:
    out = []
    for pat in app_routes():
        if ":" not in pat:
            out.append({"pattern": pat, "path": pat})
        elif pat in ROUTE_PARAM and ROUTE_PARAM[pat] in ids:
            out.append({"pattern": pat, "path": re.sub(r":\w+", str(ids[ROUTE_PARAM[pat]]), pat)})
        else:
            out.append({"pattern": pat, "path": None})
    return out


def build_frontend(log=print) -> tuple[bool, str]:
    if not (FRONTEND / "node_modules").exists():
        sh(["npm", "ci"], FRONTEND, 900)
    log("building the frontend")
    rc, out = sh(["npm", "run", "-s", "build"], FRONTEND, 900)
    return rc == 0, tail(out)


def api_routes(channel: str) -> list[tuple[str, set[str]]]:
    code = (
        "import json,os,tempfile;os.environ['HINV_DATA_DIR']=tempfile.mkdtemp();os.environ['HINV_AI_PROVIDER']='none'\n"
        "from home_inventory_server.config import Settings\n"
        "from home_inventory_server.main import build_state, create_app\n"
        f"app=create_app(build_state(Settings.load()),{channel!r})\n"
        "print(json.dumps([[p,sorted(m.upper() for m in ops)] for p,ops in app.openapi()['paths'].items()]))"
    )
    rc, out = sh([sys.executable, "-c", code], SERVER, 120)
    if rc:
        raise RuntimeError("could not list API routes:\n" + tail(out))
    return [(p, set(m)) for p, m in json.loads(out.strip().splitlines()[-1])]


PUBLIC_PATHS = {"/api/health"}  # answered without a signed-in person, by design


def _get(url, headers=None):
    try:
        with _open(url, timeout=30, headers=headers) as r:
            return r.status, dict(r.headers), r.read(2000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(2000)
    except OSError as e:
        return 0, {}, str(e).encode()


def _json(base: str, path: str):
    try:
        with _open(base + "api/v1/" + path, headers=ROLES["admin"]) as r:
            return json.loads(r.read())
    except (OSError, ValueError):
        return None


def _first_id(data, field="id"):
    rows = data.get("items") or data.get("entries") or data.get("tags") or [] if isinstance(data, dict) else data or []
    return rows[0].get(field) if rows and isinstance(rows[0], dict) else None


def _sample_ids(base: str, ids: dict) -> dict:
    """Values for API path parameters, looked up from the seeded data.  A list means: try each."""
    s = {"item_id": ids["mower"], "container_id": ids["container"], "location_id": ids["location"],
         "customer_id": ids["customer"], "job_id": ids["repair"], "kit_id": ids["kit"], "map_id": ids["map"],
         "sid": ids["verification"], "ref": ids["tag"], "code": ids["tag"],
         "fmt": ["csv", "xlsx"], "kind": ["upcoming", "overdue", "history", "consolidated"]}
    item = _json(base, f"items/{ids['mower']}") or {}
    if item.get("product_id"):
        s["product_id"] = item["product_id"]
    if (item.get("photo") or {}).get("id"):
        s["attachment_id"] = item["photo"]["id"]
    job = _json(base, f"repair-jobs/{ids['repair']}") or {}
    if job.get("number"):
        s["number"] = job["number"]
    for param, path in (("tag_id", "tags"), ("entry_id", "shopping-list")):
        v = _first_id(_json(base, path))
        if v is not None:
            s[param] = v
    return {k: v for k, v in s.items() if v is not None}


def api_sweep(server: AppServer, ids: dict) -> list[Check]:
    checks: list[Check] = []
    sample = _sample_ids(server.base, ids)

    def fills(path):
        """[(label, concrete path)] for every sample combination, or (None, missing params)."""
        params = re.findall(r"{(\w+)(?::\w+)?}", path)
        missing = [p for p in params if p not in sample]
        if missing:
            return None, missing
        combos = [("", path)]
        for p in params:
            vals = sample[p] if isinstance(sample[p], list) else [sample[p]]
            combos = [(lab + (f" [{p}={v}]" if len(vals) > 1 else ""), re.sub(r"{%s(?::\w+)?}" % p, str(v), cp))
                      for lab, cp in combos for v in vals]
        return combos, []

    for channel, base in (("ingress", server.base), ("api", server.api_base)):
        for path, methods in api_routes(channel):
            if "GET" not in methods or not path.startswith("/api"):
                continue
            combos, missing = fills(path)
            roles = ("admin", "member", "read_only", "anon") if channel == "ingress" else ("anon",)
            for role in roles:
                if combos is None:
                    checks.append(Check("browser", f"api:{channel}:{role}:GET {path}", "skip",
                                        f"no {', '.join(missing)} in the demo data"))
                    continue
                for label, url_path in combos:
                    key = f"api:{channel}:{role}:GET {path}{label}"
                    st, hdrs, body = _get(base + url_path.lstrip("/"), ROLES.get(role))
                    status = "pass"
                    if role == "anon":
                        if 200 <= st < 300 and path not in PUBLIC_PATHS:
                            status = "fail"
                        detail = f"HTTP {st} without a signed-in person" + (" (must be refused)" if status == "fail" else "")
                    else:
                        detail = f"HTTP {st} {hdrs.get('content-type', '')}"
                        if st == 503 and b"Home Assistant" in body:
                            status, detail = "skip", "needs a real Home Assistant: " + body[:200].decode(errors="replace")
                        elif st >= 500 or st == 0:
                            status, detail = "fail", detail + f"\n{body[:300]!r}"
                    checks.append(Check("browser", key, status, detail, data={"status": st}))
    # security headers on the page itself
    st, hdrs, _ = _get(server.base, ROLES["member"])
    hl = {k.lower(): v for k, v in hdrs.items()}
    for name in ("content-security-policy", "x-content-type-options", "referrer-policy", "cross-origin-resource-policy"):
        checks.append(Check("browser", f"headers:ingress:{name}", "pass" if name in hl else "fail", hl.get(name, "missing")))
    return checks


def run_node(script: Path, payload: dict, timeout=3600) -> tuple[int, str]:
    """Runs a Playwright script with its job in $QC_PAYLOAD."""
    return sh(["node", str(script)], ROOT, timeout, env={"QC_PAYLOAD": json.dumps(payload)})


def run_browser(ctx: Ctx, built: tuple[bool, str] | None = None) -> StageResult:
    res = StageResult("browser", [], started=now())
    ok, out = built or build_frontend(ctx.log)
    res.checks.append(Check("browser", "build:frontend", "pass" if ok else "fail", out))
    if not ok:
        res.complete = False
        res.note = "frontend did not build; browser checks not run"
        return res
    ing, api, sing, sapi = ctx.ports
    ctx.log("browser: smoke test on an empty App")
    shots = ctx.work / "smoke"
    shots.mkdir(exist_ok=True)
    (ctx.work / "smoke-app").mkdir(exist_ok=True)
    with AppServer(ctx.work / "smoke-app", sing, sapi) as s:
        rc, out = sh(["node", "tests/e2e/smoke.mjs", str(shots)], ROOT, 900, env={"HINV_URL": s.base})
    res.checks.append(Check("browser", "e2e:smoke", "pass" if rc == 0 else "fail", tail(out)))

    ctx.log("browser: every page, every role, on a seeded App")
    (ctx.work / "app").mkdir(exist_ok=True)
    with AppServer(ctx.work / "app", ing, api) as s:
        ids = s.seed()
        routes = concrete_routes(ids)
        for r in routes:
            if r["path"] is None:
                for role in ("admin", "member", "read_only"):
                    res.checks.append(Check("browser", f"page:{role}:{r['pattern']}", "skip", "no seeded record for this page"))
        payload = {"base": s.base, "roles": ROLES, "routes": [r for r in routes if r["path"]],
                   "out": str(ctx.work / "pages.json")}
        rc, out = run_node(QC_DIR / "browser/pages.mjs", payload)
        try:
            pages = json.loads((ctx.work / "pages.json").read_text())
            for p in pages:
                res.checks.append(Check("browser", f"page:{p['role']}:{p['pattern']}", p["status"], p["detail"]))
        except (OSError, ValueError):
            res.checks.append(Check("browser", "page:(crawl)", "fail", tail(out)))
            res.complete = False
        ctx.log("browser: API sweep")
        res.checks.extend(api_sweep(s, ids))
    res.finished = now()
    return res


# --------------------------------------------------------------------------- live

def app_version() -> str:
    return yaml.safe_load((ROOT / "home_inventory/config.yaml").read_text())["version"]


def _https_json(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "home-inventory-qc", **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def run_live(ctx: Ctx) -> StageResult:
    """Read-only checks of what is published, plus the running App if one is reachable."""
    res = StageResult("live", [], started=now())
    v = app_version()
    repo = "Legal-Copy7045/home-inventory"
    try:
        rel = _https_json(f"https://api.github.com/repos/{repo}/releases/tags/{v}")
        res.checks.append(Check("live", "release:current-version", "pass" if not rel.get("draft") else "fail",
                                f"GitHub release {rel.get('tag_name')} published {rel.get('published_at')}"))
    except urllib.error.HTTPError as e:
        res.checks.append(Check("live", "release:current-version", "fail" if e.code == 404 else "skip",
                                f"GitHub release for {v}: HTTP {e.code}"))
    except OSError as e:
        res.checks.append(Check("live", "release:current-version", "skip", f"GitHub not reachable: {e}"))
    image = yaml.safe_load((ROOT / "home_inventory/config.yaml").read_text())["image"]
    for arch in ("amd64", "aarch64"):
        name = image.replace("{arch}", arch).removeprefix("ghcr.io/")
        key = f"image:{arch}"
        try:
            tok = _https_json(f"https://ghcr.io/token?scope=repository:{name}:pull")["token"]
            req = urllib.request.Request(f"https://ghcr.io/v2/{name}/manifests/{v}", method="HEAD", headers={
                "Authorization": f"Bearer {tok}", "User-Agent": "home-inventory-qc",
                "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, "
                          "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                res.checks.append(Check("live", key, "pass", f"ghcr.io/{name}:{v} HTTP {r.status}"))
        except urllib.error.HTTPError as e:
            res.checks.append(Check("live", key, "fail" if e.code == 404 else "skip", f"ghcr.io/{name}:{v} HTTP {e.code}"))
        except OSError as e:
            res.checks.append(Check("live", key, "skip", f"ghcr.io not reachable: {e}"))
    live = os.environ.get("HINV_LIVE_URL")
    if not live:
        res.checks.append(Check("live", "app:health", "skip",
                                "No running App reachable from here: it is local-only by design. Set HINV_LIVE_URL "
                                "(e.g. http://homeassistant.local:8100/) when running on the home network."))
    else:
        st, _, body = _get(live.rstrip("/") + "/api/health")
        try:
            got = json.loads(body).get("version")
        except ValueError:
            got = None
        res.checks.append(Check("live", "app:health", "pass" if st == 200 else "fail", f"HTTP {st}"))
        res.checks.append(Check("live", "app:version", "pass" if got == v else "fail", f"running {got}, repository {v}"))
    res.finished = now()
    return res


# --------------------------------------------------------------------------- manual

def manual_items() -> list[dict]:
    return yaml.safe_load((QC_DIR / "manual.yaml").read_text())["checks"]


def manual_log() -> dict[str, dict]:
    p = QC_DIR / "manual-log.yaml"
    rows = (yaml.safe_load(p.read_text()) or []) if p.exists() else []
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["key"]] = r
    return latest


def record_manual(key: str, status: str, note: str = "", version: str | None = None) -> None:
    if status not in ("pass", "fail"):
        raise ValueError("status must be pass or fail")
    if key not in {m["key"] for m in manual_items()}:
        raise KeyError(f"no manual check {key!r} in regression/manual.yaml")
    p = QC_DIR / "manual-log.yaml"
    rows = (yaml.safe_load(p.read_text()) or []) if p.exists() else []
    rows.append({"key": key, "status": status, "date": dt.date.today().isoformat(),
                 "version": version or app_version(), "note": note})
    p.write_text("# Results of the manual checks (regression/manual.yaml), newest last.\n"
                 + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))


def run_manual(ctx: Ctx) -> StageResult:
    res = StageResult("manual", [], started=now())
    done = manual_log()
    v = app_version()
    for m in manual_items():
        r = done.get(m["key"])
        if not r:
            res.checks.append(Check("manual", m["key"], "pending", m["check"]))
        else:
            stale = r.get("version") != v
            status = "pending" if stale else r["status"]
            res.checks.append(Check("manual", m["key"], status,
                                    f"{m['check']}\nLast: {r['status']} on {r['date']} (version {r.get('version')})"
                                    + (" - not yet checked on this version" if stale else "") + (f": {r['note']}" if r.get("note") else "")))
    res.finished = now()
    return res


# --------------------------------------------------------------------------- walkthrough

WIDTHS = [{"name": "desktop", "width": 1280, "height": 900, "mobile": False},
          {"name": "phone", "width": 390, "height": 844, "mobile": True}]


def _norm_label(s: str) -> str:
    s = s.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def ux_checks(inv: dict) -> list[Check]:
    """Turn the crawl inventory into UX checks, one per check/width/page, roles combined."""
    found: dict[str, list[str]] = {}
    seen: set[str] = set()

    def note(key, role, problem=None):
        seen.add(key)
        if problem:
            found.setdefault(key, []).append(f"{role}: {problem}")

    for role, widths in inv["roles"].items():
        for width, data in widths.items():
            labels = [i["label"] for g in data["menu"] for i in g["items"]]
            dups = sorted({x for x in labels if labels.count(x) > 1})
            note(f"menu-duplicates:{width}:{role}", role, f"labels used twice: {', '.join(dups)}" if dups else None)
            for g in data["menu"]:
                for it in g["items"]:
                    page = data["pages"].get(it["href"])
                    if not page:
                        continue
                    t = page.get("title", "")
                    note(f"menu-heading:{width}:{it['href']}", role,
                         None if _norm_label(t) == _norm_label(it["label"]) else f"menu says “{it['label']}”, page says “{t}”")
                    final = page.get("final_path")
                    note(f"menu-redirect:{width}:{it['href']}", role,
                         f"opens {final} instead" if final and final != it["href"] else None)
            for route, page in data["pages"].items():
                pat = page.get("pattern", route)
                where = "" if page.get("from_menu", True) else " (detail screen, not in this role's menu)"
                note(f"refused:{width}:{pat}", role, page["refused"] + where if page.get("refused") else None)
                errs = page.get("console", [])
                note(f"console:{width}:{pat}", role, "; ".join(errs[:3]) if errs else None)
                o = page.get("overflow", [])
                note(f"overflow:{width}:{pat}", role, "; ".join(o[:4]) if o else None)
                if width == "phone":
                    note(f"hscroll:phone:{pat}", role, f"page is {page['scroll_width']}px wide" if page.get("hscroll") else None)
                    taps = page.get("small_taps", [])
                    note(f"tap-targets:phone:{pat}", role, f"{len(taps)} small: " + "; ".join(taps[:5]) if taps else None)
    return [Check("walkthrough", k, "fail" if k in found else "pass", "\n".join(found.get(k, [])))
            for k in sorted(seen)]


def run_walkthrough(ctx: Ctx, built: tuple[bool, str] | None = None) -> StageResult:
    res = StageResult("walkthrough", [], started=now())
    ok, out = built or build_frontend(ctx.log)
    if not ok:
        res.checks.append(Check("walkthrough", "build:frontend", "fail", out))
        res.complete = False
        return res
    ing, api = ctx.ports[0], ctx.ports[1]
    shots = ctx.out / "screens"
    shots.mkdir(parents=True, exist_ok=True)
    (ctx.work / "app").mkdir(exist_ok=True)
    with AppServer(ctx.work / "app", ing, api) as s:
        ids = s.seed()
        routes = [r for r in concrete_routes(ids) if r["path"]]
        payload = {"base": s.base, "roles": ROLES, "widths": WIDTHS, "routes": routes,
                   "out": str(ctx.out / "inventory.json"), "shots": str(shots)}
        ctx.log("walkthrough: crawling (3 roles x desktop and phone)")
        rc, out = run_node(QC_DIR / "walkthrough/crawl.mjs", payload, timeout=5400)
    try:
        inv = json.loads((ctx.out / "inventory.json").read_text())
    except (OSError, ValueError):
        res.checks.append(Check("walkthrough", "crawl", "fail", tail(out)))
        res.complete = False
        return res
    res.checks.extend(ux_checks(inv))
    res.finished = now()
    return res


STAGE_FUNCS = {"quality": run_quality, "tests": run_tests, "browser": run_browser, "live": run_live,
               "manual": run_manual, "walkthrough": run_walkthrough}

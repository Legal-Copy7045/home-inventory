"""Tests for the quality runner: IDs, stage selection, parsing, UX checks, catalogue and reports."""
from __future__ import annotations

import json

import pytest
import yaml

import run as runner
from qc import catalogue as cat
from qc import render, stages
from qc.ids import Registry, assign
from qc.results import Check, StageResult, load_stage, save_stage

# ----------------------------------------------------------------------------- IDs


def test_ids_are_stable_and_sequential(tmp_path):
    reg = Registry(tmp_path / "tests.yaml", "tests")
    assert reg.id_for("a", "2026-01-01") == "T0001"
    assert reg.id_for("b", "2026-01-01") == "T0002"
    assert reg.id_for("a") == "T0001"
    reg.save()
    again = Registry(tmp_path / "tests.yaml", "tests")
    assert again.id_for("b") == "T0002"
    assert again.id_for("c") == "T0003"


def test_missing_checks_are_retired_never_reused(tmp_path):
    res = StageResult("tests", [Check("tests", "a", "pass"), Check("tests", "b", "pass")])
    assign(res, tmp_path, "2026-01-01")
    res2 = StageResult("tests", [Check("tests", "b", "pass"), Check("tests", "new", "pass")])
    assert assign(res2, tmp_path, "2026-01-02") == ["T0001"]
    assert res2.checks[1].id == "T0003"  # T0001 is retired, not reused
    data = yaml.safe_load((tmp_path / "tests.yaml").read_text())
    assert data["ids"]["T0001"]["retired"] == "2026-01-02"
    # the same check coming back keeps its old ID
    res3 = StageResult("tests", [Check("tests", "a", "pass")], complete=False)
    assign(res3, tmp_path)
    assert res3.checks[0].id == "T0001"
    assert "retired" not in yaml.safe_load((tmp_path / "tests.yaml").read_text())["ids"]["T0001"]


def test_partial_runs_retire_nothing_and_scopes_limit_retirement(tmp_path):
    assign(StageResult("tests", [Check("tests", "server:x", "pass"), Check("tests", "integration:y", "pass")]), tmp_path)
    assert assign(StageResult("tests", [Check("tests", "server:x", "pass")], complete=False), tmp_path) == []
    # integration skipped: only server: keys may retire
    gone = assign(StageResult("tests", [Check("tests", "server:z", "pass")], scopes=["server:"]), tmp_path)
    assert gone == ["T0001"]
    ids = yaml.safe_load((tmp_path / "tests.yaml").read_text())["ids"]
    assert "retired" not in ids["T0002"]


def test_hand_edited_registry_never_reuses(tmp_path):
    (tmp_path / "quality.yaml").write_text("prefix: Q\nnext: 1\nids:\n  Q0001: {key: old}\n")
    assert Registry(tmp_path / "quality.yaml", "quality").id_for("new") == "Q0002"


def test_bad_status_rejected():
    with pytest.raises(ValueError):
        Check("tests", "k", "ok")

# ----------------------------------------------------------------------------- stage selection


@pytest.mark.parametrize("stages_arg, walk_flag, want", [
    (None, False, (list(stages.REGRESSION_STAGES), False)),
    (["walkthrough"], False, ([], True)),
    (None, True, (list(stages.REGRESSION_STAGES), True)),
    (["unit"], True, (["unit"], True)),
    (["live", "ci", "live"], False, (["ci", "live"], False)),
    (["quality", "tests", "browser"], False, (["ci", "unit", "page"], False)),  # old names still work
])
def test_select_stages(stages_arg, walk_flag, want):
    assert runner.select_stages(stages_arg, walk_flag) == want


def test_unknown_stage_rejected():
    with pytest.raises(SystemExit):
        runner.select_stages(["everything"], False)

# ----------------------------------------------------------------------------- parsing


def test_parse_junit(tmp_path):
    (tmp_path / "r.xml").write_text("""<testsuites><testsuite>
      <testcase classname="tests.test_auth" name="test_ok"/>
      <testcase classname="tests.test_auth.TestRoles" name="test_bad"><failure message="boom">trace</failure></testcase>
      <testcase classname="tests.test_x" name="test_s"><skipped message="needs HA"/></testcase>
    </testsuite></testsuites>""")
    got = {c.key: (c.status, c.detail) for c in stages.parse_junit(tmp_path / "r.xml", "tests", "server", stages._py_file)}
    assert got["server:tests/test_auth.py::test_ok"][0] == "pass"
    assert got["server:tests/test_auth.py::TestRoles::test_bad"][0] == "fail"
    assert "boom" in got["server:tests/test_auth.py::TestRoles::test_bad"][1]
    assert got["server:tests/test_x.py::test_s"] == ("skip", "needs HA")


def test_concrete_routes_fill_ids():
    routes = {r["pattern"]: r["path"] for r in stages.concrete_routes({"item": 5, "mower": 9, "tag": "abc"})}
    assert routes["/items"] == "/items"
    assert routes["/items/:id"] == "/items/5"
    assert routes["/equipment/:id"] == "/equipment/9"
    assert routes["/tag/:ref"] == "/tag/abc"
    assert routes["/kits/:id"] is None  # no kit seeded

# ----------------------------------------------------------------------------- UX checks


def _inv():
    page = {"pattern": "/items", "title": "Items", "final_path": "/items", "console": [], "overflow": [], "refused": ""}
    return {"roles": {"member": {
        "desktop": {"menu": [{"group": "Main", "items": [{"label": "Items", "href": "/items"},
                                                         {"label": "Print Labels", "href": "/labels"},
                                                         {"label": "Items", "href": "/old"}]}],
                    "pages": {"/items": page,
                              "/labels": {**page, "pattern": "/labels", "title": "Labels", "final_path": "/labels"},
                              "/old": {**page, "pattern": "/old", "final_path": "/items"}}},
        "phone": {"menu": [], "pages": {"/items": {**page, "hscroll": True, "scroll_width": 520,
                                                   "small_taps": ["button “x” 24×24"], "console": ["CSP: img-src"]}}}}}}


def test_ux_checks():
    got = {c.key: c for c in stages.ux_checks(_inv())}
    assert got["menu-heading:desktop:/items"].status == "pass"
    assert got["menu-heading:desktop:/labels"].status == "fail"
    assert "Print Labels" in got["menu-heading:desktop:/labels"].detail
    assert got["menu-redirect:desktop:/old"].status == "fail"
    assert got["menu-duplicates:desktop:member"].status == "fail"
    assert got["hscroll:phone:/items"].status == "fail"
    assert got["tap-targets:phone:/items"].status == "fail"
    assert got["console:phone:/items"].status == "fail"
    assert got["console:desktop:/items"].status == "pass"


def test_label_normalising():
    assert stages._norm_label("Restock & Shopping") == stages._norm_label("Restock and shopping")

# ----------------------------------------------------------------------------- catalogue


def test_catalogue_coverage(tmp_path):
    (tmp_path / "a.yaml").write_text("- feature: Items\n  match: ['test_items', '^page:page:\\w+:/items']\n  manual: [cam]\n"
                                     "- feature: Nothing\n  match: ['no-such-check']\n")
    feats = cat.load(tmp_path)
    checks = [Check("unit", "server:tests/test_items.py::t", "pass", id="T0001"),
              Check("page", "page:admin:/items", "fail", id="B0001"),
              Check("unit", "server:tests/test_other.py::t", "pass", id="T0002"),
              Check("manual", "cam", "pending", id="M0001")]
    rows = {r["name"]: r for r in cat.coverage(feats, checks)}
    assert rows["Items"]["total"] == 3 and rows["Items"]["failing"] == ["B0001"]
    assert rows["Nothing"]["total"] == 0
    assert [c.id for c in cat.uncovered(feats, checks)] == ["T0002"]


def test_repository_catalogue_patterns_compile():
    feats = cat.load(runner.QC_DIR / "catalogue")
    assert len(feats) > 20
    assert all(f["match"] for f in feats)

# ----------------------------------------------------------------------------- reports


def _stages():
    return [StageResult("quality", [Check("quality", "ruff:server", "pass", id="Q0001"),
                                    Check("quality", "audit:<python>", "fail", "bad & worse", id="Q0002")]),
            StageResult("manual", [Check("manual", "phone-camera-photo", "pending", "Take a photo", id="M0001")])]


def test_regression_report_html():
    findings = [{"id": "F0002", "title": "Low thing", "severity": "low", "file": "a.py"},
                {"id": "F0001", "title": "Bad <thing>", "severity": "high", "verified": True, "file": "b.py", "line": 3}]
    html = render.regression_report(_stages(), {"version": "1.0", "commit": "abc"}, [], [], findings,
                                    [{"name": "changes", "scope": "diff", "status": "completed"}])
    assert html.startswith("<title>Home Inventory regression</title>")
    assert "Q0002" in html and "audit:&lt;python&gt;" in html and "bad &amp; worse" in html
    assert "Bad &lt;thing&gt;" in html and "<thing>" not in html
    assert html.index("F0001") < html.index("F0002")  # high before low
    assert "prefers-color-scheme: dark" in html
    assert "<script" not in html


def test_walkthrough_report_html(tmp_path):
    from PIL import Image
    shots = tmp_path / "screens"
    shots.mkdir()
    Image.new("RGB", (390, 900), (200, 210, 220)).save(shots / "member-phone-items.png")
    roles = {"member": {"phone": {"menu": [{"group": "Main", "items": [{"label": "Items", "href": "/items"}]}],
                                          "pages": {"/items": {"pattern": "/items", "title": "Items", "shot": "member-phone-items.png",
                                                               "headings": ["Items"], "primary": ["Add"], "height": 900}}}}}
    ux = StageResult("walkthrough", [Check("walkthrough", "hscroll:phone:/items", "fail", "member: 520px", id="U0001")])
    findings = [{"id": "W0001", "group": "duplicate", "title": "Merge A and B", "detail": "two pages", "proposal": "one",
                 "roles": ["member"], "value": "high", "screenshots": ["member-phone-items.png"], "reviewer": "desktop"}]
    reviewers = [{"name": "desktop", "scope": "", "status": "completed",
                  "proposed_menus": {"member": [{"group": "Main", "items": ["Items"]}]}}]
    html = render.walkthrough_report(roles, ux, {"version": "1.0"}, findings, reviewers, shots)
    assert "Merge A and B" in html and "W0001" in html and "U0001" in html
    assert "data:image/jpeg;base64," in html
    assert "Proposed menus" in html and "Menus today" in html and "Duplicates (1)" in html


def test_security_finding_keys_are_hashed(tmp_path):
    render.assign_finding_ids([{"title": "Token leaks in log", "file": "a.py"}], tmp_path, "security")
    text = (tmp_path / "findings.yaml").read_text()
    assert "sha256:" in text and "token" not in text.lower() and "a.py" not in text


def test_finding_ids_are_stable(tmp_path):
    f1 = [{"title": "SQL injection in search", "file": "x.py"}]
    render.assign_finding_ids(f1, tmp_path, "security")
    f2 = [{"title": "Other", "file": "y.py"}, {"title": "SQL injection in search", "file": "x.py"}]
    render.assign_finding_ids(f2, tmp_path, "security")
    assert f1[0]["id"] == f2[1]["id"] == "F0001" and f2[0]["id"] == "F0002"

# ----------------------------------------------------------------------------- logs and manual results


def _out(tmp_path, monkeypatch):
    """A regression/out folder in the standard's layout, with the runner pointed at it."""
    out = tmp_path / "out"
    monkeypatch.setattr(runner, "OUT", out)
    monkeypatch.setattr(runner, "IDS", tmp_path / "ids")
    for s in _stages():
        save_stage(s, out)
    (out / "meta.json").write_text(json.dumps({"date": "2026-11-26T10:00:00Z", "commit": "abc1234", "version": "1.0"}))
    (out / "brief.json").write_text(json.dumps({"audit": "full", "why": "first run"}))
    (out / "findings").mkdir()
    (out / "findings/bugs.json").write_text(json.dumps({"reviewer": "bugs", "status": "done", "findings": [
        {"id": "BUG-001", "severity": "high", "title": "Bad", "description": "x", "verified": True}]}))
    (out / "findings/claims.json").write_text(json.dumps({"reviewer": "claims", "status": "done", "findings": [],
                                                          "claims": [{"id": "CLM-001", "held": False}]}))
    return out


def test_stage_files_are_in_the_standard_format(tmp_path, monkeypatch):
    out = _out(tmp_path, monkeypatch)
    d = json.loads((out / "manual.json").read_text())
    assert {"stage", "prefix", "started", "finished", "results"} <= set(d)
    assert d["prefix"] == "M" and d["results"][0]["status"] == "manual"  # pending is the standard's "manual"
    assert set(d["results"][0]) >= {"id", "key", "status", "detail", "evidence"}
    save_stage(StageResult("walkthrough", [Check("walkthrough", "k", "fail", id="U0003")]), out)
    assert (out / "walkthrough-checks.json").exists() and not (out / "walkthrough.json").exists()
    assert [s.stage for s in runner.load_run(out)] == ["manual", "quality"]  # never the walkthrough's files


def test_report_reads_standard_findings(tmp_path, monkeypatch):
    out = _out(tmp_path, monkeypatch)
    html = runner.render_regression().read_text()
    assert "Bad" in html and "BUG-001" in html and "F0001" in html  # reviewer's id kept beside the stable one
    assert "not held: <b>1</b>" in html and "CLM-001" in html
    assert "exposure</b>: missing" in html  # a reviewer without a file is shown, not ignored
    assert "Audit <b>full</b>" in html
    assert out / "report.html" == runner.render_regression()


def test_standard_commands_are_delegated(monkeypatch):
    seen = []
    monkeypatch.setattr(runner.standard, "main", lambda argv: seen.append(argv) or 0)
    for cmd in ("brief", "log", "summary", "schema", "walkthrough-due"):
        assert runner.main([cmd]) == 0
    assert runner.main(["manual", "cam", "pass", "--note", "ok"]) == 0
    assert [a[0] for a in seen] == ["brief", "log", "summary", "schema", "walkthrough-due", "manual"]


def test_crashed_stage_is_prefix_run(tmp_path):
    res = StageResult("unit", [Check("unit", "RUN", "fail", "boom")], complete=False)
    assign(res, tmp_path)
    assert res.checks[0].id == "T-RUN" and not (tmp_path / "unit.yaml").exists() or "RUN" not in (tmp_path / "unit.yaml").read_text()


def test_manual_results(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "QC_DIR", tmp_path)
    (tmp_path / "manual.yaml").write_text("- key: cam\n  area: phone\n  steps: [Take a photo]\n"
                                          "- key: nfc\n  area: phone\n  steps: [Tap]\n")
    (tmp_path / "manual-log.yaml").write_text("cam: {result: pass, version: '1.0', date: '2026-11-27', note: fine}\n")
    monkeypatch.setattr(stages, "app_version", lambda: "1.0")
    got = {c.key: c.status for c in stages.run_manual(stages.Ctx(tmp_path)).checks}
    assert got == {"cam": "pass", "nfc": "pending"}
    monkeypatch.setattr(stages, "app_version", lambda: "1.1")  # a new version needs checking again
    assert {c.key: c.status for c in stages.run_manual(stages.Ctx(tmp_path)).checks}["cam"] == "pending"


def test_repository_manual_and_claims_files():
    items = yaml.safe_load((runner.QC_DIR / "manual.yaml").read_text())
    assert items and all({"key", "area", "steps"} <= set(i) for i in items)
    claims = yaml.safe_load((runner.QC_DIR / "claims.yaml").read_text())
    assert len({c["id"] for c in claims}) == len(claims) and all(c["where"] and c["code"] for c in claims)
    prof = yaml.safe_load((runner.QC_DIR / "profile.yaml").read_text())
    assert set(prof["stages"]) >= {"ci", "unit", "page", "live", "manual", "walkthrough"}


def test_stage_result_roundtrip(tmp_path):
    res = StageResult("tests", [Check("tests", "k", "pass", id="T0001", data={"n": 1})], scopes=["server:"])
    p = save_stage(res, tmp_path)
    back = load_stage(p)
    assert back.checks[0].data == {"n": 1} and back.scopes == ["server:"]
    assert back.checks[0].full == "tests:k"  # what catalogue patterns match: "<stage>:<key>"


def test_json_in_ignores_trailing_text():
    assert stages._json_in('noise {"a": {"b": 1}} No known vulnerabilities found') == {"a": {"b": 1}}


def test_repository_access_rules():
    rules = stages.access_rules()
    assert any(r.search("/api/v1/customers/{customer_id}") for r in rules["read_only"])
    assert not any(r.search("/api/v1/customers") for r in rules["member"])
    assert any(r.search("/api/v1/voice/history") for r in rules["member"])

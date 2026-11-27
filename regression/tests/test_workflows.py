"""The GitHub Actions set-up keeps to the project standard (STANDARD.md sections 1 and 2): minutes are limited."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github/workflows"
STANDARDS = "Legal-Copy7045/project-standards/.github/workflows/"


def load(name: str) -> dict:
    d = yaml.safe_load((WF / name).read_text())
    d["on"] = d.pop(True, d.get("on"))  # YAML reads a bare `on:` as True
    return d


@pytest.mark.parametrize("path", sorted(WF.glob("*.y*ml")), ids=lambda p: p.name)
def test_actions_pinned_by_commit_with_version_comment(path):
    for line in path.read_text().splitlines():
        m = re.search(r"uses:\s*(\S+)", line)
        if m and not m.group(1).startswith("./"):
            assert re.search(r"@[0-9a-f]{40}\s+#\s*\S+", line), f"{path.name}: not pinned by commit: {line.strip()}"


def test_reusable_workflows_from_one_standards_tag():
    shas = set()
    for path in WF.glob("*.y*ml"):
        for m in re.finditer(rf"{re.escape(STANDARDS)}\S+@([0-9a-f]{{40}})\s+#\s*(v\S+)", path.read_text()):
            shas.add(m.groups())
    assert len(shas) == 1, shas


@pytest.mark.parametrize("path", sorted(WF.glob("*.y*ml")), ids=lambda p: p.name)
def test_least_permissions_and_timeouts(path):
    wf = load(path.name)
    assert wf.get("permissions") == {"contents": "read"}, path.name
    for name, job in wf["jobs"].items():
        if "uses" not in job:  # a reusable workflow sets its own timeouts
            assert job.get("timeout-minutes"), f"{path.name}: {name} has no timeout"


def test_ci_triggers_and_concurrency():
    wf = load("ci.yml")
    assert set(wf["on"]) == {"pull_request", "push", "schedule", "workflow_dispatch"}
    assert wf["on"]["push"] == {"branches": ["main"]}
    assert wf["on"]["pull_request"]["types"] == ["opened", "synchronize", "reopened", "ready_for_review"]
    assert wf["on"]["schedule"] == [{"cron": "17 2 * * 0"}]  # home-inventory's Sunday slot
    assert "pull_request" in wf["concurrency"]["cancel-in-progress"]  # a release on main is never cancelled


def test_drafts_run_nothing_and_everything_hangs_off_changes():
    jobs = load("ci.yml")["jobs"]
    assert "draft" in jobs["changes"]["if"] and "draft" in jobs["ci-result"]["if"]
    for name, job in jobs.items():
        if name not in ("changes", "ci-result"):
            needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
            assert "changes" in needs, name


def test_ci_result_gates_every_job():
    jobs = load("ci.yml")["jobs"]
    gate = jobs["ci-result"]
    assert set(gate["needs"]) == set(jobs) - {"ci-result"}
    assert gate["if"].startswith("always()")


def test_push_to_main_runs_only_privacy_and_the_release():
    jobs = load("ci.yml")["jobs"]
    on_push = {n for n, j in jobs.items() if "github.event_name != 'push'" not in j.get("if", "")
               and "outputs." not in j.get("if", "")}
    assert on_push == {"changes", "privacy-denylist", "ci-result"}
    assert jobs["release"]["if"] == "needs.changes.outputs.release == 'true'"
    assert jobs["images"]["if"] == "needs.release.outputs.created == 'true'"
    assert "privacy-denylist" in jobs["release"]["needs"]


def test_slow_jobs_are_gated_on_their_group():
    jobs = load("ci.yml")["jobs"]
    groups = yaml.safe_load((ROOT / ".github/changes.yaml").read_text())["groups"]
    gated = {"backend": "server", "frontend": "frontend", "integration": "app", "hassfest": "integration",
             "hacs": "integration", "standalone-build": "image", "app-build": "image"}
    for job, group in gated.items():
        assert group in groups
        assert jobs[job]["if"] == f"needs.changes.outputs.{group} == 'true'", job
        assert jobs["changes"]["outputs"][group]


def test_changes_groups(tmp_path):
    """changes.py: a docs-only PR starts nothing slow; the frontend starts its groups; an unknown file starts all."""
    def run(*files):
        repo = tmp_path / str(len(list(tmp_path.iterdir())))
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@users.noreply.github.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@users.noreply.github.com"}
        sh = lambda *a: subprocess.run(a, cwd=repo, env=env, check=True, capture_output=True, text=True).stdout  # noqa: E731
        sh("git", "init", "-q")
        (repo / ".github").mkdir()
        (repo / ".github/changes.yaml").write_text((ROOT / ".github/changes.yaml").read_text())
        sh("git", "add", ".")
        sh("git", "commit", "-qm", "base")
        for f in files:
            (repo / f).parent.mkdir(parents=True, exist_ok=True)
            (repo / f).write_text("x")
        sh("git", "add", ".")
        sh("git", "commit", "-qm", "change")
        out = subprocess.run(["python3", str(ROOT / ".github/scripts/changes.py")], cwd=repo, check=True, text=True,
                             capture_output=True, env={**env, "EVENT": "pull_request", "BASE": "HEAD~1", "HEAD": "HEAD"}).stdout
        return {k for k, v in (ln.split("=") for ln in out.split()) if v == "true"}

    assert run("docs/x.md", "VERSION") == set()
    assert run("home_inventory/frontend/src/a.tsx") == {"frontend", "app", "image"}
    assert run("regression/run.py") == {"regression"}
    assert run("somewhere/new.txt") >= {"server", "frontend", "app", "integration", "image"}


def test_images_build_from_the_release_tag():
    wf = load("images.yml")
    assert set(wf["on"]) == {"workflow_call", "workflow_dispatch"}
    steps = wf["jobs"]["images"]["steps"]
    assert steps[0]["with"]["ref"] == "v${{ inputs.version }}"
    assert wf["jobs"]["images"]["permissions"] == {"contents": "read", "packages": "write"}
    assert {m["arch"] for m in wf["jobs"]["images"]["strategy"]["matrix"]["include"]} == {"aarch64", "amd64"}


def test_codeql_weekly_and_on_demand_only():
    wf = load("codeql.yml")
    assert set(wf["on"]) == {"schedule", "workflow_dispatch"}
    assert wf["on"]["schedule"] == [{"cron": "17 4 * * 0"}]


def test_no_other_workflow_runs_on_push_or_pull_request():
    for path in WF.glob("*.y*ml"):
        on = load(path.name)["on"]
        if path.name == "dependabot-automerge.yml":
            assert set(on) == {"pull_request"}
        elif path.name != "ci.yml":
            assert not {"push", "pull_request", "pull_request_target"} & set(on), path.name


def test_dependabot_weekly_on_sunday_and_grouped():
    cfg = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert {u["package-ecosystem"] for u in cfg["updates"]} == {"github-actions", "pip", "npm"}
    for u in cfg["updates"]:
        assert u["schedule"] == {"interval": "weekly", "day": "sunday", "time": "00:30"}, u["package-ecosystem"]
        assert all(g["update-types"] == ["minor", "patch"] for g in u["groups"].values())


def test_versions_and_standard_files():
    assert subprocess.run(["python3", ".github/scripts/check_versions.py"], cwd=ROOT, capture_output=True).returncode == 0
    std = yaml.safe_load((ROOT / ".github/standard.yaml").read_text())
    assert std["standard"] == "1.2.0" and std["weekly_slot"] == "Sun 02:17"
    assert all(b["concept"] and b["what"] for b in std["beyond"])
    assert all(e["rule"] and e["reason"] for e in std["exceptions"])

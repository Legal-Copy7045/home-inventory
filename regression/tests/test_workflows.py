"""The GitHub Actions set-up keeps to the owner's rules (minutes are limited)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parents[2] / ".github/workflows"


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


def test_ci_runs_on_ready_pull_requests_weekly_and_on_demand_only():
    ci = load("ci.yaml")
    assert set(ci["on"]) == {"pull_request", "schedule", "workflow_dispatch"}
    assert "ready_for_review" in ci["on"]["pull_request"]["types"]
    for name, job in ci["jobs"].items():
        assert "draft" in str(job.get("if", "")), f"job {name} runs on draft pull requests"


def test_slow_ci_jobs_are_gated_on_what_changed():
    jobs = load("ci.yaml")["jobs"]
    for name in ("integration", "app-build", "standalone-build", "backend"):
        assert jobs[name].get("needs") == "changes" and "needs.changes.outputs" in jobs[name]["if"], name


def test_release_still_runs_when_the_version_changes_on_main():
    rel = load("release.yaml")
    assert rel["on"]["push"]["branches"] == ["main"]
    assert "home_inventory/config.yaml" in rel["on"]["push"]["paths"]


def test_codeql_is_weekly_and_on_demand():
    assert set(load("codeql.yml")["on"]) == {"schedule", "workflow_dispatch"}


def test_privacy_guard_checks_every_push_and_pull_request():
    # The project's CLAUDE.md requires it; it takes seconds.
    assert {"push", "pull_request"} <= set(load("privacy.yml")["on"])


def test_dependabot_monthly_and_grouped():
    cfg = yaml.safe_load((WF.parent / "dependabot.yml").read_text())
    for u in cfg["updates"]:
        assert u["schedule"]["interval"] == "monthly" and u.get("groups"), u["package-ecosystem"]

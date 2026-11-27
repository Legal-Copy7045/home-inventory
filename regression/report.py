#!/usr/bin/env python3
"""Render a run's reports (with any reviewer findings) and log the run.

    python3 regression/report.py [RUN_DIR]     # latest run when left out

The same as `run.py report RUN_DIR` followed by `run.py log RUN_DIR`. Publishing
the HTML as a private artifact is done by Claude Code (see the skills); put the
link in regression/published.yaml before logging so RUNS.md carries it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    target = argv[:1]
    run.main(["report", *target])
    return run.main(["log", *target])


if __name__ == "__main__":
    raise SystemExit(main())

# Quality checks

Three repeatable commands for Claude Code, kept as project skills in
`.claude/skills/`:

| Command | What it does |
|---|---|
| `/regression` | Code quality and supply chain, every test suite, a browser pass over every page and API endpoint for each role, read-only checks of the published release, the manual checklist, and a security audit by three independent reviewers. |
| `/walkthrough` | Crawls every screen for each role at desktop and phone width, runs automated UX checks, and has two independent reviewers (desktop, phone) propose merges, renames, moves, removals and flow and phone fixes. |
| `/full-check` | Both at once, with one summary. |

None of them changes product code. Each publishes a private HTML report (the
same link every run, kept in `published.yaml`) and adds a line to
[`RUNS.md`](RUNS.md) or [`WALKTHROUGHS.md`](WALKTHROUGHS.md).

## Running the stages yourself

From the repository root, with the App's Python dependencies, Node, and
Playwright with Chromium installed:

```bash
python3 regression/run.py                       # ci, unit, page, live, manual
python3 regression/run.py --stage page          # one stage (repeat --stage for more)
python3 regression/run.py --stage walkthrough   # the walkthrough crawl only
python3 regression/run.py --with-walkthrough    # both, in parallel
python3 regression/run.py summary               # summary of the latest run
python3 regression/run.py manual phone-camera-photo pass --note "Pixel, Companion app"
```

The reviewers and publishing are done by Claude Code through the skills; the
runner alone produces the reports without reviewer findings.

Security findings are counted by severity in `RUNS.md` and keyed by a hash in
`ids/findings.yaml`; their details are only in the private report, because this
repository is public.

Each run gets a folder in `regression/out/` (not committed): stage results as
JSON, reports, screenshots, and the throwaway Apps' data and logs.

## Files

| Path | What it is |
|---|---|
| `run.py` | The runner. |
| `qc/stages.py` | The stages: `ci` (lint, security scans, dependency audit), `unit` (every test suite), `page` (every page and API endpoint per role), `live` (read-only), `manual`, and `walkthrough`. The names before 0.43.1 (`quality`, `tests`, `browser`) still work with `--stage`. |
| `report.py` | Renders a run's reports with the reviewers' findings and logs it (`run.py report` + `run.py log`). |
| `qc/server.py`, `qc/seed_extra.py` | A throwaway App on its own ports and data folder, with the demo household. |
| `browser/pages.mjs` | Regression page pass (Playwright). |
| `walkthrough/crawl.mjs` | Walkthrough crawl (Playwright). |
| `qc/render.py` | Both HTML reports. |
| `ids/<stage>.yaml` | Stable check IDs: never renumbered or reused; checks that go away are marked `retired`. `findings.yaml` and `ux-findings.yaml` do the same for reviewer findings. |
| `catalogue/*.yaml` | Features and the regular expressions (over `stage:ID key`) that say which checks cover them. |
| `manual.yaml`, `manual-log.yaml` | Checks only a person can do, and their recorded results. |
| `access.yaml` | Which roles must be refused which API routes; the API sweep fails if one gets in. |
| `accepted.yaml` | Scanner findings accepted on purpose, each with its reason. |
| `schemas/` | The JSON the reviewers return. |
| `tests/` | Tests for the runner (`pytest -c regression/tests/pytest.ini regression/tests`). |

## Check keys

IDs are short (`T0042`); keys say what was checked:

- `ci`: `ruff:server`, `bandit:server`, `audit:python`, `secrets:gitleaks-history`...
- `unit`: `server:tests/test_items.py::test_move`, `frontend:src/match.test.ts::...`, `integration:...`, `qc:...`
- `page`: `page:<role>:<route>`, `api:<ingress|api>:<role|anon>:GET <path>`, `headers:ingress:<header>`, `e2e:smoke`
- `live`: `release:current-version`, `image:<arch>`, `app:health`
- `manual`: the key in `manual.yaml`
- `walkthrough`: `<check>:<desktop|phone>:<route>`

# Home Inventory: instructions for Claude Code

Home Assistant App (FastAPI + SQLite backend, React/TypeScript frontend) and a HACS companion integration. Architecture, the security model and the development workflow live in `docs/`. Follow them; this file only adds what is not written there. The repository follows the owner's project standard (`.github/standard.yaml`: version, what is done beyond it, exceptions).

## Project checks

Build, test and lint commands are in `docs/development.md`, and CI runs them in `.github/workflows/ci.yml`. For a change, run the ones it touches:

- **Before a PR is marked ready:** `python3 regression/run.py check` (lint, versions, scans and every test suite; one line per stage, logs in `regression/out/logs/`).
- **Backend:** `ruff check home_inventory_server tests` and `pytest -q` in `home_inventory/server`.
- **Frontend:** `npm run build` in `home_inventory/frontend`. It also type-checks.
- **Integration tests:** `pytest -q` from the repo root, with the HA test packages.
- **Browser smoke test:** `tests/e2e/smoke.mjs`, for user-facing flows.
- **Versions:** every merge to `main` bumps `VERSION` (and the places in `.github/versions.yaml`), with a `## [x.y.z] - date` section in `CHANGELOG.md` (the release notes) and a `## x.y.z` entry in `home_inventory/CHANGELOG.md` (what Home Assistant shows). `python3 .github/scripts/check_versions.py` checks them. Merging publishes release `v<version>` and the images (see `docs/publishing.md`).
- **Pull requests:** open them as drafts and mark them ready once the checks above pass, so CI runs once. `ci-result` is the only required check (see "CI and its cost" in `docs/development.md`).
- **Quality commands:** `/regression`, `/walkthrough` and `/full-check` (the owner's global skills; this project's profile is `regression/profile.yaml`, details in `regression/README.md`). They report and never fix product code.
- **Database changes:** follow "Database changes" in `docs/development.md`. A migration bumps at least the minor version.

## CodeRabbit (when available)

Review each complete logical change with CodeRabbit **when it is installed and authenticated**, following `docs/claude/coderabbit.md`. If it can't run, skip it and say why in the completion report; never claim a review that didn't finish.

### Completion report

End each development task with:

- **Changes:** what was implemented.
- **Checks:** build, test and lint results, including anything not run and why.
- **CodeRabbit:** whether a review ran and completed (and how many rounds), or why it was skipped.
- **Findings:** counts by severity, which ones were fixed, and every finding left open with its justification.

## Privacy (this repository is public)

Never commit anything that identifies the owner, his family or his home, in files, commit messages, PR text or comments:
real names (use made-up ones: Morgan, Casey, Alex, Jordan, Sam), real towns, streets or addresses (use Lakeview, Hillcrest),
real coordinates (use made-up ones far from home), real NFC or Bluetooth band IDs, MAC addresses, VINs, entity or device IDs and
hostnames from the real Home Assistant, email addresses, Wi-Fi names, or credentials of any kind.

- Commit as `Legal-Copy7045 <323378128+Legal-Copy7045@users.noreply.github.com>`.
- The **privacy guard** (`.github/scripts/privacy_guard.py`, run by CI on every pull request and on `main`) checks: no-reply
  commit addresses, no credentials, no photo metadata, and on `main` nothing matching the private terms in the
  `PRIVACY_DENYLIST` secret (`privacy` environment). If it fails, replace the value with a made-up one. Never put the real
  value back to make a test pass, and never print or copy the secret.

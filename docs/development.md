# Development, testing and releasing

## Backend

```bash
cd home_inventory/server
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest -q                 # 90 tests
ruff check home_inventory_server tests
```

Run locally with a fake Ingress identity:

```bash
HINV_DATA_DIR=/tmp/hinv HINV_TRUSTED_PROXIES=127.0.0.1 HINV_AI_PROVIDER=none python -m home_inventory_server
curl -H 'X-Remote-User-Id: dev' -H 'X-Remote-User-Display-Name: Dev' localhost:8099/api/v1/me
```

### Database changes

1. Edit `home_inventory_server/models.py`.
2. Generate a migration:

   ```python
   from alembic import command
   from home_inventory_server.db import alembic_config
   command.revision(alembic_config("sqlite:////tmp/x.sqlite3"), message="describe change", autogenerate=True)
   ```

3. Review it (SQLite uses batch mode). `test_migrations_match_models` fails if the models and migrations disagree.

## Frontend

```bash
cd home_inventory/frontend
npm ci
npm run dev      # proxies /api to localhost:8099
npm test         # unit tests (vitest)
npm run build    # type-checks and builds into ../server/home_inventory_server/static
```

Pages other than the dashboard, search, browse and quick photos load on first use (`page()` in `App.tsx`) and are fetched in the background once the app is idle, so they still open offline. The barcode decoder (`@zxing`) loads only when a scan needs it. `match_cases.json` holds word-search cases shared with the server's `test_textmatch.py`, so both search the same way.

The REST API is split by area: `api/routes.py` (identity, locations, containers, items, loans, categories) plus `attachments`, `scans`, `lookup` (barcode, links, search, labels), `maps`, `admin`, and the feature modules. Shared helpers (`_state`, `_err`, `_get_or_404`...) live in `routes.py`. `test_authz_routes.py` walks every router in the package. Background AI jobs (manuals, documents) go through `services/jobs.py`, a pool of three workers.

## Integration tests

```bash
pip install pytest-homeassistant-custom-component
pip install "home-assistant-frontend==<version from HA's frontend manifest>"
pip install -e home_inventory/server
pytest -q            # from the repository root; starts a real App process
```

## Browser smoke test

```bash
HINV_DATA_DIR=/tmp/e2e HINV_TRUSTED_PROXIES=127.0.0.1 HINV_ADMIN_USERS=e2e-user HINV_INGRESS_PORT=18099 HINV_API_PORT=18100 \
  HINV_BARCODE_OPEN_FACTS=false HINV_BARCODE_UPCITEMDB=false HINV_AI_PROVIDER=none python -m home_inventory_server &
node tests/e2e/smoke.mjs /tmp/screenshots
```

## CI and its cost

This repository follows the owner's project standard (`.github/standard.yaml` names the version and lists what is done
better or differently). GitHub Actions minutes are limited, so CI (`.github/workflows/ci.yml`) runs once per change:

- **Open pull requests as drafts.** Drafts run nothing. Run `python3 regression/run.py check` (lint, versions, scans and
  every test suite) and the checks above that your change touches, then mark the PR ready: CI runs once, and again only
  for new pushes.
- **Only what a PR touches.** The `changes` job (`.github/scripts/changes.py`, groups in `.github/changes.yaml`) starts
  each slow job only when the PR changes what it tests: `backend` (App tests), `frontend`, `integration` (Home Assistant
  tests and the browser smoke test), `hassfest`/`hacs`, and the two image builds (`app-build` scans the image with
  Trivy). `quick` (lint, versions, the App linter, the runner's tests; dependency audits when a lockfile changes), the
  privacy guard and gitleaks over the PR's commits always run.
- **`ci-result` is the only required check.** It fails when any job failed, so jobs can be reshaped without touching
  branch protection.
- **Merging to `main`** re-tests nothing: it checks the private terms (`PRIVACY_DENYLIST`, in the `privacy`
  environment, main only) and, when `VERSION` has no tag yet, releases (see [publishing.md](publishing.md)).
- **Weekly full run** on Sunday 02:17 UTC, and on demand (Actions → CI → Run workflow): every job, the whole history's
  secret scan and the dependency audits. **CodeQL** runs Sunday 04:17 UTC and on demand; GitHub's "default setup" for
  code scanning must stay switched off (Settings → Code security), or both run.
- **Dependabot** opens grouped minor/patch pull requests weekly on Sunday; they merge themselves once `ci-result` passes
  (`dependabot-automerge.yml`). Major updates wait for review. Versions Dependabot can't track (base images, toolchains)
  are listed in `.github/upstreams.yaml`.

Security trade-off: a problem CodeQL would find can take up to a week to show up instead of appearing on the pull
request. The secret scan runs on every pull request, the dependency audits whenever a lockfile changes.

## Releasing

See [publishing.md](publishing.md) for version bumps, GitHub releases, prebuilt images and the HACS listing.

Versions follow semantic versioning. A database migration bumps at least the minor version.

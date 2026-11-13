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
HINV_DATA_DIR=/tmp/e2e HINV_TRUSTED_PROXIES=127.0.0.1 HINV_INGRESS_PORT=18099 HINV_API_PORT=18100 \
  HINV_AI_PROVIDER=none python -m home_inventory_server &
node tests/e2e/smoke.mjs /tmp/screenshots
```

## Releasing

See [publishing.md](publishing.md) for version bumps, GitHub releases, prebuilt images and the HACS listing.

Versions follow semantic versioning. A database migration bumps at least the minor version.

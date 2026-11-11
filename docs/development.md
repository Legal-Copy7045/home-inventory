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
npm run build    # type-checks and builds into ../server/home_inventory_server/static
```

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

1. Update `version` in `home_inventory/config.yaml`, `custom_components/home_inventory/manifest.json`, `home_inventory/server/pyproject.toml` and `home_inventory_server/__init__.py`.
2. Add an entry to `home_inventory/CHANGELOG.md`.
3. Merge to `main`. Home Assistant offers the update to App users, and HACS offers the integration update.
4. Optional: publish a GitHub release to run `.github/workflows/release.yaml`, which pushes prebuilt multi-arch images to GHCR. Then add `image: "ghcr.io/legal-copy7045/{arch}-home-inventory"` to `config.yaml`, so devices download images instead of building them.

Versions follow semantic versioning. A database migration bumps at least the minor version.

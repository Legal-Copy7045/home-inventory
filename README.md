# Home Inventory for Home Assistant

A household inventory that lives inside Home Assistant. Photograph things and let AI suggest what they are. Scan barcodes and QR-labelled boxes. Track where everything is: shelves, drawers, nested boxes, sheds. Find things by asking "Where are my axle stands?".

![Home Inventory logo](home_inventory/logo.png)

It is built as a **Home Assistant App** (formerly "add-on"), opened from the Home Assistant sidebar through Ingress. A **companion integration** adds Home Assistant sensors, actions and automation events.

## Features

- **Items**: photos, AI identification (always reviewed by you), category, maker, model, barcode, serial, purchase price/date/retailer, warranty, receipts and manuals.
- **Quantities and duplicates**: one *product* (what a thing is) can have many *holdings* (where its units are). "Extension cords: 4 in total — 2 in Shed › Box 15, 1 in the garage drawer, 1 in the utility room." Similar items are never merged by accident.
- **Expiry dates and consumables**: use-by dates per holding, low-stock alerts per product, quick "used one" buttons.
- **Locations**: unlimited nesting (Property › Shed › Right side › Ground level), loose items on shelves and benches, nested containers, cycle prevention.
- **Containers**: permanent QR codes, printable PDF labels (Avery A4/Letter sheets, label printers), photograph the open box and review AI-suggested contents.
- **Scan mode**: scan a box label to open it, then scan product barcodes to add, remove or move things with one tap.
- **Barcodes**: UPC-A/E, EAN-13/8, GTIN-14. Your own inventory is checked first ("You own 3: 2 in Box 15, 1 in the garage"), then free product catalogs.
- **Natural-language search**: AI may interpret the question, but the answer is built only from your data, so locations and quantities are never invented.
- **Moves, loans and temporary locations**: partial moves, "put back" to the normal place, borrowers and due dates, overdue tracking, statuses (in use, missing…).
- **Visual maps**: photograph a wall or shelving and pin containers and locations on it.
- **Family access**: everyone signs in with their Home Assistant account; administrator, member and read-only roles; full audit history with who did what; trash and restore.
- **Home Assistant native**: sensors (totals, value, loans, overdue, expiring, low stock…), actions (search, item location, container contents, move, adjust quantity) and events for automations.

## Installation (Home Assistant OS or Supervised)

1. **Add the repository**: Settings → Apps → App store → ⋮ → **Repositories** → add `https://github.com/Legal-Copy7045/home-inventory`.
2. **Install** "Home Inventory" from the store. The first install builds the image on your device, which takes a few minutes.
3. **Configure** (optional), on the App's *Configuration* tab. The default AI provider, `home_assistant`, uses an AI Task entity you already have, such as Google Gemini. No extra key is needed.
4. **Start** the App, and enable *Start on boot*, *Watchdog* and *Show in sidebar*.
5. **Companion integration**: install it through HACS (HACS → ⋮ → Custom repositories → this URL, type *Integration*). Restart Home Assistant. The App is then offered under Settings → Devices & services → **Discovered**. Click *Configure*.
6. Open **Inventory** in the sidebar.

On **Home Assistant Container or Core** there is no Supervisor. See [docs/install-container-core.md](docs/install-container-core.md).

## Documentation

| Topic | |
|---|---|
| Using the App | [home_inventory/DOCS.md](home_inventory/DOCS.md) |
| Architecture | [docs/architecture.md](docs/architecture.md) |
| Security model | [docs/security.md](docs/security.md) |
| Storage, backup and restore, upgrades | [docs/storage-and-backup.md](docs/storage-and-backup.md) |
| AI configuration | [docs/ai.md](docs/ai.md) |
| Barcode lookup providers | [docs/barcode-providers.md](docs/barcode-providers.md) |
| Home Assistant entities, actions and events | [docs/home-assistant.md](docs/home-assistant.md) |
| Container/Core installation | [docs/install-container-core.md](docs/install-container-core.md) |
| Development, testing and releasing | [docs/development.md](docs/development.md) |

## Repository layout

```
repository.yaml                 Home Assistant App repository manifest
home_inventory/                 The App
  config.yaml, build.yaml       App manifest and base images
  Dockerfile                    Multi-stage build (frontend + Python)
  server/                       FastAPI backend, SQLAlchemy models, Alembic migrations, tests
  frontend/                     React + TypeScript UI (Vite)
custom_components/home_inventory/  Companion integration (HACS)
hacs.json                       HACS manifest
deploy/standalone/              docker-compose for Container/Core installs
tests/integration/              Integration tests against a real App process
tests/e2e/                      Browser smoke test (Playwright)
docs/                           Documentation
```

## License

MIT

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

## Before you install

- **Hobby project, no warranty.** Home Inventory is maintained in spare time and comes with no support guarantee (MIT licence). Bugs and ideas are welcome in [GitHub issues](https://github.com/Legal-Copy7045/home-inventory/issues). Please include the App log and your Home Assistant version.
- **Back up first.** Take a Home Assistant backup before installing or updating. The App keeps its own database snapshot before every database upgrade (see [storage and backup](docs/storage-and-backup.md)), but a full backup is your safety net.
- **Local only.** Your inventory and photos stay on your Home Assistant. Nothing is sent anywhere unless you turn on AI identification or online barcode lookups, and those send only what that request needs (the photo, barcode or search question). See [security](docs/security.md) and [AI configuration](docs/ai.md).
- **Requirements:** Home Assistant OS or Supervised for the App (64-bit: `aarch64` or `amd64`, e.g. Raspberry Pi 4/5, Home Assistant Green/Yellow, x86 mini PCs). Home Assistant 2025.1 or newer for the companion integration. AI features need an AI Task entity (e.g. the Google Gemini integration's free tier) or an API key.

## Installation (Home Assistant OS or Supervised)

[![Add the Home Inventory App repository to your Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FLegal-Copy7045%2Fhome-inventory)
[![Open the companion integration in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Legal-Copy7045&repository=home-inventory&category=integration)

1. **Add the repository**: use the first button above, or go to Settings → Apps → App store → ⋮ → **Repositories** and add `https://github.com/Legal-Copy7045/home-inventory`.
2. **Install** "Home Inventory" from the store. This takes a few minutes. Where no prebuilt image is available for a version, your device builds it.
3. **Configure** (optional), on the App's *Configuration* tab. The default AI provider, `home_assistant`, uses an AI Task entity you already have, such as Google Gemini. No extra key is needed.
4. **Start** the App, and enable *Start on boot*, *Watchdog* and *Show in sidebar*.
5. **Companion integration**: use the second button above, or in HACS go to ⋮ → Custom repositories → this URL, type *Integration*, then download it. Restart Home Assistant. The App is then offered under Settings → Devices & services → **Discovered**. Click *Configure*.
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
| NFC tags (phones, writing, security) | [docs/nfc.md](docs/nfc.md) |
| Restocking, shopping list and retailers | [docs/replenishment.md](docs/replenishment.md) |
| Development, testing and releasing | [docs/development.md](docs/development.md) |
| Publishing releases and the HACS listing | [docs/publishing.md](docs/publishing.md) |

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

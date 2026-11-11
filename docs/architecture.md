# Architecture

## Components

```
┌─────────────────────────── Home Assistant OS ────────────────────────────┐
│                                                                           │
│  Browser / companion app ──HTTPS──► Home Assistant Core (auth, frontend)  │
│                                          │                                │
│                     Ingress (session)    │  Supervisor discovery,         │
│                                          ▼  AI Task, users                │
│                                   Supervisor ─────────────┐               │
│                                    172.30.32.2            │               │
│                                          │ X-Remote-User-* │               │
│                                          ▼                 │               │
│        ┌──────────── Home Inventory App (container) ──────┴──────┐        │
│        │ :8099 Ingress listener  (UI + API, trusts only Supervisor)│       │
│        │ :8100 API listener      (bearer token, for integration)   │       │
│        │ FastAPI · SQLAlchemy · Alembic · SQLite · media files      │       │
│        │ /data  (persistent, included in HA backups)                │       │
│        └───────────────────────────▲───────────────────────────────┘       │
│                                    │ token, SSE event stream               │
│  Companion integration (custom_components/home_inventory, in HA Core)     │
│   sensors · actions · events · diagnostics · (Container/Core: panel proxy) │
└───────────────────────────────────────────────────────────────────────────┘
```

**The App owns all inventory data and business logic.** The integration holds no inventory state. It reads statistics, calls the App's API for actions, and re-fires the App's events on the Home Assistant bus.

## Why this split

| Need | Mechanism |
|---|---|
| Install, start/stop, auto-start, logs, watchdog, upgrade | Supervisor App lifecycle (`config.yaml`: `boot: auto`, `watchdog`) |
| Sidebar entry, authentication | Ingress (`ingress: true`, `panel_icon`, `panel_title`) |
| Who is using the app | Supervisor's `X-Remote-User-*` headers, trusted only from the Ingress proxy address |
| Persistent storage, backups | `/data` (App data directory, included in HA backups; `backup: cold`) |
| Sensors, actions, events, automations | Companion integration (config entry, coordinator, services, events) |
| Integration ↔ App connection | Supervisor discovery (`discovery: [home_inventory]`) → `async_step_hassio` |
| AI without extra keys | Home Assistant AI Task (`homeassistant_api: true`) |
| Container/Core installs | Companion container + integration-provided sidebar panel with an authenticated proxy |

## Backend

- **FastAPI**: one process runs two Uvicorn listeners that share state: Ingress on 8099 and the integration API on 8100. Neither port is published on the host.
- **SQLite** in WAL mode with foreign keys on. **SQLAlchemy 2** models. **Alembic** migrations run automatically at start, after a consistent snapshot of the existing database.
- **Media**: photos are re-encoded (EXIF/GPS stripped) with thumbnails. Documents (PDF) are stored under `/data/media` with random names.
- **Events**: domain operations queue events on the database session. They are published only after commit, then streamed to the integration over Server-Sent Events.
- **Background jobs**: warranty and expiry notices, and cleanup of photos uploaded but never attached.

### Data model

- `products`: identity of a thing (name, category, manufacturer, model, GTIN/barcode, SKU, low-stock threshold, catalog and AI metadata, identification status).
- `items`: a physical holding of a product (quantity, current placement, normal "home" placement, status, serial, purchase, warranty, expiry). Three identical extension cords in three places are 1 product and 3 items. Two in the same box are 1 item with quantity 2.
- `locations`: arbitrary tree (`parent_id`).
- `containers`: permanent `qr_code`, number, type; placed in a location **or** another container (database check constraints), with an optional home placement while temporarily moved.
- `attachments`: photos, receipts, manuals, warranty documents.
- `container_scans`: AI suggestions awaiting review. Nothing reaches the inventory until a person confirms.
- `loans`, `location_maps`, `map_markers` (normalised 0–1 coordinates), `history_events` (append-only audit), `barcode_cache`, `users`, `categories`, `settings`.

Effective locations are computed rather than stored. Moving a container therefore moves everything inside it, and nested contents can never fall out of sync. Cycles (a location under its own descendant, a box inside a box it contains) are rejected by the service layer and backed by database constraints.

## Frontend

React 19 + TypeScript, built with Vite. All URLs are relative, so the same build works under Ingress (`/api/hassio_ingress/<token>/`) and behind the integration's panel proxy. Routing uses the URL hash. The UI is mobile-first, with large touch targets and a bottom navigation bar on phones, and a sidebar on desktop. Barcode and QR decoding uses the browser's BarcodeDetector where available and ZXing otherwise, with a decode-from-photo fallback for non-HTTPS sessions.

## Integration

- Config flow: `hassio` discovery (App) or manual URL + token (Container/Core). Re-authentication is supported. There is a single instance.
- `DataUpdateCoordinator` polls `/api/v1/stats` (5 min by default) and refreshes immediately when an event arrives.
- Event listener: a long-lived SSE connection with exponential back-off.
- 11 summary sensors and 1 connectivity binary sensor. **No entity per inventory item.**
- Actions that return data (`search`, `get_item_location`, `get_container_contents`) and actions that change it (`move_item`, `move_container`, `adjust_quantity`, `refresh`). The calling Home Assistant user is passed through for permissions and audit.
- Diagnostics with the token redacted.
- Removing the integration never deletes inventory data.

## Functionality that depends on install type

| Feature | HA OS / Supervised | Container / Core |
|---|---|---|
| One-click install, updates, logs, watchdog | Supervisor | docker compose (you manage the container) |
| Sidebar access | Ingress | Integration panel (proxy) |
| User identity | Ingress headers | Integration proxy headers |
| Integration setup | Automatic discovery | Manual URL + token |
| AI Task (Gemini via HA) | Automatic | Needs `HINV_HA_URL` + `HINV_HA_TOKEN` and a shared `/media` folder |
| Included in HA backups | Yes | No: back up the container's `data` folder, or use the in-app export |
| HA administrators recognised automatically | Yes | Yes (via the proxy) |

## Not implemented (by design)

- **"Open inventory search" action**: Home Assistant actions cannot open a page in a user's browser. Use a dashboard button linking to the Inventory sidebar entry, or the `search` action with a notification.
- **AI-generated map markers**: markers are placed manually, as specified.

# Plan: security remediation and equipment management

Status: **All phases complete (0.2.0–0.7.0).** The final summary is at the end. This document records verified findings, design decisions and the phase plan. Each phase ships as its own pull request with tests, and gets its own section below once it lands.

## Ground rules

- Extend the existing architecture: FastAPI routers, SQLAlchemy models, Alembic migrations (transactional and additive only), React pages using the existing `ui.tsx` components, and aggregate Home Assistant entities in the companion integration.
- Add no new runtime dependencies unless a phase says why.
- Never destroy data. New columns are nullable or defaulted. Deletions are soft where the model already uses `deleted_at`. History tables snapshot names, so records stay readable after the source rows change.
- The backend owns the business logic. Home Assistant and the frontend only display results or call actions.

## Phase 1: security

### Findings, verified against `main` at 1cb5840

| # | Finding | Verified | Notes |
|---|---|---|---|
| 1 | First user auto-promoted to admin when the Home Assistant admin lookup returns `None` | **Yes** | `upsert_user()` sets `role_override=admin` when `ha_is_admin is None` and `_any_admin()` finds no admin in the database. `_any_admin()` ignores `admin_users`. `is_admin()` also returns `None` for a Home Assistant user created after the 5-minute cache refresh. Two first requests can both pass the check (no lock). Two first requests from the *same* user can raise a unique-constraint 500. |
| 2 | Backup restore has no size limit or archive protections | **Yes** | The route streams the upload to disk with no cap. Starlette's multipart parser also spools the whole body before the handler runs. `validate_archive` checks traversal only: no entry count, sizes, compression ratio, symlinks or allow-list. `restore_archive` uses `extractall`, deletes the live database *before* moving media, and has no rollback, so a failure mid-way leaves partial data. The schema revision is not checked, so a backup from a newer version would break start-up. |
| 3 | DNS rebinding in the web-link import | **Yes** | `_check_host()` resolves with `getaddrinfo`, then httpx resolves again when it connects. With `trust_env` on, an `HTTPS_PROXY` would also do its own resolution. |
| 4 | Whole `/media` mounted read-write | **Yes** | `map: media, read_only: false`, used only to write a temporary JPEG for AI Task. |
| 5a | No request body limit before multipart parsing | **Yes** (new) | Any authenticated user can make the server spool an arbitrarily large upload to disk. |
| 5b | No rate limits on expensive operations | **Yes** (new) | AI identification, AI search, container scans, web-link import and online barcode lookups. |
| 5c | CORS, SQL injection, command injection, file download, path traversal | Not present | No CORS middleware (same origin through Ingress). All SQL goes through the ORM, apart from fixed migration DDL. No shell calls. Uploads are re-encoded by Pillow. PDFs are served with `CSP: sandbox` and `nosniff`. `MediaStore.path()` confines paths. |
| 5d | IDOR | By design | This is a single-household app: every authenticated user sees the whole inventory, and roles gate *writes*. Customer records (Phase 3) are the first data that read-only users must not see. They get an explicit member check and tests. |
| 5e | Secrets | OK | API keys are App options and are never returned to the frontend (`/settings` reports only `ai_configured`). The integration token stays in `/data` (0600). |
| 5f | QR routing | OK | Labels carry opaque codes resolved by an authenticated API call. They contain no IDs or tokens. |

### Fixes

1. **Administrator bootstrap (fail closed).**
   - Nobody is ever promoted automatically.
   - Admin comes only from: the Home Assistant admin flag, `admin_users`, an existing administrator's role change, or a **first-run setup code**.
   - The setup code exists only while no administrator can be established: no admin in the database, none configured, and the Home Assistant admin list unavailable. It is printed in the App log, which only Home Assistant administrators can read. It is single-use, compared in constant time, limited to 5 attempts before it is rotated, held in memory only, and refused for anyone listed in `read_only_users`.
   - Claims are serialised with a lock.
   - A temporary Home Assistant failure (`None`) never changes the stored admin flag.
   - User creation copes with concurrent first requests.
2. **Restore hardening.**
   - Requests are capped by size per route by ASGI middleware, before parsing.
   - Restore upload limit is `backup_max_mb` (default 2048).
   - Archive checks: an allow-list (`manifest.json`, `inventory.sqlite3`, `media/**`), at most 200,000 entries, no duplicates, no absolute paths, traversal, backslashes or symlinks, total and per-entry uncompressed limits, and a compression-ratio limit.
   - Extraction is streamed and counts the real bytes.
   - Manifest checks: format, format version, and a schema revision that this version knows.
   - Database checks: `integrity_check`, `foreign_key_check`, required tables, and an `alembic_version` matching the manifest. The staged copy is then **migrated in staging**.
   - Only then: a safety export, an atomic directory swap with automatic rollback (including when re-opening the database fails), and cleanup in `finally`.
3. **DNS pinning.**
   - Each hop resolves once and rejects any non-global address.
   - It then connects to that exact IP: the URL host becomes the IP, the `Host` header and TLS SNI and certificate check keep the hostname, and `trust_env=False` keeps environment proxies out.
   - The connected peer address is checked against the pinned IP when the transport exposes it.
   - Redirects and image downloads go through the same function.
4. **No `/media` mount.**
   - The AI hand-off uses Home Assistant's own media API. It uploads to `media-source://media_source/local/home_inventory/.ai-tmp`, an admin-only endpoint the App's Home Assistant API access can use, then removes the file over the WebSocket (`media_source/local_source/remove`).
   - Home Assistant versions before 2025.x don't create the folder, so on a 400 the file goes to the media root with a distinctive name.
   - The App can no longer touch any file in `/media` directly.
5. **Rate limits.** In-memory per-user sliding windows: AI calls 20/min, web-link import 10/min, online barcode lookups 30/min. Exceeding one returns 429 with `Retry-After`.
6. **CI security checks.**
   - `pip-audit` for the backend, failing on known vulnerabilities with a fix.
   - `npm audit --omit=dev --audit-level=high` for the frontend.
   - Ruff `S` (bandit) rules on the backend, with the exclusions listed in `pyproject.toml`.
   - Gitleaks secret scan.
   - Trivy image scan: HIGH/CRITICAL, fixed vulnerabilities only.
   - CodeQL already runs through GitHub default setup.
   - A route-level authorization test asserts that every mutating route rejects read-only users and that the API channel requires the token.

## Phase 2: maintenance foundation (done, 0.3.0)

**As built:**
- Migration `0003`. It is additive: five new tables, plus a nullable `attachments.service_event_id` with a named FK, added by an atomic batch rebuild with FKs off, then `foreign_key_check`. To roll back, restore the automatic pre-migration snapshot in `/data/backups`. The Alembic `downgrade()` also exists.
- Endpoints (`api/maintenance.py`):
  - `GET /maintenance/presets`, `GET /maintenance/dashboard?within=`
  - `GET /items/{id}/maintenance`, `PUT|DELETE /items/{id}/equipment` (with `current_hours`)
  - `POST /items/{id}/maintenance-tasks`, `PATCH|DELETE /maintenance-tasks/{id}`, `POST /maintenance-tasks/{id}/complete|skip|defer|not-applicable`
  - `POST /items/{id}/meter-readings`, `POST /items/{id}/service-events`, `DELETE /service-events/{id}`
  - `/stats` gains `maintenance_overdue` and `maintenance_due_soon`.
- Tests: `tests/test_maintenance.py` (15), the route authorization walk, and the e2e maintenance flow.


New tables:
- `equipment_profiles`, one per item and optional:
  - Fields: `equipment_type`, `engine_manufacturer`, `engine_model`, `engine_serial`, `fuel_type`, `meter` (`none|hours|cycles|both`), `specs` (a JSON list of `{label, value, kind: spec|part, part_number}` that holds oil spec and capacity, spark plug, filters and any other parts), and `notes`.
  - Manufacturer, model and serial reuse `products.manufacturer`, `products.model_number` and `items.serial_number` rather than duplicating them.
- `maintenance_tasks`:
  - The task: `item_id`, `title`, `instructions`.
  - Any mix of intervals: `interval_days`, `interval_hours`, `interval_cycles`, or none (manual, with `next_due_on`).
  - Due-soon windows: `due_soon_days` (14) and `due_soon_hours` (5).
  - The last and next thresholds.
  - `deferred_until`, `not_applicable`, `active`, and `deleted_at`.
- `meter_readings`: `item_id`, `kind`, `value`, `recorded_at`, `source` (`manual|estimated|service|sensor`), the user, and an optional `service_event_id`. Readings may not go backwards unless the source is marked as a correction.
- `service_events` (history):
  - What was done: `item_id`, `task_id` (SET NULL), `task_title` snapshot, `performed_on`, `hours`, `cycles`, `outcome` (`completed|skipped|deferred|not_applicable`), `work_performed`, `notes`.
  - Who and what it cost: `performed_by`, `labor_cost`, `currency`, `created_by_id`.
  - `repair_job_id`, added in Phase 3.
  - `snapshot` JSON holding the equipment name, serial and owner at the time.
  - `deleted_at`.
- `service_parts`: `service_event_id`, an optional `item_id` (stock to deduct, with confirmation), `description`, `part_number`, `quantity` (Numeric), `unit_cost`.
- `attachments.service_event_id` (new nullable FK) for photos and receipts.

Equipment-type presets live in code as starting points, e.g. small engine (oil 50 h, air filter 25 h, spark plug annually, winterize annually), generator (monthly exercise run) and chainsaw (chain sharpening by cycles). They are copied into editable tasks, so the schema isn't tied to lawn equipment.

Due state is computed, not stored: `overdue`, `due_soon`, `ok`, `deferred`, `not_applicable`, or `manual` when there's no threshold. Whichever threshold comes first wins. Completing a task records a service event and its parts, and moves the task's last and next thresholds forward from the *completion* date and hours. Deleting or editing a task never touches past service events.

API: `/items/{id}/equipment` (GET/PUT/DELETE), `/items/{id}/maintenance-tasks`, `/maintenance-tasks/{id}` (PATCH/DELETE, plus `complete`, `skip`, `defer`), `/items/{id}/meter-readings`, `/items/{id}/service-events` (with backdated history), and `/maintenance/dashboard`.

UI:
- Item page: an Equipment section with a meter reading, due tasks, a "Log service" sheet, history and specs.
- A new **Maintenance** page with overdue tasks, tasks due in 7/30/90 days, equipment with no records, recent activity, and a household/customer filter.

## Phase 3: customers and repair jobs (done, 0.4.0)

**As built:**
- **Data model.** Ownership is a separate `item_owners` table (`item_id` PK, `customer_id` RESTRICT) rather than a column on `items`. This avoided rebuilding the central table, and it means ownership can only change through `POST /items/{id}/owner` with `confirm: true` or at creation via `POST /customers/{id}/equipment`.
  - `split_item` copies the owner, so partial moves and loans keep customer stock customer-owned.
  - Household totals, value, warranty and expiry alerts and low-stock totals filter with `household_only()`.
- **Migration `0004`:** additive. Five tables; `attachments.repair_job_id` and `service_events.repair_job_id` are added by atomic batch rebuilds that are safe to re-run.
- **Privacy:** every customer and job route needs the member role, including reads.
  - Read-only users get an anonymous owner block.
  - History entries for customers, repairs and ownership are hidden from them.
  - The HA event carries the job number and status only.
- **Endpoints** (`api/customers.py`):
  - Customers: `GET|POST /customers`, `GET|PATCH|DELETE /customers/{id}`, `/customers/{id}/contacts`, `/customers/{id}/equipment`.
  - Ownership: `/items/{id}/owner`.
  - Jobs: `GET|POST /repair-jobs`, `GET|PATCH|DELETE /repair-jobs/{id}`, `/repair-jobs/{id}/status`, `/repair-jobs/{id}/service`, `/items/{id}/repair-jobs`.
  - Uploads: `attachments` takes `repair_job_id`.
  - Maintenance dashboard: `owner=all|household|customer`.
- **Tests:** `tests/test_customers.py` (11), the route authorization walk, and the e2e repair flow.


- `customers`: name, phone, email, address, notes, `deleted_at`. `customer_contacts`: an optional contact log.
- `items.owner_customer_id`: a nullable FK where NULL means the household. Customer-owned items stay ordinary items, so QR codes, photos, search and maintenance all work. They are **excluded** from household totals, value, low stock, replenishment and warranty alerts. They also show an "Owned by …" badge everywhere.
  - Ownership changes only through `POST /items/{id}/owner`, which is audited and needs confirmation, never through a general item PATCH.
  - The migration adds only the column (NULL), so no existing item changes owner.
- `repair_jobs`:
  - Who and what: `number` (RJ-0001), `customer_id`, `item_id`, and snapshots of the customer and equipment.
  - The visit: `status` (`received|awaiting_diagnosis|awaiting_approval|waiting_for_parts|in_progress|completed|returned|cancelled`), `received_on`, `condition_received`, `reported_problem`, `diagnosis`.
  - Charges and dates: `labor_charge`, `parts_charge` (computed from the linked service parts, or overridden), `completed_on`, `returned_on`.
  - `repair_job_events` records status history.
  - Service events and attachments link to jobs. A job can have several service events, and an item can have many jobs.
- **Permissions:** customer and job data needs the **member** role, so read-only users get 403 and no customer names in search results. The HA aggregate sensors show counts only.
- Printable work order: a print-styled page with customer, equipment, problem, work, parts and charges. It is not an invoice system.

## Phase 4: relationships and kits (done, 0.5.0)

**As built:**
- **Migration `0005`:** three new tables; no existing table changed.
- **Duplicate links:** prevented in code, because SQLite treats NULLs as distinct in unique constraints. Symmetric types (`compatible_with`, `stored_with`) are de-duplicated in either direction.
- **Endpoints** (`api/relations.py`):
  - `GET /relation-types`
  - `GET|POST /items/{id}/relations` (with `direction`), `DELETE /relations/{id}`
  - `GET|POST /kits`, `GET|PATCH|DELETE /kits/{id}`, `POST|PATCH|DELETE /kits/{id}/members[/{member_id}]`
  - `/search` results carry `related`.
- **Kit member state:** `present` if the kit's container is in the item's container chain, or the kit's location is in its location chain. Otherwise `elsewhere`, `on_loan`, `short` (quantity below required) or `missing` (status missing or trashed).
- **Tests:** `tests/test_relations.py` (8), the route walk, and the e2e link → search → kit flow.


- `item_relations`: `from_item_id`, `to_item_id`, `relation_type` (`accessory_of`, `component_of`, `compatible_with`, `stored_with`, `replacement_for`, or a custom label), and `note`. Each pair and type is unique. Rows cascade only on hard delete; items in the trash are hidden. Every type shows from both sides using inverse labels, e.g. "Accessory of" ↔ "Accessories".
- `kits`: name, description, home `container_id`/`location_id`, `qr_code`, `deleted_at`. `kit_members`: `kit_id`, `item_id`, `quantity`, `optional`, `note`.
  - Each member's status is computed: present at the kit's home, stored separately, on loan, missing, or deleted.
- Items keep their own placement and QR code. Moving one item never moves another.
- Search results gain "Related" chips, and the item page lists related items from both directions.

## Phase 5: NFC (done, 0.6.0)

**As built:**
- **Migration `0006`** adds a `tags` table.
- **Platform findings** are in `docs/nfc.md`. Web NFC works only in Chrome on Android, and only on top-level pages, so it can't run inside the Ingress frame; the UI offers to open the App in its own tab. iOS reads URL tags natively but can't write them from the web.
- **Endpoints** (`api/tags.py`):
  - `GET|POST /tags`, `GET /tags/check?hardware_id=`, `PATCH /tags/{id}` (reassign, status, action, label)
  - `POST /tags/{id}/replace`, `DELETE /tags/{id}` (deactivate)
  - `POST /tags/resolve`, `GET /tags/{id}/qr.svg`
  - `/qr/resolve` also accepts tag links.
  - Lookups authenticate without the read-only write rule. This also fixed read-only QR scanning.
- **Tests:** `tests/test_tags.py` (12), the route walk, and the e2e assign → scan → action flow.


- `tags`:
  - The tag itself: `ref`, 22 random URL-safe characters, unique and never derived from IDs. `id_type` is `url` (default) or `ndef_serial` (Android Web NFC). `hardware_id` is optional and unique among active tags.
  - Where it points: `target_type` (`item|container|location|kit`), `target_id`, `default_action`.
  - Bookkeeping: `status` (`active|inactive|lost`), `label`, the last scan time, and who assigned it.
- The tag stores the URL `https://<home assistant>/<app panel>/n/<ref>`. It goes through the existing deep-link path, Home Assistant login is still required, and the ref is resolved by an authenticated API call. Inactive and unknown refs give the same "Tag not recognised" message. The tag refers to the record, not a place, so links survive moves.
- Writing tags:
  - Chrome on Android: Web NFC (`NDEFReader.write` of the URL, then reading `serialNumber` for duplicate detection).
  - iOS (no Web NFC in Safari): the app shows the URL with a copy button and a QR code, for writing with an NFC app such as NFC Tools. iOS reads URL tags natively and opens the link.
- On scan: the default action opens the record, and a sheet offers move, quantity, log service, meter reading, start repair, kit members, or return from loan.
- A management page lists tags, lets you reassign, deactivate or mark lost, and replace a tag (deactivates the old one and issues a new ref). It also prints a QR fallback.
- Home Assistant event: `home_inventory_tag_scanned`.

## Phase 6: replenishment (done, 0.7.0)

**As built:**
- **Migration `0007`** adds `replenishment_profiles`, `product_sources` and `shopping_list_items`. The minimum reuses `products.min_quantity`.
- **Retailers:** the research and capability matrix are in `docs/replenishment.md`.
  - `services/retailers.py` has providers for Amazon (cart hand-off via the Add to Cart form), Lowe's, Tractor Supply and "other", each declaring its capabilities.
  - There is no scraping, no stored credentials and no browser automation.
  - Statuses: `listed` → `handed_off` (a link was opened) → `in_cart` (user confirmed only) → `purchased`, plus `deferred` and `cancelled`.
- **Endpoints** (`api/replenishment.py`):
  - `GET /retailers`, `GET /replenishment`
  - `GET|PUT /products/{id}/restock`, `POST /products/{id}/sources`, `PATCH|DELETE /sources/{id}`
  - `GET|POST /shopping-list`, `PATCH /shopping-list/{id}`, `POST /shopping-list/handoff`, `POST /shopping-list/{id}/purchased`
  - Settings gain `amazon_domain` and `amazon_associate_tag`.
- **Tests:** `tests/test_replenishment.py` (17), the route walk, and the e2e flow (settings → source → list → hand-off → confirm → buy → stock).


- `replenishment_profiles` (one per product, because stock is summed across holdings): enabled, `min_stock` (reuses `products.min_quantity`), `target_stock`, `reorder_quantity`, `unit`, notes, plus the last purchase date and price.
- `product_sources` (several per product): `retailer` key, `url`, `retailer_product_id` (ASIN, Lowe's item number, Tractor Supply SKU), GTIN, `preferred`, pack size, estimated price.
- Suggested order quantity is `max(target − stock, reorder_quantity if stock ≤ min)`, rounded up to whole packs.
- `shopping_list_items` status, in order: `listed` → `handed_off` (product page or cart link opened) → `in_cart` (only when the **user confirms**) → `purchased`, or `deferred` / `cancelled`.
  - Purchase confirmation can add the quantity to a chosen holding.
  - The app never sets `in_cart` by itself.
- `RetailerProvider` declares `capabilities`: product URL, cart handoff URL, cart API, price, availability. The UI shows each capability honestly.
- Retailers are investigated before implementing. The results go in this document as a capability matrix.

## Phase 6.5: Home Assistant

- Aggregate sensors: maintenance overdue, maintenance due soon, under repair, awaiting pickup, needs replenishing, shopping list.
- Events: `maintenance_completed`, `tag_scanned`, `repair_job_status_changed`, `shopping_list_updated`.
- Actions: record meter reading, complete maintenance.
- No per-item entities.

## Decisions taken without blocking (change on request)

- Read-only users can't see customers or repair jobs.
- The setup code is in the App log rather than a UI-only claim. Only Home Assistant admins can read App logs.
- Money uses the existing `currency` setting. Part quantities are decimals.
- Operating-hour readings from Home Assistant sensors are left for later. The `source=sensor` value exists so it can be added without a migration.


---

# Final summary

## 1–2. Security findings and fixes
See *Phase 1* above: the four reported findings were all confirmed and fixed, and two more gaps were found and fixed. Further issues were found and fixed along the way:
- Read-only users couldn't scan QR labels (0.6.0).
- A button inside a form label had the wrong accessible name (0.5.0).
- A sheet stayed open when moving between items (0.3.0).

## 3. Database schema and migrations
| Revision | Version | Change | Existing tables touched |
|---|---|---|---|
| 0003 | 0.3.0 | equipment_profiles, maintenance_tasks, meter_readings, service_events, service_parts | `attachments` gains a nullable `service_event_id` (batch rebuild, atomic) |
| 0004 | 0.4.0 | customers, customer_contacts, item_owners, repair_jobs, repair_job_events | `attachments` and `service_events` gain a nullable `repair_job_id` |
| 0005 | 0.5.0 | item_relations, kits, kit_members | none |
| 0006 | 0.6.0 | tags | none |
| 0007 | 0.7.0 | replenishment_profiles, product_sources, shopping_list_items | none |

How migrations run:
- Each one runs in a single transaction with foreign keys off. `foreign_key_check` is verified before commit.
- A snapshot of the database is written to `/data/backups/pre-migration-<rev>-*.sqlite3` first.
- Migrations are additive: no existing column is changed or dropped, and no data is rewritten. The ones that add columns check what already exists, so they can be re-run.
- **Rollback:** stop the App, copy the pre-migration snapshot over `/data/inventory.sqlite3`, and install the previous App version. A Home Assistant backup taken before updating restores everything. Alembic `downgrade()` functions exist but aren't used at runtime.

## 4. API endpoints
Listed in each phase section above. All routes are covered by `tests/test_authz_routes.py`:
- writes need the member role;
- customer and repair routes need member even to read;
- the API port needs the token.

## 5. Frontend
New pages:
- Maintenance, Customers / Customer, Repairs / Repair job / Work order
- Kits / Kit, NFC tags / Tag scan, Restock & shopping

New item-page sections:
- Owner banner and repair jobs
- Maintenance (hours, tasks, service history)
- Related items, NFC tags, Restock

Elsewhere:
- Customer badges in all lists, related-item chips in search, and dashboard cards.
- Settings gain the Amazon store and Associates tag.

## 6. Home Assistant
- Sensors (aggregate only): maintenance overdue and due soon, repairs in progress and awaiting pickup, supplies below minimum, shopping list items.
- Events: `maintenance_completed`, `repair_job_status`, `tag_scanned`, `shopping_list_updated`.

These events carry no customer details and no tag references.

## 7. Retailer capability matrix
See `docs/replenishment.md`.

## 8. Test results at 0.7.0
- **Backend:** 255 tests passed. Ruff, including the security rules, is clean.
- **Integration:** 11 tests passed, against a real Home Assistant, including the media API contract tests.
- **Browser e2e:** passed. Flows covered: dashboard, search, scan, quick photos, maintenance, repair job with work order, relationships and kits, NFC assign and scan, and replenishment.
- **CI:** green on every phase PR, including dependency audits, gitleaks, Trivy, CodeQL, hassfest, HACS and the App linter.

## 9. Known limitations
- **Web NFC** works only in Chrome on Android, in the App's own tab. It isn't available inside the Home Assistant frame or on iOS, where tags are written with an NFC app. It wasn't tested on a physical phone; headless browsers have no NFC.
- **Retailer carts:** no retailer offers cart APIs. Amazon gets a confirm-to-add link, the other shops get product links. Prices aren't fetched automatically.
- **Operating-hour sensors** from Home Assistant aren't connected yet. Readings are entered by hand (`source=sensor` is reserved for this).
- **Work orders** use browser printing. There is no PDF export or invoicing, by design.
- **Tag reassignment** to another record is in the API (`PATCH /tags/{id}`), but the management page doesn't offer it.
- **Customer data:** visible to all members and administrators. There is no per-member restriction.

## 10. Installation and upgrade
- **Before updating**, take a Home Assistant backup. Every database change is also preceded by an automatic snapshot.
- **Updating from 0.1.x:** install the update and start the App.
  - The migrations add tables only; existing items, photos and roles are kept.
  - Existing administrators stay administrators.
  - If nobody can be identified as administrator (rare: only when Home Assistant's user list can't be read and `admin_users` is empty), the App log shows a setup code to enter in the app.
- **Permissions:** 0.2.0 removes the `/media` mount. Home Assistant may show the App's permissions as changed; that's expected.
- **Integration:** update it through HACS, then restart Home Assistant, to get the new sensors and events.

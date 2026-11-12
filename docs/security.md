# Security model

## Exposure

- The App publishes **no host ports**. The UI is reachable only through Home Assistant Ingress, which requires a logged-in Home Assistant session. Nothing is exposed to the internet beyond what Home Assistant itself exposes.
- The integration API port (8100) is reachable only on the Supervisor's internal Docker network, and every request needs the App-generated bearer token.
- No Cloudflare tunnel, public reverse proxy or separate login service is used or needed.

## Identity

Ingress alone does **not** pass identity to an App unless the App checks it. Here is how it is handled:

1. The Supervisor's Ingress proxy authenticates the Home Assistant session and adds `X-Remote-User-Id`, `X-Remote-User-Name` and `X-Remote-User-Display-Name` headers.
2. Any container on the internal network could forge those headers. The backend therefore accepts them **only when the TCP peer is the Ingress proxy (172.30.32.2)**. Requests from any other peer are rejected with 403, and requests without an identity get 401 (see `auth.py`, tested in `tests/test_auth.py`).
3. Home Assistant administrator status is read from Home Assistant (`config/auth/list` via the Supervisor's Home Assistant API proxy) and cached for five minutes. If Home Assistant can't be asked, the stored status is kept unchanged, so an outage neither grants nor removes administrator rights.
   - **Nobody is made administrator automatically.** If no administrator can be established at all (Home Assistant's user list is unavailable, none is configured in `admin_users`, and none exists in the database), the App writes a one-time **setup code** to its log. Only Home Assistant administrators can read App logs.
   - Entering the code in the app grants the administrator role to that one person. The code is single-use, held only in memory, rotated after 5 wrong attempts and on every restart, and refused for anyone listed in `read_only_users`.
   - Claims are serialised, so only one of several simultaneous claims can succeed (tested in `tests/test_auth.py`).
4. On the API port, the integration authenticates with the bearer token (constant-time comparison). It may act for a Home Assistant user by forwarding that user's id, name and admin flag. The App trusts these only because the token proves the caller is the integration.
5. Container/Core installs: the integration's sidebar panel proxy obtains a single-use, 60-second token through Home Assistant's authenticated WebSocket. It exchanges the token for an `HttpOnly; SameSite=Strict` session cookie scoped to `/api/home_inventory/panel/`. Every proxied request re-checks that the Home Assistant user is still active. Cross-origin writes are refused. Client cookies and `Authorization` headers are never forwarded.

## Authorisation

Roles are `admin`, `member` and `read_only`, enforced server-side on every endpoint. Writes require `member`. User management, settings, backup/restore and permanent deletion require `admin`. An administrator cannot remove their own admin role.

### Customer data and read-only users (0.7.1)

**Rule:** read-only users never see customer information or customer repair records. That covers customers, their equipment (including its name, photos, documents, notes, serial number, maintenance and meter history, NFC tags, related-item links and kit membership), repair jobs, and history entries about any of these. Household inventory is unaffected.

Enforcement is central (`privacy.py`):
- **ORM filter.** Every ORM query made for a read-only user gets `with_loader_criteria` filters that hide customer-owned items, products that only customers own, their attachments, service events and parts, meter readings, tasks, loans, links, kit members, tags, restock records and history, as well as all customers and repair jobs. This covers direct lookups (`Session.get`), lists, search, relationship loads and anything added later. Hidden records answer exactly like missing ones (404 / "Tag not recognised").
- **Route checks.** Customer and repair routes also require the member role, even to read (403).
- **Second-record history.** A history entry about one record that mentions another item (links, kit members, tag moves, parts taken from stock) says "customer equipment" instead of naming it.
- **Home Assistant events.** Events about customer equipment never carry its name, because Home Assistant's event bus is visible to every Home Assistant user.
- **Ownership is immutable from the household side.** Household purchases can't be added to a customer's holding, and service parts can't be taken from customer stock.

| Endpoint group | Read-only users |
|---|---|
| Items, containers, locations, search, dashboard, loans, low stock, trash, labels, maps, kits, item links, history | Household records only |
| Attachment file and thumbnail | Household attachments only; 404 for customer equipment and repair-job files |
| Maintenance (item summary, dashboard) | Household equipment only; `owner=customer` returns 403 |
| Restock, shopping list | Household products only |
| NFC tag list and resolve, QR resolve | Allowed (lookups); customer equipment tags don't resolve |
| Customers, customer equipment, repair jobs, item repair jobs | 403 |
| Barcode lookup | 403 (unchanged: it can call paid external services) |
| All writes | 403 |

`tests/test_privacy.py` checks each path, and `tests/test_authz_routes.py` walks the real route table, so new endpoints are covered automatically. It checks that every state-changing route refuses read-only users, that every route on the API port requires the token, and that every Ingress route refuses untrusted peers.

## Secrets

- AI and barcode API keys are entered in the App configuration (stored by the Supervisor in `/data/options.json`, schema type `password`). They are never written to the repository or logs.
- The integration token is generated randomly (256 bits), stored in `/data/integration_token` (mode 600), handed to Home Assistant through Supervisor discovery, and redacted in diagnostics.
- When using the default `home_assistant` AI provider, the App holds **no** AI key. Home Assistant's AI Task integration does the call.

## Uploads

- The type is determined from file content (Pillow decode or PDF magic), never from the filename or Content-Type. Only JPEG/PNG/WebP/GIF images and PDFs are accepted.
- Images are decoded and re-encoded. This strips EXIF (including GPS) and anything appended to the file. There are pixel and size limits (decompression-bomb protection).
- Storage names are random and generated by the server. Path traversal is impossible by construction and is also checked.
- Files are served with `X-Content-Type-Options: nosniff`. PDFs get `Content-Security-Policy: sandbox`.
- Uploads are limited to `max_upload_mb`. The limit is enforced by ASGI middleware *before* the multipart parser spools anything to disk: an oversized `Content-Length` is refused at once, and streamed bodies are cut off at the limit.

## Backup restore

Restore is administrator-only and treats the archive as untrusted:

- The upload is limited to `backup_max_mb` (default 2048).
- Before anything is extracted, the zip directory is checked:
  - only `manifest.json`, `inventory.sqlite3` and `media/**` may appear, with no duplicates;
  - no absolute paths, `..`, backslashes, drive letters, symlinks, special files or encrypted entries;
  - entry count, per-file size, total size and compression ratio are limited (zip bombs).
- Entries are extracted by streaming, and the real bytes written are counted, because header sizes can lie.
- The manifest must have the right format, a supported format version and a schema revision this version knows. Backups from newer versions are refused.
- The database must:
  - be SQLite;
  - pass `integrity_check` and `foreign_key_check`;
  - contain the required tables;
  - carry an `alembic_version` matching the manifest.
- It is then upgraded to the current schema *in staging*.
- Only then is the current data exported to `/data/backups/before-restore-*.zip` and swapped in by renames. If the swap or re-opening the database fails, the previous data is put back. Staging and rollback folders are always removed.

## Web hardening

- A strict Content-Security-Policy (`script-src 'self'`, `frame-ancestors 'self'`, `object-src 'none'`), `Referrer-Policy: same-origin`, and a `Permissions-Policy` that allows only the camera.
- All request bodies are validated with Pydantic models that reject unknown fields. Database check constraints back up the important invariants.
- API docs (OpenAPI) are not served on the Ingress listener.

## External calls

- **Product web links** (import from a URL): every hop, including redirects and the image download, is resolved **once**. Every address must be public: no loopback, private, link-local, CGNAT, multicast or IPv4-mapped private addresses. The connection is then made to that exact IP, while the `Host` header, TLS SNI and certificate verification keep the hostname. DNS is never asked again, so DNS rebinding can't swap in a private address. Environment proxies are ignored, and the connected peer is checked when the transport reports it. Responses are size- and time-limited.
- **Rate limits** (per user, in memory): AI calls 20 per minute, web-link imports 10 per minute, online barcode lookups 30 per minute. Over the limit, the API answers 429 with `Retry-After`; AI search falls back to keyword search instead.
- **AI Task photo hand-off**: the App mounts **no** Home Assistant folders.
  - Photos go through Home Assistant's media upload API into `media/home_inventory/.ai-tmp/`. On older Home Assistant versions they go to the media root, named `home_inventory_ai_*`.
  - They are removed with the `media_source/local_source/remove` WebSocket command as soon as the AI call returns, even if it fails.
  - Both calls are admin-only in Home Assistant. The App reaches them through the Supervisor's Home Assistant API proxy (`homeassistant_api: true`), which already gives the App that level of Home Assistant access.
  - Without a folder mount, a bug in the App can't touch other files in `/media`.
- Barcode catalogs (Open Food/Products/Beauty Facts, UPCitemdb, optional Barcode Lookup) receive only the barcode number. Results are cached.
- AI providers receive the photo (downscaled to 1568 px) and a fixed prompt. With the default provider, the request goes through your Home Assistant AI Task configuration. Note that Google's Gemini free tier allows Google to use submitted content to improve its products. Use a paid tier or a local model (`ollama`) if that matters to you.

## Automated checks (CI)

| Check | Fails the build on | Why this threshold |
|---|---|---|
| `pip-audit` (backend and its dependencies) | Any known vulnerability | Few dependencies; findings are actionable. |
| `npm audit --omit=dev` | High or critical, shipped code only | Dev-only tooling never reaches users. |
| Ruff `S` (bandit) rules | Any finding | S104 (bind all interfaces: required inside the container; Ingress peer is checked) and S110 (deliberate parser fallbacks) are excluded; tests may use asserts and dummy credentials. |
| Gitleaks (whole history) | Any secret | Test fixtures are allow-listed in `.gitleaks.toml`. |
| Trivy (built image) | Critical with a fix available | HIGH and unfixed issues are reported, not failed: most come from the Home Assistant base image and can only be fixed by its next release. Trivy runs from its pinned container image rather than a third-party action. |
| CodeQL | Alerts in GitHub code scanning | GitHub default setup. |
| Route authorization tests | Any route without the right role check | Covers new endpoints automatically. |

## Residual risks and recommendations

- Anyone with a Home Assistant account can open the App with the default `member` role. Set `default_role: read_only` and list members explicitly if you share Home Assistant with guests.
- Use HTTPS for Home Assistant. It is also required for live camera scanning.
- A Home Assistant administrator can read the App's data through backups, as with any App.

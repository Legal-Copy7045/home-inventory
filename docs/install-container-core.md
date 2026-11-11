# Installing on Home Assistant Container or Core

Without the Supervisor there are no Apps, no Ingress, no Supervisor discovery and no automatic App backups. The closest native setup has three parts:

1. The **companion container** runs the inventory backend.
2. The **integration** (installed through HACS) connects to it.
3. The **integration's sidebar panel** shows the UI inside Home Assistant, using your Home Assistant login. You never manage a separate website or password.

## 1. Run the companion container

```bash
git clone https://github.com/Legal-Copy7045/home-inventory
cd home-inventory/deploy/standalone
docker compose up -d --build
docker compose exec home-inventory cat /data/integration_token
```

Only the token-protected API port 8100 is published. Bind it to your LAN interface (`"192.168.1.10:8100:8100"`) and never forward it from the internet.

## 2. Install the integration

HACS → ⋮ → Custom repositories → `https://github.com/Legal-Copy7045/home-inventory` (type *Integration*) → install **Home Inventory**, then restart Home Assistant.

Settings → Devices & services → Add integration → **Home Inventory**:

- URL: `http://<docker host>:8100`
- API token: the value from step 1
- **Add Inventory to the sidebar**: on

## 3. Optional AI through Home Assistant

To reuse Home Assistant's AI Task (e.g. Gemini):

- Create a long-lived access token in your Home Assistant profile.
- In `docker-compose.yaml`, set `HINV_AI_PROVIDER: home_assistant`, `HINV_HA_URL` and `HINV_HA_TOKEN` (use an `.env` file and keep it out of Git).
- Mount Home Assistant's `media` folder at `/media`, so photos can be handed to AI Task.

Or use `HINV_AI_PROVIDER: gemini` with `HINV_AI_API_KEY`.

## Configuration

Every App option is available as an environment variable prefixed `HINV_` (upper case), e.g. `HINV_BARCODE_UPCITEMDB=false` or `HINV_ADMIN_USERS=jo,sam`.

## Backups and upgrades

- Back up the `data/` folder next to `docker-compose.yaml`, or use **Settings → Backup & restore → Download backup** in the app.
- Upgrade with `git pull && docker compose up -d --build`. Migrations run automatically and snapshot the database first.

## What differs from the App install

| | App (OS/Supervised) | Container/Core |
|---|---|---|
| Install/update | Apps store | docker compose |
| Sidebar | Ingress | Integration panel (authenticated proxy) |
| Integration setup | Discovered automatically | URL + token |
| Home Assistant backups include inventory | Yes | No (back up `data/`) |
| Watchdog | Supervisor | Docker `restart: unless-stopped` |

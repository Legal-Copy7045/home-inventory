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

Only the token-protected API port 8100 is published, and by default only on `127.0.0.1` (Home Assistant on the same host). If Home Assistant runs on another machine, put this host's LAN address in a `.env` file next to the compose file, `HINV_BIND_IP=192.168.1.10`, and run `docker compose up -d` again. Never use `0.0.0.0` and never forward the port from the internet: the API token is sent unencrypted on your network and gives full access to the inventory, so treat it like a password and keep the port off networks you don't trust.

The container runs without extra Linux capabilities, and the App writes its data folder readable by itself only.

## 2. Install the integration

HACS → ⋮ → Custom repositories → `https://github.com/Legal-Copy7045/home-inventory` (type *Integration*) → install **Home Inventory**, then restart Home Assistant.

Settings → Devices & services → Add integration → **Home Inventory**:

- URL: `http://<docker host>:8100`
- API token: the value from step 1
- **Add Inventory to the sidebar**: on

## 3. Give the App access to Home Assistant (recommended)

Under Home Assistant OS the App reaches Home Assistant through the Supervisor. Without it, give the App Home Assistant's address and a token instead:

- Create a **long-lived access token** in your Home Assistant profile (Profile → Security), signed in as an **administrator**. Reading who is an administrator needs one.
- In `docker-compose.yaml`, set `HINV_HA_URL` (e.g. `http://homeassistant.local:8123`) and `HINV_HA_TOKEN: ${HA_LONG_LIVED_TOKEN}`. Put the token in an `.env` file next to the compose file and keep it out of Git.

This turns on:

- **Who's asking**: voice and automation requests carry only a Home Assistant user id. The App looks up the person's name and whether they're an administrator. Without access, those people show as "Home Assistant user 1a2b3c", and their role comes from `HINV_ADMIN_USERS` or from what the sidebar page last told the App.
- **Equipment sensors**: linking Home Assistant entities to equipment (hour meters, battery, power), and reading hour-meter sensors every 15 minutes.
- **AI through Home Assistant's AI Task** (next step).

Everything else works without it: the inventory, labels, scanning, voice answers, maintenance, shopping and the sidebar page.

## 4. Optional AI through Home Assistant

To reuse Home Assistant's AI Task (e.g. Gemini):

- Set up Home Assistant access as in step 3, then set `HINV_AI_PROVIDER: home_assistant`.
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
| Reaching Home Assistant (names and admins for voice, entity links, AI Task) | Automatic | `HINV_HA_URL` + `HINV_HA_TOKEN` (step 3) |
| Who can reach the App | Only through Home Assistant | Token-protected port 8100 on your LAN: the integration only talks to it on your own network |

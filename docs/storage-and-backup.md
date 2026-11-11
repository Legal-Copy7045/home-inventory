# Storage, backup, restore and upgrades

## Where data lives

Everything is in the App's persistent data directory, `/data`, which the Supervisor keeps across restarts, upgrades and reboots.

| Path | Contents |
|---|---|
| `/data/inventory.sqlite3` (+ `-wal`, `-shm`) | The database |
| `/data/media/` | Photos, thumbnails, receipts, manuals |
| `/data/integration_token` | Token for the companion integration |
| `/data/backups/` | Automatic pre-migration snapshots (last 5) and pre-restore safety archives |
| `/data/options.json` | App configuration (written by the Supervisor) |

Nothing is stored in Home Assistant's recorder database. The integration only creates a handful of summary sensors.

`/media/home_inventory/.ai-tmp/` is used only for a few seconds while handing a photo to Home Assistant's AI Task. Each file is deleted immediately after the call.

## Home Assistant backups

`/data` is included in every Home Assistant backup (full backups, or partial backups that tick *Home Inventory*). The App uses `backup: cold`: the Supervisor stops it for the few seconds the backup takes and then starts it again, so the SQLite database and media folder are captured in a consistent state. Restoring a Home Assistant backup that contains the App restores the inventory exactly.

## Portable export

Administrators can use **Settings → Backup & restore → Download backup** to get a `.zip` file containing:

- `manifest.json` (format version, schema revision, timestamp)
- `inventory.sqlite3`, a consistent snapshot made with SQLite's online backup API
- `media/`

**Restore from file** validates the archive (format, safe paths, SQLite integrity check) and saves the current data as `before-restore-<timestamp>.zip` in `/data/backups`. It then replaces the database and media and reopens the database. If the archive is from an older version, migrations bring it up to date.

This is also how you move between installation types, for example from a Container install to Home Assistant OS.

## Upgrades

- Updating the App replaces the container image only. `/data` is untouched.
- On start, the App compares the database schema with the newest Alembic revision. If an upgrade is needed, it first writes `pre-migration-<old revision>-<timestamp>.sqlite3` to `/data/backups/` and then migrates.
- Home Assistant can also make its own backup before an App update (the "Backup before update" option).
- Rolling back: restore the Home Assistant backup taken before the update. You can also stop the App, copy the pre-migration snapshot over `inventory.sqlite3` and install the previous version.

## Restart behaviour

The App starts with Home Assistant (`boot: auto`). The Supervisor's watchdog restarts it if `/api/health` stops responding. SQLite in WAL mode with `synchronous=NORMAL` means a crash or power loss loses at most the last moment of activity, never the database.

All of this is covered by automated tests: `test_restart_and_upgrade_persistence`, `test_backup_export_restore`, `test_migrations_match_models`, `test_pre_migration_snapshot`, and the Docker lifecycle check in CI.

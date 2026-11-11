# Home Inventory

A household inventory you open from the Home Assistant sidebar.

## First steps

1. **Locations**: create your places, as deep as you like. For example *Property › House › Garage › Upper shelving*. Shelves, drawers, workbenches and tool racks are locations too, so items can sit on them directly ("loose items").
2. **Containers**: create boxes, totes and bins, give them a number, and place them in a location or inside another container. Print QR labels from **Print Labels**.
3. **Items**: tap **+**, take a photo, let AI suggest what it is (optional), check the details, pick where it is, and save.
4. **Scan mode**: in the storage area, scan a box's label to open it, then scan product barcodes to add (+1) or take out (−1). Tap ⇄ to move something to another box.

## Configuration

| Option | Default | Meaning |
|---|---|---|
| `ai_provider` | `home_assistant` | `home_assistant` uses a Home Assistant **AI Task** entity (Settings → Devices & services → e.g. *Google Generative AI*); the Gemini free tier works. `gemini`, `anthropic` and `openai` call a provider directly with `ai_api_key`. `ollama` uses a local Ollama server (`ai_base_url`). `none` turns AI off. |
| `ai_task_entity` | *(auto)* | For example `ai_task.google_ai_task`. Leave empty to use the first AI Task entity that accepts images. |
| `ai_api_key` | | Only for direct providers. It is stored by the Supervisor, never in Git. |
| `ai_model`, `ai_base_url` | | Optional overrides for direct providers. |
| `barcode_open_facts` | `true` | Open Food/Products/Beauty Facts lookups (free, no key). |
| `barcode_upcitemdb` | `true` | UPCitemdb trial lookups (free, 100/day). |
| `barcode_lookup_api_key` | | Optional commercial barcodelookup.com key. |
| `admin_users` | `[]` | Extra administrators (Home Assistant username or display name). Home Assistant administrators are always administrators here. |
| `read_only_users` | `[]` | People who may only view. |
| `default_role` | `member` | Role for everyone else (`member` or `read_only`). |
| `warranty_notice_days` | `30` | When to fire `home_inventory_warranty_expiring`. |
| `expiry_notice_days` | `14` | When to fire `home_inventory_item_expiring`. |
| `max_upload_mb` | `25` | Upload size limit. |
| `log_level` | `info` | |

AI is optional. Without it you type the details in. Search still works: the built-in parser handles questions like "How many USB-C chargers do I own?".

## Roles

- **Administrator**: everything, plus settings, user roles, backup and restore, and permanent deletion.
- **Member**: search, add, edit, move, lend, scan, manage containers and locations, view history, restore from trash.
- **Read-only**: view and search only.

People are identified by their Home Assistant login, and the history records who made each change.

## Camera access

Live scanning needs Home Assistant to be opened over **HTTPS** (Home Assistant Cloud, or your own certificate) or in the Home Assistant companion app. Over plain `http://` the browser won't open the camera, but **Photo of code** still works: it takes a picture with the camera app and decodes it.

## QR labels

Each container has a permanent 8-character code, and its label keeps working after renaming, renumbering or moving it. Labels can be read with the in-app scanner. If you set *QR label link* in Settings to your Home Assistant address for this App, a phone's normal camera can open them too.

## Backups

The App's data (database, photos, receipts) is included in Home Assistant backups. The App pauses for a few seconds during a backup so the database is captured consistently. Restoring a Home Assistant backup restores the inventory. Administrators can also download a portable archive under **Settings → Backup & restore**.

## Support

See https://github.com/Legal-Copy7045/home-inventory

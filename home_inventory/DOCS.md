# Home Inventory

A household inventory you open from the Home Assistant sidebar.

## First steps

1. **Locations**: create your places, as deep as you like. To set up many at once, use *Locations → Import from Excel*: download the template, write one row per place (`Property | Shed | Rear Right Shelving | Middle Shelf`), upload it and confirm the preview. For example *Property › House › Garage › Upper shelving*. Shelves, drawers, workbenches and tool racks are locations too, so items can sit on them directly ("loose items").
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
| `backup_max_mb` | `2048` | Largest backup archive accepted by *Restore*. |
| `log_level` | `info` | |

AI is optional. Without it you type the details in. Search still works: the built-in parser handles questions like "How many USB-C chargers do I own?".

## Roles

The first administrator comes from Home Assistant: anyone who is a Home Assistant administrator is an administrator here, as is anyone listed in `admin_users`. If the App can't read Home Assistant's user list and nobody is configured, nobody is made administrator automatically. Instead, the app shows **Set up administrator**: copy the setup code from this App's **Log** tab and enter it there.

- **Administrator**: everything, plus settings, user roles, backup and restore, and permanent deletion.
- **Member**: search, add, edit, move, lend, scan, manage containers and locations, view history, restore from trash.
- **Read-only**: view and search only.

People are identified by their Home Assistant login, and the history records who made each change.

## Quick photos

To inventory a box fast, open the container (or location) and tap **Quick photos**. Take one photo per item; each is saved into that box at once as "Item to identify". Come back later via **To finish** in the menu, which shows the photo beside the details form. **Save & next** moves on to the next one. If AI is configured, a suggested name and category is already filled in. The live shutter needs Home Assistant over https; otherwise the button opens your phone's camera app.

## Maintenance

For mowers, trimmers, saws, generators, pressure washers, snow blowers or anything else that needs servicing:

1. Open the item and tap **Track maintenance**.
2. Pick the kind of equipment. Typical tasks are added as a starting point, such as oil every 50 hours or yearly, whichever comes first. Check them against your manual, then change, add or remove them.
3. For a used machine, enter its current hours first, so it isn't shown as overdue for everything.

On the item page:
- **Log hours** records a meter reading. Tick *Estimate* if there's no hour meter, or *Correction* if the meter was replaced.
- **Done** on a task records the work, including parts, costs, who did it, and photos or receipts. It also works out when the task is next due, from the date and hours you entered.
- A task can also be skipped once, deferred to a date, or marked not applicable.
- **Log service** records unscheduled work or older history; past dates are fine.

The **Maintenance** page lists what's overdue, what's due in the next 7, 30 or 90 days, equipment with no service recorded yet, and recent work.

Service history is kept when tasks are changed or removed, and even when you stop tracking an item.

## Related items and kits

Keep accessories as separate items, each with its own place, QR label and search entry, and link them:

- On an item, tap **Link**, then choose how they're related. The options are accessory, component, compatible with, stored with, replacement for, or your own words. The link shows on both items, and searching for one shows the others alongside it.
- A **kit** (e.g. "Air compressor kit") groups items that belong together. Give it the container or shelf where it's kept. The kit then shows which items are *with the kit*, *stored separately*, *on loan*, *missing*, or short (not enough). You can mark an item as *Optional*.
- Moving one item never moves another. Removing a link or deleting a kit never deletes items.

## Customers and repairs

If you repair equipment for other people:

1. Add them under **Customers**, with name, phone and email.
2. Open a **repair job**. You can add their equipment at the same time.
3. Record its condition when received and the reported problem.
4. Move the job through *received → awaiting diagnosis → awaiting approval → waiting for parts → in progress → ready for pickup → returned*.
5. Use **Record work** to log work and parts. These also go into the equipment's own service history, so return visits keep the full record.
6. **Work order** gives a printable summary with charges. It's a work record, not an invoice.

Customer equipment:
- is shown with a purple **Customer** badge everywhere;
- is kept out of your household totals, value, warranty and expiry alerts, and low-stock counts;
- can only change owner through **Change owner** on the item page, which asks for confirmation and is recorded in the history.

Customer names and contact details are visible to members and administrators only. Read-only users see "Customer equipment", and Home Assistant events never include customer details.

## Camera access

Live scanning needs Home Assistant to be opened over **HTTPS** (Home Assistant Cloud, or your own certificate) or in the Home Assistant companion app. Over plain `http://` the browser won't open the camera, but **Photo of code** still works: it takes a picture with the camera app and decodes it.

## QR labels

Every container and every location (shelf, cabinet, drawer…) has a permanent 8-character code. Its label keeps working after renaming, renumbering or moving.

Scan a label with your **phone's normal camera**. It opens Home Assistant (log in the first time) and shows that container's contents, or everything stored at that location, including the containers on it.

The link printed on the label is the address you used to open Home Assistant when you printed it, e.g. `https://ha.example.com/b1a256fc_home_inventory/c/ABCD2345`. Print labels while using the address your phone can reach. To always print a specific address (for example your external one), set *QR label link* in Settings.

The in-app **Scan** screen reads every label format, including labels printed before version 0.1.2.

## Backups

The App's data (database, photos, receipts) is included in Home Assistant backups. The App pauses for a few seconds during a backup so the database is captured consistently. Restoring a Home Assistant backup restores the inventory. Administrators can also download a portable archive under **Settings → Backup & restore**.

## Support

See https://github.com/Legal-Copy7045/home-inventory

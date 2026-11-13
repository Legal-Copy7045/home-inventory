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

To photograph a whole room first and sort later, tap **Quick photos** in the menu or on the dashboard. Photos taken this way have no place yet. Tap **Next shelf** each time you move to another shelf, box or drawer. Afterwards, open **To sort**: the photos are grouped shelf by shelf. Tap **Put all in…** and choose the shelf (a location) or box (a container). Untick any photo in the group that belongs somewhere else and put it away separately.

Quick photos are saved on your phone before they upload. If you lose signal, or close the app part-way, they upload the next time you open Home Inventory; a banner shows how many are still waiting.

## Asking by voice

Ask "Where is the hedge trimmer?", "What maintenance is due?", "What's running low?" or "What's lent out?". It works with Home Assistant Assist once you add a small sentences file, with Siri through an Apple Shortcut that hands your words to Assist, and with Alexa through your own custom skill ("Alexa, ask Home Inventory where the hedge trimmer is"). See `docs/voice.md` in the repository for the setup and the trade-offs (Alexa needs Home Assistant reachable from the internet).

## Taking things out of stock by barcode

**Scan → Take one out** turns the scanner into a "used one" button for consumables: every barcode scanned removes one from stock. With a container open in scan mode it takes from that container; otherwise, if you keep the product in more than one place, you're asked which. Low-stock alerts and the shopping list update immediately.

## Using it like an app on your phone

Inside Home Assistant, Home Inventory is a panel in the Home Assistant app, so the simplest "app" is the Home Assistant companion app (or a home-screen bookmark to the panel). The App also ships a web-app manifest and a service worker, so the app loads quickly and keeps working on a weak signal (it caches only the app itself, never inventory data; this needs Home Assistant over https). A standalone install only works where the page is opened on its own rather than inside Home Assistant, because Home Assistant's sign-in session belongs to its own frame.

## Photo inventory (AI, several items in one photo)

With an AI provider set up, one photo can add many things. Open a **location** or a **container** and tap **Photo inventory**.

**Photographing a whole area.** Open the location that holds the area, for example "Basement storage" or "Garage", and photograph the whole area in one picture: several racks side by side, a single shelving unit, a workbench, a pegboard or a closet. The AI:

- works out the **layout**: each rack or unit, left to right, and its shelves from the top down, including things sitting on top;
- lists everything, saying which rack and shelf each thing is on;
- lists things it can see but can't identify too, marked **Unidentified – to finish**. They're saved with their own cut-out photo on the **To finish** list, so you can say what they are later (untick the badge if you name one in the review);
- treats storage boxes, bins and totes as **containers**, named from their labels ("Party supplies", "Double bedding covers"), so you can open each one later and photograph what's inside.

In the review, the **Layout** panel matches each rack and shelf to a place you already have, or creates it (rename it if you like). Each entry's **Put on** can be changed, and only places that something actually goes on are created. Photographing the same area again later reuses those places and recognises what's already recorded. Everything is saved together when you tap **Confirm inventory**: new places, containers and items, each with a photo cut out of the big one. Each row shows its cut-out; if it shows the wrong part of the photo, untick **Photo** and that entry is saved without one. For things already saved with a wrong cut-out, open any one of them and tap **Wrong part of the photo? Redo cut-outs**: the AI looks at the original photo again and makes new cut-outs for everything that photo inventory added (items and boxes). Anything it can't place shows the whole photo instead, and photos you added or chose yourself are kept.

**A long wall or bench.** If one photo can't take it all in, tap **Wide area (2-4 photos)** and choose 2 to 4 photos taken left to right, overlapping a little. They're joined side by side into one panorama, and the AI looks at the whole area at once. Cut-outs and later photo inventories work as with a single photo.

**Labels for new boxes.** When a photo inventory adds boxes, it offers to **Print labels** for them straight away, so they can be scanned later.

**One shelf or one box.** Open that shelf or container and photograph it; everything goes there.

**Look again without a new photo.** **Last photo again** has the AI go through the latest photo inventory photo of that place once more. This is useful after an update, or to pick up things it couldn't identify before. Things already recorded are recognised, so you only review what's new. Every photo button also lets you choose a photo from your library instead of taking one.

In every case nothing is saved until you confirm. Edit names and quantities, tap **Details** for category, maker, model, notes and whether it's an item or a box, remove anything wrong and **Add something it missed**. Things already recorded but not seen in the photo can be kept, marked missing or deleted. The AI's guesses can be wrong, especially counts, brands and partly hidden things, so check before confirming.

## Receipts, warranties and other documents

Add documents to an item under **Receipts & documents**. A document can be a PDF or a photo of a receipt, warranty card, invoice, order email, manual or certificate. If you're not sure what type it is, choose *Not sure – let AI work it out*.

With AI set up, each document is read in the background. A few seconds later the document shows what it is (e.g. *Receipt – 6 things to fill in*). Tap **Review** to see what it found:
- Purchase date, price, shop, warranty start and end, make, model and serial number, whichever are printed on it.
- **Empty fields are ticked.** Anything that would replace a value you already have is shown with both values and left unticked, so nothing is overwritten unless you choose it.
- You can correct a value before saving. The document can also be filed under the right type and named (e.g. "Lowe's receipt").
- A document that's clearly about a different product isn't used to fill anything in.

**Warranty claims.** **Download the claim pack** (under *Receipts & documents*) gives you a ZIP to send to the shop or maker: a one-page summary (make, model, serial number, where and when it was bought, price, warranty dates) plus the receipts, warranty documents, other documents and photos.

**What it's worth.** With AI set up, **Estimate with AI** (next to *Value* in *Details*) gives a rough replacement price and a second-hand range, with what it assumed. It's marked as an AI estimate and dated. The **Inventory report** keeps it in its own column and shows a total for items without a price; a price you entered always comes first.

Values are only ever copied from what's printed, never guessed. A warranty end date is worked out only when the document gives both the purchase date and the warranty length. A scanned PDF is read from pictures of its first pages. Files added from **Read the manual** are recognised too, so a receipt added there by mistake is flagged and can be filed correctly. **Dismiss** hides a suggestion, and **Read with AI** reads an older document.

## Working with several items

On **Items**, tap **Select**, tick the items you want (or **Select all**), then use **Move**, **Edit** or **Delete** in the bar at the bottom. **Edit** changes only the fields you fill in. Choose **Sort: Location** to see items grouped by where they are.

## Maintenance

For mowers, trimmers, saws, generators, pressure washers, snow blowers or anything else that needs servicing:

1. Make sure the item's category is **Equipment** (the AI suggests it for powered machines), then open it and tap **Track maintenance** (at the foot of *Details*). Only Equipment items get this link, and the **Maintenance** section only appears on items tracked this way, so other things stay uncluttered.
2. Pick the kind of equipment. Typical tasks are added as a starting point, such as oil every 50 hours or yearly, whichever comes first. Check them against your manual, then change, add or remove them.
3. For a used machine, enter its current hours first, so it isn't shown as overdue for everything.

On the item page:
- **Log hours** records a meter reading. Tick *Estimate* if there's no hour meter, or *Correction* if the meter was replaced.
  - **Photograph the hour meter** to log a reading from a photo. With AI set up it reads the number for you (including the tenths drum) and you check it; the photo is kept with the reading.
  - **Date of the reading** can be any past day, so older photos can be logged afterwards. A photo picked from the gallery uses the day it was taken. Each reading has to fit between the readings either side of its date.
  - Readings build a **chart of use over time**, with your average hours a week. Hours-based tasks then show roughly when they'll be due at that rate (e.g. "in 18.0 h (≈ 14 Mar at your usage)"), and the **Maintenance** page lists them among the jobs due in the next 7, 30 or 90 days.
- **Done** on a task records the work, including parts, costs, who did it, and photos or receipts. It also works out when the task is next due, from the date and hours you entered.
- A task can also be skipped once, deferred to a date, or marked not applicable.
- **Log service** records unscheduled work or older history; past dates are fine.

The **Maintenance** page lists what's overdue, what's due in the next 7, 30 or 90 days, equipment with no service recorded yet, and recent work.

**This season** lists the dated jobs for the next three months, and suggests seasonal jobs your equipment doesn't have yet (soonest first), such as winterising the mower in October or a pre-season check in March. **Add** turns one into a yearly task, due on the 1st of its month. If you're in the southern hemisphere, choose it under **Settings → Seasons**.

**Reminders.** Once a day, each task that has just become due soon or overdue sends one `home_inventory_maintenance_due` event to Home Assistant. It says whether the parts are in stock and which are missing. Parts it needs that aren't in stock are added to **Restock** (turn this off under **Settings → Add missing maintenance parts to the shopping list**). To get these on your phone, turn on **Send maintenance reminders to your phone** in the Home Inventory integration's options and choose your phone's notify action.

Service history is kept when tasks are changed or removed, and even when you stop tracking an item.

### Reading the manual

On equipment you track, **Read the manual** has the AI read its owner's manual. Choose the PDF attached to the item, or add one. A scanned PDF is read page by page as pictures: the first 12 pages, or the pages you type in **Pages to read** (e.g. 18-26). For a paper manual, photograph the maintenance pages. For equipment you haven't set up yet, **Track maintenance** offers **Read the manual** first: reading it sets up the schedule and parts and turns tracking on.

The AI picks out:
- **each service and its interval**, such as "change oil every 50 hours or yearly, first at 5 hours";
- **what that particular service needs**, since the 50-hour service may need oil only while the 100-hour service also needs a plug and an air filter;
- **the parts and consumables**, with part numbers and grades exactly as printed and the page they're on.

You then review the result:
- Untick anything you don't want, and correct anything that's wrong.
- A service that matches one of your tasks updates it instead of adding a copy.
- A part that's already in your inventory, matched by part number, is reused.
- Oil or grease given only by its grade becomes a **standard** (see below).

Nothing is saved until you tap **Save the plan**. Part numbers are only copied from the manual, never guessed.

### Parts, consumables and standards

Each task can list the parts it uses: tap **+ Parts** under the task. Each part shows whether you have enough:
- **In stock**;
- **Not enough**;
- **None in stock**;
- **Check**: something might do, but you haven't confirmed it yet.

**Standards (any brand will do).** A standard is a grade rather than a brand, such as "Engine oil SAE 10W-30".
- A task that needs a standard can use any brand you've confirmed as meeting it.
- A task that names a brand can also use equivalents, unless you untick **Or an equivalent** (for example, if the warranty requires that brand).
- To link a product to a standard, open it and tap **Part details**. Enter its pack size (e.g. 1 L) and specifications (e.g. Viscosity: 10W-30). Matching standards are then *suggested*, and you **Confirm** each one. Nothing counts as equivalent until you confirm it.
- **It's been replaced by…** records a superseded part number. The newer part counts too, but only one step: a part that replaced the replacement isn't counted.

**Parts needed.** On the **Maintenance** page, this tab adds up the parts for everything due across all your equipment. If the mower and the generator both need 10W-30, one bottle of Castrol counts once for both. Anything short can be added to the shopping list.

**When you complete a service**, its parts are filled in from your stock:
- Pieces such as plugs and filters are taken out of stock.
- For oil and other liquids, tick *used up* when a bottle is empty.

## Restocking

Set a minimum and a restock level in an item's **Restock** section, and add where to buy it (Amazon, Lowe's, Tractor Supply or any shop). The **Restock** page lists what's running low with suggested quantities, and keeps a shopping list grouped by shop.

**Open at <shop>** opens the product pages; for Amazon, it opens Amazon's own add-to-cart confirmation. Nothing is bought or added for you. Tap **It's in my cart** when it is, and **Bought** to add it to your stock. See the [restock guide](https://github.com/Legal-Copy7045/home-inventory/blob/main/docs/replenishment.md) for what each shop allows.

## NFC tags

Stick NFC tags on equipment, boxes or shelves. Tapping one with your phone opens the record, or goes straight to an action such as *Log hours*. Assign tags from the record's **NFC tags** section. Android phones can write tags directly; on iPhone, copy the link into an NFC writer app. See the [NFC guide](https://github.com/Legal-Copy7045/home-inventory/blob/main/docs/nfc.md).

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

**Shelf labels.** To label a shelf, rack, cabinet or drawer, open it and tap the QR icon → **Print label**, or choose it under **Print Labels → Locations**. Tick **Print contents list** (on by default from a shelf) and the label also lists what belongs there: the shelves or drawers within it (› Top shelf), the boxes (#7 Christmas lights) and the loose items. That way anyone putting something away can see where it goes. Scanning it shows the up-to-date list.

**Arrows.** A label can carry a large arrow (up, down, left, right or a diagonal) pointing to where the shelf or box is, e.g. a label at eye level on the end of a rack with an arrow up to the top shelf. Set one for all the selected labels, or pick one per label in the list.

The link printed on the label is the address you used to open Home Assistant when you printed it, e.g. `https://ha.example.com/b1a256fc_home_inventory/c/ABCD2345`. Print labels while using the address your phone can reach. To always print a specific address (for example your external one), set *QR label link* in Settings.

The in-app **Scan** screen reads every label format, including labels printed before version 0.1.2.

## Backups

The App's data (database, photos, receipts) is included in Home Assistant backups. The App pauses for a few seconds during a backup so the database is captured consistently. Restoring a Home Assistant backup restores the inventory. Administrators can also download a portable archive under **Settings → Backup & restore**.

## Support

See https://github.com/Legal-Copy7045/home-inventory

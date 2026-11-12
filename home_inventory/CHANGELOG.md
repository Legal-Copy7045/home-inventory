# Changelog

## 0.9.0

- **Photo inventory for shelves.** Open a location (a shelf, racking bay, bench or wall) and tap **Photo inventory**. The AI lists every kind of item in the one photo, with quantities and where each is ("top shelf, left").
  - Review everything before saving: edit the name and quantity, and tap **Details** for category, maker, model and notes.
  - Remove wrong suggestions and add anything the AI missed.
  - Confirmed items become loose items on that shelf, with the photo attached.
  - Re-photographing a shelf matches what's already recorded instead of duplicating it, and offers to remove things that have gone.
  - Container photo inventory gets the same fuller editing.
- **Fixed:** the photo inventory review could open empty after the AI finished. It now shows the results.
- **Select several items** on the Items page: tap **Select**, tick items (or **Select all**), then **Move**, **Edit** (category, maker, shop, purchase date, status) or **Delete** them together.
  - Each action applies to all the selected items or none.
  - Deleted items can still be restored one by one.
- **Sort by location** on the Items page groups items under their place.
- Read-only users no longer see names of customer equipment in photo inventory suggestions.
- Database: photo inventories can belong to a location (an automatic, additive migration with a snapshot first).

## 0.8.0

- **Quick photos on their own.** Quick photos is now in the main menu and on the dashboard, not only inside a container or location.
  - Take photos with no place yet. Tap **Next shelf** when you move to another shelf, box or drawer.
  - **To sort** (new) shows the photos grouped shelf by shelf. Choose where a group is and every photo in it moves there at once. Untick any that belong somewhere else first.
  - Sorting is recorded in history as a normal move. Details are still added from **To finish**, before or after sorting.
  - Quick photos from inside a container or location work as before.
- The dashboard shows how many photos are waiting to be sorted (`unsorted_photos` in the stats API).

## 0.7.1

Security and data-integrity fixes. Update recommended; nothing to do afterwards.

- **Read-only users could open customer repair photos and documents.** The attachment links didn't check whether a file belonged to customer equipment or a repair job. Now:
  - read-only users don't see customer equipment at all: not its items, files, service history, NFC tags, links or history;
  - customers and repair jobs stay members-only;
  - this is enforced centrally for every page and lookup, not endpoint by endpoint.
- **A household purchase could be added to a customer's holding.** Choosing a customer's holding when marking a shopping-list item bought is now refused, and nothing changes. Taking service parts from a customer's stock is refused too.
- Someone made read-only in the app can't use the first-run setup code to become administrator.
- History entries and Home Assistant events no longer include the names of customer equipment.
- **Item photos on phones.**
  - The whole photo now shows, centred, instead of a cropped strip.
  - Tapping a thumbnail switches the photo.
  - Tapping the photo opens a full-screen viewer in the app, with swipe, arrows and pinch-to-zoom; swipe down or tap ✕ to close.
  - The iPhone long-press preview pop-up no longer appears on photos.

## 0.7.0

- **Restocking and shopping list.**
  - Per item: minimum, restock level, buy-at-least and unit, plus one or more shops (Amazon, Lowe's, Tractor Supply, any other) with link, product number, pack size and price.
  - The **Restock** page shows what's below minimum or getting low, with suggested quantities (e.g. stock 1, minimum 2, restock to 5 → buy 4) and estimated cost.
  - The shopping list is grouped by shop. Select items, change quantities, move them to later, mark them bought (optionally adding them to stock), and see the last price paid.
  - **Open at shop:** Amazon gets one "review & add" link to Amazon's own add-to-cart page, where you confirm. Lowe's and Tractor Supply open the exact product page (or a search), because neither offers a way for apps to add to your cart.
  - The app never claims something is in your cart until you tap **It's in my cart**.
  - Scanning a barcode you already own offers **Add to shopping list**. Customer equipment is never restocked.
- Settings: Amazon store (e.g. amazon.co.uk) and an optional Associates tag.
- Home Assistant: sensors for supplies below minimum and shopping list items; event `home_inventory_shopping_list_updated`.

## 0.6.0

- **NFC tags** for items, containers, locations and kits, alongside QR labels.
  - Tags hold a link with a random reference: no IDs and no tokens. Opening one still needs your Home Assistant login. The link keeps working when things move.
  - Choose what a scan does: open the record, log hours, log service, move, use one, start a repair job, or mark returned. The scan page shows that action first as one big button.
  - **Android:** write and read tags straight from the app (in its own tab). Duplicate stickers are detected from the chip's serial number.
  - **iPhone:** copy the link (or scan its QR code) into an NFC writer app. iPhones open link tags natively.
  - **NFC tags** page: change actions, deactivate or reactivate, replace lost or damaged tags, and show the link and QR fallback.
  - Home Assistant event `home_inventory_tag_scanned`.
- Fix: read-only users could not scan QR labels (the lookup was treated as a change).

## 0.5.0

- **Related items.** Link accessories, components, compatible parts, things stored together and replacements, or use your own description. The link shows on both items. Items stay separate, with their own place and QR label.
- **Search shows related equipment.** Searching for "air compressor" shows its hose, inflator and fittings right under the result.
- **Kits.** Name a collection (e.g. "Air compressor kit") and where it's kept. It shows which items are with the kit, stored separately, on loan, missing or short. Optional members don't count against completeness.
- Fix: a button inside a form label took the label's text as its name (affected screen readers).

## 0.4.0

- **Customers and repair jobs** for equipment you fix for other people.
  - Customers with phone, email, address, notes and an optional contact log.
  - Customer-owned equipment is clearly marked (purple **Customer** badge) and kept out of your own totals, value and alerts. Ownership only changes through the confirmed, recorded **Change owner** action, never by editing an item.
  - Repair jobs (RJ-0001…) with condition on arrival, reported problem, diagnosis, approval notes, estimate, and status from received through ready for pickup to returned.
  - Work and parts recorded on a job also go into the equipment's service history.
  - Charges are worked out from the recorded parts and labor, or you can set them yourself.
  - Photos of damage and receipts, multiple visits per item, and a **printable work order**.
  - Repairs and Customers pages; repairs in progress and ready for pickup on the Maintenance page and dashboard.
  - Customer details are visible to members and administrators only.
- Home Assistant: sensors for repairs in progress and awaiting pickup; events `home_inventory_maintenance_completed` and `home_inventory_repair_job_status` (no customer details).

## 0.3.0

- **Maintenance tracking** for equipment: open an item and tap **Track maintenance**.
  - Ready-made starting points for lawn mowers, string trimmers, chainsaws, leaf blowers, generators, pressure washers and snow blowers, or any other equipment.
  - Equipment details: engine make, model and serial, fuel, and your own list of specifications and part numbers (oil, spark plug, filters…).
  - Tasks by calendar, operating hours, uses (e.g. tanks of fuel), or any mix, whichever comes first. One-off tasks too.
  - Log operating hours, including estimates for machines with no hour meter.
  - Record service with parts (optionally taken from your stock), costs, who did it, and photos or receipts. The next due date and hours are worked out for you. A task can also be skipped, deferred or marked not applicable.
  - Full service history per item, including work done before you started using the app.
  - New **Maintenance** page: overdue, due in 7/30/90 days, equipment with no records, and recent work. Dashboard card for overdue maintenance.
  - Home Assistant sensors: maintenance overdue, maintenance due soon.
- Fix: opening another item while an edit sheet was open kept the sheet open on the new item.

## 0.2.0

Security release. Nothing to do when updating; your data and roles are kept.

- **No automatic administrator.** Previously, if the App couldn't read Home Assistant's user list, the first person to open it became administrator. Now nobody is promoted automatically.
  - Administrators come from Home Assistant, from `admin_users`, or (when neither is available) from a one-time **setup code** printed in the App log, which only Home Assistant administrators can see.
  - Existing administrators keep their role, and a temporary Home Assistant outage no longer changes anyone's role.
- **Safer restore.**
  - Backup archives are fully checked before anything is touched: size limits (new option `backup_max_mb`), allowed files only, no unsafe paths or links, and zip-bomb detection.
  - The database is checked and upgraded in a staging area first. Your current data is swapped out only at the end, and put back automatically if anything fails.
- **Product link import** connects only to the exact public address it checked, which closes a DNS-rebinding gap.
- **No access to Home Assistant's `/media` folder.** Photos for Home Assistant's AI Task are now handed over through Home Assistant's media API and removed straight after. The App no longer mounts `/media`.
- **Limits.** Uploads are size-limited before they reach the disk. AI, web-link and online barcode lookups are rate-limited per person.
- CI now runs dependency audits (Python and JavaScript), security lint rules, a secret scan and a container image scan, plus tests that check every API route's permissions.

## 0.1.7

- **Fix: Save & next showed the previous item's details.** When moving to the next quick photo, the form could open before the next item had loaded and keep the last item's name and category (for example, a photo of funnels pre-filled as "carburettor"). The form now waits for the right item.

## 0.1.6

- **Quick photos**: open a container or location and tap **Quick photos**, then photograph item after item. Each photo becomes an item in that box straight away. Uploads queue in the background, so you can keep shooting, and a failed upload can be retried.
- **To finish** list (sidebar, and a card on the dashboard) gathers everything captured this way. Open one to get the photo next to an **Add details** form, then use **Save & next** to work through the rest.
- When AI is set up, it looks at each quick photo in the background and pre-fills a suggested name, category and description for you to confirm.

## 0.1.5

- Security clean-up (GitHub code scanning): the app's web files are now served by the framework's built-in static file handler, and a failed AI search interpretation no longer returns the provider's error text to the browser (details go to the App log).
- Label sheets are listed by every compatible Avery number: **5163 / 5523 WeatherProof / 8163 / 5963** (2×4 in, 10 per sheet) and **5160 / 5520 WeatherProof / 8160 / 5960** (1×2⅝ in, 30 per sheet). New: **5164 / 5524 WeatherProof / 8164** (3⅓×4 in, 6 per sheet) for large totes. US Letter layouts now use Avery's exact published dimensions.

## 0.1.4

- **Fix: App would not start after updating to 0.1.2 or 0.1.3** on installations with nested locations ("duplicate column name: url" in the log). The database upgrade stopped half way and could not be retried. This version completes the upgrade automatically on start; no data is lost and nothing needs to be done by hand.
- Database upgrades now run as a single all-or-nothing step, so a failure can never leave a half-upgraded database again, and the first pre-upgrade snapshot in `/data/backups` is kept instead of being replaced on every restart.

## 0.1.3

- **Import locations from Excel**: *Locations → Import from Excel* creates your whole location hierarchy from a spreadsheet. Download the template in the app (blank, or pre-filled with your current locations), fill in one row per place (`Level 1 | Level 2 | … | Kind | Description`), upload, check the preview and confirm. Parents are created automatically, existing places are reused (safe to import again), outline-style sheets and CSV files work too, and problem rows are listed instead of stopping the import.
- Phone layout fixes on the Locations page header and in pop-up panels with long text.

## 0.1.2

- **QR labels open in your phone's camera**: labels now carry a link to the item's page in Home Assistant (built from the address you use to open Home Assistant, or the *QR label link* setting). Scanning one shows the container's contents. Labels printed with earlier versions only work in the in-app Scan screen, so reprint them.
- **Location labels**: shelves, cabinets, drawers and other locations now have permanent QR codes too (existing locations get one on upgrade). Scanning one shows everything stored there. Print them from *Print Labels → Locations* or the QR button on a location's page.
- **Import from a web link**: paste a shop or manufacturer link when adding an item and the App fills in name, brand, model, barcode, description, category, price, currency, retailer and the product photo (from the page's structured product data, falling back to AI). Everything is shown for review before saving. The link is kept on the item ("Product page"), and *Edit → Fill from a web link* fills any empty fields later. Links to local/private network addresses are refused.
- Search results: fix the broken row layout (photo, name and quantity stacked instead of side by side); map links now sit inside each result.
- Long item names wrap onto two lines instead of being cut off on phones.
- Clearer answer when several different items match ("Found 5 items matching "hose" in 2 places: ...").
- Packaging: Docker HEALTHCHECK replaces the obsolete watchdog URL; manifest tidied for the App linter.

## 0.1.1

- Fix "Frontend not built" on Home Assistant OS: the base image's s6-overlay started the App with an empty environment. The environment is now kept, and settings, the Supervisor token and the UI folder are also found without it. This also restores AI Task (Gemini), automatic discovery of the companion integration and detection of Home Assistant administrators.
- New icon and logo (light and dark), favicon and home-screen icon.

## 0.1.0

First release.

- Items with photos, AI identification (via Home Assistant AI Task such as Google Gemini, or a direct provider), purchase, warranty, receipts and documents.
- Product identities vs physical holdings; quantities, splitting, "track individually", duplicate-safe barcode matching.
- Expiry / use-by dates with an expiring-soon view, and low-stock thresholds for consumables with quick "use one" buttons.
- Hierarchical locations, nested containers, loose items, cycle prevention, full path resolution.
- Container photo inventory with review-before-save and re-scan matching.
- Camera barcode scanning (UPC-A/E, EAN-13/8, GTIN-14, Code 128) with inventory-first lookup and Open Food/Products/Beauty Facts, UPCitemdb and Barcode Lookup catalogs.
- Permanent container QR codes, PDF label sheets (Avery A4 and Letter, label printers) and Scan mode.
- Natural-language search; AI may interpret questions, answers always come from the database.
- Moves (partial, temporary, return home), loans with overdue tracking, statuses.
- Visual maps with manually placed markers.
- Home Assistant users and roles (admin, member, read-only), full audit history, trash and restore.
- Companion integration: sensors, actions, automation events, diagnostics, Supervisor discovery, sidebar proxy for Container/Core installs.

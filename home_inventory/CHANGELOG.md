# Changelog

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

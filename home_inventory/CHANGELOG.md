# Changelog

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

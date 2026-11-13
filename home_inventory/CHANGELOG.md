# Changelog

## 0.25.0

- **Photo inventory: Look closer for missed things.** A button in the review has the AI look again at overlapping close-ups of the photo (2 × 2, more across a panorama) and adds what the first look missed (small, half-hidden and edge items) to the list for checking. Things already listed or already recorded there aren't added twice. A few AI requests, within your rate limits.
- **Voice history** (Admin, administrators): each question asked by voice (Alexa, Assist, Siri through Assist) and its answer, with a filter for the ones it couldn't answer. The last 500 for 90 days, stored on your machine only; can be cleared. **Update the Home Inventory integration** for it to record questions.

## 0.24.0

- **Categories are a fixed list.** Items choose a category from a list instead of typing one, so "Kitchenware" and "Kitchen and dining" no longer appear side by side by accident. The item form, bulk edit and the photo inventory review all use the list.
- **Admin → Categories** (administrators): add, rename, give "also known as" names, **merge** (moves every item across and keeps the old name as an also-known-as name) and delete empty categories. Equipment is always available, because maintenance uses it.
- AI, barcode and photo inventory suggestions are matched to your categories (other spellings and also-known-as names count); anything else is left blank and shown as a suggestion. Administrators can add a category straight from the list with **＋ New category…**.
- Adding, renaming or deleting categories now needs an administrator (members could before). Saving an item with a category that isn't on the list is refused with a clear message.
- Existing categories are kept as they are: merge the duplicates under Admin → Categories.

## 0.23.0

- **Voice: "I've moved the engine oil to small engine repair".** Tell Home Inventory you've moved something ("Alexa, tell Home Inventory that I've moved the engine oil to container number 5"; with Assist: "I've moved…", "I put the drill in the garage", "The hedge trimmer is now in the shed"). It finds the item and the place, both forgivingly: a location by name, or a container by number ("container number 5", "box five") or name. A clear match is moved and confirmed ("Done. I've moved Engine oil to Small Engine Repair, in Shed."). If the place is uncertain it asks "Did you mean…?" (answer yes or no, or say the right place). If there are several places, or several items, it asks which. The move is permanent and is recorded in Activity history; to move it back, say so again or use Move on the item page.
- **Voice: yes and no.** Answers to "Did you mean…?" with Assist and Alexa.
- **Menu:** Maintenance & Repairs moved up with the main pages, so the folding sections (Inventory, Reports, Admin) are together.
- New App endpoint `GET /places/resolve` finds a location or container from spoken words.
- **Updating:** update the App and the integration, copy the new Assist sentences file, and update the Alexa skill (interaction model and forwarder code; see *Updating the skill* in `docs/voice.md`).

## 0.22.1

- **Alexa: "where does it go?"** The Alexa skill now understands "Alexa, ask Home Inventory where the hammer goes", "…where does the hammer go", "…where to put the engine oil" and "…to tell me where to put the engine oil", and answers with where the thing belongs (as Assist does since 0.22.0). Before, Alexa answered "Sorry, I only know where things are". **Update the skill's interaction model and forwarder code** (`deploy/alexa/`, see *Updating the skill* in `docs/voice.md`). Help now mentions the new question.

## 0.22.0

- **Voice: where does it go?** Ask Assist "Where does the hammer go?", "Tell me where to put the engine oil", "Where should I put my drill back?" or "Where does the jigsaw belong?" and it answers with the thing's usual place ("Hammer goes in Garage, Tool wall."), adding where it is now if it's out ("Saw goes in Shed. It's in Kitchen for now."). "Which one did you mean?" works as for "where is". **Update the Home Inventory integration and copy the new `deploy/assist/custom_sentences/en/home_inventory.yaml`** to your Home Assistant `custom_sentences/en/` folder, then reload conversation. The Alexa skill is unchanged.
- **Folding menu.** Menu sections (Inventory, Reports, Admin) fold away: tap the heading to open or close one. Folded sections show how many pages they hold, the menu remembers your choice, and the section with the current page stays open. A section with one page (Maintenance & Repairs) is just that page.

## 0.21.0

- **Take it / put away.** **Take it** on an item (or after scanning its barcode) marks it as with you; the Scan page shows what you're carrying. Scan a box or shelf label to put things away: in its own place (or the usual place it was temporarily moved from) it simply goes back; somewhere new, choose **Move it here** or **Just this once** (a temporary move that remembers the usual place). Items also have **Put away** to choose the place without a label.
- **What is this? Identify by photo** (Scan page, with AI): photograph something, the AI says what it is and lists your matching items; pick one to **Take it**, **Take one out**, put it in the open box, or open it. Not in your inventory: **None of these: add it** starts a new item with the name filled in. The photo isn't stored. One AI request per photo.

## 0.20.1

- **Security:** error messages no longer pass on the internal details of a failure (code scanning alert *Information exposure through an exception*). When Home Assistant, an AI service or a PDF reader fails, the App shows a plain message ("Home Assistant didn't answer", "Gemini rejected the request (400)", "This PDF couldn't be read") and writes the details to the App log. A rejected API key or unknown model is still named, so you know what to fix.

## 0.20.0

- **AI rate limits.** New App options `ai_requests_per_minute` (15), `ai_tokens_per_minute` (250000) and `ai_requests_per_day` (500), set to Gemini's free tier by default. Every AI request waits its turn so they're never exceeded, and the day's count survives restarts. Background work (manuals, documents, value estimates) waits as long as it takes and finishes over time; something you're waiting for gives up after 30 seconds and says when to try again. If the provider still says "too many requests", everything pauses for `ai_retry_cooldown` seconds (default 60) and tries again. **Settings → AI** shows what's been used.
- **Gemini's default model** is now `gemini-flash-lite-latest`, which follows Google's current Flash-Lite model instead of a version that gets retired.
- **The menu is grouped:** Inventory, Care, Reports and Admin. *To sort* and *To finish* are one page with two tabs; *Maintenance*, *Repairs* and *Customers* are one page, **Maintenance & Repairs**; **Reports** collects the inventory report, the new maintenance plan, restock & shopping and the rest; Print labels, NFC tags, Activity history, Possible duplicates, Recently deleted and Settings are under **Admin**.
- **Maintenance plan** (Reports): every piece of equipment, its jobs, how often, when last done and the parts each needs. Printable.
- **Previous / Next on item pages**, through the list you came from (Items with its filters, or the inventory report) or the whole inventory by name.
- **Inventory report:** item names open the item.
- **Location labels** print the location path bold and larger, above a line.
- **Search and voice:** "lawnmower" finds "Lawn mower" before anything that only mentions a lawnmower in its description (Alexa answered with the engine oil).
- **Photo inventory re-scans** reuse the names and boxes already recorded at that place instead of adding them again, and match plain boxes ("Plastic container" / "Plastic storage bin"). A box with a wrongly cut photo has **Redo cut-outs**, like items.

## 0.19.0

- **Undo.** Using or adding one, moving an item, changing its status, deleting an item or an empty location, and moving several items now show what happened with an **Undo** button, instead of asking first. After deleting an item you land on the shelf or box it was in.
- **Photo detail** (App configuration, `photo_detail`): `standard` suits a Raspberry Pi; `high`, for a powerful machine or VM, keeps photos at 6144 px instead of 4096, makes sharper wide-area panoramas and cut-outs, and sends the AI more detail (2048 px instead of 1568).
- **One photo button.** *Add item* has a single **Add photos** button; your phone offers the camera, photo library or files.
- **Home** no longer lists recent activity (it's under **Activity history** in the menu); a short getting-started note shows while the inventory is empty.
- **Voice: numbers either way.** "0 turn mower" (as Alexa writes "zero") finds "Zero-turn lawn mower", and "two stroke oil" finds "2 stroke oil". This works in the app's search too.
- **Alexa: bare answers.** Answering "which one did you mean?" with just a name ("zero turn", "toro lawn mower") now works. When no question is open, a bare name is answered as "where is…". If Alexa doesn't understand something it now ends the skill, so your next "Alexa, ask Home Inventory…" starts afresh instead of getting stuck. **Update the Alexa interaction model and the forwarder code** (see `docs/voice.md`).

## 0.18.0

Less clutter, fewer taps.

- **Adding an item** asks for what matters first: name, category, quantity, then **Where is it?**. Make, model, barcode, link, description, expiry and low-stock alert are under *Make, model, barcode & more details*, which opens by itself when AI, a barcode or a web link fills any of them. Importing from a web link moved to the end.
- **It remembers where you put the last thing.** A new item starts in the same place as the last one you added (tap to change), so filling a shelf doesn't mean picking it every time.
- **Item pages are shorter.** With no photo there's a small **Add photo** button instead of a big empty frame. Related items, restock alerts, NFC tags and documents only take up space once you use them; until then they're one row: *Add: Receipt or document · Related item · Restock alert · NFC tag*.
- **Places say what's in them.** A rack's shelves show "1 box · 4 things" (counting everything on them, including in boxes and smaller places), and "No loose items here" no longer appears when the things are on its shelves.
- **Box and shelf lists don't repeat the place.** Items in a box or on a shelf show their category instead of the place you're already looking at.
- **Home** shows the last 5 changes (all of them under *All*).
- Locations and boxes: NFC tags move to an *Add* row until one is assigned.

## 0.17.0

- **Estimate values for many items at once.** On **Inventory report**, *Estimate values with AI* estimates replacement and second-hand values for **items without a price**, **items in a category**, or **everything**. It shows how many items and AI requests that is before you start; the same product kept in several places is estimated once, and items estimated in the last 30 days are skipped unless you include them. It runs in the background with a progress bar and can be stopped. Administrators only (it may cost money with a paid AI provider); customer equipment is never included.
- The report shows an AI estimate (≈) for items without a price.
- Radio buttons are now the normal size everywhere.

## 0.16.2

Security hardening.

- API responses are never cached by browsers or proxies (`Cache-Control: no-store`), and other sites can't embed the App's pages, photos or data (`Cross-Origin-Resource-Policy: same-origin`). The web server no longer announces itself.
- API docs on the internal API port are only served while `log_level` is `debug`.
- The QR label address in Settings must be a web address (http/https).
- Wide-area photo inventories shrink each photo as it's read, so very large photos can't exhaust the App's memory.
- Integration (Container/Core sidebar panel): writes the browser marks as cross-site are refused even without an Origin header, and sign-in links and sessions are capped.
- `docs/security.md` has a checklist for hardening Home Assistant itself, which is the internet edge.

## 0.16.1

- **Arrows on labels.** Container and shelf labels can carry a large arrow (up, down, left, right or diagonal) pointing to where the shelf or box is. Set it for all selected labels or per label on **Print Labels**.
- **Shelf labels list what belongs there.** A location's contents list now includes the shelves or drawers within it (› Top shelf) as well as its boxes and loose items, and it's ticked by default when you print a label from a shelf, so things get put back in the right place.

## 0.16.0

- **Maintenance reminders.** Once a day, a task that has just become due soon or overdue sends one `home_inventory_maintenance_due` event (once per service interval), saying whether its parts are in stock and which are missing. The integration can send it to your phone: turn on **Send maintenance reminders to your phone** in its options.
- **Parts onto the shopping list.** Parts a due task needs that aren't in stock are added to **Restock** automatically, with a note saying which job they're for (Settings → *Add missing maintenance parts to the shopping list*; on by default).
- **This season.** A new tab on the Maintenance page lists dated jobs for the next three months and suggests seasonal jobs your equipment doesn't have yet (winterise the mower in October, pre-season check in March...). **Add** makes it a yearly task. Northern or southern hemisphere under Settings → Seasons.
- **Wide areas.** On a location, **Wide area (2-4 photos)** joins photos taken left to right into one panorama for the photo inventory.
- **Labels for new boxes.** After a photo inventory adds boxes, it offers to print their labels.
- **Warranty claim pack.** On an item, **Download the claim pack** gives a ZIP with a summary PDF (make, model, serial number, purchase and warranty details) and the receipts, documents and photos.
- **Value estimates.** **Estimate with AI** gives a rough replacement price and second-hand range for an item, kept with it and dated. The inventory report shows it in its own column and totals it for items without a price.
- **More voice questions.** "What maintenance is due?", "What's running low?" and "What's lent out?" with Assist, Siri (through Assist) and the Alexa skill. Update your custom sentences and the Alexa interaction model (see `docs/voice.md`).
- Database: one new column (`maintenance_tasks.due_notified_key`); the update migrates automatically.

## 0.15.4

- **Opens faster.** The app's main download is about a third of the size: pages load when first opened (and in the background once the app is idle, so they still work offline), and the barcode decoder loads only when a scan needs it.
- **Faster maintenance dashboard.** Rates of use are worked out for all equipment in one query, and only for tasks counted in hours or uses.
- **Faster lookups.** Matching a part number, redoing a photo inventory's cut-outs and filtering search by status, loans, warranty or expiry no longer read every product, item or history entry.
- Behind the scenes: the API is split into smaller modules, background polling shares one helper (and pauses while the app is hidden), and the web app has unit tests, including word-search cases shared with the server.

## 0.15.3

- **Photo dates.** A meter photo picked from your library now takes its date from the photo itself (when it was taken), not from the file's modified date, which is often the day it was copied or shared. Photos are still stored without their location data.
- **AI jobs queue up.** Reading manuals and documents now runs on a small shared pool (three at a time); more wait their turn instead of all starting at once.
- **No silent skips.** If AI was too busy to read a document you uploaded, the document now says so, with **Try again**, instead of quietly not being read.
- **Consistent plurals.** Search, photo inventory matching and location search now share one rule for plurals ("shelves" = "shelf", "boxes" = "box").
- **Faster lists.** Item lists, search results and the dashboard load photos, loans, categories and owners in a few queries instead of several per item.

## 0.15.2

- **Maintenance only on equipment.** Equipment is now the only category with maintenance by default. The Maintenance section appears only on items tracked as equipment, and **Track maintenance** (a link at the foot of *Details*) only shows on items whose category is **Equipment**. Items already being tracked keep their section. The AI now suggests the Equipment category for powered machines such as mowers, generators and pressure washers. The setup screen offers **Read the manual** first (which sets up the schedule and parts) or a type of equipment.
- Removed the extra "Read for the service schedule and parts" button from manual documents, which repeated **Read the manual** and overflowed on phones.

## 0.15.1

- **Unidentified things are kept for later.** A photo inventory now also lists objects the AI can see but can't identify, with a plain description ("Black hard case") and the badge **Unidentified – to finish**. When you confirm, they're saved with their own cut-out photo on the **To finish** list, where you can say what they are. Untick the badge if you name one during the review.
- **Last photo again.** On a location or container, **Last photo again** has the AI look at the latest photo inventory photo once more, without taking a new one. Things already recorded are recognised, and anything new (such as unidentified objects) is offered for review.
- **Choose photos from the library everywhere.** Every photo button (item photos, photo inventory, the barcode photo, the hour meter, new items, and Quick photos without the live camera) lets you take a photo or pick one from your library; none forces the camera.

## 0.15.0

- **Photograph the hour meter.** In **Log hours** on equipment, take a photo of the hour meter (or use counter). AI reads the number, including the tenths drum on mechanical meters, and you check it before saving. The photo is kept with the reading.
- **Readings have dates.** Log a reading for any past day; a photo picked from the gallery uses the day it was taken. Each reading has to fit between the readings either side of its date, unless it's marked as a correction.
- **Use over time.** The maintenance section shows a chart of hours (or uses) by date and your average use per week, worked out from the last six months.
- **Hours-based work gets a date.** At your recent rate of use, a task like "oil every 50 hours" shows roughly when it will be due (e.g. "in 18.0 h (≈ 14 Mar at your usage)"). The Maintenance page lists it among the jobs due in the next 7, 30 or 90 days, as well as once its hours are near.
- Database migration 0012 adds one column.

## 0.14.0

- **Fixed: photo inventory cut-outs showing the wrong part of the photo.** The AI is now asked for each thing's position in the box format vision models are trained on (`[ymin, xmin, ymax, xmax]` on 0–1000), and it's converted in one place. A box that covers most of the photo is ignored. In the review, each row's cut-out has a **Photo** tick: untick it when the cut-out is wrong and the entry is saved without one. For things already saved with wrong cut-outs, **Redo cut-outs** (on any item from that photo inventory) makes them again from the original photo for every item and box it added. Anything it can't place shows the whole photo instead, and photos chosen by hand are kept.
- **Scanned PDFs can be read.** A PDF that's only pictures of pages is now read as pictures:
  - a manual page by page, the first 12 pages or the ones you choose in **Pages to read** (e.g. 18-26);
  - a receipt or warranty from its first page or two.
- **Read the manual is always available** for items with AI set up, including new equipment that isn't tracked yet. Reading a manual turns maintenance tracking on. Manual documents also have a **Read for the service schedule and parts** button.
- **Labels:**
  - Titles and locations wrap onto more lines between words, at full size; words are never split, and the location is never cut off.
  - The order is number (large), title, description, contents, with the location at the foot.
  - When contents don't fit, "+ N more" is shown.
- **Container numbers:** a new container starts with the next free number, and you're warned (and asked to confirm) when a number is already used by another container.
- **Search by words in any order:** "shed right front shelf" finds *Shed › Front Right Shelving › Right Bottom Shelf*. This works in the place picker (Move, new container…) and the Containers search, across the whole path, with plurals and word endings handled ("shelf" finds "Shelving").

## 0.13.1

- **Fixed: "The update could not be started: [object Object]".** Home Assistant was building the App on your own machine at every update (installing build tools and compiling the web app), and that build could fail. The App now uses the ready-made image published with each release, so updates just download it: they're faster and don't depend on building locally.

## 0.13.0

- **AI reads the documents you add to an item.** Add a receipt, warranty card, invoice, order email, manual or certificate (as a PDF or a photo). In the background, AI works out what it is and what's printed that belongs in the item's fields: purchase date, price, shop, warranty start and end, make, model and serial number. **Review** shows the suggestions.
  - Empty fields are ticked.
  - Anything that would replace an existing value is shown with both values and left unticked.
  - Values can be corrected before saving, and the document can be filed under the right type and given a name.
  - A document about a different product isn't used to fill anything in.
  - Older documents can be read on request.
- The document type list starts with *Not sure – let AI work it out* when AI is set up.
- The old "read this receipt" step while uploading is replaced by the background reading, which also works for PDFs and every document type.
- Database migration 0011 adds one table.

## 0.12.1

- **Fixed: "Load failed" / "Failed to fetch" when reading a manual.** Reading a long manual can take minutes, longer than browsers, phones and proxies keep a request open. It now runs in the background: the app shows *Reading the manual…* and checks every few seconds. You can close it and come back; the result waits on the item. Asking again while one is running doesn't start a second reading. A reading cut off by an App restart is reported as interrupted, so it can't spin forever.

## 0.12.0

- **Read the manual (equipment).** The AI reads an equipment's owner's manual (its PDF, or photos of the pages for a paper or scanned manual). It finds each service and its interval, including first/break-in services, and what *that* service needs: the 50-hour service and the 100-hour service can need different parts. It also lists the parts and consumables, with part numbers and grades exactly as printed and their page numbers. You review and edit everything before it's saved. Matching tasks are updated rather than duplicated, and parts already in your inventory are reused.
- **Parts on maintenance tasks, with readiness.** Each task lists what it uses, and each part shows *In stock*, *Not enough*, *None in stock* or *Check*. The equipment's model remembers what it takes, so a second machine of the same model shares it.
- **Standards: any brand that meets the grade.** A standard such as "Engine oil SAE 10W-30" accepts any product you've confirmed as meeting it. Matching specifications only *suggest* the link. A task that names a brand can use equivalents unless you untick that, for example for a warranty. Superseded part numbers count one step only.
- **Parts needed** on the Maintenance page adds up the parts for everything due across all equipment, so shared oil or plugs are counted once. Shortfalls go onto the shopping list.
- **Completing a service fills in the parts from stock.** Pieces are taken out of stock; for liquids you tick when a bottle is used up.
- Products have an optional pack size (e.g. 5 L) and specifications.
- Database migration 0010, which only adds tables and columns.

## 0.11.0

- **Photo inventory of a whole area.** Photograph a whole shelving rack, bench, pegboard or corner of a room, choosing "Photo inventory" on a location. The AI:
  - works out the layout (racks or units and their shelves);
  - recognises each thing and puts it on the right shelf;
  - turns labelled boxes and totes into containers, using their label as the name.
  
  You review the layout and every row before anything is saved. Only the shelves you actually use are created, and existing shelves and boxes are matched by name, so photographing the same area again doesn't duplicate them. It works with any layout, including a single shelf or a photo with no shelves at all.
- **Voice answers on your phone.** A new integration option, "Also send voice answers to your phone" (off, Alexa only, or all voice assistants), sends the final answer with its location as a notification through the notify action you choose, so you don't have to ask again. Questions and "couldn't find" answers aren't sent.
- **Fixed: voice name matching.** Plurals are now made singular by explicit rules instead of dropping every final "s". So:
  - "Glass", "Compass", "Class" and "Status" match again;
  - "Safety glasses" stays different from "Safety glass";
  - sizes ("50 ft" = "50ft" = "50-foot") and model numbers ("HRX-217" = "HRX 217") match however they're written, and different sizes never match.

## 0.10.3

- **Voice answers use "are" for plural names:** "Wood screws are in Basement…", "Pliers are lent to Sam", while "Axle stand is…" and "Box of screws is…" stay singular. It goes by the name, not the quantity.

## 0.10.2

- **Voice answers say the box first.** "Kink Control Plus Water Hose 50ft is in Plastic tote number 1, labelled Hoses, located in Shed, Rear Right Shelving, Middle Shelf." instead of reading the whole path in one go. Boxes inside boxes are named innermost first. The box's type (set on the container, e.g. "Plastic tote") is used when it has one.

## 0.10.1

- **Voice asks when it's unclear.** "Where is the hose?" used to read out every match in one long sentence. Now, when your words match different things, it asks: "I found 3 things for hose: Kink Control Plus Water Hose 50ft, Siphon pump hose and Siphon pump with hose and funnel. Which one did you mean?" Reply "the water hose one", "the one with the funnel" or "the second one".
  - Several records of the same thing are answered together ("Battery is in A, and in B").
  - Alexa and Assist voice devices keep listening for your reply.
- **Update your voice setup:**
  - Alexa: paste the new `deploy/alexa/interaction-model.json` (adds the reply intent), build the skill again, and replace the skill code with the new `deploy/alexa/lambda_function.py`, keeping your three settings.
  - Assist: add the `HomeInventoryChoose` lines from `docs/voice.md` to your sentences file.

## 0.10.0

### Fixed
- **The Items page showed at most 300 items.** It now pages through everything with **Show more**. Filtering and paging happen in the database, so large inventories stay fast.
- **Select all** on the Items page now selects every item matching the filter, not just the ones on screen.
- **Changing an item's status could leave a loan open.** Setting a lent-out item to "In storage" (singly or in bulk) is now refused; mark the loan returned instead.

### New
- **Undo after deleting**, and a **Recently deleted** page (link under the Items list) to restore several items at once.
- **Multi-select everywhere:** container contents, a location's loose items, search results and To finish, as well as Items.
- **Bulk Lend** (several items to one person) and **Add to kit** (an existing kit or a new one), next to Move, Edit and Delete.
- **NFC tags: Move to…** points an existing tag at a different item, container, location or kit.
- **Quick photos are saved on the phone first.** If the signal drops or the app is closed mid-way, they upload the next time Home Inventory is opened. A banner shows how many are waiting; refused uploads can be retried or discarded.
- **Scan → Take one out:** in this mode every barcode scanned uses one from stock (from the open container when there is one; you're asked which if it's ambiguous). Restock alerts update straight away.
- **Inventory report** (menu → Inventory Report): everything you own grouped by room, with photos, maker, model, serial number, purchase details, recorded value and which items have receipts. Filter by place or category; print it (or save as PDF), or download **CSV** or **Excel** (with a value-by-room sheet). Customer equipment is never included. Spreadsheet formulas typed into item names are neutralised in downloads.
- **Read receipts with AI:** adding a receipt photo to an item reads the purchase date, price, shop and any printed warranty length; tick what to copy to the item. Nothing is saved until you choose.
- Docs: a ready-made Home Assistant automation for the existing warranty-ending event.
- **Operating hours from Home Assistant:** in an item's equipment details, pick a runtime sensor (a smart plug's runtime, a `history_stats` sensor…) and/or a uses counter. They're read every 15 minutes (minutes and seconds are converted to hours) and maintenance due dates follow real use. A sensor that goes backwards (reset) isn't recorded; the item page says why. **Read now** checks straight away.
- **Possible duplicates** (link under the Items list): pairs with the same barcode, the same maker and model, or very similar names. **Keep this one** merges the other into it — holdings, photos, shop links, restock settings and shopping-list lines move over and blank details are filled in; holdings stay in their own places. "Not the same thing" stops suggesting a pair.
- **Photo inventory improvements:** each new item gets its own picture cut from the shelf or box photo (where the AI could say where it is), shown as a preview while reviewing. Things already recorded but not seen in the photo can be left alone, **marked missing**, or deleted.
- **Voice: "Where is the hedge trimmer?"** The integration adds the `HomeInventoryWhereIs` intent with a short spoken answer (place, who it's lent to, or "for now, normally…"). Works with Assist (add the sentences file from `docs/voice.md`), Siri via a Shortcut that sends text to Assist, and an Alexa custom skill ("Alexa, ask Home Inventory where…"). Update the integration in HACS to get it. For Alexa, `deploy/alexa/` has a ready-to-paste skill definition and forwarding function, and `docs/voice.md` has step-by-step setup.
- Database: equipment profiles can name Home Assistant sensors (an automatic, additive migration with a snapshot first).
- **Installable web app (PWA):** a manifest, icons and a service worker that caches the app itself (never your inventory data) for faster, flaky-signal loading. See the docs for where installing works.

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

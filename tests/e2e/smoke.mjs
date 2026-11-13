// End-to-end smoke test of the web UI against a running backend.
// The backend must trust 127.0.0.1 as the Ingress proxy; identity headers are
// injected the way the Supervisor's Ingress proxy does it.
//   HINV_URL=http://localhost:18099/ node tests/e2e/smoke.mjs [screenshot-dir]
import { createRequire } from "module";
const require = createRequire(import.meta.url);
const { chromium, devices } = require("playwright");

const BASE = process.env.HINV_URL || "http://localhost:18099/";
const SHOTS = process.argv[2];
const headers = { "X-Remote-User-Id": "e2e-user", "X-Remote-User-Name": "sam", "X-Remote-User-Display-Name": "Sam Example" };

const browser = await chromium.launch();
const ctx = await browser.newContext({ ...devices["iPhone 13"], extraHTTPHeaders: headers });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => m.type() === "error" && !m.text().includes("favicon") && errors.push(m.text()));
const api = (path, opts = {}) => page.request.fetch(BASE + "api/v1/" + path, { headers, ...opts }).then((r) => r.json());
const shot = async (name) => SHOTS && page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });

// Seed through the API
const prop = await api("locations", { method: "POST", data: { name: "Property" } });
const shed = await api("locations", { method: "POST", data: { name: "Shed", parent_id: prop.id } });
const garage = await api("locations", { method: "POST", data: { name: "Garage", parent_id: prop.id } });
const box = await api("containers", { method: "POST", data: { name: "Box", number: "15", location_id: shed.id } });
await api("items", { method: "POST", data: { name: "Extension cord", quantity: 2, container_id: box.id, category: "Electrical", barcode: "012345678905" } });
await api("items", { method: "POST", data: { name: "Extension cord", quantity: 1, location_id: garage.id, barcode: "012345678905" } });
await api("items", { method: "POST", data: { name: "AA batteries", quantity: 3, min_quantity: 4, container_id: box.id, expires_on: "2026-11-20" } });

// Dashboard
await page.goto(BASE);
await page.getByText("Running low").first().waitFor();
await shot("01-dashboard");

// Natural-language search
await page.getByRole("searchbox").fill("how many extension cords do I own");
await page.keyboard.press("Enter");
await page.getByText("You own 3").waitFor();
await shot("02-search");

// Add an item manually into the box
await page.goto(BASE + "#/add?container_id=" + box.id);
await page.getByText("Property › Shed › Box #15", { exact: true }).waitFor();
await page.getByLabel("Name").fill("Hedge trimmer");
await page.getByLabel("Quantity").fill("1");
await page.getByRole("button", { name: "Save", exact: true }).click();
await page.getByText("Hedge trimmer").first().waitFor();
await page.getByText("Quantity here").waitFor();
await shot("03-item");

// Container page lists contents
await page.goto(BASE + "#/containers/" + box.id);
await page.getByText("Contents (6)").waitFor();
await shot("04-container");

// Scan mode with manual barcode entry
await page.goto(BASE + "#/scan?container=" + box.id);
await page.getByPlaceholder("Type a barcode or label code").fill("012345678905");
await page.keyboard.press("Enter");
await page.getByText("You own 3").waitFor();
await shot("05-scan-barcode");
await page.getByRole("button", { name: /Add one to Box/ }).click();
await page.getByText("+1 in Box").waitFor();

// Locations tree & labels page
await page.goto(BASE + "#/locations");
await page.getByRole("link", { name: "Shed" }).waitFor();
await page.goto(BASE + "#/labels?ids=" + box.id);
await page.getByText("1 label selected").waitFor();
await shot("06-labels");

// Quick photos: queue two photos into the box, then finish one later
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAGUlEQVR4nGM8EaDBQApgIkn1qIZRDUNKAwAGbAFge9OrNwAAAABJRU5ErkJggg==",
  "base64",
);
await page.goto(BASE + "#/capture?container_id=" + box.id);
await page.locator('input[type="file"]').setInputFiles([
  { name: "a.png", mimeType: "image/png", buffer: png },
  { name: "b.png", mimeType: "image/png", buffer: png },
]);
await page.getByRole("link", { name: /Add details now/ }).waitFor();
await page.getByText("2 added", { exact: true }).waitFor();
await shot("07-quick-photos");
// Pretend the AI has suggested a name for the first photo, and make loading
// the next item slow (like a phone) to catch the form showing stale details.
const queued = (await api("items?needs_details=true&sort=oldest")).items;
await page.route(new RegExp(`/api/v1/items/${queued[0].id}$`), async (route) => {
  const res = await route.fetch();
  const body = await res.json();
  body.capture = { ai_status: "ready", suggestion: { name: "Carburettor", confidence: 0.9 } };
  await route.fulfill({ response: res, json: body });
});
await page.route(new RegExp(`/api/v1/items/${queued[1].id}$`), async (route) => {
  await new Promise((r) => setTimeout(r, 1200));
  await route.continue();
});
await page.getByRole("link", { name: /Add details now/ }).click();
await page.locator(".list a").first().click();
await page.getByLabel("Name", { exact: true }).waitFor();
if ((await page.getByLabel("Name", { exact: true }).inputValue()) !== "Carburettor") throw new Error("AI suggestion not pre-filled");
await page.getByRole("button", { name: "Save & next" }).waitFor();
await page.getByLabel("Name", { exact: true }).fill("Hammer");
await page.getByRole("button", { name: "Save & next" }).click();
await page.getByText("Saved").first().waitFor();
await page.getByRole("button", { name: "Save & next" }).waitFor();
// The next item's form must not carry over the previous item's details.
const nextName = await page.getByLabel("Name", { exact: true }).inputValue();
if (nextName === "Hammer" || nextName === "Carburettor") throw new Error("Save & next carried the previous item's details into the next item");
const pending = await api("items?needs_details=true");
const pendingItems = pending.items ?? pending;
if (pendingItems.length !== 1) throw new Error(`expected 1 item still to finish, got ${pendingItems.length}`);

// Quick photos with no place: two shelves' worth, then say which shelf is which
await page.goto(BASE);
await page.getByRole("link", { name: /Quick photos/ }).first().click();
await page.getByText("Quick photos · Shelf 1").waitFor();
const files = page.locator('input[type="file"]');
await files.setInputFiles([
  { name: "c.png", mimeType: "image/png", buffer: png },
  { name: "d.png", mimeType: "image/png", buffer: png },
]);
await page.getByText("2 added", { exact: true }).waitFor();
await page.getByRole("button", { name: "Next shelf" }).click();
await page.getByText("Quick photos · Shelf 2").waitFor();
await files.setInputFiles([{ name: "e.png", mimeType: "image/png", buffer: png }]);
await page.getByText("3 added", { exact: true }).waitFor();
await shot("07b-quick-photos-unsorted");
await page.getByRole("link", { name: /Say where they are/ }).click();
await page.getByRole("heading", { name: "To sort" }).waitFor();
const shelf1 = page.locator(".card", { hasText: "Shelf 1" });
await shelf1.getByText("2 of 2 selected").waitFor();
await shot("07c-to-sort");
await shelf1.getByRole("button", { name: /Put all 2 in/ }).click();
await page.getByRole("button", { name: /Locations/ }).click();
await page.getByRole("dialog").getByText("Shed", { exact: true }).click();
await page.getByText("2 items put in").waitFor();
const shelf2 = page.locator(".card", { hasText: "Shelf 2" });
await shelf2.getByRole("button", { name: /Put it in/ }).click();
await page.getByRole("dialog").getByText("#15 Box").click();
await page.getByText("Nothing to sort").waitFor();
const unsortedLeft = await api("capture/unsorted");
if (unsortedLeft.total !== 0) throw new Error("photos still unsorted");
const inShed = (await api(`items?location_id=${shed.id}&needs_details=true`)).items;
if (inShed.length < 3) throw new Error(`expected the shelf photos in the shed, got ${inShed.length}`);

// Items page: sort by location, multi-select, bulk move / edit / delete
const rake = (await api("items", { method: "POST", data: { name: "Rake", location_id: garage.id } })).items[0];
const hoe = (await api("items", { method: "POST", data: { name: "Hoe", location_id: garage.id } })).items[0];
const trowel = (await api("items", { method: "POST", data: { name: "Trowel", location_id: garage.id } })).items[0];
await page.goto(BASE + "#/items?sort=location");
await page.locator(".list-head", { hasText: "Property › Garage" }).first().waitFor();
await page.getByRole("button", { name: "Select", exact: true }).click();
await page.getByRole("checkbox", { name: /Rake/ }).click();
await page.getByRole("checkbox", { name: /Hoe/ }).click();
await page.getByText("2 selected").waitFor();
await shot("07d-multiselect");
await page.getByRole("toolbar").getByRole("button", { name: "Move" }).click();
await page.getByRole("dialog").getByText("#15 Box").click();
await page.getByText("Moved 2 items").waitFor();
if ((await api(`items/${rake.id}`)).placement.container?.id !== box.id) throw new Error("bulk move failed");
await page.getByRole("button", { name: "Select", exact: true }).click();
await page.getByRole("checkbox", { name: /Hoe/ }).click();
await page.getByRole("checkbox", { name: /Trowel/ }).click();
await page.getByRole("toolbar").getByRole("button", { name: "Edit" }).click();
await page.getByLabel("Category").fill("Garden tools");
await page.getByRole("button", { name: "Apply to 2 items" }).click();
await page.getByText("Saved").first().waitFor();
if ((await api(`items/${trowel.id}`)).category !== "Garden tools") throw new Error("bulk edit failed");
await page.getByRole("button", { name: "Select", exact: true }).click();
await page.getByRole("checkbox", { name: /Trowel/ }).click();
await page.getByRole("toolbar").getByRole("button", { name: "Delete" }).click();
await page.getByRole("button", { name: "Delete 1 item" }).click();
await page.locator(".selection-bar[role=status]", { hasText: "Deleted 1 item" }).waitFor();
if (!(await api(`items/${trowel.id}`)).deleted_at) throw new Error("bulk delete failed");
await page.locator(".selection-bar[role=status]").getByRole("button", { name: "Undo" }).click();
await page.getByText("Restored 1 item").first().waitFor();
if ((await api(`items/${trowel.id}`)).deleted_at) throw new Error("undo failed");
// Lend two at once
await page.getByRole("button", { name: "Select", exact: true }).click();
await page.getByRole("checkbox", { name: /Trowel/ }).click();
await page.getByRole("checkbox", { name: /Hoe/ }).click();
await page.getByRole("toolbar").getByRole("button", { name: "Lend" }).click();
await page.getByLabel("Lent to").fill("Pat");
await page.getByRole("button", { name: "Lend 2 items" }).click();
await page.getByText("Lent 2 items to Pat").first().waitFor();
if ((await api(`items/${hoe.id}`)).status !== "on_loan") throw new Error("bulk lend failed");

// Take it / put away: take the drill from the garage, put it in the shed just this once.
const drill = (await api("items", { method: "POST", data: { name: "Cordless drill", location_id: garage.id } })).items[0];
await page.goto(BASE + "#/items/" + drill.id);
await page.getByRole("button", { name: "Take it" }).click();
await page.getByText(/^Taken;/).first().waitFor();
if ((await api(`items/${drill.id}`)).status !== "in_use") throw new Error("take it failed");
await page.goto(BASE + "#/scan");
await page.getByText("With you:").waitFor();
await shot("09b-taken");
await page.goto(BASE + "#/items/" + drill.id);
await page.getByRole("button", { name: "Put away" }).click();
await page.getByRole("dialog").getByPlaceholder("Search by name, number or place…").fill("Shed");
await page.getByRole("dialog").getByText("Shed", { exact: false }).first().click();
await page.getByRole("button", { name: "Put away here" }).click();
await page.getByRole("button", { name: "Just this once" }).click();
await page.getByText(/Here for now/).first().waitFor();
const putDrill = await api(`items/${drill.id}`);
if (putDrill.status !== "temporarily_relocated" || putDrill.home_placement?.location?.id !== garage.id) throw new Error("put away just this once failed");
// Take it again and scan the garage's label: it goes back home.
await page.getByRole("button", { name: "Take it" }).click();
for (let i = 0; (await api(`items/${drill.id}`)).status !== "in_use"; i++) {
  if (i > 50) throw new Error("second take it failed");
  await page.waitForTimeout(100);
}
await page.goto(BASE + "#/scan");
await page.getByPlaceholder("Type a barcode or label code").fill(garage.qr_code);
await page.getByPlaceholder("Type a barcode or label code").press("Enter");
const putSheet = page.getByRole("dialog");
await putSheet.getByText(/Put away in/).waitFor();
await shot("09c-put-away");
await putSheet.getByRole("button", { name: "Put away here" }).click();
await page.getByText(/Back home/).first().waitFor();
const homeDrill = await api(`items/${drill.id}`);
if (homeDrill.status !== "stored" || homeDrill.placement.location?.id !== garage.id) throw new Error("scan-to-put-away failed");
await putSheet.getByRole("button", { name: "Done" }).click();

// Maintenance: track a mower, log hours, record the overdue oil change
const mower = (await api("items", { method: "POST", data: { name: "Lawn mower", category: "Equipment", location_id: garage.id } })).items[0];
await page.goto(BASE + "#/items/" + mower.id);
const track = page.getByRole("button", { name: "Track maintenance" });
await track.scrollIntoViewIfNeeded();
await track.click();
await page.getByRole("button", { name: /Lawn mower/ }).click();
const maint = page.locator(".card", { has: page.getByRole("heading", { name: /^Maintenance/ }) });
const oilRow = maint.locator("li", { hasText: "Engine oil change" });
await oilRow.waitFor();
await maint.getByRole("button", { name: /Log hours/ }).click();
await page.getByLabel(/Meter reading/).fill("51");
await page.getByRole("button", { name: "Save reading" }).click();
await maint.getByText("51 h").waitFor();
await oilRow.getByText("Overdue").waitFor();
await shot("08-maintenance");
await oilRow.getByRole("button", { name: "Record Engine oil change" }).click();
await page.getByLabel("Work performed").fill("Drained and refilled with SAE 30");
await page.getByRole("button", { name: "Add part" }).click();
await page.getByLabel("Part", { exact: true }).fill("SAE 30 oil");
const saved = page.waitForResponse((r) => r.url().includes("/complete") && r.request().method() === "POST");
await page.getByRole("button", { name: "Save", exact: true }).click();
if ((await saved).status() !== 201) throw new Error("recording the service failed");
await maint.getByText("Drained and refilled with SAE 30").waitFor();
const ms = await api(`items/${mower.id}/maintenance`);
const oil = ms.tasks.find((t) => t.title === "Engine oil change");
if (oil.status === "overdue" || oil.next_due_hours !== 101) throw new Error(`oil change not rescheduled: ${JSON.stringify(oil)}`);
if (ms.history[0].parts[0].description !== "SAE 30 oil") throw new Error("part not recorded");
await page.goto(BASE + "#/maintenance");
await page.getByRole("button", { name: /Recent/ }).click();
await page.getByText(/Lawn mower – Engine oil change/).waitFor();

// Customer repair: customer, job with new equipment, status, work, work order
await page.goto(BASE + "#/customers");
await page.getByRole("button", { name: "Customer" }).click();
await page.getByLabel("Name").fill("Pat Neighbour");
await page.getByLabel("Phone").fill("555-0100");
await page.getByRole("button", { name: "Save" }).click();
await page.getByRole("heading", { name: "Pat Neighbour" }).waitFor();
await page.getByRole("button", { name: "New repair job" }).click();
await page.getByLabel("Equipment", { exact: true }).fill("Toro Recycler mower");
await page.getByLabel("Serial number").fill("TR-9");
await page.getByLabel("Reported problem").fill("Won't start");
await page.getByRole("button", { name: "Open job" }).click();
await page.getByRole("heading", { name: /RJ-0001/ }).waitFor();
await page.getByText("Pat Neighbour's equipment").waitFor();
await page.getByRole("button", { name: "Waiting for parts" }).click();
await page.getByRole("button", { name: "Set to Waiting for parts" }).click();
await page.locator(".badge", { hasText: "Waiting for parts" }).first().waitFor();
await page.getByRole("button", { name: "Record work" }).click();
await page.getByLabel("What was done").fill("Carburetor rebuilt");
await page.getByLabel("Work performed").fill("Rebuilt carburetor");
await page.getByRole("button", { name: "Add part" }).click();
await page.getByLabel("Part", { exact: true }).fill("Carb kit");
await page.getByLabel("Each").fill("18.50");
const workSaved = page.waitForResponse((r) => r.url().includes("/service") && r.request().method() === "POST");
await page.getByRole("button", { name: "Save", exact: true }).click();
if ((await workSaved).status() !== 201) throw new Error("recording repair work failed");
await page.locator(".card", { hasText: "Work performed" }).getByText("Carburetor rebuilt").waitFor();
await shot("09-repair-job");
await page.getByRole("link", { name: /Work order/ }).click();
await page.getByRole("heading", { name: "Repair work order RJ-0001" }).waitFor();
await page.getByText("Carb kit").waitFor();
await shot("10-work-order");
const job = (await api("repair-jobs"))[0];
if (job.status !== "in_progress" || job.parts_charge !== 18.5) throw new Error(`repair job wrong: ${JSON.stringify(job)}`);
const stats2 = await api("stats");
const cust = await api(`items/${job.item.id}`);
if (!cust.owner || cust.owner.name !== "Pat Neighbour") throw new Error("customer equipment not marked as such");

// Related items and kits: accessories stay separate items in their own places
const compressor = (await api("items", { method: "POST", data: { name: "Air compressor", location_id: garage.id } })).items[0];
const airHose = (await api("items", { method: "POST", data: { name: "Air hose 50 ft", container_id: box.id } })).items[0];
await page.goto(BASE + "#/items/" + compressor.id);
await page.getByRole("button", { name: "Related item" }).click();  // the item page's "Add:" row
await page.getByRole("button", { name: "Add an accessory for this…" }).click();
await page.getByRole("button", { name: "Choose the item" }).click();
await page.getByPlaceholder("Search your items").fill("air hose");
await page.getByRole("button", { name: /Air hose 50 ft/ }).click();
const related = page.locator(".card", { has: page.getByRole("heading", { name: "Related items" }) });
await related.getByText("Air hose 50 ft").waitFor();
await page.goto(BASE + "#/search");
await page.getByRole("searchbox").fill("air compressor");
await page.keyboard.press("Enter");
await page.getByRole("button", { name: "Accessory: Air hose 50 ft" }).waitFor();
await shot("11-search-related");
await page.goto(BASE + "#/kits");
await page.getByRole("button", { name: "Kit" }).click();
await page.getByLabel("Name").fill("Air compressor kit");
await page.getByRole("button", { name: "Choose a container or location" }).click();
await page.getByRole("button", { name: /Locations/ }).click();
await page.getByRole("link", { name: /^Garage/ }).first().click();
await page.getByRole("button", { name: "Save" }).click();
await page.getByRole("heading", { name: "Air compressor kit" }).waitFor();
for (const name of ["Air compressor", "Air hose 50 ft"]) {
  await page.getByRole("button", { name: "Add item" }).click();
  await page.getByPlaceholder("Search your items").fill(name);
  await page.getByRole("button", { name: new RegExp(name) }).first().click();
  await page.locator("li", { hasText: name }).first().waitFor();
}
await page.locator("li", { hasText: "Air hose 50 ft" }).getByText("Stored separately").waitFor();
await page.locator("li", { hasText: "Air compressor" }).getByText("With the kit").waitFor();
await shot("12-kit");

// NFC: assign a tag (link workflow, as on iPhone/desktop), then scan it
await page.goto(BASE + "#/items/" + mower.id);
const tagsCard = page.locator(".card", { has: page.getByRole("heading", { name: "NFC tags" }) });
await page.getByRole("button", { name: "NFC tag" }).click();  // no tags yet: from the "Add:" row
const assign = page.getByRole("dialog");
await assign.getByLabel("Label (optional)").fill("Handle");
await assign.getByRole("button", { name: "Log hours" }).click();
await assign.getByRole("button", { name: /Get the link/ }).click();
await page.getByAltText("QR code of the tag link").waitFor();
const tagUrl = await page.locator('input[readonly]').inputValue();
await page.getByRole("button", { name: "Done" }).click();
await tagsCard.getByText("Handle").waitFor();
const ref = /\/n\/([A-Za-z0-9_-]+)/.exec(tagUrl)?.[1] || /HINV:N:(.+)$/.exec(tagUrl)?.[1];
if (!ref || String(mower.id) === ref) throw new Error(`unexpected tag link ${tagUrl}`);
await page.goto(BASE + "#/tag/" + ref);
await page.getByRole("button", { name: "Log hours" }).first().waitFor();
await shot("13-tag-scan");
await page.getByRole("button", { name: "Log hours" }).first().click();
await page.getByLabel(/Meter reading/).waitFor();
const tags = await api(`tags?target_type=item&target_id=${mower.id}`);
if (tags[0].scan_count !== 1 || tags[0].label !== "Handle") throw new Error(`tag not recorded: ${JSON.stringify(tags[0])}`);

// Replenishment: restock settings, Amazon source, list, hand-off, confirm, buy
const oilItem = (await api("items", { method: "POST", data: { name: "SAE 30 engine oil", quantity: 1, min_quantity: 2, location_id: garage.id } })).items[0];
await page.goto(BASE + "#/items/" + oilItem.id);
const restock = page.locator(".card", { has: page.getByRole("heading", { name: "Restock" }) });
await restock.getByRole("button", { name: "Settings" }).click();
await page.getByRole("dialog").getByLabel("Restock to").fill("5");
await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
await restock.getByText("Below minimum").waitFor();
await restock.getByRole("button", { name: "Where to buy" }).click();
await page.getByRole("dialog").getByRole("button", { name: "Amazon" }).click();
await page.getByRole("dialog").getByLabel("Product page link").fill("https://www.amazon.com/dp/B000BQ7JXA");
await page.getByRole("dialog").getByLabel("Price (optional)").fill("8.99");
await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
await restock.getByText("B000BQ7JXA").waitFor();
await page.goto(BASE + "#/shopping");
await page.locator("li", { hasText: "SAE 30 engine oil" }).getByRole("button", { name: "4" }).click();
await page.locator("li", { hasText: "SAE 30 engine oil" }).getByText("On list").waitFor();
await page.getByRole("button", { name: /Shopping list/ }).click();
await page.getByRole("button", { name: /Open all at Amazon/ }).click();
const cartLink = page.getByRole("link", { name: /Review & add 1 item on Amazon/ });
await cartLink.waitFor();
const href = await cartLink.getAttribute("href");
if (!href.includes("/gp/aws/cart/add.html") || !href.includes("ASIN.1=B000BQ7JXA") || !href.includes("Quantity.1=4")) throw new Error(`bad cart link ${href}`);
await shot("14-handoff");
await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
await page.getByText("Opened at shop").waitFor();  // not "in cart"
await page.getByRole("button", { name: "It's in my cart" }).click();
await page.getByText("In cart (you confirmed)").waitFor();
await page.getByRole("button", { name: "Bought" }).click();
await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
await page.getByText("The shopping list is empty").waitFor();
if ((await api(`items/${oilItem.id}`)).quantity !== 5) throw new Error("purchase did not update stock");

// Scan mode "Take one out": typing the barcode takes one AA battery out
const aaBefore = (await api("items?q=AA batteries")).items[0];
await api(`items/${aaBefore.id}`, { method: "PATCH", data: { barcode: "5012345678900" } });
await page.goto(BASE + "#/scan?mode=use");
await page.getByPlaceholder("Type a barcode or label code").fill("5012345678900");
await page.keyboard.press("Enter");
await page.getByText(/Took 1 × AA batteries/).first().waitFor();
if ((await api(`items/${aaBefore.id}`)).quantity !== aaBefore.quantity - 1) throw new Error("take one out failed");

// Quick photos taken offline survive closing the app and upload later
const unsortedBefore = (await api("capture/unsorted")).total;
const offline = await ctx.newPage();
await offline.goto(BASE + "#/capture");
await offline.getByText("Quick photos · Shelf 1").waitFor();
await ctx.setOffline(true);
await offline.locator('input[type="file"]').setInputFiles([{ name: "o.png", mimeType: "image/png", buffer: png }]);
await offline.getByText("Saved · waiting").first().waitFor();
await offline.close();
await ctx.setOffline(false);
await page.goto(BASE);
for (let i = 0; i < 40 && (await api("capture/unsorted")).total === unsortedBefore; i++) await page.waitForTimeout(500);
if ((await api("capture/unsorted")).total !== unsortedBefore + 1) throw new Error("offline photo was not uploaded after reopening");
const manifest = await page.request.fetch(BASE + "manifest.json");
if (!manifest.ok() || (await manifest.json()).name !== "Home Inventory") throw new Error("manifest missing");

// Inventory report + downloads
await page.goto(BASE + "#/report");
await page.getByText("Recorded value").waitFor();
await page.locator(".report-room").first().waitFor();
await shot("15-report");
// Report item names open the item, and Previous/Next step through the report's order.
const firstLink = page.locator(".report-room .t a").first();
const firstName = await firstLink.innerText();
await firstLink.click();
await page.getByRole("heading", { name: firstName }).waitFor();
await page.getByRole("button", { name: "Next item" }).click();
await page.getByText(/^2 of \d+$/).waitFor();
await page.getByRole("button", { name: "Previous item" }).click();
await page.getByRole("heading", { name: firstName }).waitFor();
await shot("15b-item-steps");

// Grouped menu and the hub pages.
await page.goto(BASE + "#/reports");
await page.getByRole("heading", { name: "Reports" }).waitFor();
await page.getByRole("link", { name: /Maintenance plan/ }).first().click();
await page.getByRole("heading", { name: /Maintenance plan/ }).waitFor();
await shot("15c-maintenance-plan");
await page.goto(BASE + "#/todo");
await page.getByRole("heading", { name: "To sort" }).waitFor();
await page.goto(BASE + "#/care");
await page.getByRole("button", { name: "Repairs", exact: true }).click();
await page.getByRole("button", { name: "Customers", exact: true }).click();
await page.getByRole("button", { name: "Maintenance", exact: true }).click();
await page.locator(".nav-group", { hasText: "Admin" }).first().waitFor({ state: "attached" });
await shot("15d-care");
await page.getByRole("button", { name: "More" }).click();
await page.locator(".menu-group", { hasText: "Reports" }).waitFor();
await shot("15e-grouped-menu");
await page.keyboard.press("Escape");

const csvRes = await page.request.fetch(BASE + "api/v1/reports/inventory.csv");
if (!csvRes.ok() || !(await csvRes.text()).includes("Serial number")) throw new Error("CSV export failed");
const xlsxRes = await page.request.fetch(BASE + "api/v1/reports/inventory.xlsx");
if (!xlsxRes.ok() || (await xlsxRes.body()).subarray(0, 2).toString() !== "PK") throw new Error("Excel export failed");

const after = await api(`containers/${box.id}`);
const cord = after.items.find((i) => i.name === "Extension cord");
if (!after.items.some((i) => i.name === "Hammer")) throw new Error("finished quick photo missing from box");
if (cord.quantity !== 3) throw new Error(`expected 3 cords in box after scan, got ${cord.quantity}`);
if (errors.length) throw new Error("Browser errors:\n" + errors.join("\n"));
await browser.close();
console.log("E2E smoke test passed");

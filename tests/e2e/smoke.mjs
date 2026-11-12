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
await page.getByText("Shed › Box #15").waitFor();
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

// Maintenance: track a mower, log hours, record the overdue oil change
const mower = (await api("items", { method: "POST", data: { name: "Lawn mower", location_id: garage.id } })).items[0];
await page.goto(BASE + "#/items/" + mower.id);
await page.getByRole("button", { name: "Track maintenance" }).click();
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

const after = await api(`containers/${box.id}`);
const cord = after.items.find((i) => i.name === "Extension cord");
if (!after.items.some((i) => i.name === "Hammer")) throw new Error("finished quick photo missing from box");
if (cord.quantity !== 3) throw new Error(`expected 3 cords in box after scan, got ${cord.quantity}`);
if (errors.length) throw new Error("Browser errors:\n" + errors.join("\n"));
await browser.close();
console.log("E2E smoke test passed");

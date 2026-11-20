// Records the demo walkthrough (see make_video.sh). Each scene lasts as long as its narration.
import { createRequire } from "module";
import fs from "fs";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
// Usage: node record.mjs <work dir with durations.json and ids.json>
const BASE = process.env.DEMO_BASE || "http://localhost:18099/", D = process.argv[2];
const dur = JSON.parse(fs.readFileSync(`${D}/durations.json`, "utf8"));
const IDS = JSON.parse(fs.readFileSync(`${D}/ids.json`, "utf8"));

const b = await chromium.launch();
const ctx = await b.newContext({
  viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1,
  recordVideo: { dir: `${D}/raw`, size: { width: 1280, height: 720 } },
  extraHTTPHeaders: { "X-Remote-User-Id": "demo-admin", "X-Remote-User-Name": "alex", "X-Remote-User-Display-Name": "Alex" },
});
// A visible pointer (headless recordings have none).
await ctx.addInitScript(() => {
  addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.style.cssText = "position:fixed;left:0;top:0;width:22px;height:22px;border-radius:50%;background:rgba(25,118,210,.35);border:2px solid #1976d2;z-index:2147483647;pointer-events:none;transform:translate(-50%,-50%);transition:width .12s,height .12s;left:640px;top:360px";
    document.body.appendChild(c);
    addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
    addEventListener("mousedown", () => { c.style.width = c.style.height = "14px"; }, true);
    addEventListener("mouseup", () => { c.style.width = c.style.height = "22px"; }, true);
  });
});
const page = await ctx.newPage();
const t0 = Date.now();
const marks = {};
const sleep = (ms) => page.waitForTimeout(ms);
let mouse = { x: 640, y: 360 };
async function moveTo(loc) {
  loc = loc.filter({ visible: true }).first();
  await loc.waitFor({ timeout: 8000 });
  await loc.scrollIntoViewIfNeeded();
  const bb = await loc.boundingBox();
  const x = bb.x + Math.min(bb.width / 2, 60), y = bb.y + bb.height / 2;
  await page.mouse.move(x, y, { steps: 25 }); mouse = { x, y };
  await sleep(250);
}
async function click(loc) { await moveTo(loc); await page.mouse.down(); await sleep(80); await page.mouse.up(); await sleep(400); }
async function scrollBy(px, steps = 12) { for (let i = 0; i < steps; i++) { await page.mouse.wheel(0, px / steps); await sleep(60); } }
async function scrollTo(loc) { loc = loc.filter({ visible: true }).first(); await loc.evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "start" })); await sleep(900); }
async function scene(key, fn) {
  const start = Date.now();
  marks[key] = (start - t0) / 1000;
  await fn();
  const left = dur[key] * 1000 + 900 - (Date.now() - start);
  if (left > 0) await sleep(left);
}
const go = async (path) => { await page.goto(BASE + "#/" + path); await sleep(1200); };

await go(""); await sleep(1500);
await scene("intro", async () => {
  await sleep(4000);
  await moveTo(page.getByText("Running low")); await sleep(1800);
  await moveTo(page.getByText(/On loan/)); await sleep(1800);
  await moveTo(page.getByText("Needs attention", { exact: true }).last()); await sleep(1500);
});
await scene("search", async () => {
  const box = page.getByPlaceholder(/Ask:/);
  await click(box);
  await box.pressSequentially("where's the drill", { delay: 90 });
  await sleep(400); await page.keyboard.press("Enter"); await sleep(2500);
  await moveTo(page.getByText(/Cordless drill/).first()); await sleep(1500);
});
await scene("item", async () => {
  await click(page.getByText(/Cordless drill DCD777/).first()); await sleep(2000);
  await scrollBy(500, 15); await sleep(1500); await scrollBy(600, 15); await sleep(1200); await scrollBy(-1100, 10);
});
await scene("box", async () => {
  await go(`containers/${IDS.tote}`); await sleep(1500);
  await moveTo(page.getByText("Cordless drill DCD777").first()); await sleep(1500);
  await moveTo(page.getByText("18V battery 2Ah").first()); await sleep(2000);
  const label = page.getByRole("button", { name: /Print label/ }).or(page.getByRole("link", { name: /Print label/ })).first();
  if (await label.count()) await moveTo(label);
  await sleep(1200);
});
await scene("checklist", async () => {
  await click(page.getByRole("link", { name: /Printed checklist/ })); await sleep(1800);
  await click(page.getByRole("button", { name: "Fill in by hand" })); await sleep(800);
  const sel = page.getByLabel(/What happened to Cordless drill/);
  await moveTo(sel); await sel.selectOption("back"); await sleep(900);
  const s2 = page.getByLabel(/What happened to Jigsaw/);
  await moveTo(s2); await s2.selectOption("missing"); await sleep(900);
  await scrollBy(500, 14); await sleep(800);
});
await scene("equipment", async () => {
  await go(`equipment/${IDS.mower}`); await sleep(2500);
  await moveTo(page.getByText("1 overdue").first()); await sleep(1500);
  await scrollTo(page.getByText("Maintenance checklist").first()); await sleep(1500); await scrollBy(400, 12);
});
await scene("parts", async () => {
  await scrollTo(page.getByText("Parts and consumables", { exact: true }).first()); await sleep(1500);
  const partsCard = page.locator(".card", { has: page.getByText("Parts and consumables", { exact: true }) }).first();
  const oil = partsCard.locator("li", { hasText: "Engine oil SAE 10W-30" }).first();
  await oil.evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "center" })); await sleep(900);
  await moveTo(oil.getByText(/Engine oil SAE 10W-30/)); await sleep(3000);
  const kept = oil.getByText(/Kept in/); if (await kept.count()) await moveTo(kept);
  await sleep(3500);
  const add = page.getByRole("button", { name: /to list/ }).first(); if (await add.count()) await moveTo(add);
});
await scene("manuals", async () => {
  await scrollTo(page.getByText(/Manuals \(first one wins\)/).first()); await sleep(1200);
  await moveTo(page.getByText(/Kawasaki FR691V engine manual/).first()); await sleep(2500);
  await click(page.getByRole("button", { name: /Move Kawasaki FR691V engine manual.pdf up/ })); await sleep(1500);
});
await scene("attention", async () => {
  await go("attention"); await sleep(2000); await scrollBy(400, 14);
});
await scene("shopping", async () => {
  await go("shopping"); await sleep(1000);
  await click(page.getByRole("button", { name: /^Shopping list/ })); await sleep(1500);
  await moveTo(page.getByText(/Part \/ model no/).first()); await sleep(1800);
  await click(page.getByRole("button", { name: "Bought" }).first()); await sleep(3000);
  await page.keyboard.press("Escape"); await sleep(500);
});
await scene("duplicates", async () => {
  await go("duplicates"); await sleep(1500);
  await scrollTo(page.getByText(/WD-40 Multi-Use Product/).first()); await sleep(800);
  await click(page.getByRole("button", { name: /Show photo 1 of WD-40/ }).first()); await sleep(1800);
  await page.keyboard.press("Escape"); await sleep(500);
  await click(page.getByRole("button", { name: "Merge into this one" }).first()); await sleep(2500);
  await page.keyboard.press("Escape");
});
await scene("outro", async () => {
  await go(""); await sleep(2500);
  await click(page.getByText("Equipment").first()); await sleep(2000);
  await go(""); await sleep(1500);
});
await sleep(1500);
const total = (Date.now() - t0) / 1000;
fs.writeFileSync(`${D}/marks.json`, JSON.stringify({ marks, total }, null, 1));
const video = page.video();
await ctx.close();
fs.writeFileSync(`${D}/video_path.txt`, await video.path());
await b.close();
console.log(JSON.stringify({ marks, total }));

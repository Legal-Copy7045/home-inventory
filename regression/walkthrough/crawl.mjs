// Walkthrough crawl: for every role at desktop and phone width, record the
// menu, visit every screen reachable from it plus one of each detail screen,
// note what each screen shows, run the UX measurements and take a full-page
// screenshot.  Reads its job from $QC_PAYLOAD:
//   {base, roles: {role: headers}, widths: [{name, width, height, mobile}], routes: [{pattern, path}], out, shots}
import { createRequire } from "module";
import { writeFileSync } from "fs";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const job = JSON.parse(process.env.QC_PAYLOAD);
// A fake camera, so pages that open the camera behave as on a phone.
const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
const inventory = { base: job.base, taken: new Date().toISOString(), widths: job.widths, roles: {} };
const slug = (s) => s.replace(/^\//, "").replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "dashboard";

// Collects CSP violations before any page script runs.
const INIT = () => {
  window.__csp = [];
  document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`));
};

async function readMenu(page, mobile) {
  await page.goto(job.base + "#/", { waitUntil: "networkidle" });
  if (!mobile) {
    for (let i = 0; i < 10; i++) {
      const closed = page.locator('.sidebar .nav-group[aria-expanded="false"]');
      if (!(await closed.count())) break;
      await closed.first().click();
    }
    return page.evaluate(() => {
      const groups = [{ group: "Main", items: [] }];
      for (const el of document.querySelector(".sidebar").children) {
        if (el.matches(".nav-group")) groups.push({ group: el.textContent.trim(), items: [] });
        else if (el.tagName === "A") groups.at(-1).items.push({ label: el.textContent.trim(), href: new URL(el.href).hash.slice(1) || "/" });
      }
      return groups.filter((g) => g.items.length);
    });
  }
  const bar = await page.evaluate(() => [...document.querySelectorAll(".bottomnav a, .bottomnav button")].map((a) => ({
    label: a.textContent.trim() || a.getAttribute("aria-label") || "",
    href: a.href ? new URL(a.href).hash.slice(1) || "/" : `(${(a.getAttribute("aria-label") || a.textContent).trim()} button)`,
  })));
  await page.locator(".bottomnav").getByRole("button", { name: "More" }).click();
  await page.locator(".sheet").waitFor();
  for (let i = 0; i < 10; i++) {
    const closed = page.locator('.sheet .menu-group[aria-expanded="false"]');
    if (!(await closed.count())) break;
    await closed.first().click();
  }
  const sheet = await page.evaluate(() => {
    const groups = [{ group: "Menu", items: [] }];
    for (const el of document.querySelectorAll(".sheet .menu-group, .sheet .quick a")) {
      if (el.matches(".menu-group")) groups.push({ group: el.textContent.trim(), items: [] });
      else groups.at(-1).items.push({ label: el.textContent.trim(), href: new URL(el.href).hash.slice(1) || "/" });
    }
    return groups.filter((g) => g.items.length);
  });
  return [{ group: "Bottom bar", items: bar }, ...sheet];
}

// Measurements taken inside the page.
const MEASURE = (mobile) => {
  const vw = window.innerWidth;
  const visible = (el) => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const desc = (el) => {
    const t = (String(el.innerText ?? el.textContent ?? "").trim() || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""}${t ? ` “${t}”` : ""}`;
  };
  const inScroller = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const o = getComputedStyle(p).overflowX;
      if (o === "auto" || o === "scroll" || o === "hidden" || o === "clip") return true;
    }
    return false;
  };
  const main = document.querySelector(".main") || document.body;
  const overflow = [];
  for (const el of main.querySelectorAll("*")) {
    if (overflow.length >= 8 || !visible(el) || el.closest("svg") || inScroller(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 || r.left < -1) { overflow.push(`${desc(el)} runs off screen (${Math.round(r.left)}–${Math.round(r.right)}px)`); continue; }
    const p = el.parentElement;
    const pos = getComputedStyle(el).position;
    if (p && pos !== "absolute" && pos !== "fixed" && getComputedStyle(el).display !== "inline" && p.clientWidth > 0 && r.width > p.getBoundingClientRect().width + 2)
      overflow.push(`${desc(el)} is wider than its container (${Math.round(r.width)} > ${Math.round(p.getBoundingClientRect().width)}px)`);
  }
  const small = [];
  if (mobile) {
    for (const el of document.querySelectorAll("a, button, input, select, textarea, [role=button], summary")) {
      if (!visible(el) || el.closest(".sidebar")) continue;
      if (el.tagName === "A" && getComputedStyle(el).display === "inline" && el.closest("p, li")) continue; // link inside a sentence
      let r = el.getBoundingClientRect();
      if ((el.type === "checkbox" || el.type === "radio") && el.closest("label")) r = el.closest("label").getBoundingClientRect();
      if (r.width < 40 || r.height < 40) small.push(`${desc(el)} ${Math.round(r.width)}×${Math.round(r.height)}`);
    }
  }
  const sw = document.scrollingElement.scrollWidth;
  const txt = (e) => String(e.innerText ?? e.textContent ?? "").trim().replace(/\s+/g, " ");
  const text = (sel, n) => [...document.querySelectorAll(sel)].filter(visible).map(txt).filter(Boolean).slice(0, n);
  return {
    title: document.querySelector(".topbar .title")?.textContent?.trim() || "",
    headings: text(".main h1, .main h2, .main h3", 12),
    breadcrumbs: [...new Set(text(".main *:not(:has(*))", 400).filter((t) => t.includes(" › ")))].slice(0, 3),
    primary: text(".main .btn.primary", 8),
    buttons: [...new Set(text(".main button, .main .btn", 60))].slice(0, 30),
    fields: [...document.querySelectorAll(".main label.field, .main label")].filter(visible).map((l) => txt(l).split(" ").slice(0, 6).join(" ")).filter(Boolean).slice(0, 25),
    forms: document.querySelectorAll(".main form").length,
    tables: document.querySelectorAll(".main table").length,
    errors: text(".error", 3),
    refusal_notice: [...document.querySelectorAll(".notice")].some((n) => /member access|Only administrators/.test(n.textContent || "")),
    notFound: document.body.innerText.includes("Page not found."),
    hscroll: sw > window.innerWidth + 1, scroll_width: sw,
    height: document.scrollingElement.scrollHeight,
    overflow, small_taps: small, csp: window.__csp || [],
  };
};

async function crawl(role, headers, w) {
  const ctx = await browser.newContext({
    viewport: { width: w.width, height: w.height }, isMobile: w.mobile, hasTouch: w.mobile, deviceScaleFactor: 1,
    extraHTTPHeaders: headers,
  });
  await ctx.addInitScript(INIT);
  const page = await ctx.newPage();
  const menu = await readMenu(page, w.mobile);
  const targets = new Map();
  for (const g of menu) for (const it of g.items) if (it.href.startsWith("/")) targets.set(it.href, { pattern: it.href, path: it.href, from_menu: true });
  for (const r of job.routes) if (r.pattern.includes(":") && !targets.has(r.path)) targets.set(r.path, { ...r, from_menu: false });
  const pages = {};
  for (const [href, t] of targets) {
    const p = await ctx.newPage();
    const consoleErr = [], refused = [];
    p.on("pageerror", (e) => consoleErr.push(e.message));
    // "Failed to load resource" repeats a refused or failed request, which is recorded separately.
    p.on("console", (m) => m.type() === "error" && !/favicon|Failed to load resource/.test(m.text()) && consoleErr.push(m.text()));
    p.on("response", (res) => res.url().includes("/api/") && res.status() === 403 && refused.push(new URL(res.url()).pathname));
    const rec = { pattern: t.pattern, from_menu: t.from_menu };
    try {
      await p.goto(job.base + "#" + t.path, { waitUntil: "networkidle", timeout: 30000 });
      await p.waitForTimeout(400);
      Object.assign(rec, await p.evaluate(MEASURE, w.mobile));
      rec.final_path = new URL(p.url()).hash.slice(1).split("?")[0] || "/";
      rec.console = [...new Set([...consoleErr, ...rec.csp.map((c) => "CSP: " + c)])];
      const why = [...rec.errors];
      if (rec.notFound) why.push("Page not found");
      if (refused.length) why.push("refused: " + [...new Set(refused)].join(", "));
      rec.refused = why.length ? why.join("; ") : "";
      rec.shot = `${role}-${w.name}-${slug(t.pattern)}.png`;
      await p.screenshot({ path: `${job.shots}/${rec.shot}`, fullPage: true });
    } catch (e) {
      rec.refused = "did not load: " + e.message.split("\n")[0];
      rec.console = consoleErr;
    }
    pages[href] = rec;
    await p.close();
  }
  await ctx.close();
  return { menu, pages };
}

const jobs = [];
for (const [role, headers] of Object.entries(job.roles)) {
  inventory.roles[role] = {};
  for (const w of job.widths) jobs.push(crawl(role, headers, w).then((r) => { inventory.roles[role][w.name] = r; }));
}
await Promise.all(jobs);
await browser.close();
writeFileSync(job.out, JSON.stringify(inventory, null, 1));
const n = Object.values(inventory.roles).flatMap((r) => Object.values(r)).reduce((a, x) => a + Object.keys(x.pages).length, 0);
console.log(`walkthrough: ${n} screens recorded`);

// Regression browser pass: every App page, for every role, at desktop width.
// Reads its job from $QC_PAYLOAD: {base, roles: {role: headers}, routes: [{pattern, path}], out}
// Writes [{role, pattern, path, status, detail}] to `out`.
import { createRequire } from "module";
import { writeFileSync } from "fs";
const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const job = JSON.parse(process.env.QC_PAYLOAD);
// A fake camera, so pages that open the camera behave as on a phone.
const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
const results = [];

async function visit(ctx, role, r) {
  const page = await ctx.newPage();
  const errors = [], failed = [], refused = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && !/favicon|Failed to load resource/.test(m.text()) && errors.push(m.text()));
  page.on("response", (res) => {
    if (!res.url().includes("/api/")) return;
    if (res.status() >= 500) failed.push(`${res.status()} ${new URL(res.url()).pathname}`);
    if (res.status() === 403) refused.push(new URL(res.url()).pathname);
  });
  let status = "pass", detail = "";
  try {
    await page.goto(job.base + "#" + r.path, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(300);
    const info = await page.evaluate(() => ({
      title: document.querySelector(".topbar .title")?.textContent?.trim() || "",
      notFound: document.body.innerText.includes("Page not found."),
      errorBox: [...document.querySelectorAll(".error")].map((e) => e.textContent.trim()).filter(Boolean).slice(0, 3),
      empty: (document.querySelector(".main")?.innerText || "").trim().length < 20,
    }));
    // A clear "you can't use this" for a role without access is correct behaviour, not a failure.
    const REFUSAL = /read-only access|Administrator access required|need a signed-in/i;
    const refusal = info.errorBox.filter((t) => REFUSAL.test(t));
    const otherErrors = info.errorBox.filter((t) => !REFUSAL.test(t));
    const problems = [];
    if (info.notFound) problems.push("shows “Page not found”");
    if (info.empty && !refusal.length) problems.push("page is blank");
    if (otherErrors.length) problems.push("error shown: " + otherErrors.join(" | "));
    if (failed.length) problems.push("server errors: " + [...new Set(failed)].join(", "));
    if (errors.length) problems.push("console: " + [...new Set(errors)].slice(0, 3).join(" | "));
    status = problems.length ? "fail" : "pass";
    detail = problems.length ? problems.join("\n")
      : `“${info.title}”` + (refusal.length ? ` - refuses this role: ${refusal[0]}` : refused.length ? ` (some data refused: ${[...new Set(refused)].join(", ")})` : "");
    if (!problems.length && errors.length === 0 && refusal.length) status = "pass";
  } catch (e) {
    status = "fail";
    detail = "did not load: " + e.message.split("\n")[0];
  }
  await page.close();
  results.push({ role, pattern: r.pattern, path: r.path, status, detail });
}

await Promise.all(Object.entries(job.roles).map(async ([role, headers]) => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, extraHTTPHeaders: headers });
  for (const r of job.routes) await visit(ctx, role, r);
  await ctx.close();
}));
await browser.close();
writeFileSync(job.out, JSON.stringify(results, null, 1));
console.log(`pages: ${results.length} visits, ${results.filter((r) => r.status === "fail").length} failing`);

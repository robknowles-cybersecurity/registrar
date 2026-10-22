// End-to-end verification of the BUILT app (site/app/) in headless Chromium,
// served with site/_headers applied, so the real Content-Security-Policy is
// in force.
//
// The core check, as specified: log an activity, attach a fixture PDF,
// confirm the parsed hours appear, export, wipe, import, and assert the
// activity is back. Then the rest of the core flow: progress, submissions,
// packet zip, OCR of a photo and of an image-only PDF, a Python export
// import, offline reload, BYOK sends text only and never stores the key,
// no auto-save, no request to any other origin, no CSP violation.
//
//   node e2e/verify.mjs                 # exit 0 only if every check passes
//   node e2e/verify.mjs --break parser  # a mutated build: must FAIL
//   node e2e/verify.mjs --break import  # a mutated build: must FAIL
//   node e2e/verify.mjs --break export  # a mutated build: must FAIL
//   node e2e/verify.mjs --break sample  # a mutated build (F1 reverted): must FAIL
//   node e2e/verify.mjs --break escape  # a mutated build (F3 reverted): must FAIL
//   node e2e/verify.mjs --break filename  # a mutated build (F4 reverted): must FAIL
//   node e2e/verify.mjs --skip-ocr      # skip the two OCR checks (faster)
import { createRequire } from "node:module";
import { execSync, execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";
import { readZip } from "../src/zip.js";

const here = dirname(fileURLToPath(import.meta.url));
const webapp = dirname(here);
const repo = dirname(webapp);
const fixtures = join(webapp, "tests", "fixtures");
const args = process.argv.slice(2);
const breakWhat = args.includes("--break") ? args[args.indexOf("--break") + 1] : null;
const skipOcr = args.includes("--skip-ocr");

const require = createRequire(join(execSync("npm root -g").toString().trim(), "/"));
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  ({ chromium } = await import("playwright"));
}

// -- the site to serve: the real build, or a deliberately broken copy
let siteDir = join(repo, "site");
let tmp = null;
if (breakWhat) {
  tmp = mkdtempSync(join(tmpdir(), "registrar-mutant-"));
  cpSync(siteDir, tmp, { recursive: true });
  siteDir = tmp;
  const mutate = (file, from, to) => {
    const p = join(siteDir, "app", file);
    const s = readFileSync(p, "utf8");
    if (!s.includes(from)) throw new Error(`mutation anchor not found in ${file}`);
    writeFileSync(p, s.replace(from, to));
  };
  if (breakWhat === "parser") mutate("parser.js", "hours: bestHours(lines) || none,", "hours: none,");
  else if (breakWhat === "import") mutate("schema.js", "await led.store.put(\"entries\", e);", "/* mutated: entries dropped */");
  else if (breakWhat === "export") mutate("exports.js", "export const ALARM_DAYS = [90, 30];", "export const ALARM_DAYS = [90];");
  // F1 reverted: remove sample data undoes every setting it wrote, changed or not
  else if (breakWhat === "sample") mutate("sample.js", "if (!sameRow(cur, s.wrote, s.fields)) {", "if (false) {");
  // F4 reverted: imported filenames stored raw, zip names not flattened
  else if (breakWhat === "filename") {
    mutate("schema.js", "filename: safeFilename(ev.filename),", "filename: ev.filename,");
    mutate("packets.js", ".replace(/\\.\\.+/g, \"\").replace(/[\\\\/]+/g, \"_\")", "");
  }
  // F3 reverted: count rendered unescaped, import does not type-check it
  else if (breakWhat === "escape") {
    mutate("app.js", "<dd>${esc(e.count)}</dd>", "<dd>${e.count}</dd>");
    mutate("schema.js", "if (e.count !== undefined &&", "if (false &&");
  }
  else throw new Error(`unknown --break ${breakWhat}`);
  console.log(`MUTATED BUILD (--break ${breakWhat}): this run must FAIL\n`);
}

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "  ok  " : "  FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
}

const { server, port, log } = await serve(siteDir);
const base = `http://127.0.0.1:${port}/app/`;
const browser = await chromium.launch();
const context = await browser.newContext({ acceptDownloads: true, serviceWorkers: "allow" });
const page = await context.newPage();
const consoleErrors = [];
const foreign = [];
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
context.on("request", (r) => {
  const u = new URL(r.url());
  // api.anthropic.com is answered by page.route() below, inside the browser.
  if (u.hostname === "api.anthropic.com") return;
  if (!["127.0.0.1", "localhost"].includes(u.hostname) && !["blob:", "data:"].includes(u.protocol)) foreign.push(r.url());
});
page.on("dialog", (d) => d.accept());

const go = async (hash) => {
  await page.evaluate((h) => (location.hash = h), hash);
  await page.waitForFunction((h) => location.hash === h && document.querySelector("main h1"), hash);
  await page.waitForTimeout(150);
};
const entryCount = () => page.evaluate(async () => (await window.__registrar.led.listEntries()).length);
const ingestDone = (timeout) => page.waitForFunction(() => /fields found|could not read/.test(document.querySelector("#ingest-status")?.textContent || ""), null, { timeout });
const val = (sel) => page.locator(sel).inputValue();

async function downloadOf(click) {
  const [dl] = await Promise.all([page.waitForEvent("download"), click()]);
  return readFileSync(await dl.path());
}

try {
  await page.goto(base);
  await page.waitForFunction(() => window.__registrar && document.querySelector("main h1"));
  check("app loads under the site's CSP; a first run asks which credentials you hold",
    (await page.locator("main h1").innerText()) === "Which credentials do you hold?" && (await page.locator('input[name="hold"]').count()) === 7);
  const buildTxt = readFileSync(join(siteDir, "app", "BUILD.txt"), "utf8");
  const pkgVersion = JSON.parse(readFileSync(join(webapp, "package.json"), "utf8")).version;
  await page.waitForFunction(() => document.getElementById("version").textContent.includes("build"));
  const footer = await page.locator("#version").innerText();
  check("footer shows the version and build hash, linking to the release notes",
    footer.includes(`v${pkgVersion}`) && footer.includes(/version (\w+)/.exec(buildTxt)[1]) &&
    (await page.locator("#version a").getAttribute("href")).endsWith(`/releases/tag/v${pkgVersion}`), footer);
  await page.click("#welcome-skip");
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Credentials");
  check("first run is skippable: the empty ledger offers sample data and a certificate to try",
    (await page.locator("#sample-load").count()) === 1 && (await page.locator("#try-capture").getAttribute("href")) === "sample/try-capture-certificate.pdf");
  const tryPdf = await page.evaluate(async () => { const r = await fetch("sample/try-capture-certificate.pdf"); return [r.status, (await r.text()).slice(0, 5)]; });
  check("the try-capture certificate is served", tryPdf[0] === 200 && tryPdf[1] === "%PDF-", JSON.stringify(tryPdf));
  check("'your data lives in this browser' banner with Export / Import / Wipe",
    (await page.locator(".local").innerText()).includes("Your data lives in this browser") &&
    (await page.locator("#btn-export").count()) && (await page.locator("#btn-import").count()) && (await page.locator("#btn-wipe").count()));

  // -- settings: holder name + one tracked credential
  await go("#/settings");
  await page.fill("#s-holder", "Jordan Sample");
  await page.check('input[name="track-cissp"]');
  await page.fill("#a-cissp", "2025-09-01");
  await page.click('#settings-form button[type="submit"]');
  await page.waitForFunction(() => location.hash === "#/");
  check("CISSP tracked after Settings", (await page.locator("main").innerText()).includes("CISSP"));

  // == THE CORE CHECK ==================================================
  await go("#/log");
  const before = await entryCount();
  await page.setInputFiles("#file-input", join(fixtures, "certificates", "01-isc2-webinar-centred.pdf"));
  await ingestDone(30000);
  const hours = await val("#f-hours");
  check("parsed hours appear in the form (fixture PDF: 1.5)", hours === "1.5", `got "${hours}"`);
  check("parsed date pre-filled (2026-03-12)", (await val("#f-activity_date")) === "2026-03-12", await val("#f-activity_date"));
  check("parsed title pre-filled", (await val("#f-title")) === "Zero Trust Architecture in Practice", await val("#f-title"));
  check("parsed provider pre-filled", (await val("#f-provider")) === "Example Security Institute", await val("#f-provider"));
  check("per-field confidence shown", (await page.locator("#why-hours .conf").innerText()).includes("confidence"));
  check("holder name checked against Settings", (await page.locator("#holder-note").innerText()).includes("matches your name"));
  check("never auto-saves: reading the file wrote nothing", (await entryCount()) === before);
  await page.click('button[name="save"][value="confirm"]');
  await page.waitForFunction(() => /^#\/entry\/[0-9a-f]{32}$/.test(location.hash));
  const entryHash = await page.evaluate(() => location.hash);
  check("activity saved and confirmed", (await page.locator("main h1 .pill").innerText()) === "confirmed");
  check("entry shows 1.5 hours", (await page.locator("#entry-hours").innerText()) === "1.5");
  check("evidence attached and hash-verified", (await page.locator(".files").innerText()).includes("hash OK"));
  await go("#/");
  check("progress counts it (CISSP 1.5 of 120)", (await page.locator("main").innerText()).includes("1.5 of 120 CPE"));

  const exported = await downloadOf(() => page.click("#btn-export"));
  const doc = JSON.parse(exported.toString("utf8"));
  check("export is cpebot-ledger-v0.2", doc.format === "cpebot-ledger-v0.2");
  check("export holds the activity", doc.entries.some((e) => e.title === "Zero Trust Architecture in Practice" && e.hours === 1.5));
  check("export holds the evidence file", Object.keys((doc.registrar || {}).files || {}).length === 1);

  await page.click("#btn-wipe");
  await page.waitForTimeout(500);
  check("wipe empties the ledger", (await entryCount()) === 0);
  check("after wipe the first-run question is back", (await page.locator("main h1").innerText()) === "Which credentials do you hold?");

  const exportFile = join(mkdtempSync(join(tmpdir(), "registrar-e2e-")), "export.json");
  writeFileSync(exportFile, exported);
  await page.setInputFiles("#import-file", exportFile);
  await page.waitForFunction(() => /Imported|refused/.test(document.getElementById("toast").textContent));
  check("import reports success", (await page.locator("#toast").innerText()).startsWith("Imported 1 activities"), await page.locator("#toast").innerText());
  await go(entryHash);
  check("THE ACTIVITY IS BACK after export -> wipe -> import",
    (await page.locator("main h1").innerText()).includes("Zero Trust Architecture in Practice") && (await page.locator("#entry-hours").innerText()) === "1.5");
  check("its evidence is back and still verifies", (await page.locator(".files").innerText()).includes("hash OK"));
  await go("#/");
  check("progress is back (CISSP 1.5 of 120)", (await page.locator("main").innerText()).includes("1.5 of 120 CPE"));
  // ====================================================================

  // -- submissions
  await go("#/submissions");
  const queued = await page.locator("[data-mark]").count();
  check("submission tracking lists the unsubmitted activity", queued === 1);
  await page.click("[data-mark]");
  await page.waitForTimeout(300);
  check("marking it submitted clears the queue", (await page.locator("[data-mark]").count()) === 0);

  // -- packet zip
  await go("#/packets");
  const zipBytes = await downloadOf(() => page.click('[data-zip="isc2"]'));
  const files = readZip(new Uint8Array(zipBytes));
  const names = files.map((f) => f.name);
  const cover = new TextDecoder().decode(files.find((f) => f.name === "COVER.txt")?.data || new Uint8Array());
  check("packet zip has a cover sheet, manifest and the evidence", names.includes("COVER.txt") && names.includes("manifest.json") && names.some((n) => n.startsWith("evidence/")), names.join(", "));
  check("cover sheet lists the activity and says no portal login", cover.includes("Zero Trust Architecture in Practice") && cover.includes("never asks for, stores or uses your portal login"));

  // -- OCR
  if (!skipOcr) {
    await go("#/log");
    await page.setInputFiles("#photo-input", join(fixtures, "images", "photo-cert.png"));
    await ingestDone(180000);
    check("OCR of a photo: hours 6", (await val("#f-hours")) === "6", `${await val("#f-hours")} | ${await page.locator("#ingest-status").innerText()}`);
    check("OCR of a photo: date 2026-06-18", (await val("#f-activity_date")) === "2026-06-18", await val("#f-activity_date"));
    // F5: OCR that runs past its deadline is stopped and says so; the next OCR
    // (the image-only PDF below) gets a fresh worker and still works.
    const timedOut = await page.evaluate(async (b64) => {
      const { extractText } = await import("./ingest.js");
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const t0 = performance.now();
      try {
        await extractText({ type: "image/png" }, bytes, { ocrTimeoutMs: 1 });
        return { ok: false, why: "no timeout" };
      } catch (err) {
        return { ok: err.message === "OCR timed out", why: err.message, ms: Math.round(performance.now() - t0) };
      }
    }, readFileSync(join(fixtures, "images", "photo-cert.png")).toString("base64"));
    check("F5: OCR past its deadline stops with 'OCR timed out'", timedOut.ok, JSON.stringify(timedOut));
    await go("#/entries");
    await go("#/log");
    await page.setInputFiles("#file-input", join(fixtures, "images", "scan-cert-image-only.pdf"));
    await ingestDone(180000);
    const st = await page.locator("#ingest-status").innerText();
    check("image-only PDF goes to OCR", st.includes("OCR"), st);
    check("image-only PDF: hours 3.5", (await val("#f-hours")) === "3.5", await val("#f-hours"));
  }

  // -- a real Python export
  const pyDoc = JSON.parse(execFileSync("python3", [join(webapp, "tests", "py_export.py")], { maxBuffer: 64 << 20 }).toString()).export;
  const pyFile = join(dirname(exportFile), "python-export.json");
  writeFileSync(pyFile, JSON.stringify(pyDoc));
  await page.setInputFiles("#import-file", pyFile);
  await page.waitForFunction(() => /Imported \d+ activities \(0/.test(document.getElementById("toast").textContent) || /refused/.test(document.getElementById("toast").textContent));
  check("a Python Registrar export imports", (await entryCount()) === 1 + pyDoc.entries.length, `${await entryCount()} entries`);

  // -- BYOK: off by default, text only, key never stored
  await go("#/log");
  check("BYOK is off by default", (await page.locator("#ask-llm").count()) === 0);
  await go("#/settings");
  const keyAttrs = await page.evaluate(() => { const k = document.getElementById("l-key"); return [k.getAttribute("autocomplete"), k.hasAttribute("data-1p-ignore"), k.getAttribute("data-lpignore")]; });
  check("F8: the key field asks password managers not to save it", JSON.stringify(keyAttrs) === JSON.stringify(["new-password", true, "true"]), JSON.stringify(keyAttrs));
  const KEY = "sk-test-not-a-real-key-0000";
  await page.fill("#l-key", KEY);
  await page.click('#llm-form button[type="submit"]');
  await page.waitForTimeout(200);
  let sent = null;
  await page.route("https://api.anthropic.com/**", async (route) => {
    sent = { headers: route.request().headers(), body: route.request().postData() };
    await route.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" },
      body: JSON.stringify({ stop_reason: "end_turn", content: [{ type: "text", text: '{"activity_date":"2026-03-12","hours":2,"provider":"P","title":"T","holder_name":null}' }] }) });
  });
  await go("#/log");
  await page.setInputFiles("#file-input", join(fixtures, "certificates", "01-isc2-webinar-centred.pdf"));
  await ingestDone(30000);
  await page.click("#ask-llm");
  await page.waitForFunction(() => /answer is in the form|failed/.test(document.querySelector("#ingest-status").textContent));
  const pdfB64 = readFileSync(join(fixtures, "certificates", "01-isc2-webinar-centred.pdf")).toString("base64").slice(0, 40);
  check("BYOK sends the extracted text", sent && sent.body.includes("Zero Trust Architecture in Practice"));
  check("BYOK never sends the file", sent && !sent.body.includes(pdfB64) && !sent.body.includes("%PDF"));
  check("BYOK sends the key only to the provider", sent && sent.headers["x-api-key"] === KEY);
  const stored = await page.evaluate(async (key) => {
    const ls = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
    const exp = JSON.stringify(await (await import("./schema.js")).exportLedger(window.__registrar.led));
    const all = [];
    for (const s of ["entries", "evidence", "credential_state", "fees", "config"]) all.push(JSON.stringify(await window.__registrar.led.store.getAll(s)));
    return [ls, exp, all.join("")].some((t) => t.includes(key));
  }, KEY);
  check("the API key is in no storage and no export", stored === false);
  await page.unroute("https://api.anthropic.com/**");
  await page.reload();
  await page.waitForFunction(() => window.__registrar && document.querySelector("main h1"));
  await go("#/settings");
  check("a reload forgets the key", (await page.locator("#llm-status").innerText()) === "off");

  // -- offline
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  await context.setOffline(true);
  await page.reload();
  await page.waitForFunction(() => window.__registrar && document.querySelector("main h1"), null, { timeout: 15000 });
  await go("#/");
  check("works offline (service worker)", (await page.locator("main").innerText()).includes("CISSP"));
  await go("#/log");
  await page.setInputFiles("#file-input", join(fixtures, "certificates", "03-eccouncil-table.pdf"));
  await ingestDone(30000);
  check("evidence parsing works offline", (await val("#f-hours")) === "4", await val("#f-hours"));
  await context.setOffline(false);

  // == F5: an upload over 15 MB is refused before any of it is read ==
  await go("#/log");
  await page.evaluate(() => {
    window.__reads = 0;
    const orig = Blob.prototype.arrayBuffer;
    Blob.prototype.arrayBuffer = function (...a) { window.__reads += 1; return orig.apply(this, a); };
  });
  const big = Buffer.alloc(16 * 1024 * 1024, 0x20);
  big.write("%PDF-1.4\n");
  await page.setInputFiles("#file-input", { name: "big.pdf", mimeType: "application/pdf", buffer: big });
  await page.waitForFunction(() => /too large/.test(document.querySelector("#ingest-status")?.textContent || ""));
  const bigStatus = await page.locator("#ingest-status").innerText();
  const reads = await page.evaluate(() => window.__reads);
  check("F5: a 16 MB upload is refused by its size, before it is read", reads === 0 && /16\.0 MB \(cap 15 MB\)/.test(bigStatus), `${reads} reads; ${bigStatus}`);

  // == SAMPLE DATA: onboarding -> load sample -> reports -> export -> wipe -> import -> remove ==
  await page.click("#btn-wipe");
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Which credentials do you hold?");
  await page.check('input[name="hold"][value="cissp"]');
  check("ticking a credential asks for its cycle start", await page.locator("#w-cissp").isVisible());
  await page.fill("#w-cissp", "2025-09-01");
  await page.check('input[name="hold"][value="cfe"]'); // held, start left for later
  await page.click('#welcome-form button[type="submit"]');
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Credentials" && document.querySelector("#sample-load"));
  check("onboarding drives the Credentials screen (CISSP card, CFE asks for its start)",
    (await page.locator("#c-cissp").count()) === 1 && (await page.locator('form[data-start="cfe"]').count()) === 1);
  await page.click("#sample-load");
  await page.waitForFunction(() => document.querySelector("#sample-remove"));
  check("sample data loads: 9 activities over 3 bodies, clearly marked", (await entryCount()) === 9 &&
    (await page.locator(".banner.sample").innerText()).includes("Sample data is loaded") &&
    (await page.locator("#c-cissp").count()) === 1 && (await page.locator("#c-cysa_plus").count()) === 1 && (await page.locator("#c-cfe").count()) === 1);
  const sampleTitles = await page.evaluate(async () => (await window.__registrar.led.listEntries()).map((e) => e.title));
  check("every sample activity is titled SAMPLE —", sampleTitles.every((t) => t.startsWith("SAMPLE — ")));
  const sampleVerify = await page.evaluate(async () => (await window.__registrar.led.verifyEvidence()).map((r) => r.verify));
  check("sample evidence: 8 certificates, every hash OK", sampleVerify.length === 8 && sampleVerify.every((v) => v === "OK"), sampleVerify.join(","));

  const csv = (await downloadOf(() => page.click("#dl-csv"))).toString("utf8");
  check("CSV report: header + 9 activities", csv.startsWith("\ufeffDate,Title,") && csv.trim().split("\r\n").length === 10);
  const xlsx = readZip(new Uint8Array(await downloadOf(() => page.click("#dl-xlsx"))));
  const xnames = xlsx.map((f) => f.name);
  const wb = new TextDecoder().decode(xlsx.find((f) => f.name === "xl/workbook.xml").data);
  check("XLSX report: Activities, Evidence and Totals sheets", xnames.includes("xl/worksheets/sheet3.xml") && /Activities.*Evidence.*Totals/.test(wb), xnames.join(", "));
  const ics = (await downloadOf(() => page.click("#dl-ics"))).toString("utf8");
  check("ICS report: one cycle end per credential with a start (3), two alarms each",
    (ics.match(/BEGIN:VEVENT/g) || []).length === 3 && (ics.match(/TRIGGER:-P90D/g) || []).length === 3 && (ics.match(/TRIGGER:-P30D/g) || []).length === 3);

  const sampleExport = await downloadOf(() => page.click("#btn-export"));
  await page.click("#btn-wipe");
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Which credentials do you hold?");
  const sampleFile = join(dirname(exportFile), "sample-export.json");
  writeFileSync(sampleFile, sampleExport);
  await page.setInputFiles("#import-file", sampleFile);
  await page.waitForFunction(() => /Imported|refused/.test(document.getElementById("toast").textContent));
  await go("#/");
  check("SAMPLE DATA IS BACK after export -> wipe -> import", (await entryCount()) === 9 && (await page.locator("#sample-remove").count()) === 1);
  const backVerify = await page.evaluate(async () => (await window.__registrar.led.verifyEvidence()).map((r) => r.verify));
  check("its certificates are back and verify", backVerify.length === 8 && backVerify.every((v) => v === "OK"));
  await page.click("#sample-remove");
  await page.waitForFunction(() => !document.querySelector("#sample-remove"));
  const afterRemove = await page.evaluate(async () => ({
    entries: (await window.__registrar.led.listEntries()).length,
    files: (await window.__registrar.led.store.getAll("files")).length,
    creds: (await window.__registrar.led.allCredentialState()).map((s) => `${s.credential}:${s.anchor_date}`).sort(),
  }));
  // An import never carries the sample's record of what it wrote, so the
  // credential settings that came in with the import are all kept (F1).
  check("remove sample data after an import: activities and certificates gone, every imported credential setting kept",
    afterRemove.entries === 0 && afterRemove.files === 0 && afterRemove.creds.length === 3 && afterRemove.creds.includes("cissp:2025-09-01") &&
    afterRemove.creds.every((c) => !c.endsWith(":null")), JSON.stringify(afterRemove));

  // == F1: a credential setting you change after loading the sample survives its removal ==
  await page.click("#btn-wipe");
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Which credentials do you hold?");
  await page.click("#welcome-skip");
  await page.waitForFunction(() => document.querySelector("#sample-load"));
  await page.click("#sample-load");
  await page.waitForFunction(() => document.querySelector("#sample-remove"));
  await go("#/settings");
  await page.fill("#a-cysa_plus", "2025-02-03");
  await page.fill("#co-cysa_plus", "4");
  await page.click('#settings-form button[type="submit"]');
  await page.waitForFunction(() => location.hash === "#/" && document.querySelector("#sample-remove"));
  await page.click("#sample-remove");
  await page.waitForFunction(() => !document.querySelector("#sample-remove"));
  const toastText = await page.locator("#toast").innerText();
  const kept = await page.evaluate(async () => (await window.__registrar.led.allCredentialState()).map((s) => `${s.credential}:${s.anchor_date}:${s.carryover_in}`).sort());
  check("F1: a sample-created credential you edited keeps your values after Remove sample data", kept.join() === "cysa_plus:2025-02-03:4", kept.join());
  check("F1: the toast says it kept the setting you changed", toastText.includes("Kept 1 credential setting you changed"), toastText);

  // == F3: a crafted import is refused with a reason, and count renders as text ==
  const crafted = (entries) => {
    const f = join(dirname(exportFile), `crafted-${Math.random().toString(16).slice(2)}.json`);
    writeFileSync(f, JSON.stringify({ format: "cpebot-ledger-v0.2", entries }));
    return f;
  };
  const evil = "<img src=x onerror=\"window.__pwned=1\">";
  const craftedEntry = { uuid: "feedfacefeedfacefeedfacefeedface", activity_date: "2026-05-05", title: "Crafted", provider: "X", hours: 1, count: evil, tags: ["training"], markers: [], submitted_to: [], notes: "", status: "confirmed", capture_source: "web", evidence: [] };
  const entriesBefore = await entryCount();
  await page.evaluate(() => (document.getElementById("toast").textContent = ""));
  await page.setInputFiles("#import-file", crafted([craftedEntry]));
  await page.waitForFunction(() => /Imported|refused/.test(document.getElementById("toast").textContent));
  const refusedText = await page.locator("#toast").innerText();
  check("F3: an import whose count is markup is refused, naming the field", /Import refused: entries\[0\]\.count/.test(refusedText) && (await entryCount()) === entriesBefore, refusedText);
  await page.evaluate(() => (document.getElementById("toast").textContent = ""));
  await page.setInputFiles("#import-file", crafted([{ ...craftedEntry, count: 1, title: { html: evil } }]));
  await page.waitForFunction(() => /Imported|refused/.test(document.getElementById("toast").textContent));
  check("F3: an import whose title is not text is refused", /Import refused: entries\[0\]\.title/.test(await page.locator("#toast").innerText()), await page.locator("#toast").innerText());
  // Defence in depth: a row that is already stored with markup in count still renders as text.
  await page.evaluate(async (e) => {
    const { blankEntry } = await import("./ledger.js");
    await window.__registrar.led.store.put("entries", { ...blankEntry(), ...e, evidence: undefined });
  }, craftedEntry);
  await go(`#/entry/${craftedEntry.uuid}`);
  const countCell = await page.evaluate(() => {
    const dt = [...document.querySelectorAll("main dt")].find((d) => d.textContent === "Count");
    return { text: dt.nextElementSibling.textContent, imgs: dt.nextElementSibling.querySelectorAll("img").length, pwned: Boolean(window.__pwned) };
  });
  check("F3: a stored count is escaped on the activity page (no element, no script)", countCell.imgs === 0 && !countCell.pwned && countCell.text === evil, JSON.stringify(countCell));
  await page.evaluate(async (u) => window.__registrar.led.store.delete("entries", u), craftedEntry.uuid);

  // == F4: an imported filename with a path in it reaches the packet zip as one flat, safe name ==
  const evilPdf = Buffer.from("%PDF-1.4 sample evidence for the filename check\n");
  const { createHash } = await import("node:crypto");
  const evilSha = createHash("sha256").update(evilPdf).digest("hex");
  const travDoc = {
    format: "cpebot-ledger-v0.2",
    entries: [{ ...craftedEntry, uuid: "bada55edbada55edbada55edbada55ed", title: "Path traversal check", count: 1, submitted_to: [],
      evidence: [{ sha256: evilSha, filename: "x/../../../.bashrc", bytes: evilPdf.length, mime: "application/pdf", added_at: "", note: "" }] }],
    registrar: { extension: "registrar-browser-1", credential_state: [{ credential: "cissp", anchor_date: "2025-09-01", active: 1 }], fees: [], config: {},
      files: { [evilSha]: { mime: "application/pdf", data_base64: evilPdf.toString("base64") } } },
  };
  const travFile = join(dirname(exportFile), "traversal.json");
  writeFileSync(travFile, JSON.stringify(travDoc));
  await page.evaluate(() => (document.getElementById("toast").textContent = ""));
  await page.setInputFiles("#import-file", travFile);
  await page.waitForFunction(() => /Imported|refused/.test(document.getElementById("toast").textContent));
  const storedName = await page.evaluate(async () => (await window.__registrar.led.listEvidence()).map((r) => r.filename));
  check("F4: an imported filename 'x/../../../.bashrc' is stored as '.bashrc'", storedName.join() === ".bashrc", storedName.join());
  await go("#/packets");
  const travZip = readZip(new Uint8Array(await downloadOf(() => page.click('[data-zip="isc2"]'))));
  const travNames = travZip.map((f) => f.name);
  const travEv = travNames.filter((n) => n.startsWith("evidence/"));
  check("F4: the packet zip entry is evidence/<date>_<uuid8>_.bashrc, with no '..', no '\\' and no deeper path",
    travEv.join() === "evidence/2026-05-05_bada55ed_.bashrc" && travNames.every((n) => !n.includes("..") && !n.includes("\\") && n.split("/").length <= 2), travNames.join(", "));
} catch (err) {
  check("verification ran to the end", false, err.stack.split("\n").slice(0, 3).join(" | "));
}

check("no request left 127.0.0.1 (BYOK call was intercepted)", foreign.length === 0, foreign.slice(0, 3).join(", "));
const csp = consoleErrors.filter((e) => /Content Security Policy|Refused to/i.test(e));
check("no Content-Security-Policy violation", csp.length === 0, csp.slice(0, 2).join(" | "));
const other = consoleErrors.filter((e) => !csp.includes(e) && !/Failed to load resource.*(ERR_INTERNET_DISCONNECTED|net::)/.test(e));
check("no other console errors", other.length === 0, other.slice(0, 3).join(" | "));

await browser.close();
server.close();
if (tmp) rmSync(tmp, { recursive: true, force: true });
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);

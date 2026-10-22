// Accessibility audit of every screen of the built site, headless, in light
// and dark colour schemes, under the real Content-Security-Policy.
//
//   AXE=/path/to/axe.min.js node e2e/a11y.mjs [--json out.json]
//
// axe-core is not a dependency of this repo: point AXE at a copy (or have
// axe-core resolvable from the global node_modules). It is injected through
// the DevTools protocol, which the page's CSP does not govern, and nothing
// about it is shipped. Without axe the run stops: a silent pass would be
// worse than no audit. Exit 0 only when no screen has a violation.
import { createRequire } from "node:module";
import { execSync, execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const webapp = dirname(here);
const repo = dirname(webapp);
const args = process.argv.slice(2);
const jsonOut = args.includes("--json") ? args[args.indexOf("--json") + 1] : null;

const require = createRequire(join(execSync("npm root -g").toString().trim(), "/"));
const { chromium } = require("playwright");
let axePath = process.env.AXE;
if (!axePath) {
  try {
    axePath = require.resolve("axe-core/axe.min.js");
  } catch {
    console.error("axe-core not found: set AXE=/path/to/axe.min.js (see webapp/README.md)");
    process.exit(2);
  }
}
if (!existsSync(axePath)) {
  console.error(`AXE=${axePath} does not exist`);
  process.exit(2);
}
const axeSource = readFileSync(axePath, "utf8");

const { server, port } = await serve(join(repo, "site"));
const base = `http://127.0.0.1:${port}`;
const browser = await chromium.launch();
const report = [];

async function audit(page, name) {
  await page.evaluate(axeSource);
  const r = await page.evaluate(() => window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"] }, resultTypes: ["violations"] }));
  const v = r.violations.map((x) => ({ id: x.id, impact: x.impact, nodes: x.nodes.length, help: x.help, targets: x.nodes.slice(0, 3).map((n) => n.target.join(" ")) }));
  const n = v.reduce((s, x) => s + x.nodes, 0);
  report.push({ screen: name, violations: v, count: n });
  console.log(`${n ? "  FAIL" : "  ok  "} ${name}: ${n} violation node(s)${v.length ? ` — ${v.map((x) => `${x.id}×${x.nodes}`).join(", ")}` : ""}`);
}

async function appPage(scheme, seed) {
  const context = await browser.newContext({ colorScheme: scheme, viewport: { width: 1100, height: 900 } });
  const page = await context.newPage();
  page.on("dialog", (d) => d.accept());
  await page.goto(`${base}/app/`);
  await page.waitForFunction(() => window.__registrar && document.querySelector("main h1"));
  if (seed) {
    const py = JSON.parse(execFileSync("python3", [join(webapp, "tests", "py_export.py")], { maxBuffer: 64 << 20 }).toString());
    const file = join(mkdtempSync(join(tmpdir(), "registrar-a11y-")), "demo.json");
    writeFileSync(file, JSON.stringify(py.export));
    await page.setInputFiles("#import-file", file);
    await page.waitForFunction(() => /Imported/.test(document.getElementById("toast").textContent));
    await page.evaluate(async (states) => {
      const led = window.__registrar.led;
      await led.setConfig("attendee_name", "Jordan Sample");
      await led.setConfig("onboarding", "done");
      for (const s of states) await led.setCredentialState(s.credential, { anchor_date: s.anchor_date, carryover_in: 0, active: 1 });
    }, py.credential_state);
  }
  return { context, page };
}

const go = async (page, hash) => {
  await page.evaluate((h) => (location.hash = h), hash);
  await page.waitForFunction((h) => location.hash === h && document.querySelector("main h1"), hash);
  await page.waitForTimeout(250);
};

try {
  for (const scheme of ["light", "dark"]) {
    console.log(`-- ${scheme}`);
    for (const path of ["/", "/privacy"]) {
      const page = await browser.newPage({ colorScheme: scheme });
      await page.goto(base + path);
      await audit(page, `${scheme} site ${path}`);
      await page.close();
    }
    // the rules section as cards and as the grid, unfiltered and filtered
    for (const width of [390, 1280]) {
      const page = await browser.newPage({ colorScheme: scheme, viewport: { width, height: 900 } });
      await page.goto(base + "/");
      await audit(page, `${scheme} site / at ${width}px`);
      for (const id of ["isc2", "acfe"]) await page.click(`label[for="rf-${id}"]`);
      await audit(page, `${scheme} site / at ${width}px, filtered to two bodies`);
      await page.close();
    }
    {
      // first run: whatever the app shows to a fresh browser
      const { context, page } = await appPage(scheme, false);
      await audit(page, `${scheme} app first run (${await page.locator("main h1").innerText()})`);
      if (await page.locator("#welcome-skip").count()) {
        await page.check('input[name="hold"][value="cissp"]');
        await audit(page, `${scheme} app first run, a credential ticked`);
        await page.click("#welcome-skip");
        await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Credentials");
        await audit(page, `${scheme} app empty ledger`);
        await page.click("#sample-load");
        await page.waitForFunction(() => document.querySelector("#sample-remove"));
        await audit(page, `${scheme} app sample data loaded`);
        await go(page, "#/entries");
        await audit(page, `${scheme} app #/entries with sample data`);
      }
      await context.close();
    }
    const { context, page } = await appPage(scheme, true);
    const cred = (await page.evaluate(async () => (await window.__registrar.led.allCredentialState())[0].credential));
    const entry = (await page.evaluate(async () => (await window.__registrar.led.listEntries())[0].uuid));
    for (const hash of ["#/", `#/credential/${cred}`, "#/log", "#/entries", `#/entry/${entry}`, "#/submissions", "#/packets", "#/bodies", "#/settings", "#/nope"]) {
      await go(page, hash);
      await audit(page, `${scheme} app ${hash.replace(/[0-9a-f]{32}/, "<uuid>")}`);
    }
    // the Log screen with a parsed certificate in the form
    await go(page, "#/log");
    await page.setInputFiles("#file-input", join(webapp, "tests", "fixtures", "certificates", "01-isc2-webinar-centred.pdf"));
    await page.waitForFunction(() => /fields found|could not read/.test(document.querySelector("#ingest-status")?.textContent || ""));
    await audit(page, `${scheme} app #/log after reading a certificate`);
    await context.close();
  }
} catch (err) {
  console.error(err);
  report.push({ screen: "audit ran to the end", violations: [{ id: "error", nodes: 1, help: String(err) }], count: 1 });
}

await browser.close();
server.close();
const total = report.reduce((s, r) => s + r.count, 0);
const rules = {};
for (const r of report) for (const v of r.violations) rules[v.id] = (rules[v.id] || 0) + v.nodes;
console.log(`\naxe-core ${JSON.parse(readFileSync(join(dirname(axePath), "package.json"), "utf8")).version}: ${total} violation node(s) across ${report.length} screen audits`);
for (const [id, n] of Object.entries(rules).sort((a, b) => b[1] - a[1])) console.log(`  ${id}: ${n}`);
if (jsonOut) writeFileSync(jsonOut, JSON.stringify({ total, rules, report }, null, 1));
process.exit(total ? 1 : 0);

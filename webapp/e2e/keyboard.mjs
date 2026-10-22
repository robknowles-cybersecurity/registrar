// Keyboard-only walk of the core flow in the built app: no mouse, no
// element.click(). Every step reaches its control with Tab (or Shift+Tab),
// checks that the focused control shows a visible focus indicator, and acts
// with Space, Enter or typing. The one file dialog is answered through
// Playwright's file chooser, as a person would pick a file.
//
//   node e2e/keyboard.mjs      # exit 0 only if the whole walk succeeds
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const webapp = dirname(here);
const repo = dirname(webapp);
const require = createRequire(join(execSync("npm root -g").toString().trim(), "/"));
const { chromium } = require("playwright");

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok) });
  console.log(`${ok ? "  ok  " : "  FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
}

const { server, port } = await serve(join(repo, "site"));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1100, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
let presses = 0;

/** Tab until the focused element matches sel; check its focus is visible. */
async function tabTo(sel, label, { back = false, max = 120 } = {}) {
  for (let i = 0; i < max; i++) {
    if (await page.evaluate((s) => document.activeElement?.matches(s), sel)) {
      const visible = await page.evaluate(() => {
        const el = document.activeElement;
        const cs = getComputedStyle(el);
        const ring = cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
        const shadow = cs.boxShadow && cs.boxShadow !== "none";
        const r = el.getBoundingClientRect();
        return (ring || shadow) && r.width > 0 && r.height > 0;
      });
      check(`reach ${label} by keyboard (${i} ${back ? "Shift+Tab" : "Tab"}), focus visible`, visible);
      return;
    }
    await page.keyboard.press(back ? "Shift+Tab" : "Tab");
    presses++;
  }
  check(`reach ${label} by keyboard`, false, `not reached in ${max} presses`);
  throw new Error(`could not reach ${label}`);
}
const h1 = () => page.locator("main h1").innerText();

try {
  await page.goto(`http://127.0.0.1:${port}/app/`);
  await page.waitForFunction(() => window.__registrar && document.querySelector("main h1"));

  // skip link first
  await page.keyboard.press("Tab");
  presses++;
  check("first Tab lands on the skip link", await page.evaluate(() => document.activeElement?.classList.contains("skip")));

  // first run: tick CISSP, give its start date, continue
  await tabTo('input[name="hold"][value="cissp"]', "the CISSP checkbox");
  await page.keyboard.press("Space");
  await tabTo("#w-cissp", "the CISSP cycle-start date");
  await page.keyboard.type("09012025");
  await tabTo('#welcome-form button[type="submit"]', "Continue");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Credentials");
  check("onboarding done by keyboard: CISSP is tracked", (await page.locator("#c-cissp").count()) === 1);

  // load the sample data
  await tabTo("#sample-load", "Load sample data");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#sample-remove"));
  await page.waitForTimeout(300);
  check("sample data loaded by keyboard", (await page.locator(".banner.sample").count()) === 1);

  // to Log activity through the main navigation
  await page.evaluate(() => document.body.focus());
  await tabTo('.top nav a[href="#/log"]', "the Log activity link");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("main h1")?.textContent === "Log an activity");
  await page.waitForTimeout(300); // let the screen bind its handlers, as e2e/verify.mjs does
  check("hash navigation moves focus to the main content", await page.evaluate(() => document.activeElement?.id === "view"));

  // choose a certificate with the keyboard
  await tabTo("#pick-file", "Choose file");
  // What a keyboard user depends on: Enter gives a trusted click, and the
  // hidden file input is clicked while that user activation is live (which
  // is what opens the picker in a real browser). Headless Chromium surfaces
  // the picker as a "filechooser" event only some of the time, so when it
  // does not, the file goes to the same input the picker would fill.
  await page.evaluate(() => {
    window.__kbd = [];
    document.getElementById("file-input").addEventListener("click", () => window.__kbd.push(navigator.userActivation.isActive), { once: true });
  });
  const cert = join(webapp, "src", "sample", "try-capture-certificate.pdf");
  const chooserSeen = page.waitForEvent("filechooser", { timeout: 5000 }).catch(() => null);
  await page.keyboard.press("Enter");
  const chooser = await chooserSeen;
  check("Enter on Choose file clicks the file input with user activation (opens the picker)", (await page.evaluate(() => window.__kbd)).join() === "true");
  if (chooser) await chooser.setFiles(cert);
  else await page.setInputFiles("#file-input", cert);
  console.log(`        (picker ${chooser ? "surfaced as a filechooser event" : "not surfaced by headless Chromium; file set on the input"})`);
  await page.waitForFunction(() => /fields found|could not read/.test(document.querySelector("#ingest-status")?.textContent || ""));
  check("the certificate is read and the form filled", (await page.locator("#f-hours").inputValue()) === "3.5" && (await page.locator("#f-title").inputValue()) === "Incident Response Tabletop Exercises");

  // the parsed form's fields are reachable in order, then Save and confirm
  await tabTo("#f-activity_date", "the Date field");
  await tabTo("#f-hours", "the Hours field");
  await tabTo("#f-title", "the Title field");
  await tabTo('input[name="tags"][value="training"]', "a Kind of activity checkbox");
  await tabTo('button[name="save"][value="confirm"]', "Save and confirm");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => /^#\/entry\/[0-9a-f]{32}$/.test(location.hash));
  check("activity saved and confirmed by keyboard", (await h1()).includes("Incident Response Tabletop Exercises") && (await page.locator("main h1 .pill").innerText()) === "confirmed");

  // reports and the banner actions are reachable too
  await page.evaluate(() => document.body.focus());
  await tabTo('.top nav a[href="#/"]', "the Credentials link");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#dl-xlsx"));
  await page.waitForTimeout(300);
  await tabTo("#dl-xlsx", "the Workbook (.xlsx) button");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.keyboard.press("Enter")]);
  check("the workbook downloads by keyboard", (await dl.suggestedFilename()).endsWith(".xlsx"));
  await tabTo("#sample-remove", "Remove sample data", { back: true });
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !document.querySelector("#sample-remove"));
  check("sample data removed by keyboard; your activity stays", (await page.evaluate(async () => (await window.__registrar.led.listEntries()).length)) === 1);
  await page.evaluate(() => document.body.focus());
  await tabTo("#btn-export", "Export");
  const [ex] = await Promise.all([page.waitForEvent("download"), page.keyboard.press("Enter")]);
  check("Export by keyboard", (await ex.suggestedFilename()).endsWith(".json"));
} catch (err) {
  check("keyboard walk ran to the end", false, err.message.split("\n")[0]);
}
check("no page errors", errors.length === 0, errors.join(" | "));
await browser.close();
server.close();
const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed} passed, ${failed} failed (${presses} key presses to move focus)`);
process.exit(failed ? 1 : 0);

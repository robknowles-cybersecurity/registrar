// Headless check of the built site's share and trust surface: the landing
// page, /privacy, the share card, the icons and security.txt, served with
// site/_headers applied (the real Content-Security-Policy).
//
//   node e2e/site.mjs                # exit 0 only if every check passes
//   node e2e/site.mjs --break og     # a copy of the site without og.png: must FAIL
//   node e2e/site.mjs --break csp    # a copy whose landing page loads a third-party image: must FAIL
//   node e2e/site.mjs --break breakpoint  # a copy whose rules grid has no breakpoint: must FAIL
//   node e2e/site.mjs --break csp-all     # a copy with the script-free CSP on / only, not /*: must FAIL
//   node e2e/site.mjs --shots DIR    # also save a screenshot of the rules section at each width
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = dirname(dirname(here));
const args = process.argv.slice(2);
const breakWhat = args.includes("--break") ? args[args.indexOf("--break") + 1] : null;
const shotsDir = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : null;
const WIDTHS = [360, 390, 768, 1024, 1280, 1600];
const BREAKPOINT = 960;
const PUBLIC = "https://ceu.tools";
const REPORT_URL = "https://github.com/robknowles-cybersecurity/registrar/security/advisories/new";

const require = createRequire(join(execSync("npm root -g").toString().trim(), "/"));
const { chromium } = require("playwright");

let siteDir = join(repo, "site");
let tmp = null;
if (breakWhat) {
  tmp = mkdtempSync(join(tmpdir(), "registrar-site-mutant-"));
  cpSync(siteDir, tmp, { recursive: true });
  siteDir = tmp;
  if (breakWhat === "og") rmSync(join(siteDir, "og.png"));
  else if (breakWhat === "csp") {
    const p = join(siteDir, "index.html");
    writeFileSync(p, readFileSync(p, "utf8").replace("</main>", '<img src="https://example.com/pixel.png" alt=""></main>'));
  } else if (breakWhat === "breakpoint") {
    const p = join(siteDir, "style.css");
    const css = readFileSync(p, "utf8");
    if (!css.includes(`@media (min-width: ${BREAKPOINT}px) {`)) throw new Error("the rules breakpoint is not in style.css");
    writeFileSync(p, css.replace(`@media (min-width: ${BREAKPOINT}px) {`, "@media all {"));
  } else if (breakWhat === "csp-all") {
    // F9 reverted: the script-free policy back on / and /privacy only, not /*
    const p = join(siteDir, "_headers");
    const h = readFileSync(p, "utf8");
    const line = /\n(  Content-Security-Policy: default-src 'none'; style-src 'self';[^\n]*)\n\n# The landing page/.exec(h);
    if (!line) throw new Error("the /* policy line is not in _headers");
    writeFileSync(p, h.replace(`${line[1]}\n`, "").replace("/\n  Permissions-Policy", `/\n${line[1]}\n  Permissions-Policy`));
  } else throw new Error(`unknown --break ${breakWhat}`);
  console.log(`MUTATED SITE (--break ${breakWhat}): this run must FAIL\n`);
}

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "  ok  " : "  FAIL"} ${name}${detail ? ` — ${detail}` : ""}`);
}

const { server, port } = await serve(siteDir);
const base = `http://127.0.0.1:${port}`;
const local = (u) => (u.startsWith(PUBLIC) ? base + u.slice(PUBLIC.length) : u);

async function get(path) {
  const res = await fetch(base + path, { redirect: "manual" });
  return { status: res.status, type: res.headers.get("content-type") || "", headers: res.headers, body: Buffer.from(await res.arrayBuffer()) };
}

function walkHtml(dir, prefix = "") {
  return readdirSync(join(dir, prefix), { withFileTypes: true }).flatMap((d) => {
    const rel = prefix ? `${prefix}/${d.name}` : d.name;
    if (d.isDirectory()) return rel === "app/vendor" ? [] : walkHtml(dir, rel);
    return d.name.endsWith(".html") ? [rel] : [];
  }).sort();
}

function pngSize(buf) {
  if (buf.length < 24 || buf.toString("latin1", 1, 4) !== "PNG") return null;
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

const browser = await chromium.launch();
try {
  // -- plain fetches
  const og = await get("/og.png");
  check("og.png 200", og.status === 200, String(og.status));
  check("og.png is a 1200x630 PNG", String(pngSize(og.body)) === "1200,630", String(pngSize(og.body)));
  for (const p of ["/favicon.ico", "/favicon.svg", "/icons/apple-touch-icon.png", "/icons/icon-192.png", "/icons/icon-512.png", "/icons/icon-maskable-512.png"]) {
    const r = await get(p);
    check(`${p} 200`, r.status === 200 && r.body.length > 0, `${r.status} ${r.type}`);
  }
  for (const [p, size] of [["/icons/icon-192.png", "192,192"], ["/icons/icon-512.png", "512,512"], ["/icons/apple-touch-icon.png", "180,180"]]) {
    const r = await get(p);
    check(`${p} is ${size.replace(",", "x")}`, String(pngSize(r.body)) === size, String(pngSize(r.body)));
  }
  const sec = await get("/.well-known/security.txt");
  const secText = sec.body.toString("utf8");
  check("security.txt 200, text/plain", sec.status === 200 && sec.type.startsWith("text/plain"), `${sec.status} ${sec.type}`);
  check("security.txt Contact is the private vulnerability reporting URL", new RegExp(`^Contact: ${REPORT_URL.replace(/[.?]/g, "\\$&")}$`, "m").test(secText));
  const expires = /^Expires: (\S+)$/m.exec(secText);
  check("security.txt Expires is in the future and within a year and a half", expires && Date.parse(expires[1]) > Date.now() && Date.parse(expires[1]) < Date.now() + 548 * 864e5, expires && expires[1]);
  check("security.txt names no email address", !/@|mailto:/i.test(secText));
  const priv = await get("/privacy");
  check("/privacy 200", priv.status === 200 && priv.type.startsWith("text/html"), `${priv.status} ${priv.type}`);
  check("/privacy has the landing page's script-free CSP", /default-src 'none'/.test(priv.headers.get("content-security-policy") || "") && !/script-src/.test(priv.headers.get("content-security-policy") || ""));
  const privText = priv.body.toString("utf8");
  check("/privacy states what BYOK sends and to whom", privText.includes("api.anthropic.com") && privText.includes("api.openai.com") && /What is never sent/.test(privText) && /20,000 characters/.test(privText));

  // -- F9: every HTML file under site/, at each address it is served from,
  // and any address with no file (404.html), carries exactly one CSP
  const htmlFiles = walkHtml(siteDir);
  const addresses = htmlFiles.flatMap((rel) => {
    if (rel === "index.html") return ["/", "/index.html"];
    if (rel.endsWith("/index.html")) return [`/${rel.slice(0, -"index.html".length)}`, `/${rel}`];
    return [`/${rel}`, `/${rel.replace(/\.html$/, "")}`];
  });
  const noCsp = [];
  for (const a of [...addresses, "/no-such-page", "/deep/no/such/page.html", "/app/no-such-file"]) {
    const r = await get(a);
    const csp = r.headers.get("content-security-policy") || "";
    const policies = csp ? csp.split(/,\s*(?=default-src)/).length : 0;
    const appArea = a.startsWith("/app/"); // the app's own policy, for a missing file there too
    if (policies !== 1 || (!appArea && /script-src/.test(csp)) || (appArea && !/script-src 'self'/.test(csp))) noCsp.push(`${a} (${r.status}): ${policies} policies${csp ? `, ${csp.slice(0, 40)}…` : ""}`);
  }
  check(`F9: each of ${addresses.length + 3} addresses (${htmlFiles.length} HTML files and 3 missing ones) has exactly one CSP, script-free outside /app/`, noCsp.length === 0, noCsp.join(" | "));
  // B: every page names its ceu.tools address as canonical; the landing page
  // carries the static "served at" banner (no script)
  const ALIAS_TEXT = "Served at ceu.tools, cpe.insiderthreatanalyst.com and a GitHub Pages mirror. Your data lives in your browser under whichever address you use; export before switching.";
  const canonicalOf = { "index.html": "/", "404.html": "/" };
  const badCanonical = [];
  for (const rel of htmlFiles) {
    const want = PUBLIC + (canonicalOf[rel] || (rel.endsWith("/index.html") ? `/${rel.slice(0, -"index.html".length)}` : `/${rel.replace(/\.html$/, "")}`));
    const found = [...readFileSync(join(siteDir, rel), "utf8").matchAll(/<link rel="canonical" href="([^"]*)">/g)].map((m) => m[1]);
    if (found.length !== 1 || found[0] !== want) badCanonical.push(`${rel}: ${JSON.stringify(found)}, want ${want}`);
  }
  check(`B: each of the ${htmlFiles.length} HTML pages has one canonical link to its ceu.tools address`, badCanonical.length === 0, badCanonical.join(" | "));
  const landingHtml = (await get("/")).body.toString("utf8");
  const aliasBanner = /<aside class="alias-banner"[^>]*>([\s\S]*?)<\/aside>/.exec(landingHtml);
  check("B: the landing page's banner says the data is per address (static HTML)", aliasBanner && aliasBanner[1].replace(/<[^>]+>/g, "") === ALIAS_TEXT && !/<script/i.test(landingHtml));

  // A: the landing and privacy HTML ask the edge not to rewrite them; /app/ is unchanged
  const ct = [];
  for (const a of ["/", "/index.html", "/privacy", "/privacy.html", "/app/", "/app/app.js"]) ct.push([a, (await get(a)).headers.get("cache-control")]);
  check("A: Cache-Control: no-transform on / and /privacy, not on /app/", ct.every(([a, v]) => (a.startsWith("/app/") ? !/no-transform/.test(v || "") : v === "no-transform")), JSON.stringify(ct));
  const nf = await get("/no-such-page");
  const nfText = nf.body.toString("utf8");
  check("F9: a missing address gets 404.html with status 404 (no single-page fallback), linking to / and /app/",
    nf.status === 404 && nf.type.startsWith("text/html") && nfText.includes("<h1>Page not found</h1>") && nfText.includes('href="/"') && nfText.includes('href="/app/"'), `${nf.status} ${nf.type}`);

  // -- pages in a browser
  for (const path of ["/", "/privacy"]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const errors = [];
    const foreign = [];
    const failed = [];
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    context.on("request", (r) => {
      const u = new URL(r.url());
      if (u.hostname !== "127.0.0.1" && !["data:", "blob:"].includes(u.protocol)) foreign.push(r.url());
    });
    context.on("response", (r) => r.status() >= 400 && failed.push(`${r.status()} ${r.url()}`));
    await page.goto(base + path, { waitUntil: "load" });
    // lazy images: scroll through a screen at a time so each one starts
    // loading, then wait for all of them. Decoding first would wait forever
    // on a lazy image that is still far below the fold.
    await page.evaluate(async () => {
      for (let y = 0; y < document.body.scrollHeight; y += window.innerHeight) {
        window.scrollTo(0, y);
        await new Promise((r) => setTimeout(r, 50));
      }
      window.scrollTo(0, document.body.scrollHeight);
    });
    await page.evaluate(() => Promise.all([...document.images].map((i) => i.decode().catch(() => null))));
    await page.waitForTimeout(300);
    const meta = await page.evaluate(() => Object.fromEntries([...document.querySelectorAll("meta[property^='og:'], meta[name^='twitter:']")].map((m) => [m.getAttribute("property") || m.getAttribute("name"), m.content])));
    const broken = await page.evaluate(() => [...document.images].filter((i) => !i.complete || !i.naturalWidth).map((i) => i.getAttribute("src")));
    const icons = await page.evaluate(() => [...document.querySelectorAll("link[rel~='icon'], link[rel='apple-touch-icon']")].map((l) => l.href));
    check(`${path}: no console errors`, errors.length === 0, errors.slice(0, 2).join(" | "));
    check(`${path}: no off-origin requests`, foreign.length === 0, foreign.slice(0, 3).join(", "));
    check(`${path}: no failed requests`, failed.length === 0, failed.slice(0, 3).join(", "));
    check(`${path}: every image loads`, broken.length === 0, broken.join(", "));
    check(`${path}: og:image and twitter:card set`, meta["og:image"] === `${PUBLIC}/og.png` && meta["twitter:card"] === "summary_large_image" && meta["og:title"] && meta["og:description"], JSON.stringify(meta).slice(0, 120));
    const ogRes = meta["og:image"] ? await get(new URL(local(meta["og:image"])).pathname) : { status: 0 };
    check(`${path}: og:image resolves on this site`, ogRes.status === 200, String(ogRes.status));
    let iconsOk = icons.length >= 3;
    for (const href of icons) if ((await get(new URL(href).pathname)).status !== 200) iconsOk = false;
    check(`${path}: favicon and touch icon links resolve`, iconsOk, icons.map((h) => new URL(h).pathname).join(", "));
    if (path === "/") {
      const text = (await page.locator("main").innerText()).toLowerCase();
      check("landing: explains the problem before the tool", text.indexOf("the problem") >= 0 && text.indexOf("the problem") < text.indexOf("tool #1"));
      check("landing: links to /app, GitHub, release notes and /privacy",
        (await page.locator('a[href="/app/"]').count()) > 0 &&
        (await page.locator('a[href="https://github.com/robknowles-cybersecurity/registrar"]').count()) > 0 &&
        (await page.locator('a[href$="/registrar/releases"]').count()) > 0 &&
        (await page.locator('a[href="/privacy"]').count()) > 0);
      check("landing: two screenshots and a GIF", (await page.locator('img[src$=".png"]').count()) === 2 && (await page.locator('img[src$=".gif"]').count()) === 1);
      check("landing: runs no scripts", (await page.locator("script").count()) === 0);
    }
    await context.close();
  }

  // -- B: the app's local-data notice carries the same sentence at any other
  // address, and hides it when the app is served as ceu.tools. Chromium is
  // told that ceu.tools is this local server, so no request leaves it.
  {
    const appText = async (b, url) => {
      const ctx = await b.newContext({ serviceWorkers: "block" });
      const pg = await ctx.newPage();
      await pg.goto(url);
      await pg.waitForFunction(() => document.querySelector("main h1"));
      const r = await pg.evaluate(() => { const n = document.getElementById("alias-note"); return { visible: n.getClientRects().length > 0, text: n.textContent.replace(/\s+/g, " ").trim(), host: location.hostname }; });
      await ctx.close();
      return r;
    };
    const here = await appText(browser, `${base}/app/`);
    check("B: the app's local-data notice shows the 'served at' sentence at 127.0.0.1", here.visible && here.text === ALIAS_TEXT, JSON.stringify(here));
    const asCeu = await chromium.launch({ args: [`--host-resolver-rules=MAP ceu.tools 127.0.0.1:${port}`] });
    try {
      const there = await appText(asCeu, "http://ceu.tools/app/");
      check("B: the app hides that sentence when served as ceu.tools", there.host === "ceu.tools" && !there.visible, JSON.stringify(there));
    } finally {
      await asCeu.close();
    }
  }

  // -- "How each body counts": no horizontal scroll at any width, cards
  // below the breakpoint and the grid above it, and a filter that works.
  const data = JSON.parse(readFileSync(join(siteDir, "app", "data.json"), "utf8"));
  const total = data.programs.filter((p) => p.landing).length;
  const board = async (page) => page.evaluate(() => {
    const cards = [...document.querySelectorAll(".rules-cards > .rb")];
    const shown = cards.filter((c) => c.getClientRects().length > 0);
    const heads = document.querySelector(".rb-colheads");
    const headCycle = heads.children[1].getBoundingClientRect();
    const dts = shown.map((c) => c.querySelector("dt").getBoundingClientRect().width);
    const cycleX = shown.map((c) => Math.round(c.querySelector(".rb-cycle").getBoundingClientRect().left));
    const b = document.querySelector(".rules-board");
    return {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      shown: shown.map((c) => c.querySelector("h3").textContent),
      count: document.querySelector(".rules-count").innerText.trim(),
      headsShown: heads.getClientRects().length > 0,
      dtsVisible: dts.every((w) => w > 1),
      cycleAligned: cycleX.every((x) => x === Math.round(headCycle.left)),
      boardFits: b.scrollWidth <= b.clientWidth + 1 && b.getBoundingClientRect().right <= document.documentElement.clientWidth + 0.5,
      boardWidth: Math.round(b.getBoundingClientRect().width),
    };
  });
  const measured = [];
  for (const w of WIDTHS) {
    const context = await browser.newContext({ viewport: { width: w, height: 900 } });
    const page = await context.newPage();
    await page.goto(base + "/", { waitUntil: "load" });
    const m = await board(page);
    measured.push({ width: w, scrollWidth: m.scrollWidth, clientWidth: m.clientWidth, boardWidth: m.boardWidth });
    check(`rules @${w}px: no horizontal scroll (scrollWidth ${m.scrollWidth} = clientWidth ${m.clientWidth})`, m.scrollWidth === m.clientWidth);
    check(`rules @${w}px: the board fits inside the viewport`, m.boardFits, `board ${m.boardWidth}px`);
    check(`rules @${w}px: all ${total} bodies shown, count says so`, m.shown.length === total && m.count === `${total} of ${total} bodies shown`, `${m.shown.length}; "${m.count}"`);
    if (w < BREAKPOINT) check(`rules @${w}px: stacked cards (field labels visible, no column header row)`, !m.headsShown && m.dtsVisible);
    else check(`rules @${w}px: comparison grid (column header row, every body's cycle under its heading)`, m.headsShown && m.cycleAligned);
    if (shotsDir) {
      mkdirSync(shotsDir, { recursive: true });
      const bb = await page.locator("section[aria-labelledby=rules]").boundingBox();
      await page.screenshot({ path: join(shotsDir, `rules-${w}.png`), fullPage: true, clip: { x: 0, y: bb.y, width: w, height: bb.height } });
    }
    await context.close();
  }
  if (shotsDir) writeFileSync(join(shotsDir, "widths.json"), `${JSON.stringify(measured, null, 1)}\n`);

  for (const w of [390, 1280]) {
    const context = await browser.newContext({ viewport: { width: w, height: 900 } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(base + "/", { waitUntil: "load" });
    for (const id of ["isc2", "comptia", "acfe"]) await page.click(`label[for="rf-${id}"]`);
    let m = await board(page);
    check(`filter @${w}px: three chips show three bodies, count "3 of ${total}"`, m.shown.join("|") === "ISC2|CompTIA|ACFE" && m.count === `3 of ${total} bodies shown`, `${m.shown.join(", ")}; "${m.count}"`);
    check(`filter @${w}px: still no horizontal scroll when filtered`, m.scrollWidth === m.clientWidth, `${m.scrollWidth}/${m.clientWidth}`);
    if (w === 1280) check("filter @1280px: filtered rows stay aligned to the grid", m.cycleAligned);
    const states = await page.evaluate(() => [...document.querySelectorAll(".chip-input")].filter((i) => i.checked).map((i) => i.id));
    check(`filter @${w}px: the ticked chips report checked`, states.join(",") === "rf-isc2,rf-comptia,rf-acfe", states.join(","));
    await page.click(".chip-all");
    m = await board(page);
    check(`filter @${w}px: All shows every body again`, m.shown.length === total && m.count === `${total} of ${total} bodies shown`, `${m.shown.length}; "${m.count}"`);
    // keyboard: Tab from All reaches the first body chip, Space ticks it,
    // Tab moves on, Space ticks the next; focus is visible on the chip.
    await page.focus(".chip-all");
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement.id);
    const ring = await page.evaluate(() => getComputedStyle(document.querySelector(`label[for="${document.activeElement.id}"]`)).outlineStyle);
    await page.keyboard.press("Space");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Space");
    m = await board(page);
    check(`filter @${w}px: keyboard (Tab, Space) ticks chips, focus ring visible`, focused === "rf-isc2" && ring !== "none" && m.shown.join("|") === "ISC2|ISACA" && m.count === `2 of ${total} bodies shown`, `${focused}; outline ${ring}; ${m.shown.join(", ")}; "${m.count}"`);
    await page.focus(".chip-all");
    await page.keyboard.press("Enter");
    m = await board(page);
    check(`filter @${w}px: Enter on All clears the filter`, m.shown.length === total);
    // every possible count, ticking in reverse order: label = rendered cards
    const ids = await page.evaluate(() => [...document.querySelectorAll(".chip-input")].map((i) => i.id).reverse());
    const bad = [];
    for (let k = 1; k <= ids.length; k++) {
      await page.click(`label[for="${ids[k - 1]}"]`);
      m = await board(page);
      if (m.shown.length !== k || m.count !== `${k} of ${total} bodies shown`) bad.push(`${k}: ${m.shown.length} shown, "${m.count}"`);
    }
    check(`filter @${w}px: for every count 1 to ${total}, the label matches the cards rendered`, ids.length === total && !bad.length, bad.join("; "));
    const ax = await page.locator(".rules-count").ariaSnapshot();
    check(`filter @${w}px: the count is in the accessibility tree`, ax.includes(`${total} of ${total} bodies shown`), ax);
    await page.click(".chip-all");
    // What a reload does. The page runs no scripts, so nothing is stored:
    // a reload shows every body. This check records that behaviour; if it
    // ever starts failing, the page has gained storage it does not declare.
    await page.click('label[for="rf-giac"]');
    await page.reload({ waitUntil: "load" });
    m = await board(page);
    check(`filter @${w}px: a reload shows every body again (no storage on this page)`, m.shown.length === total, `${m.shown.length} shown`);
    check(`filter @${w}px: no page errors`, errors.length === 0, errors.join(" | "));
    await context.close();
  }

  // Could the landing page pre-select the bodies chosen in /app? The app's
  // IndexedDB is per origin, not per path, so the data is there for any
  // script on this origin; but the landing page's policy runs no script.
  {
    const context = await browser.newContext();
    const app = await context.newPage();
    await app.goto(base + "/app/", { waitUntil: "load" });
    await app.waitForFunction(async () => (await indexedDB.databases()).some((d) => d.name === "registrar"), null, { timeout: 15000 });
    const page = await context.newPage();
    await page.goto(base + "/", { waitUntil: "load" });
    // evaluate() runs through the DevTools protocol, outside the page's CSP
    const dbs = await page.evaluate(async () => (await indexedDB.databases()).map((d) => d.name));
    check("same origin: the app's IndexedDB is visible from / (to the DevTools protocol)", dbs.includes("registrar"), dbs.join(","));
    const blocked = await page.evaluate(() => new Promise((resolve) => {
      window.__ran = false;
      document.addEventListener("securitypolicyviolation", (e) => resolve(e.effectiveDirective), { once: true });
      const s = document.createElement("script");
      s.src = "/app/schema.js";
      s.onload = () => resolve("loaded");
      document.head.append(s);
      setTimeout(() => resolve("timeout"), 3000);
    }));
    check("but the landing page's CSP blocks any script of its own, same-origin included", blocked === "script-src-elem" || blocked === "script-src", blocked);
    await context.close();
  }

  // -- the app's manifest icons
  const man = JSON.parse((await get("/app/manifest.webmanifest")).body.toString("utf8"));
  const sizes = man.icons.map((i) => i.sizes);
  check("app manifest lists 192 and 512 px icons and a maskable one", sizes.includes("192x192") && sizes.includes("512x512") && man.icons.some((i) => i.purpose === "maskable"));
  let manOk = true;
  for (const i of man.icons) if ((await get(new URL(i.src, `${base}/app/manifest.webmanifest`).pathname)).status !== 200) manOk = false;
  check("every app manifest icon resolves", manOk);
} catch (err) {
  check("site check ran to the end", false, err.stack.split("\n").slice(0, 3).join(" | "));
}

await browser.close();
server.close();
if (tmp) rmSync(tmp, { recursive: true, force: true });
const failedN = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failedN} passed, ${failedN} failed`);
process.exit(failedN ? 1 : 0);

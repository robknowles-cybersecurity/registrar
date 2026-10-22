// Headless check of the built site's share and trust surface: the landing
// page, /privacy, the share card, the icons and security.txt, served with
// site/_headers applied (the real Content-Security-Policy).
//
//   node e2e/site.mjs                # exit 0 only if every check passes
//   node e2e/site.mjs --break og     # a copy of the site without og.png: must FAIL
//   node e2e/site.mjs --break csp    # a copy whose landing page loads a third-party image: must FAIL
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "./serve.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const repo = dirname(dirname(here));
const args = process.argv.slice(2);
const breakWhat = args.includes("--break") ? args[args.indexOf("--break") + 1] : null;
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

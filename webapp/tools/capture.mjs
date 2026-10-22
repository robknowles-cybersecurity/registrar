// Render the landing page's images headlessly: two screenshots of the
// built app, a short GIF of dropping a certificate PDF onto the Log screen,
// the 1200x630 share card (og.png) and the icon set.
//
//   node tools/capture.mjs            # writes into ../site/media and ../site/icons
//
// Everything shown is synthetic: the Python demo ledger ("SAMPLE —" titles,
// "Example ..." providers) and the app's sample certificate. Uses the machine's
// Playwright (npm root -g), like e2e/verify.mjs; the GIF needs ffmpeg.
import { createRequire } from "node:module";
import { execFileSync, execSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { serve } from "../e2e/serve.mjs";

const webapp = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(webapp);
const site = join(repo, "site");
const media = join(site, "media");
const icons = join(site, "icons");
mkdirSync(media, { recursive: true });
mkdirSync(icons, { recursive: true });

const require = createRequire(join(execSync("npm root -g").toString().trim(), "/"));
const { chromium } = require("playwright");

const { server, port } = await serve(site);
const base = `http://127.0.0.1:${port}`;
const browser = await chromium.launch();
const tmp = mkdtempSync(join(tmpdir(), "registrar-capture-"));

let pageCreated = 0;
async function seeded(context) {
  pageCreated = Date.now();
  const page = await context.newPage();
  await page.goto(`${base}/app/`);
  await page.waitForFunction(() => window.__registrar && document.querySelector("main h1"));
  const py = JSON.parse(execFileSync("python3", [join(webapp, "tests", "py_export.py")], { maxBuffer: 64 << 20 }).toString());
  const file = join(tmp, "demo.json");
  writeFileSync(file, JSON.stringify(py.export));
  await page.setInputFiles("#import-file", file);
  await page.waitForFunction(() => /Imported/.test(document.getElementById("toast").textContent));
  await page.evaluate(async (states) => {
    const led = window.__registrar.led;
    await led.setConfig("attendee_name", "Jordan Sample");
    await led.setConfig("onboarding", "done");
    for (const s of states) {
      if (["cissp", "cysa_plus", "gcitp"].includes(s.credential)) {
        await led.setCredentialState(s.credential, { anchor_date: s.anchor_date, carryover_in: 0, active: 1 });
      }
    }
  }, py.credential_state);
  return page;
}

async function go(page, hash) {
  await page.evaluate((h) => (location.hash = h), hash);
  await page.waitForFunction((h) => location.hash === h && document.querySelector("main h1"), hash);
  await page.evaluate(() => document.getElementById("toast").classList.remove("show"));
  await page.waitForTimeout(300);
}

// -- screenshots
{
  const context = await browser.newContext({ viewport: { width: 1200, height: 780 }, colorScheme: "light" });
  const page = await seeded(context);
  await go(page, "#/");
  await page.screenshot({ path: join(media, "progress.png") });
  await go(page, "#/credential/cissp");
  await page.screenshot({ path: join(media, "cissp-detail.png") });
  await context.close();
}

// -- the GIF: Log screen, a certificate dropped on the evidence area, the form fills
{
  const videoDir = join(tmp, "video");
  const size = { width: 900, height: 620 };
  const context = await browser.newContext({ viewport: size, colorScheme: "light", recordVideo: { dir: videoDir, size } });
  const page = await seeded(context);
  await go(page, "#/log");
  await page.evaluate(() => document.getElementById("drop").scrollIntoView({ block: "start" }));
  await page.evaluate(() => window.scrollBy(0, -(document.querySelector("header.top").offsetHeight + 16)));
  const pdf = readFileSync(join(webapp, "src", "sample", "try-capture-certificate.pdf")).toString("base64");
  const t0 = Date.now();
  await page.waitForTimeout(1200);
  await page.evaluate(() => document.getElementById("drop").classList.add("over"));
  await page.waitForTimeout(900);
  await page.evaluate((b64) => {
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const dt = new DataTransfer();
    dt.items.add(new File([bytes], "sample-certificate.pdf", { type: "application/pdf" }));
    document.getElementById("drop").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, pdf);
  await page.waitForFunction(() => /fields found/.test(document.querySelector("#ingest-status")?.textContent || ""));
  await page.waitForTimeout(400);
  await page.waitForTimeout(2600);
  const video = page.video();
  await context.close();
  const webm = await video.path();
  // The video starts when the page is created; skip the seeding part.
  const lead = (t0 - pageCreated) / 1000;
  const palette = join(tmp, "palette.png");
  const filters = "fps=8,scale=720:-1:flags=lanczos";
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(lead), "-i", webm, "-vf", `${filters},palettegen=max_colors=96`, palette]);
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", "-ss", String(lead), "-i", webm, "-i", palette, "-lavfi", `${filters} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=4`, "-loop", "0", join(media, "capture.gif")]);
}

// -- share card and icons, rendered from small HTML documents
async function render(html, width, height, path, omitBackground = false) {
  const page = await browser.newPage({ viewport: { width, height } });
  await page.setContent(html);
  await page.screenshot({ path, omitBackground });
  await page.close();
}

const favicon = readFileSync(join(site, "favicon.svg"), "utf8");
const shot = readFileSync(join(media, "progress.png")).toString("base64");
await render(`<!doctype html><html><head><style>
  html,body{margin:0;width:1200px;height:630px;background:#f7f6f2;color:#1d2328;font-family:"DejaVu Sans",Arial,sans-serif;overflow:hidden}
  .l{position:absolute;left:72px;top:64px;width:580px}
  .b{display:flex;align-items:center;gap:14px;font-weight:700;font-size:30px;color:#1f4e5f}
  .b svg{width:54px;height:54px}
  h1{font-size:50px;line-height:1.1;letter-spacing:-1.2px;margin:38px 0 22px}
  p{font-size:25px;line-height:1.4;color:#5b6670;margin:0}
  .u{position:absolute;left:72px;bottom:62px;font-size:24px;color:#1f4e5f;font-weight:700}
  .s{position:absolute;left:690px;top:70px;width:620px;height:500px;border-radius:14px;overflow:hidden;
     box-shadow:0 18px 50px rgba(0,0,0,.18);border:1px solid #e2dfd6;background:#fff url(data:image/png;base64,${shot}) 0 0/860px auto no-repeat}
</style></head><body>
  <div class="l"><div class="b">${favicon}<span>ceu.tools</span></div>
  <h1>Track CPEs, CEUs and PDUs the way your body counts them.</h1>
  <p>Registrar applies each body's caps, minimums and deadlines. It runs in your browser, and your data stays there.</p></div>
  <div class="u">ceu.tools/app &nbsp;·&nbsp; free software, no account</div>
  <div class="s"></div>
</body></html>`, 1200, 630, join(site, "og.png"));

const iconDoc = (size, pad, bg) => `<!doctype html><html><head><style>
  html,body{margin:0;width:${size}px;height:${size}px;background:${bg};overflow:hidden}
  svg{display:block;width:${size - 2 * pad}px;height:${size - 2 * pad}px;margin:${pad}px}
</style></head><body>${favicon}</body></html>`;
await render(iconDoc(16, 0, "transparent"), 16, 16, join(icons, "favicon-16.png"), true);
await render(iconDoc(32, 0, "transparent"), 32, 32, join(icons, "favicon-32.png"), true);
await render(iconDoc(48, 0, "transparent"), 48, 48, join(tmp, "favicon-48.png"), true);
await render(iconDoc(180, 0, "#1f4e5f"), 180, 180, join(icons, "apple-touch-icon.png"));
await render(iconDoc(192, 0, "transparent"), 192, 192, join(icons, "icon-192.png"), true);
await render(iconDoc(512, 0, "transparent"), 512, 512, join(icons, "icon-512.png"), true);
// maskable: the glyph inside the 80% safe zone on a full-bleed background
await render(iconDoc(512, 52, "#1f4e5f"), 512, 512, join(icons, "icon-maskable-512.png"));

// favicon.ico: an ICO container holding the 16, 32 and 48 px PNGs
{
  const pngs = [join(icons, "favicon-16.png"), join(icons, "favicon-32.png"), join(tmp, "favicon-48.png")].map((p) => readFileSync(p));
  const sizes = [16, 32, 48];
  const head = Buffer.alloc(6 + 16 * pngs.length);
  head.writeUInt16LE(0, 0);
  head.writeUInt16LE(1, 2);
  head.writeUInt16LE(pngs.length, 4);
  let offset = head.length;
  pngs.forEach((png, i) => {
    const o = 6 + 16 * i;
    head.writeUInt8(sizes[i], o);
    head.writeUInt8(sizes[i], o + 1);
    head.writeUInt16LE(1, o + 4);
    head.writeUInt16LE(32, o + 6);
    head.writeUInt32LE(png.length, o + 8);
    head.writeUInt32LE(offset, o + 12);
    offset += png.length;
  });
  writeFileSync(join(site, "favicon.ico"), Buffer.concat([head, ...pngs]));
}

await browser.close();
server.close();
rmSync(tmp, { recursive: true, force: true });
for (const d of [media, icons]) for (const f of readdirSync(d)) console.log(`wrote ${join(d, f).slice(repo.length + 1)} (${readFileSync(join(d, f)).length} bytes)`);
console.log("wrote site/og.png, site/favicon.ico");

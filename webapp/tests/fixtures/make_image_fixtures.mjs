// Photo-style fixtures for the OCR path: a certificate rendered by headless
// Chromium, slightly rotated on a grey "desk" like a phone photo, saved as
// PNG and JPEG; the JPEG is then wrapped in an image-only PDF (no text layer)
// by make_image_pdf.py. Invented content, as in make_fixtures.py.
//
//   node tests/fixtures/make_image_fixtures.mjs && python3 tests/fixtures/make_image_pdf.py
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(execSync("npm root -g").toString().trim(), "/"));
const { chromium } = require("playwright");

const CERT = (title, name, date, hours, provider) => `<!doctype html><html><body style="margin:0;background:#8b8f94;display:flex;align-items:center;justify-content:center;height:900px">
<div style="width:1100px;height:760px;background:#fbfaf6;transform:rotate(-1.6deg);box-shadow:0 6px 30px rgba(0,0,0,.5);border:14px double #6b5b2e;
font-family:Georgia,serif;text-align:center;color:#222;padding:40px;box-sizing:border-box">
<div style="font-size:54px;font-weight:bold;margin-top:30px">Certificate of Completion</div>
<div style="font-size:26px;margin-top:40px;font-style:italic">This certifies that</div>
<div style="font-size:48px;font-weight:bold;margin-top:20px">${name}</div>
<div style="font-size:26px;margin-top:30px">has successfully completed the course</div>
<div style="font-size:34px;font-weight:bold;margin-top:14px">${title}</div>
<div style="font-size:26px;margin-top:26px">Completion Date: ${date}</div>
<div style="font-size:26px;margin-top:10px">CPE Credits: ${hours}</div>
<div style="font-size:28px;font-weight:bold;margin-top:44px">${provider}</div>
</div></body></html>`;

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1300, height: 900 } });
await p.setContent(CERT("Secure Network Design", "Jordan Sample", "June 18, 2026", "6", "Example Security Institute"));
await p.screenshot({ path: join(here, "images", "photo-cert.png") });
await p.setContent(CERT("Incident Handling Basics", "Alex Example", "April 22, 2026", "3.5", "Sample Training Academy"));
await p.screenshot({ path: join(here, "images", "scan-cert.jpg"), type: "jpeg", quality: 82 });
await b.close();
console.log("wrote images/photo-cert.png and images/scan-cert.jpg");

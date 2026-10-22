# Registrar in the browser

The browser edition of Registrar, served at <https://ceu.tools/app>. Same
rules (`bodies.yaml`), same credit engine, same ledger model and export
format as the self-hosted Python app, running entirely in the browser.

- **Local-first.** Activities, settings and evidence files live in this
  browser's IndexedDB. No account, no server, no telemetry. Works offline
  once loaded (service worker).
- **Evidence capture.** Drop a PDF or take a photo. Text PDFs are read with
  pdf.js; photos and image-only PDFs with tesseract.js (loaded on first
  use). A heuristic parser fills date, hours, provider, title and holder
  name, each with a confidence, and you confirm before anything is saved.
- **Optional, off by default:** "use my own API key" sends the extracted
  *text* to Anthropic or an OpenAI-compatible endpoint to fill the form.
- Credentials and cycle progress (caps, floors, pace, naive vs counted),
  submission tracking, and packet export (a zip per body: cover sheet in
  the body's form order, manifest with SHA-256s, evidence files).
- **First run** asks which credentials you hold (skippable); progress, the
  activity form, Settings, Rules, the calendar and the reports follow from it.
- **Sample data** on the empty ledger: three credentials, nine invented
  activities and generated certificates, all marked SAMPLE, removed in one
  click. A sample certificate to try evidence capture with.
- **Reports**, made in the browser: activities as CSV; a workbook (.xlsx)
  with Activities, Evidence and Totals sheets; a calendar (.ics) of each held
  credential's cycle end with alerts 90 and 30 days before. The JSON Export
  stays the backup format and is byte-compatible with v1.1.
- The footer shows the version and the build's content hash, linking to the
  release notes.

## Layout

```
src/                 the app, as shipped (no bundler)
  index.html app.js  screens and wiring
  engine.js          port of cpebot/engine.py (parity-tested against it)
  ledger.js db.js    ledger rules on IndexedDB (state machine, supersede, hashes)
  schema.js          export / import, cpebot-ledger-v0.2 (+ extension)
  parser.js          certificate heuristics
  pdftext.js ingest.js   pdf.js text, OCR path
  llm.js             optional BYOK seam
  packets.js zip.js  packet cover sheet and zip
  exports.js         CSV, .xlsx (SpreadsheetML on zip.js) and .ics reports
  sample.js          sample data; generates its certificate PDFs
  sample/            the "try capture with this" certificate
  sw.js              offline cache
  data.json          generated from bodies.yaml + packetmaps/ (do not edit)
  vendor/            pdf.js and tesseract.js, pinned; MANIFEST.json has hashes
tools/               vendor.mjs, build.mjs, export_data.py, check_public_text.py,
                     site_rules.mjs (the landing page's per-body section),
                     capture.mjs (landing-page images), make_sample_certificate.py
tests/               unit tests, parity with Python, fixtures
e2e/                 serve.mjs (applies site/_headers), verify.mjs, site.mjs,
                     a11y.mjs, keyboard.mjs
```

## Build

Needs Node 22 and Python 3 (the build exports `bodies.yaml` through the
Python loader, so the browser can only ever use rules the Python app
accepts).

```bash
cd webapp
npm ci --ignore-scripts     # pinned dev packages; see docs/DEPENDENCIES.md
npm run vendor              # only when changing a library version: re-copies vendor/ and MANIFEST.json
npm run build               # -> ../site/app/ (checks every vendored file's SHA-256 first)
npm run check-public        # fails if anything under site/ carries internal wording
npm run build:mirror        # -> dist/mirror/: site/ for GitHub Pages under BASE_PATH (default /registrar/)
npm run serve:mirror        # serves dist/mirror/ at http://127.0.0.1:8766/registrar/, as GitHub Pages would
```

`build:mirror` builds the mirror at
`https://robknowles-cybersecurity.github.io/registrar/`, for networks whose
proxies block newly registered domains like ceu.tools. It reads `site/` and
never writes it. The output is the same files at the same version, with
these changes:

- every root-absolute URL moves under `BASE_PATH`;
- every page gets a canonical link to its ceu.tools page;
- a one-line static banner goes on the app; the landing page keeps
  site/'s own banner, which already names the mirror (one banner per page);
- each page's Content-Security-Policy from `site/_headers` is carried as a
  `<meta>`, because GitHub Pages sends no custom headers. A meta policy
  cannot express `frame-ancestors`, and the other `_headers` lines
  (X-Frame-Options, Permissions-Policy, Cross-Origin-Opener-Policy,
  X-Clacks-Overhead) cannot be sent at all;
- `.nojekyll` and a `404.html` are added.

`tests/mirror.test.mjs` checks all of this.

`site/app/` is committed and deploys with the rest of `site/` on Cloudflare
Pages. Never edit it by hand; rebuild.

The build also writes the landing page's "How each body counts" section
(between the `rules:begin` and `rules:end` comments in `site/index.html`, and
the matching block at the end of `site/style.css`) from the same
`data.json`, so a `bodies.yaml` change updates the app and the page
together. `tests/site_rules.test.mjs` fails if either file was not rebuilt.

## Tests

```bash
npm test                    # unit tests (node:test), including:
                            #   engine parity: 140 random ledgers through engine.js and cpebot/engine.py
                            #   round trip: Python export -> import -> export, byte-identical entries;
                            #     export -> wipe -> import with files; Python reads the browser's export
                            #   parser: 16 synthetic certificates (tests/fixtures/), every v1 body
npm run heldout             # parser score on held-out certificates it was not tuned on (a score, not a gate)
npm run e2e                 # the built app in headless Chromium, under the real CSP (67 checks; 62 with --skip-ocr)
npm run e2e:prove-fails     # the same against six deliberately broken builds (parser, import, export,
                            #   and the v1.2.2 fixes F1, F3, F4 reverted): all must fail
npm run e2e:site            # landing page, /privacy, 404, share card, icons, security.txt, one CSP
                            #   on every page and address, canonical links, and the
                            #   "How each body counts" section at 360-1600 px (92 checks)
npm run e2e:site:prove-fails  # a site without og.png, one loading a third-party image, one whose
                              #   rules grid has no breakpoint, and one with the script-free CSP
                              #   off /*: all four must fail
node e2e/keyboard.mjs       # keyboard-only walk of the core flow, focus visible at every stop
AXE=/path/to/axe.min.js npm run a11y   # axe-core on every screen, light and dark
```

`npm run e2e` serves `site/` with `site/_headers` applied and checks, among
others: the first-run question; log an activity, attach a fixture PDF, the
parsed hours appear, export, wipe, import, the activity is back with
verified evidence; sample data load, the three reports, export, wipe,
import, sample data back, remove sample data (and a credential setting you
changed after loading it survives the removal); a crafted import (markup in
count, a path in a filename) is refused or sanitized; an upload over 15 MB is
refused before it is read; OCR past its deadline stops; packet
zip contents; OCR of a photo and of an image-only PDF; a Python export
imports; offline reload; BYOK sends text only and stores no key; no request
to any other origin; no CSP violation. It uses the machine's Playwright
(`npm root -g`), as the repo pins none.

axe-core is not a dependency either: `e2e/a11y.mjs` needs a copy you
point it at (`AXE=`), injects it through the DevTools protocol (outside
the page's CSP), and ships nothing. The R3 audit used axe-core 4.13.0
(`axe.min.js` SHA-256 `c24f097bd2f451d4f933e8bc7d8d539f8672a2ebcb5cc9f9f3eec8ca9470a0c1`).

## Threat model

**What stays local.** Every activity, setting and evidence file is stored
in this browser's IndexedDB for this origin, and nowhere else. Certificates
are read on the device: pdf.js and the OCR engine run in the page and in
same-origin workers, loaded from this site, never from a CDN. The Content-
Security-Policy (`site/_headers`) permits scripts only from this origin and
network connections only to this origin, plus the two BYOK provider hosts
and localhost. Sample data, its certificates and the CSV, .xlsx and .ics reports
are generated in the page; making them sends nothing anywhere. There is no analytics, no telemetry, no account and no
server-side copy, so there is nothing for ceu.tools to leak; equally,
nothing is backed up for you. Clearing site data, a browser profile reset
or a lost device loses the ledger: **Export** regularly. Anyone with access
to this browser profile can read the ledger, as with any local file.
Registrar never asks for, stores or uses a certifying body's portal login;
packets are filled in by you.

**What BYOK sends.** Only when you set a key in Settings *and* press "Ask
the model" on an activity: the text already extracted from that one
certificate (at most 20,000 characters) and a fixed instruction, sent over
HTTPS to the provider you chose (Anthropic's API, or an OpenAI-compatible
endpoint: api.openai.com or a model on localhost). It never sends the file,
other activities, your settings or your export. The key is held in a
JavaScript variable for that tab only: it is not written to IndexedDB,
localStorage, cookies or exports, and a reload forgets it. The provider
sees the certificate text and your key and handles them under its own
terms. The model's answer fills the form marked "llm", and you confirm it.

## Known limits

- Parser accuracy: 16/16 on the fixtures it was developed against, 25/30
  fields on held-out text it was not tuned on (`npm run heldout`). Wrong
  guesses are usually marked low confidence or left empty; always check.
- OCR was tested on rendered synthetic certificates, not on real phone
  photos with blur, glare or perspective.
- One browser profile, one ledger: no sync between devices other than
  Export / Import.

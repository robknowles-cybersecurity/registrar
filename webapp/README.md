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
  sw.js              offline cache
  data.json          generated from bodies.yaml + packetmaps/ (do not edit)
  vendor/            pdf.js and tesseract.js, pinned; MANIFEST.json has hashes
tools/               vendor.mjs, build.mjs, export_data.py, check_public_text.py
tests/               unit tests, parity with Python, fixtures
e2e/                 serve.mjs (applies site/_headers), verify.mjs
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
npm run check-public        # fails if the built data carries internal wording
```

`site/app/` is committed and deploys with the rest of `site/` on Cloudflare
Pages. Never edit it by hand; rebuild.

## Tests

```bash
npm test                    # unit tests (node:test), including:
                            #   engine parity: 140 random ledgers through engine.js and cpebot/engine.py
                            #   round trip: Python export -> import -> export, byte-identical entries;
                            #     export -> wipe -> import with files; Python reads the browser's export
                            #   parser: 16 synthetic certificates (tests/fixtures/), every v1 body
npm run heldout             # parser score on held-out certificates it was not tuned on (a score, not a gate)
npm run e2e                 # the built app in headless Chromium, under the real CSP (43 checks)
npm run e2e:prove-fails     # the same against two deliberately broken builds: both must fail
```

`npm run e2e` serves `site/` with `site/_headers` applied and checks, among
others: log an activity, attach a fixture PDF, the parsed hours appear,
export, wipe, import, the activity is back with verified evidence; packet
zip contents; OCR of a photo and of an image-only PDF; a Python export
imports; offline reload; BYOK sends text only and stores no key; no request
to any other origin; no CSP violation. It uses the machine's Playwright
(`npm root -g`), as the repo pins none.

## Threat model

**What stays local.** Every activity, setting and evidence file is stored
in this browser's IndexedDB for this origin, and nowhere else. Certificates
are read on the device: pdf.js and the OCR engine run in the page and in
same-origin workers, loaded from this site, never from a CDN. The Content-
Security-Policy (`site/_headers`) permits scripts only from this origin and
network connections only to this origin, plus the two BYOK provider hosts
and localhost. There is no analytics, no telemetry, no account and no
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

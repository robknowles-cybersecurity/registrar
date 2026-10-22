# Changelog

All notable changes to Registrar, the self-hosted app and the browser app
at <https://ceu.tools/app>. Versions follow
[Semantic Versioning](https://semver.org/) and are tagged on this
repository as a whole; the browser app shows its version in its footer.

## v1.2.1

The landing page's per-body rules, readable at any width. Release notes:
[docs/release-notes/v1.2.1.md](docs/release-notes/v1.2.1.md).

### Changed

- **"How each body counts"** on <https://ceu.tools> is no longer a table
  that scrolls sideways. Below 960 px wide each body is a card (name, unit
  and credentials, then cycle, cycle total, annual minimum, categories and
  caps, how you submit, and the policy link); from 960 px it is a
  comparison grid that widens past the reading column, up to 1200 px. The
  page has no horizontal scroll at any width from 360 px up.
- The section is now **built from `bodies.yaml`** by `npm run build` (new
  `landing:` summaries on each body), from the same export the browser app
  uses, instead of being written by hand. The wording is unchanged.

### Added

- **A body filter** above the section: a chip per body plus "All", with a
  visible count ("3 of 10 bodies shown"). It is plain HTML and CSS, as the
  landing page runs no scripts, so the choice is not remembered after a
  reload.

## v1.2.0

Polish for first-time visitors, a CPE/CEU guide, plus reports. Release
notes: [docs/release-notes/v1.2.0.md](docs/release-notes/v1.2.0.md).

### Added

- **First run** in the browser app asks which credentials you hold (you can
  skip it). Your progress, the activity form, Settings, Rules, the calendar
  and the reports follow from that answer. A credential you hold without a
  cycle start date gets a prompt on the Credentials screen.
- **Sample data** on an empty ledger: three credentials (CISSP, CySA+ and
  CFE) and nine invented activities with generated certificates, all marked
  SAMPLE, so you can see caps, minimums, pace and packets at work. One
  click removes exactly the sample and nothing of yours.
- **A sample certificate** to try evidence capture with ("try capture with
  this"), linked from the empty ledger.
- **Reports, made in your browser:**
  - activities as CSV, with the evidence file names
  - an Excel workbook (.xlsx) with Activities, Evidence and Totals sheets
  - a calendar (.ics) with each credential's cycle end, alerting 90 and 30
    days before; its events merge with the self-hosted app's calendar
    instead of duplicating it
- **Version footer**: the app shows its version and build hash, linked to
  the release notes.
- **Landing page** at <https://ceu.tools> rewritten to explain the problem
  first: who it is for, the credentials covered and what UNVERIFIED means,
  how your data stays in your browser, screenshots and a short capture
  demo.
- **A CPE/CEU guide** on the landing page: how cycles, groups and caps
  work, keeping audit-ready records, what usually does not count, the first
  90 days with a new certification, where to earn credits, a per-body rules
  table and a glossary. Adapted from insiderthreatanalyst.com/cpe
  (CC BY 4.0); sources in `docs/CONTENT-SOURCES.md`.
- **Privacy page** at <https://ceu.tools/privacy>: the threat model, and
  exactly what the optional bring-your-own-key feature sends and to whom.
- **Share and trust plumbing**: link-preview card and tags, a full icon set
  (including installable-app icons), and `/.well-known/security.txt`
  pointing at private vulnerability reporting.

### Changed

- Settings lets you mark a credential as held before you know its cycle
  start date.
- Rules and Settings list the credentials you hold first.

### Fixed

- Accessibility: a duplicate landmark label on the landing page. An axe
  audit of every screen, light and dark, now reports no violations, and
  the core flow works with the keyboard alone.

### Compatibility

- The JSON Export / Import format is unchanged and byte-compatible with
  v1.1.0: a v1.1.0 export imports and re-exports to the same bytes.

## v1.1.0

Registrar in your browser, and a container setup for self-hosting.

### Added

- **Registrar in your browser** at <https://ceu.tools/app>: the same rules,
  credit engine and ledger as the self-hosted app, running entirely in the
  browser. Activities, settings and evidence stay in the browser
  (IndexedDB); no account, no server, no telemetry; works offline.
  - Evidence capture: drop a certificate PDF or take a photo. Text PDFs are
    read with pdf.js, photos and scans with tesseract.js, both served from
    the site, never a CDN. The date, hours, provider, title and holder name
    are filled in with a confidence each, and nothing is saved until you
    confirm.
  - Optional, off by default: use your own API key to have a model read the
    extracted text (never the file).
  - Export / Import in the self-hosted app's JSON format, both ways; packet
    zips per body; submission tracking.
- **Docker Compose self-hosting**: one container, the ledger in a named
  volume, a health check, bound to 127.0.0.1. See
  [docs/SELF-HOST.md](docs/SELF-HOST.md).
- Every web page starts with a skip link.

### Changed

- User-facing text says Registrar throughout (the Python package is still
  `cpebot`).
- `/health` reports status figures only, never a file path.
- The ATAP membership unit note is reworded.

### Fixed

- A Host header of `localhost..` is refused instead of being read as
  `localhost`.
- The README now says that an IPv6 entry with a port in
  `CPEBOT_ALLOWED_HOSTS` must be bracketed.

## v1.0.0

The first public release: a self-hosted continuing-education tracker on the
Python 3 standard library. Full notes:
[docs/RELEASE-NOTES-v1.md](docs/RELEASE-NOTES-v1.md).

### Added

- Totals per credential that apply each body's caps, category minimums and
  yearly registration windows, with the naive sum of hours and any
  forfeited credits shown next to them.
- Seven credentials: CISSP (ISC2), CySA+ (CompTIA), CEH (EC-Council), GCITP
  (University of Maryland), ATAP membership, CTM (ATAP) and CFE (ACFE),
  with rules as data in `bodies.yaml`, a source link on every number, and
  UNVERIFIED badges where a value could not be confirmed.
- Capture then confirm: web form, or optional Telegram capture with your
  own bot.
- Evidence files hashed on arrival and re-checked on demand.
- Deadline calendar (.ics), submission packets with your evidence, renewal
  report, quiet reminders, CSV and JSON exports, a what-if calculator.
- Nothing is deleted: corrections supersede, mistakes are voided.

### Security

- No login by design: binds to loopback by default. Host-header allowlist
  against DNS rebinding, anti-forgery tokens on every form, a strict
  Content-Security-Policy with no scripts, generic error pages.

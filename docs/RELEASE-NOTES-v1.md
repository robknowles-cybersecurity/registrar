# Registrar v1.0.0

The first public release of Registrar, a self-hosted tracker for the
continuing-education credits (CPEs, CEUs, PDUs) that professional
certifications require. It runs on the Python 3 standard library, keeps
your records in one SQLite file on your own machine, and has no account,
no cloud service and no telemetry.

## What it does

- **Totals the way each body counts them.** Caps, category minimums,
  yearly registration windows and per-item activities are applied per
  credential, and every screen shows the counted total next to the naive
  sum of hours, with any forfeited credits stated.
- **Seven credentials out of the box:** CISSP (ISC2), CySA+ (CompTIA), CEH
  (EC-Council), GCITP (University of Maryland), ATAP membership, CTM (ATAP)
  and CFE (ACFE). The rules live in one data file, `bodies.yaml`, with a
  source link on every number.
- **Capture, then confirm.** Log an activity from the web form or,
  optionally, from your own Telegram bot. A capture counts only once you
  confirm it.
- **Evidence with hashes.** Attached files are hashed on arrival and
  re-checked on demand; a file that no longer matches is refused.
- **Deadline calendar.** One `.ics` file with cycle ends, grace periods,
  registration windows, mid-cycle minimums and fees. Re-importing updates
  events rather than duplicating them.
- **Submission packets.** Your body's form fields, in order, with the
  values filled in, plus a zip of your evidence with a manifest. You sign
  in to the body's portal and submit yourself: Registrar never asks for,
  stores or uses your portal login.
- **Renewal report**, quiet reminders (at most one a day, only when you
  are behind or something is overdue), CSV and JSON exports, and a what-if
  calculator that writes nothing.
- **Nothing is deleted.** Edits supersede the old version and mistakes are
  voided; both stay on record and voided entries never count.
- **Command line:** `python3 -m cpebot ics`, `packet` and `packetmaps`.

## Security posture

- No login by design: bind to loopback (the default) or to one private
  interface you trust, never to a public one. There is no TLS.
- The web app answers only to an allowed `Host` header (`localhost`,
  `127.0.0.1`, `[::1]`, the bind address, or names you list in
  `CPEBOT_ALLOWED_HOSTS`) and refuses anything else with
  `400 Bad Host header`, which blocks DNS-rebinding attacks from other web
  pages in your browser.
- Every form post carries an anti-forgery token, and every page is served
  with a strict Content-Security-Policy and no scripts.
- An unexpected error shows a plain error page; the details go to the
  server log only. Rule source links are rendered only for `http(s)` URLs.
- The Telegram bot ships without a token and refuses every chat until you
  set your own chat id; it warns about this when it starts.

## Known limitations

- **Unverified rule values.** Some numbers in `bodies.yaml` could not be
  confirmed against the body's own pages. They are shown with an
  **UNVERIFIED** badge on every screen, and where a body's category caps
  are unknown (CTM) Registrar gives no compliance verdict at all. The
  README's units table lists each one. Your body's portal is always
  authoritative.
- **ISC2 packet field labels are unverified.** The values in an ISC2
  packet are right, but the field labels and their order were not read off
  the live form, which needs a member login. Corrections are welcome in
  `packetmaps/isc2.yaml`.
- **No container setup yet.** Run it from a checkout, or as a systemd user
  service from the templates in `systemd/`. A `docker-compose` setup is
  coming in a v1.0.x release.
- No authentication, TLS or rate limiting, so it must not be exposed to the
  internet (see above).

## Upgrading

This is the first release; there is nothing to migrate. A ledger created
by an earlier development build opens as-is: schema changes are additive.

## Reporting security issues

Please report vulnerabilities privately through GitHub's private
vulnerability reporting for this repository (the *Security* tab, then
*Report a vulnerability*). Do not open a public issue. `SECURITY.md` gives
an email fallback and describes what is in scope.

## License

AGPL-3.0. See `LICENSE`, `NOTICE` and `THIRDPARTY.md`.

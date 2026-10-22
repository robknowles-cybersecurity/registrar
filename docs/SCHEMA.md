# Ledger export format

Registrar's ledger export is one JSON document. The self-hosted Python app
writes it (**Export → entries.json**, `cpebot/exports.py` `entries_json()`);
the browser app at `/app` writes and reads it (**Export** / **Import**).

## Base format: `cpebot-ledger-v0.2` (unchanged)

```json
{
 "exported_at": "2026-10-21 18:30:00",
 "format": "cpebot-ledger-v0.2",
 "entries": [ { ...entry..., "evidence": [ ...evidence rows... ] } ]
}
```

Each entry carries every column of the Python ledger's `entries` table, in
the same order: `uuid` (32 hex characters), `created_at`, `updated_at`,
`activity_date` (`YYYY-MM-DD`), `title`, `provider`, `hours`, `count`,
`tags` (list), `markers` (list), `applies_to` (list, or `null` for every
eligible credential), `overrides` (`{credential_id: credits}`),
`submitted_to` (list of body ids), `notes`, `status` (`captured`,
`confirmed`, `rejected`, `superseded`, `void`), `capture_source` (`web`,
`telegram`, `backfill`, `import`, `fixture`), `capture_ref`, `confirmed_at`,
`supersedes`, `cost_usd`, `voided_at`, `submitted_dates`
(`{body_id: YYYY-MM-DD}`), and `evidence`.

Each evidence row is `{filename, sha256, bytes, mime, added_at, note}`. The
file's bytes are **not** in the base format.

Every state is exported, superseded and void included: an audit trail that
exports only its flattering rows is not an audit trail.

The format identifier keeps the package's pre-release name, `cpebot`. It is
an interface, not branding, and changing it would break every existing
export.

## Browser extension: the optional `registrar` key

The browser app writes everything above unchanged, plus one extra top-level
key. A reader that does not know it ignores it, so a browser export is still
a valid `cpebot-ledger-v0.2` document (the browser tests check this against
the Python side: same keys, same types, same totals from the Python engine).

```json
"registrar": {
 "extension": "registrar-browser-1",
 "credential_state": [ {"credential": "cissp", "anchor_date": "2025-09-01",
   "carryover_in": 0, "portal_credits": null, "portal_checked": null,
   "active": 1, "note": ""} ],
 "fees": [ {"id": 1, "credential": "cissp", "name": "...", "amount_usd": 135,
   "due_date": "2026-09-01", "paid_on": null, "note": ""} ],
 "config": { "attendee_name": "..." },
 "files": { "<sha256>": {"mime": "application/pdf", "data_base64": "..."} }
}
```

- `credential_state`, `fees`: the Python ledger's tables of the same names,
  same columns.
- `config`: settings as strings (`attendee_name` is the holder name used on
  packets). **Never** an API key: the browser app does not store one.
- `files`: each evidence file once, keyed by its SHA-256 and base64-encoded.
  Optional; an export without it is still complete as a record.

## Import rules (browser app)

- `format` must be `cpebot-ledger-v0.2`; anything else is refused.
- Every entry is checked first (uuid, real date, known status and source,
  list-typed fields, 64-hex evidence hashes; `count` a whole number 0 or
  more; `title`, `provider`, `notes` and evidence `filename` and `mime`
  text when present). One bad entry refuses the whole import and writes
  nothing.
- Evidence filenames are stored through the same `safeFilename` as an
  upload: any path is dropped and unusual characters become `_`.
- The `sample_data` config key is never imported: it records what the
  sample wrote into the ledger it was loaded into, and "Remove sample
  data" must not act on it in another one.
- An entry whose `uuid` is already in the browser is skipped, never
  overwritten.
- A file in `registrar.files` is stored only if its bytes hash to the
  SHA-256 it is filed under. An altered file is refused and its evidence row
  shows as missing.
- A file in `registrar.files` larger than 15 MB (the upload cap) is refused
  on its encoded length, before it is decoded, and counted in
  `files_too_large`.
- Evidence rows with no file (any Python export) are kept as records and
  show "file not in this browser".

The Python app has no JSON import; its import path is the CSV backfill.

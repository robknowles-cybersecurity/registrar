// Export and import in the Python Registrar's ledger format.
//
// The base document is exactly what the Python exports.py entries_json() writes:
//   { exported_at, format: "cpebot-ledger-v0.2", entries: [ entry + evidence[] ] }
// A v0.2 reader that knows nothing about the browser app reads it unchanged.
//
// The browser app adds one optional top-level key, "registrar", for what the
// v0.2 dump does not carry: per-credential cycle dates, fees, settings, and
// (optionally) the evidence files themselves, base64-encoded and keyed by
// SHA-256. Readers that do not know the key ignore it. docs/SCHEMA.md has the
// full description.
import { LedgerError, STATES, SOURCES, blankEntry, now, sha256Hex, blobBytes, validateDate } from "./ledger.js";

export const FORMAT = "cpebot-ledger-v0.2";
export const EXTENSION = "registrar-browser-1";

const ENTRY_KEYS = Object.keys(blankEntry());
const EVIDENCE_KEYS = ["filename", "sha256", "bytes", "mime", "added_at", "note"];

function toBase64(bytes) {
  let s = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) s += String.fromCharCode(...bytes.subarray(i, i + chunk));
  return btoa(s);
}

function fromBase64(b64) {
  const s = atob(b64);
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

/** The whole ledger as a cpebot-ledger-v0.2 document (plus the extension). */
export async function exportLedger(led, { includeFiles = true, exportedAt = now() } = {}) {
  const evByEntry = {};
  const rows = await led.listEvidence();
  for (const r of rows) {
    (evByEntry[r.entry_uuid] = evByEntry[r.entry_uuid] || []).push(Object.fromEntries(EVIDENCE_KEYS.map((k) => [k, r[k]])));
  }
  const entries = (await led.listEntries())
    .sort((a, b) => (a.activity_date + a.created_at).localeCompare(b.activity_date + b.created_at))
    .map((e) => {
      const d = Object.fromEntries(ENTRY_KEYS.map((k) => [k, e[k] === undefined ? blankEntry()[k] : e[k]]));
      d.evidence = evByEntry[e.uuid] || [];
      return d;
    });
  const ext = {
    extension: EXTENSION,
    credential_state: await led.allCredentialState(),
    fees: await led.listFees(),
    config: await led.allConfig(),
    files: {},
  };
  if (includeFiles) {
    for (const sha of new Set(rows.map((r) => r.sha256))) {
      const f = await led.store.get("files", sha);
      if (!f) continue;
      ext.files[sha] = { mime: f.mime || "", data_base64: toBase64(new Uint8Array(await blobBytes(f.blob))) };
    }
  }
  return { exported_at: exportedAt, format: FORMAT, entries, registrar: ext };
}

function checkEntry(e, i) {
  const where = `entries[${i}]`;
  if (!e || typeof e !== "object") throw new LedgerError(`${where}: not an object`);
  if (!/^[0-9a-f]{32}$/.test(String(e.uuid || ""))) throw new LedgerError(`${where}: uuid must be 32 hex characters`);
  validateDate(e.activity_date);
  if (!STATES.includes(e.status)) throw new LedgerError(`${where}: unknown status '${e.status}'`);
  if (e.capture_source && !SOURCES.includes(e.capture_source)) throw new LedgerError(`${where}: unknown capture_source '${e.capture_source}'`);
  for (const k of ["tags", "markers", "submitted_to"]) {
    if (e[k] != null && !Array.isArray(e[k])) throw new LedgerError(`${where}.${k}: expected a list`);
  }
  if (e.applies_to != null && !Array.isArray(e.applies_to)) throw new LedgerError(`${where}.applies_to: expected a list or null`);
  if (!Number.isFinite(Number(e.hours ?? 0))) throw new LedgerError(`${where}.hours: not a number`);
  for (const ev of e.evidence || []) {
    if (!/^[0-9a-f]{64}$/.test(String(ev.sha256 || ""))) throw new LedgerError(`${where}.evidence: sha256 must be 64 hex characters`);
  }
}

/**
 * Read a cpebot-ledger-v0.2 document into the ledger. Entries already present
 * (same uuid) are left alone and counted as skipped; nothing is overwritten.
 * A file in the extension is stored only if its bytes hash to the recorded
 * SHA-256: an altered file is refused and its evidence row reports MISSING.
 */
export async function importLedger(led, doc) {
  if (!doc || typeof doc !== "object") throw new LedgerError("not a Registrar export (not a JSON object)");
  if (doc.format !== FORMAT) throw new LedgerError(`unsupported format '${doc.format}' (expected ${FORMAT})`);
  if (!Array.isArray(doc.entries)) throw new LedgerError("entries must be a list");
  doc.entries.forEach(checkEntry);
  const ext = doc.registrar && doc.registrar.extension === EXTENSION ? doc.registrar : null;
  const report = { entries_added: 0, entries_skipped: 0, evidence_rows: 0, files_stored: 0, files_refused: [], files_absent: 0, credential_state: 0, fees: 0, config: 0 };

  const files = (ext && ext.files) || {};
  const stored = new Set();
  for (const [sha, f] of Object.entries(files)) {
    const bytes = fromBase64(f.data_base64 || "");
    if ((await sha256Hex(bytes)) !== sha) {
      report.files_refused.push(sha);
      continue;
    }
    if (!(await led.store.get("files", sha))) {
      await led.store.put("files", { sha256: sha, mime: f.mime || "", blob: new Blob([bytes], { type: f.mime || "application/octet-stream" }) });
      report.files_stored += 1;
    }
    stored.add(sha);
  }

  for (const raw of doc.entries) {
    if (await led.getEntry(raw.uuid)) {
      report.entries_skipped += 1;
      continue;
    }
    const e = blankEntry();
    for (const k of ENTRY_KEYS) if (raw[k] !== undefined) e[k] = raw[k];
    await led.store.put("entries", e);
    report.entries_added += 1;
    for (const ev of raw.evidence || []) {
      await led.store.put("evidence", { entry_uuid: e.uuid, filename: ev.filename, stored_path: "", sha256: ev.sha256, bytes: ev.bytes, mime: ev.mime || "", added_at: ev.added_at || "", note: ev.note || "" });
      report.evidence_rows += 1;
      if (!stored.has(ev.sha256) && !(await led.store.get("files", ev.sha256))) report.files_absent += 1;
    }
  }
  if (ext) {
    for (const cs of ext.credential_state || []) {
      if (!cs.credential) continue;
      if (await led.getCredentialState(cs.credential)) continue;
      const fields = {};
      for (const k of ["anchor_date", "carryover_in", "portal_credits", "portal_checked", "active", "note"]) if (k in cs) fields[k] = cs[k];
      await led.setCredentialState(cs.credential, fields);
      report.credential_state += 1;
    }
    const have = new Set((await led.listFees()).map((f) => `${f.credential}|${f.name}|${f.due_date}`));
    for (const f of ext.fees || []) {
      if (have.has(`${f.credential}|${f.name}|${f.due_date}`)) continue;
      await led.addFee({ ...f, id: undefined });
      report.fees += 1;
    }
    for (const [k, v] of Object.entries(ext.config || {})) {
      if ((await led.getConfig(k)) == null) {
        await led.setConfig(k, v);
        report.config += 1;
      }
    }
  }
  return report;
}

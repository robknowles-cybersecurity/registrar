// The browser ledger: the same entry model and state machine as
// the Python ledger.py, stored in IndexedDB instead of SQLite.
//
// The rules carried over unchanged:
//   * Nothing auto-commits. A new entry is `captured` and counts for nothing
//     until a person confirms it; confirming checks for a soft duplicate.
//   * Confirmed entries are never edited in place. A correction is a new
//     confirmed entry that supersedes the old one; both are kept.
//   * Mistakes are voided, not deleted.
//   * Evidence is hashed (SHA-256) when it arrives and re-checked on demand.
//     Files are stored once per hash, as in the Python app.

export const CAPTURED = "captured";
export const CONFIRMED = "confirmed";
export const REJECTED = "rejected";
export const SUPERSEDED = "superseded";
export const VOID = "void";
export const STATES = [CAPTURED, CONFIRMED, REJECTED, SUPERSEDED, VOID];
export const TRANSITIONS = {
  [CAPTURED]: [CONFIRMED, REJECTED, VOID],
  [CONFIRMED]: [SUPERSEDED, REJECTED, VOID],
  [REJECTED]: [CAPTURED, VOID],
  [SUPERSEDED]: [],
  [VOID]: [],
};
// Same vocabulary as the Python ledger, so an export is readable there.
// Browser captures are "web" with capture_ref "browser".
export const SOURCES = ["web", "telegram", "backfill", "import", "fixture"];

export const MAX_EVIDENCE_BYTES = 15 * 1024 * 1024;
export const EVIDENCE_TYPES = {
  ".pdf": ["application/pdf", [[0x25, 0x50, 0x44, 0x46, 0x2d]]],
  ".png": ["image/png", [[0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]]],
  ".jpg": ["image/jpeg", [[0xff, 0xd8, 0xff]]],
  ".jpeg": ["image/jpeg", [[0xff, 0xd8, 0xff]]],
};

export const STORES = ["entries", "evidence", "files", "credential_state", "fees", "config"];

export class LedgerError extends Error {}

export class SoftDuplicate extends LedgerError {
  constructor(uuid, matches) {
    super(`entry ${uuid} looks like ${matches.length} already-confirmed entr${matches.length === 1 ? "y" : "ies"} (same date and provider, hours within 25%)`);
    this.uuid = uuid;
    this.matches = matches;
  }
}

export function now(d = new Date()) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function newUuid() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(bytes) {
  const buf = bytes instanceof ArrayBuffer ? bytes : bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const h = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(h)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export function validateDate(s) {
  s = String(s ?? "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) throw new LedgerError(`activity_date must be YYYY-MM-DD, got '${s}'`);
  const [y, m, d] = s.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  if (t.getUTCFullYear() !== y || t.getUTCMonth() !== m - 1 || t.getUTCDate() !== d) {
    throw new LedgerError(`'${s}' is not a real date`);
  }
  return s;
}

export function normProvider(v) {
  return String(v || "").trim().toLowerCase().replace(/\s+/g, " ");
}

export function safeFilename(name) {
  let n = String(name || "file").split(/[\\/]/).pop();
  n = n.replace(/[^A-Za-z0-9._-]/g, "_");
  return n.slice(0, 120) || "file";
}

/** Size, extension AND magic bytes, as the Python web boundary checks. */
export function validateUpload(filename, bytes) {
  if (!bytes || !bytes.length) throw new LedgerError("empty upload");
  if (bytes.length > MAX_EVIDENCE_BYTES) {
    throw new LedgerError(`evidence file too large: ${(bytes.length / 1048576).toFixed(1)} MB (cap ${MAX_EVIDENCE_BYTES / 1048576} MB)`);
  }
  const m = /\.[^.]+$/.exec(safeFilename(filename));
  const ext = m ? m[0].toLowerCase() : "";
  if (!(ext in EVIDENCE_TYPES)) throw new LedgerError(`unsupported evidence type '${ext || filename}' — PDF, PNG or JPEG only`);
  const [mime, magics] = EVIDENCE_TYPES[ext];
  if (!magics.some((mg) => mg.every((b, i) => bytes[i] === b))) {
    throw new LedgerError(`file content does not match its ${ext} extension`);
  }
  return mime;
}

/** An empty entry with every field the Python ledger's rows carry, in its column order. */
export function blankEntry() {
  return {
    uuid: "",
    created_at: "",
    updated_at: "",
    activity_date: "",
    title: "",
    provider: "",
    hours: 0,
    count: 1,
    tags: [],
    markers: [],
    applies_to: null,
    overrides: {},
    submitted_to: [],
    notes: "",
    status: CAPTURED,
    capture_source: "web",
    capture_ref: "",
    confirmed_at: null,
    supersedes: null,
    cost_usd: null,
    voided_at: null,
    submitted_dates: {},
  };
}

// ---------------------------------------------------------------- storage

/** In-memory store with the same interface as the IndexedDB one (tests). */
export class MemoryStore {
  constructor() {
    this.data = Object.fromEntries(STORES.map((s) => [s, new Map()]));
    this.seq = 0;
  }
  async get(store, key) {
    const v = this.data[store].get(key);
    return v === undefined ? undefined : structuredClone(v);
  }
  async getAll(store) {
    return [...this.data[store].values()].map((v) => structuredClone(v));
  }
  async put(store, value, keyName) {
    const v = structuredClone(value);
    if (store === "evidence" || store === "fees") {
      if (v.id == null) v.id = ++this.seq;
    }
    const key = v[keyName || keyOf(store)];
    this.data[store].set(key, v);
    return key;
  }
  async delete(store, key) {
    this.data[store].delete(key);
  }
  async clearAll() {
    for (const s of STORES) this.data[s].clear();
  }
}

export function keyOf(store) {
  return { entries: "uuid", evidence: "id", files: "sha256", credential_state: "credential", fees: "id", config: "key" }[store];
}

// ---------------------------------------------------------------- ledger

export class Ledger {
  constructor(store) {
    this.store = store;
  }

  // -- entries

  async addEntry(fields = {}) {
    const e = { ...blankEntry(), ...pick(fields, ["activity_date", "title", "provider", "hours", "count", "tags", "markers", "applies_to", "overrides", "notes", "capture_source", "capture_ref", "status", "cost_usd", "submitted_to", "submitted_dates"]) };
    if (!STATES.includes(e.status)) throw new LedgerError(`unknown status '${e.status}'`);
    if (!SOURCES.includes(e.capture_source)) throw new LedgerError(`unknown capture_source '${e.capture_source}'`);
    validateDate(e.activity_date);
    const ts = now();
    e.uuid = newUuid();
    e.created_at = ts;
    e.updated_at = ts;
    e.title = e.title || "";
    e.provider = e.provider || "";
    e.hours = Number(e.hours || 0);
    e.count = parseInt(e.count || 1, 10) || 1;
    e.tags = [...(e.tags || [])];
    e.markers = [...(e.markers || [])];
    e.applies_to = e.applies_to == null ? null : [...e.applies_to];
    e.overrides = { ...(e.overrides || {}) };
    e.submitted_to = [...(e.submitted_to || [])];
    e.submitted_dates = { ...(e.submitted_dates || {}) };
    e.notes = e.notes || "";
    e.cost_usd = e.cost_usd == null || e.cost_usd === "" ? null : Number(e.cost_usd);
    e.confirmed_at = e.status === CONFIRMED ? ts : null;
    await this.store.put("entries", e);
    return e.uuid;
  }

  async getEntry(uuid) {
    return (await this.store.get("entries", uuid)) || null;
  }

  async listEntries({ status = null } = {}) {
    let rows = await this.store.getAll("entries");
    if (status) {
      const s = Array.isArray(status) ? status : [status];
      rows = rows.filter((r) => s.includes(r.status));
    }
    rows.sort((a, b) => (b.activity_date + b.created_at).localeCompare(a.activity_date + a.created_at));
    return rows;
  }

  async updateEntry(uuid, fields) {
    const allowed = ["activity_date", "title", "provider", "hours", "count", "tags", "markers", "applies_to", "overrides", "submitted_to", "submitted_dates", "notes", "cost_usd"];
    const bad = Object.keys(fields).filter((k) => !allowed.includes(k));
    if (bad.length) throw new LedgerError(`not updatable here: ${bad.sort().join(", ")}`);
    const e = await this.getEntry(uuid);
    if (!e) throw new LedgerError(`no such entry '${uuid}'`);
    const content = Object.keys(fields).filter((k) => k !== "submitted_to" && k !== "submitted_dates");
    if (e.status === CONFIRMED && content.length) {
      throw new LedgerError("confirmed entries are never edited in place — a correction writes a new copy and keeps both");
    }
    if ((e.status === SUPERSEDED || e.status === VOID) && Object.keys(fields).length) {
      throw new LedgerError(`${e.status} entries are read-only`);
    }
    if ("activity_date" in fields) validateDate(fields.activity_date);
    Object.assign(e, fields);
    if ("hours" in fields) e.hours = Number(e.hours || 0);
    if ("count" in fields) e.count = parseInt(e.count, 10) || 1;
    e.updated_at = now();
    await this.store.put("entries", e);
    return e;
  }

  async setStatus(uuid, to, force = false) {
    if (!STATES.includes(to)) throw new LedgerError(`unknown status '${to}'`);
    const e = await this.getEntry(uuid);
    if (!e) throw new LedgerError(`no such entry '${uuid}'`);
    if (to !== e.status && !force && !TRANSITIONS[e.status].includes(to)) {
      throw new LedgerError(`illegal transition ${e.status} -> ${to} for ${uuid}`);
    }
    const ts = now();
    e.status = to;
    e.updated_at = ts;
    if (to === CONFIRMED && !e.confirmed_at) e.confirmed_at = ts;
    if (to === VOID && !e.voided_at) e.voided_at = ts;
    await this.store.put("entries", e);
    return e;
  }

  async softDuplicates(entry, tolerance = 0.25) {
    const provider = normProvider(entry.provider);
    if (!provider) return [];
    const hours = Number(entry.hours || 0);
    const lo = hours * (1 - tolerance);
    const hi = hours * (1 + tolerance);
    const rows = await this.store.getAll("entries");
    return rows.filter((o) => {
      if (o.activity_date !== entry.activity_date || o.status !== CONFIRMED || o.uuid === entry.uuid) return false;
      if (normProvider(o.provider) !== provider) return false;
      const oh = Number(o.hours || 0);
      return (hours === 0 && oh === 0) || (lo <= oh && oh <= hi);
    });
  }

  async confirm(uuid, force = false) {
    const e = await this.getEntry(uuid);
    if (!e) throw new LedgerError(`no such entry '${uuid}'`);
    const dupes = await this.softDuplicates(e);
    if (dupes.length && !force) throw new SoftDuplicate(uuid, dupes);
    return this.setStatus(uuid, CONFIRMED);
  }

  async supersede(uuid, fields) {
    const old = await this.getEntry(uuid);
    if (!old) throw new LedgerError(`no such entry '${uuid}'`);
    if (old.status !== CONFIRMED) throw new LedgerError(`only confirmed entries are superseded; ${uuid} is ${old.status}`);
    const merged = { ...old, ...fields, status: CONFIRMED };
    const id = await this.addEntry(merged);
    const neu = await this.getEntry(id);
    neu.supersedes = uuid;
    await this.store.put("entries", neu);
    await this.setStatus(uuid, SUPERSEDED);
    // Carry evidence whose bytes still match their hash, as the Python app does.
    for (const ev of await this.listEvidence(uuid)) {
      const file = await this.store.get("files", ev.sha256);
      if (!file) continue;
      const bytes = new Uint8Array(await blobBytes(file.blob));
      if ((await sha256Hex(bytes)) !== ev.sha256) continue;
      await this.store.put("evidence", { ...ev, id: undefined, entry_uuid: id, added_at: now() });
    }
    return id;
  }

  void(uuid) {
    return this.setStatus(uuid, VOID);
  }

  async supersededBy(uuid) {
    const rows = await this.store.getAll("entries");
    const r = rows.find((x) => x.supersedes === uuid);
    return r ? r.uuid : null;
  }

  async markSubmitted(uuid, bodyId, onDate) {
    const e = await this.getEntry(uuid);
    if (!e) throw new LedgerError(`no such entry '${uuid}'`);
    const bodies = [...e.submitted_to];
    if (!bodies.includes(bodyId)) bodies.push(bodyId);
    const dates = { ...e.submitted_dates, [bodyId]: validateDate(onDate) };
    return this.updateEntry(uuid, { submitted_to: bodies, submitted_dates: dates });
  }

  // -- evidence

  async addEvidence(uuid, filename, bytes, mime = "", note = "") {
    if (!(await this.getEntry(uuid))) throw new LedgerError(`no such entry '${uuid}'`);
    const digest = await sha256Hex(bytes);
    const safe = safeFilename(filename);
    if (!(await this.store.get("files", digest))) {
      await this.store.put("files", { sha256: digest, mime, blob: new Blob([bytes], { type: mime || "application/octet-stream" }) });
    }
    const row = { entry_uuid: uuid, filename: safe, stored_path: "", sha256: digest, bytes: bytes.length, mime: mime || "", added_at: now(), note: note || "" };
    row.id = await this.store.put("evidence", row);
    return row;
  }

  async listEvidence(uuid = null) {
    const rows = await this.store.getAll("evidence");
    rows.sort((a, b) => a.id - b.id);
    return uuid ? rows.filter((r) => r.entry_uuid === uuid) : rows;
  }

  async evidenceFile(row) {
    const f = await this.store.get("files", row.sha256);
    return f ? f.blob : null;
  }

  /** Re-hash every stored file: OK / MISSING / ALTERED per evidence row. */
  async verifyEvidence(uuid = null) {
    const out = [];
    for (const row of await this.listEvidence(uuid)) {
      const blob = await this.evidenceFile(row);
      if (!blob) {
        out.push({ ...row, verify: "MISSING" });
        continue;
      }
      const digest = await sha256Hex(new Uint8Array(await blobBytes(blob)));
      out.push({ ...row, verify: digest === row.sha256 ? "OK" : "ALTERED", observed_sha256: digest });
    }
    return out;
  }

  // -- holder state

  async getConfig(key, dflt = null) {
    const r = await this.store.get("config", key);
    return r ? r.value : dflt;
  }

  async setConfig(key, value) {
    await this.store.put("config", { key, value: String(value) });
  }

  async allConfig() {
    return Object.fromEntries((await this.store.getAll("config")).map((r) => [r.key, r.value]));
  }

  async setCredentialState(credential, fields) {
    const allowed = ["anchor_date", "carryover_in", "portal_credits", "portal_checked", "active", "note"];
    const bad = Object.keys(fields).filter((k) => !allowed.includes(k));
    if (bad.length) throw new LedgerError(`unknown credential-state field(s): ${bad.join(", ")}`);
    if (fields.anchor_date) validateDate(fields.anchor_date);
    const cur = (await this.store.get("credential_state", credential)) || { credential, anchor_date: null, carryover_in: 0, portal_credits: null, portal_checked: null, active: 1, note: "" };
    Object.assign(cur, fields);
    cur.carryover_in = Number(cur.carryover_in || 0);
    await this.store.put("credential_state", cur);
    return cur;
  }

  async getCredentialState(credential) {
    return (await this.store.get("credential_state", credential)) || null;
  }

  async allCredentialState() {
    const rows = await this.store.getAll("credential_state");
    return rows.sort((a, b) => a.credential.localeCompare(b.credential));
  }

  async listFees() {
    const rows = await this.store.getAll("fees");
    return rows.sort((a, b) => a.id - b.id);
  }

  async addFee(fields) {
    const row = { credential: fields.credential, name: fields.name, amount_usd: fields.amount_usd ?? null, due_date: fields.due_date ?? null, paid_on: fields.paid_on ?? null, note: fields.note || "" };
    row.id = await this.store.put("fees", row);
    return row;
  }

  /** Entries in engine shape, with evidence counts (Python Ledger.engine_entries). */
  async engineEntries(includePending = false) {
    const states = includePending ? [CONFIRMED, CAPTURED] : [CONFIRMED];
    const rows = await this.listEntries({ status: states });
    const counts = {};
    for (const ev of await this.listEvidence()) counts[ev.entry_uuid] = (counts[ev.entry_uuid] || 0) + 1;
    return rows.map((r) => ({
      id: r.uuid, date: r.activity_date, title: r.title, provider: r.provider, hours: r.hours, count: r.count,
      tags: r.tags, markers: r.markers, overrides: r.overrides, applies_to: r.applies_to, status: r.status,
      submitted_to: r.submitted_to, submitted_dates: r.submitted_dates, evidence_count: counts[r.uuid] || 0,
    }));
  }

  async stats() {
    const rows = await this.store.getAll("entries");
    const byStatus = {};
    for (const r of rows) byStatus[r.status] = (byStatus[r.status] || 0) + 1;
    const ev = await this.store.getAll("files");
    let bytes = 0;
    for (const f of ev) bytes += f.blob ? f.blob.size : 0;
    return { entries: byStatus, evidence_files: (await this.store.getAll("evidence")).length, evidence_bytes: bytes };
  }

  async wipe() {
    await this.store.clearAll();
  }
}

function pick(obj, keys) {
  const out = {};
  for (const k of keys) if (k in obj) out[k] = obj[k];
  return out;
}

export async function blobBytes(blob) {
  if (blob.arrayBuffer) return blob.arrayBuffer();
  return new Response(blob).arrayBuffer();
}

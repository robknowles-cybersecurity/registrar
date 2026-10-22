// Imports are untrusted input: field types (F3), evidence filenames (F4) and
// embedded file sizes (F5) are checked before anything is stored.
import { test } from "node:test";
import assert from "node:assert/strict";
import { Ledger, MemoryStore, CONFIRMED } from "../src/ledger.js";
import { exportLedger, importLedger, FORMAT } from "../src/schema.js";

const UUID = "0123456789abcdef0123456789abcdef";
const SHA = "a".repeat(64);

function entry(over = {}) {
  return { uuid: UUID, activity_date: "2026-05-05", title: "T", provider: "P", hours: 1, count: 1, tags: ["training"], markers: [], submitted_to: [], notes: "", status: CONFIRMED, capture_source: "web", evidence: [], ...over };
}

async function refused(e) {
  const led = new Ledger(new MemoryStore());
  await assert.rejects(importLedger(led, { format: FORMAT, entries: [e] }), (err) => {
    assert.match(err.message, /entries\[0\]/, "the reason names the entry");
    return true;
  });
  assert.equal((await led.listEntries()).length, 0, "nothing is stored");
}

test("F3: count must be a whole number, 0 or more", async () => {
  for (const count of ["<img src=x onerror=alert(1)>", "3", -1, 1.5, null, {}]) await refused(entry({ count }));
  const led = new Ledger(new MemoryStore());
  const r = await importLedger(led, { format: FORMAT, entries: [entry({ count: 0 })] });
  assert.equal(r.entries_added, 1);
});

test("F3: title, provider, notes, evidence filename and mime must be text", async () => {
  for (const k of ["title", "provider", "notes"]) {
    await refused(entry({ [k]: { toString: "x" } }));
    await refused(entry({ [k]: ["<b>x</b>"] }));
    await refused(entry({ [k]: 7 }));
  }
  await refused(entry({ evidence: [{ sha256: SHA, filename: 42, mime: "application/pdf" }] }));
  await refused(entry({ evidence: [{ sha256: SHA, mime: "application/pdf" }] }));
  await refused(entry({ evidence: [{ sha256: SHA, filename: "a.pdf", mime: ["text/html"] }] }));
  await refused(entry({ evidence: "a.pdf" }));
});

test("F3: absent optional fields still default (older exports keep importing)", async () => {
  const led = new Ledger(new MemoryStore());
  const e = entry();
  for (const k of ["count", "title", "provider", "notes"]) delete e[k];
  e.evidence = [{ sha256: SHA, filename: "a.pdf" }];
  const r = await importLedger(led, { format: FORMAT, entries: [e] });
  assert.equal(r.entries_added, 1);
  const got = await led.getEntry(UUID);
  assert.equal(got.count, 1);
  assert.equal(got.title, "");
});

test("F3: a real export still round-trips", async () => {
  const src = new Ledger(new MemoryStore());
  const u = await src.addEntry({ activity_date: "2026-05-05", title: "Course", provider: "Prov", hours: 2, count: 3, tags: ["training"], status: CONFIRMED });
  await src.addEvidence(u, "cert.pdf", new TextEncoder().encode("%PDF-1.4 x"), "application/pdf");
  const doc = await exportLedger(src);
  const led = new Ledger(new MemoryStore());
  const r = await importLedger(led, JSON.parse(JSON.stringify(doc)));
  assert.equal(r.entries_added, 1);
  assert.equal(r.files_stored, 1);
});

test("F4: an imported evidence filename is sanitized; the packet's zip entry has no path", async () => {
  const { loadRules } = await import("../src/engine.js");
  const { bodyPacket, packetZip, bundleName } = await import("../src/packets.js");
  const { readZip } = await import("../src/zip.js");
  const { sha256Hex } = await import("../src/ledger.js");
  const { readFileSync } = await import("node:fs");
  const rules = loadRules(JSON.parse(readFileSync(new URL("../src/data.json", import.meta.url), "utf8")));
  const bytes = new TextEncoder().encode("%PDF-1.4 evil");
  const sha = await sha256Hex(bytes);
  const doc = {
    format: FORMAT,
    entries: [entry({ evidence: [{ sha256: sha, filename: "x/../../../.bashrc", bytes: bytes.length, mime: "application/pdf" }] })],
    registrar: { extension: "registrar-browser-1", credential_state: [{ credential: "cissp", anchor_date: "2025-09-01", active: 1 }], files: { [sha]: { mime: "application/pdf", data_base64: Buffer.from(bytes).toString("base64") } } },
  };
  const led = new Ledger(new MemoryStore());
  await importLedger(led, doc);
  const [row] = await led.listEvidence();
  assert.equal(row.filename, ".bashrc", "stored through the upload path's safeFilename");
  const p = await bodyPacket(led, rules, "isc2", { asOf: "2026-10-21" });
  const { zip } = await packetZip(led, p);
  const names = readZip(zip).map((f) => f.name);
  const ev = names.filter((n) => n.startsWith("evidence/"));
  assert.deepEqual(ev, [`evidence/2026-05-05_01234567_.bashrc`]);
  for (const n of names) {
    assert.ok(!n.includes(".."), n);
    assert.ok(!n.includes("\\"), n);
    assert.equal(n.split("/").length <= 2, true, n);
  }
  // bundleName on its own, for a row stored before this fix
  const e = { activity_date: "2026-05-05", uuid: UUID };
  assert.equal(bundleName(e, { filename: "x/../../../.bashrc" }), "2026-05-05_01234567_x_.bashrc");
  assert.equal(bundleName(e, { filename: "..\\..\\win.ini" }), "2026-05-05_01234567__win.ini");
  assert.equal(bundleName(e, { filename: ".." }), "2026-05-05_01234567_file");
  assert.equal(bundleName(e, { filename: "cert.pdf" }), "2026-05-05_01234567_cert.pdf");
});

test("F5: an embedded file over 15 MB is refused before it is decoded", async () => {
  const { MAX_EVIDENCE_BYTES } = await import("../src/ledger.js");
  const led = new Ledger(new MemoryStore());
  const big = "A".repeat(Math.ceil(MAX_EVIDENCE_BYTES / 3) * 4 + 8);
  const doc = { format: FORMAT, entries: [entry({ evidence: [{ sha256: SHA, filename: "big.pdf" }] })],
    registrar: { extension: "registrar-browser-1", files: { [SHA]: { mime: "application/pdf", data_base64: big } } } };
  const realAtob = globalThis.atob;
  let decoded = 0;
  globalThis.atob = (s) => { decoded += s.length; return realAtob(s); };
  try {
    const r = await importLedger(led, doc);
    assert.deepEqual(r.files_too_large, [SHA]);
    assert.equal(r.files_stored, 0);
    assert.equal(r.files_absent, 0, "counted once, as too large");
    assert.equal(decoded, 0, "never decoded");
  } finally {
    globalThis.atob = realAtob;
  }
  assert.equal((await led.store.getAll("files")).length, 0);
});

test("F5: render scale is clamped to about 25 megapixels; deadlines reject and clean up", async () => {
  const { renderScale, withDeadline, MAX_RENDER_PIXELS } = await import("../src/ingest.js");
  assert.equal(renderScale(612, 792), 2.5, "a US Letter page keeps the full 2.5x");
  const s = renderScale(14400, 14400); // a 200-inch page
  assert.ok(s < 2.5);
  assert.ok(14400 * s * 14400 * s <= MAX_RENDER_PIXELS * 1.0001);
  let stopped = false;
  const never = new Promise(() => {});
  await assert.rejects(withDeadline(never, Date.now() + 20, "OCR timed out", () => { stopped = true; }), /OCR timed out/);
  assert.ok(stopped, "the timeout hook ran (it terminates the worker)");
  assert.equal(await withDeadline(Promise.resolve(7), Date.now() + 1000, "late"), 7);
});

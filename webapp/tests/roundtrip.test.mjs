// Export / Import must round-trip with the Python Registrar's export format.
//
// 1. The Python app builds its demo ledger and exports it (cpebot/exports.py).
// 2. The browser ledger imports that export and exports it again: the entries
//    must come back identical, field for field and in the same key order.
// 3. With evidence files attached, export -> wipe -> import -> export must be
//    identical, files included, and every file must re-verify.
// 4. The Python side reads the browser's export: same keys, same types, and
//    the Python engine's totals equal the browser engine's.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Ledger, MemoryStore } from "../src/ledger.js";
import { exportLedger, importLedger, FORMAT } from "../src/schema.js";
import * as E from "../src/engine.js";

const here = dirname(fileURLToPath(import.meta.url));
const rules = E.loadRules(JSON.parse(readFileSync(join(here, "..", "src", "data.json"), "utf8")));
const py = JSON.parse(execFileSync("python3", [join(here, "py_export.py")], { maxBuffer: 64 << 20 }).toString());
const AS_OF = "2026-10-21";

test("a Python export imports and re-exports unchanged", async () => {
  const led = new Ledger(new MemoryStore());
  const rep = await importLedger(led, py.export);
  assert.equal(rep.entries_added, py.export.entries.length);
  assert.ok(rep.entries_added > 10);
  assert.ok(rep.evidence_rows > 0, "the demo ledger has evidence rows");
  const out = await exportLedger(led, { exportedAt: py.export.exported_at });
  assert.equal(out.format, FORMAT);
  assert.deepEqual(out.entries, py.export.entries);
  assert.equal(JSON.stringify(out.entries), JSON.stringify(py.export.entries), "same key order too");
  // Files were not in the Python export: they report missing, not fine.
  const v = await led.verifyEvidence();
  assert.ok(v.length > 0 && v.every((r) => r.verify === "MISSING"));
});

test("export -> wipe -> import -> export is lossless, files included", async () => {
  const led = new Ledger(new MemoryStore());
  await importLedger(led, py.export);
  for (const s of py.credential_state) {
    await led.setCredentialState(s.credential, { anchor_date: s.anchor_date, carryover_in: s.carryover_in, portal_credits: s.portal_credits, portal_checked: s.portal_checked, active: s.active, note: s.note });
  }
  // Re-attach the Python evidence files' bytes to a fresh entry so the browser
  // store holds real blobs for those hashes.
  const target = (await led.listEntries())[0].uuid;
  for (const b64 of Object.values(py.evidence_files)) {
    await led.addEvidence(target, "certificate.pdf", Uint8Array.from(Buffer.from(b64, "base64")), "application/pdf");
  }
  await led.setConfig("attendee_name", "Sample Holder");
  const first = await exportLedger(led, { exportedAt: "2026-10-21 12:00:00" });
  assert.ok(Object.keys(first.registrar.files).length > 0);
  await led.wipe();
  assert.equal((await led.listEntries()).length, 0);
  const rep = await importLedger(led, JSON.parse(JSON.stringify(first)));
  assert.equal(rep.files_refused.length, 0);
  const second = await exportLedger(led, { exportedAt: "2026-10-21 12:00:00" });
  assert.deepEqual(second, first);
  const v = await led.verifyEvidence();
  assert.ok(v.every((r) => r.verify === "OK"), JSON.stringify(v.filter((r) => r.verify !== "OK")));

  // Python reads it: same keys and types, and the same totals.
  const dir = mkdtempSync(join(tmpdir(), "registrar-rt-"));
  try {
    const f = join(dir, "browser-export.json");
    writeFileSync(f, JSON.stringify(second));
    const res = JSON.parse(execFileSync("python3", [join(here, "py_export.py"), "--check", f, AS_OF]).toString());
    assert.deepEqual(res.problems, []);
    const anchors = Object.fromEntries(second.registrar.credential_state.map((s) => [s.credential, s.anchor_date]));
    const entries = await led.engineEntries();
    for (const [cid, t] of Object.entries(res.totals)) {
      const a = E.assess(rules.get(cid), entries, { anchor: anchors[cid], asOf: AS_OF });
      assert.equal(a.awarded, t.awarded, cid);
      assert.equal(a.naive_total, t.naive_total, cid);
      assert.equal(a.verdict, t.verdict, cid);
    }
    assert.ok(Object.keys(res.totals).length >= 5);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("an altered file in an import is refused, not stored", async () => {
  const led = new Ledger(new MemoryStore());
  const uuid = await led.addEntry({ activity_date: "2026-03-01", title: "x" });
  await led.addEvidence(uuid, "a.pdf", new TextEncoder().encode("%PDF-1.4 original"), "application/pdf");
  const doc = await exportLedger(led);
  const sha = Object.keys(doc.registrar.files)[0];
  doc.registrar.files[sha].data_base64 = Buffer.from("%PDF-1.4 tampered").toString("base64");
  const fresh = new Ledger(new MemoryStore());
  const rep = await importLedger(fresh, doc);
  assert.deepEqual(rep.files_refused, [sha]);
  const v = await fresh.verifyEvidence();
  assert.equal(v[0].verify, "MISSING");
});

test("import refuses documents that are not the ledger format", async () => {
  const led = new Ledger(new MemoryStore());
  await assert.rejects(importLedger(led, { format: "something-else", entries: [] }), /unsupported format/);
  await assert.rejects(importLedger(led, { format: FORMAT, entries: [{ uuid: "nope", activity_date: "2026-01-01", status: "confirmed" }] }), /uuid/);
  await assert.rejects(importLedger(led, { format: FORMAT, entries: [{ uuid: "a".repeat(32), activity_date: "2026-02-30", status: "confirmed" }] }), /not a real date/);
  assert.equal((await led.listEntries()).length, 0, "a refused import writes nothing");
});

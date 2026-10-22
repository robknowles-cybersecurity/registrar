// The browser ledger keeps the Python ledger's rules.
import { test } from "node:test";
import assert from "node:assert/strict";
import { Ledger, MemoryStore, SoftDuplicate, LedgerError, validateUpload } from "../src/ledger.js";

const mk = () => new Ledger(new MemoryStore());
const base = { activity_date: "2026-03-01", title: "Talk", provider: "Example Institute", hours: 2, tags: ["training"] };

test("new entries are captured and count for nothing until confirmed", async () => {
  const led = mk();
  const id = await led.addEntry(base);
  assert.equal((await led.getEntry(id)).status, "captured");
  assert.equal((await led.engineEntries()).length, 0);
  await led.confirm(id);
  assert.equal((await led.engineEntries()).length, 1);
});

test("confirm refuses a soft duplicate unless forced", async () => {
  const led = mk();
  await led.confirm(await led.addEntry(base));
  const dup = await led.addEntry({ ...base, hours: 2.25, provider: "  example   INSTITUTE " });
  await assert.rejects(led.confirm(dup), SoftDuplicate);
  await led.confirm(dup, true);
  const far = await led.addEntry({ ...base, hours: 4 });
  await led.confirm(far); // hours outside 25%: not a duplicate
});

test("confirmed entries are never edited in place; corrections supersede and keep both", async () => {
  const led = mk();
  const id = await led.addEntry(base);
  await led.addEvidence(id, "c.pdf", new TextEncoder().encode("%PDF-1.4 x"), "application/pdf");
  await led.confirm(id);
  await assert.rejects(led.updateEntry(id, { hours: 3 }), /never edited in place/);
  await led.updateEntry(id, { submitted_to: ["isc2"] }); // bookkeeping is allowed
  const neu = await led.supersede(id, { hours: 3 });
  assert.equal((await led.getEntry(id)).status, "superseded");
  assert.equal((await led.getEntry(neu)).supersedes, id);
  assert.equal((await led.getEntry(neu)).hours, 3);
  assert.equal((await led.listEvidence(neu)).length, 1, "evidence carried to the correction");
  assert.equal(await led.supersededBy(id), neu);
});

test("void is terminal and nothing is deleted", async () => {
  const led = mk();
  const id = await led.addEntry(base);
  await led.void(id);
  const e = await led.getEntry(id);
  assert.equal(e.status, "void");
  assert.ok(e.voided_at);
  await assert.rejects(led.setStatus(id, "confirmed"), /illegal transition/);
  assert.equal((await led.listEntries()).length, 1);
});

test("evidence re-verification catches an altered file", async () => {
  const led = mk();
  const id = await led.addEntry(base);
  const row = await led.addEvidence(id, "c.pdf", new TextEncoder().encode("%PDF-1.4 original"), "application/pdf");
  assert.equal((await led.verifyEvidence())[0].verify, "OK");
  const f = await led.store.get("files", row.sha256);
  f.blob = new Blob([new TextEncoder().encode("%PDF-1.4 tampered")]);
  await led.store.put("files", f);
  assert.equal((await led.verifyEvidence())[0].verify, "ALTERED");
});

test("uploads are checked by size, extension and magic bytes", () => {
  const pdf = new TextEncoder().encode("%PDF-1.7 ...");
  assert.equal(validateUpload("a.pdf", pdf), "application/pdf");
  assert.throws(() => validateUpload("a.png", pdf), /does not match/);
  assert.throws(() => validateUpload("a.exe", pdf), /unsupported/);
  assert.throws(() => validateUpload("a.pdf", new Uint8Array(0)), /empty/);
  assert.throws(() => validateUpload("a.pdf", new Uint8Array(16 * 1024 * 1024)), /too large/);
});

test("dates are validated like the Python ledger", async () => {
  const led = mk();
  await assert.rejects(led.addEntry({ ...base, activity_date: "2026-02-30" }), LedgerError);
  await assert.rejects(led.addEntry({ ...base, activity_date: "03/01/2026" }), LedgerError);
});

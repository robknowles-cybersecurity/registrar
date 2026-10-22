// Sample data and the "try capture with this" certificate.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as pdfjs from "../node_modules/pdfjs-dist/legacy/build/pdf.mjs";
import { pdfText } from "../src/pdftext.js";
import { parseCertificate } from "../src/parser.js";
import { Ledger, MemoryStore, CONFIRMED, CAPTURED, validateUpload } from "../src/ledger.js";
import { loadSample, removeSample, sampleState, SAMPLE_REF, SAMPLE_ACTIVITIES, SAMPLE_CREDENTIALS } from "../src/sample.js";
import * as E from "../src/engine.js";

const here = dirname(fileURLToPath(import.meta.url));
const rules = E.loadRules(JSON.parse(readFileSync(join(here, "..", "src", "data.json"), "utf8")));
const TODAY = "2026-10-21";

test("the try-capture certificate parses: date, hours, provider, title, holder", async () => {
  const bytes = new Uint8Array(readFileSync(join(here, "..", "src", "sample", "try-capture-certificate.pdf")));
  assert.equal(validateUpload("sample-certificate.pdf", bytes), "application/pdf");
  const text = await pdfText(pdfjs, bytes);
  assert.match(text, /NOT A REAL RECORD/);
  const f = parseCertificate(text).fields;
  assert.equal(f.activity_date.value, "2026-09-24", f.activity_date.why);
  assert.equal(f.hours.value, 3.5, f.hours.why);
  assert.equal(f.provider.value, "Example Cyber Academy", f.provider.why);
  assert.equal(f.title.value, "Incident Response Tabletop Exercises", f.title.why);
  assert.equal(f.holder_name.value, "Jordan Sample");
});

test("load sample: 3 credentials, 9 activities, synthetic evidence, all marked", async () => {
  const led = new Ledger(new MemoryStore());
  const r = await loadSample(led, { today: TODAY });
  assert.deepEqual(r, { activities: 9, files: 8, credentials: 3 });
  const states = await led.allCredentialState();
  assert.deepEqual(states.map((s) => s.credential).sort(), SAMPLE_CREDENTIALS.map((c) => c.id).sort());
  const entries = await led.listEntries();
  assert.equal(entries.length, SAMPLE_ACTIVITIES.length);
  for (const e of entries) {
    assert.match(e.title, /^SAMPLE — /);
    assert.match(e.provider, /^(Example|Placeholder) /);
    assert.equal(e.capture_ref, SAMPLE_REF);
    assert.equal(e.capture_source, "fixture");
  }
  assert.equal(entries.filter((e) => e.status === CAPTURED).length, 1);
  // Every certificate is a real PDF the app's own parser reads, and agrees with its entry.
  const ev = await led.verifyEvidence();
  assert.equal(ev.length, 8);
  assert.ok(ev.every((x) => x.verify === "OK"));
  for (const row of ev) {
    const bytes = new Uint8Array(await (await led.evidenceFile(row)).arrayBuffer());
    assert.equal(validateUpload(row.filename, bytes), "application/pdf");
    const text = await pdfText(pdfjs, bytes);
    assert.match(text, /SAMPLE CERTIFICATE - NOT A REAL RECORD/);
    const e = entries.find((x) => x.uuid === row.entry_uuid);
    const f = parseCertificate(text).fields;
    assert.equal(f.activity_date.value, e.activity_date, `${row.filename}: date`);
    assert.equal(f.hours.value, e.hours, `${row.filename}: hours`);
    assert.equal(`SAMPLE — ${f.title.value}`, e.title, `${row.filename}: title`);
  }
});

test("the sample shows caps at work and sits inside the current cycles", async () => {
  const led = new Ledger(new MemoryStore());
  await loadSample(led, { today: TODAY });
  const entries = await led.engineEntries();
  const byId = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  const cissp = E.assess(rules.get("cissp"), entries, { anchor: byId.cissp.anchor_date, asOf: TODAY });
  const cysa = E.assess(rules.get("cysa_plus"), entries, { anchor: byId.cysa_plus.anchor_date, asOf: TODAY });
  const cfe = E.assess(rules.get("cfe"), entries, { anchor: byId.cfe.anchor_date, asOf: TODAY });
  assert.equal(cissp.entries_counted, 8, "every confirmed sample activity is in the CISSP cycle");
  assert.equal(cissp.forfeited, 10, "Group B: 40 hours against a cap of 30");
  assert.equal(cysa.forfeited, 1, "CySA+ conferences: 16 hours against a cap of 15");
  assert.ok(cissp.naive_total > cissp.awarded);
  // CFE: the hours are all there, the fraud minimum is not. The one course on
  // fraud and ethics counts toward both floors.
  assert.equal(cfe.awarded, 20, "CFE: 20 of 20 hours in the one-year cycle");
  assert.equal(cfe.gap, 0);
  const g = Object.fromEntries(cfe.groups.map((x) => [x.id, x]));
  assert.equal(g.fraud.awarded, 4);
  assert.equal(g.fraud.shortfall, 6, "CFE fraud: 4 against a floor of 10");
  assert.equal(g.ethics.awarded, 4);
  assert.equal(g.ethics.shortfall, 0, "CFE ethics: 4 against a floor of 2");
  assert.equal(cfe.verdict, "BEHIND", "a full total with a short minimum is still behind");
});

test("remove sample: only sample rows go; your own data stays", async () => {
  const led = new Ledger(new MemoryStore());
  await led.setCredentialState("cfe", { anchor_date: "2026-01-01", active: 1 });
  await led.setCredentialState("cissp", { anchor_date: null, active: 1 }); // held, no start yet
  const mine = await led.addEntry({ activity_date: "2026-05-05", title: "My own course", provider: "Real Provider", hours: 2, tags: ["training"], status: CONFIRMED });
  await led.addEvidence(mine, "mine.pdf", new TextEncoder().encode("%PDF-1.4 my own"), "application/pdf");
  await loadSample(led, { today: TODAY });
  await assert.rejects(loadSample(led, { today: TODAY }), /already loaded/);
  assert.ok(await sampleState(led));
  assert.ok((await led.getCredentialState("cissp")).anchor_date, "the sample gave the held CISSP a start date");
  // a correction of a sample entry is sample data too
  const s1 = (await led.listEntries({ status: CONFIRMED })).find((e) => e.capture_ref === SAMPLE_REF);
  await led.supersede(s1.uuid, { hours: 9 });

  const r = await removeSample(led);
  assert.equal(r.activities, 10);
  const left = await led.listEntries();
  assert.deepEqual(left.map((e) => e.uuid), [mine]);
  const ev = await led.verifyEvidence();
  assert.equal(ev.length, 1);
  assert.equal(ev[0].verify, "OK");
  assert.equal((await led.store.getAll("files")).length, 1);
  assert.deepEqual((await led.allCredentialState()).map((s) => s.credential).sort(), ["cfe", "cissp"]);
  assert.equal((await led.getCredentialState("cissp")).anchor_date, null, "the start date the sample filled in is cleared again");
  assert.equal(await sampleState(led), null);
});

test("remove sample keeps a credential setting you changed after loading (F1)", async () => {
  const led = new Ledger(new MemoryStore());
  await led.setCredentialState("cfe", { anchor_date: null, active: 1, note: "mine" }); // held, start left for later
  await loadSample(led, { today: TODAY });
  // created by the sample, then edited by you
  await led.setCredentialState("cissp", { anchor_date: "2025-03-01", carryover_in: 12 });
  // created by the sample, edited only in a field the sample did not set
  await led.setCredentialState("cysa_plus", { portal_credits: 40 });
  // the start date the sample filled in, then edited by you
  await led.setCredentialState("cfe", { anchor_date: "2026-02-02" });
  const r = await removeSample(led);
  assert.equal(r.kept, 3);
  const by = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  assert.deepEqual(Object.keys(by).sort(), ["cfe", "cissp", "cysa_plus"]);
  assert.equal(by.cissp.anchor_date, "2025-03-01");
  assert.equal(by.cissp.carryover_in, 12);
  assert.equal(by.cysa_plus.portal_credits, 40);
  assert.equal(by.cfe.anchor_date, "2026-02-02");
  assert.equal(by.cfe.note, "mine");
});

test("remove sample undoes only the untouched settings, and reports none kept", async () => {
  const led = new Ledger(new MemoryStore());
  await led.setCredentialState("cfe", { anchor_date: null, active: 0, note: "mine" });
  await loadSample(led, { today: TODAY });
  await led.setCredentialState("cissp", { carryover_in: 5 }); // changed: kept
  const r = await removeSample(led);
  assert.equal(r.kept, 1);
  const by = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  assert.deepEqual(Object.keys(by).sort(), ["cfe", "cissp"], "the untouched CySA+ row the sample created is gone");
  assert.equal(by.cfe.anchor_date, null, "the start date it filled in is cleared");
  assert.equal(by.cfe.active, 0, "and the field it set is put back as it was");
});

test("a sample loaded by v1.2.1 (old record) still removes cleanly, keeping changed rows", async () => {
  const led = new Ledger(new MemoryStore());
  for (const c of SAMPLE_CREDENTIALS) {
    await led.setCredentialState(c.id, { anchor_date: E.addMonths(TODAY, -c.monthsAgo), carryover_in: 0, active: 1, note: "sample data" });
  }
  await led.setCredentialState("cfe", { note: "my note" });
  await led.setConfig("sample_data", JSON.stringify({ loaded_on: TODAY, created: SAMPLE_CREDENTIALS.map((c) => c.id), anchored: [] }));
  const r = await removeSample(led);
  assert.equal(r.kept, 1);
  assert.deepEqual((await led.allCredentialState()).map((s) => s.credential), ["cfe"]);
});

test("import never carries the sample record: removing after an import deletes no setting (F1)", async () => {
  const { exportLedger, importLedger } = await import("../src/schema.js");
  // An export whose sample record claims it created every credential row,
  // while the rows themselves are the user's own.
  const src = new Ledger(new MemoryStore());
  await src.setCredentialState("cissp", { anchor_date: "2025-01-01", carryover_in: 7, active: 1, note: "mine" });
  await src.setCredentialState("cfe", { anchor_date: "2025-06-01", active: 1 });
  const mine = await src.addEntry({ activity_date: "2026-05-05", title: "My own course", provider: "Real Provider", hours: 2, tags: ["training"], status: CONFIRMED });
  await src.setConfig("sample_data", JSON.stringify({ loaded_on: TODAY, created: ["cissp", "cfe"], settings: [
    { credential: "cissp", before: null, wrote: { anchor_date: "2025-01-01", carryover_in: 7, portal_credits: null, portal_checked: null, active: 1, note: "mine" } },
    { credential: "cfe", before: null, wrote: { anchor_date: "2025-06-01", carryover_in: 0, portal_credits: null, portal_checked: null, active: 1, note: "" } },
  ] }));
  const doc = await exportLedger(src);
  assert.ok("sample_data" in doc.registrar.config, "the export does carry the key");

  const led = new Ledger(new MemoryStore());
  const rep = await importLedger(led, JSON.parse(JSON.stringify(doc)));
  assert.equal(rep.entries_added, 1);
  assert.equal(await led.getConfig("sample_data"), null, "import skips the sample record");
  assert.equal(await sampleState(led), null);
  const before = JSON.stringify(await led.allCredentialState());
  const r = await removeSample(led);
  assert.deepEqual(r, { activities: 0, files: 0, kept: 0 });
  assert.equal(JSON.stringify(await led.allCredentialState()), before, "no credential setting deleted or changed");
  assert.deepEqual((await led.listEntries()).map((e) => e.uuid), [mine]);
});

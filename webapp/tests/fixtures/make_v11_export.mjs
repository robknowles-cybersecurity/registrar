// Write tests/fixtures/v1.1-export.json: an export made by the v1.1.0 app
// code (webapp/src at main cadba36), kept as a frozen fixture. The round-trip
// test imports it with the current code and requires a byte-identical
// re-export, so a later change cannot drift the format unnoticed.
//
// Run only against v1.1.0 sources; regenerating it with newer code would
// defeat its purpose.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = process.argv[2] || join(here, "..", "..", "src");
const { Ledger, MemoryStore } = await import(join(src, "ledger.js"));
const { exportLedger, importLedger } = await import(join(src, "schema.js"));

const py = JSON.parse(execFileSync("python3", [join(here, "..", "py_export.py")], { maxBuffer: 64 << 20 }).toString());
const led = new Ledger(new MemoryStore());
await importLedger(led, py.export);
for (const s of py.credential_state) {
  await led.setCredentialState(s.credential, { anchor_date: s.anchor_date, carryover_in: s.carryover_in, portal_credits: s.portal_credits, portal_checked: s.portal_checked, active: s.active, note: s.note });
}
const entries = await led.listEntries();
for (const [i, name] of ["01-isc2-webinar-centred.pdf", "03-eccouncil-table.pdf"].entries()) {
  await led.addEvidence(entries[i].uuid, name, new Uint8Array(readFileSync(join(here, "certificates", name))), "application/pdf");
}
await led.setConfig("attendee_name", "Jordan Sample");
await led.addFee({ credential: "cissp", name: "Sample annual fee", amount_usd: 135, due_date: "2026-09-01", paid_on: null });
const doc = await exportLedger(led, { exportedAt: "2026-10-21 12:00:00" });
// Volatile fields (created_at etc. of the evidence rows) are fixed by the
// MemoryStore run; normalise added_at so the fixture is reproducible.
for (const e of doc.entries) for (const ev of e.evidence) if (/^\d{4}-/.test(ev.added_at) && !py.export.entries.some((p) => p.evidence.some((x) => x.sha256 === ev.sha256))) ev.added_at = "2026-10-21 12:00:00";
// One more v1.1 import -> export: evidence rows are renumbered on import, so
// the first re-export can order the files map differently. The second is a
// fixed point, and that is the fixture.
const again = new Ledger(new MemoryStore());
await importLedger(again, JSON.parse(JSON.stringify(doc)));
const fixed = await exportLedger(again, { exportedAt: "2026-10-21 12:00:00" });
writeFileSync(join(here, "v1.1-export.json"), JSON.stringify(fixed, null, 1));
console.log(`wrote v1.1-export.json: ${fixed.entries.length} entries, ${Object.keys(fixed.registrar.files).length} files`);

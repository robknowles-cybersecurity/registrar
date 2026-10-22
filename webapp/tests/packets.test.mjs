// Packet zips: cover sheet per body, manifest hashes, refusal of altered files.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Ledger, MemoryStore, sha256Hex } from "../src/ledger.js";
import { loadRules } from "../src/engine.js";
import { bodyPacket, packetZip, coverSheet, mapFor } from "../src/packets.js";
import { readZip, crc32 } from "../src/zip.js";

const rules = loadRules(JSON.parse(readFileSync(new URL("../src/data.json", import.meta.url), "utf8")));

test("crc32 known value", () => {
  assert.equal(crc32(new TextEncoder().encode("123456789")), 0xcbf43926);
});

test("ISC2 credentials use the ISC2 packet map; others the generic one", () => {
  assert.equal(mapFor(rules.get("cissp"), rules.packetmaps).id, "isc2");
  assert.equal(mapFor(rules.get("cfe"), rules.packetmaps).id, "generic");
});

test("packet: cover sheet fields in form order, evidence bundled, altered file left out", async () => {
  const led = new Ledger(new MemoryStore());
  await led.setConfig("attendee_name", "Jordan Sample");
  await led.setCredentialState("cissp", { anchor_date: "2025-09-01" });
  const a = await led.addEntry({ activity_date: "2026-03-12", title: "Zero Trust", provider: "Example Institute", hours: 1.5, tags: ["training"], markers: ["domain_security"] });
  const b = await led.addEntry({ activity_date: "2026-04-01", title: "Leadership", provider: "Sample Co", hours: 2, tags: ["professional_dev"] });
  await led.confirm(a);
  await led.confirm(b);
  const good = new TextEncoder().encode("%PDF-1.4 good");
  await led.addEvidence(a, "cert.pdf", good, "application/pdf");
  const bad = await led.addEvidence(b, "b.pdf", new TextEncoder().encode("%PDF-1.4 b"), "application/pdf");
  const f = await led.store.get("files", bad.sha256);
  f.blob = new Blob([new TextEncoder().encode("%PDF-1.4 changed")]);
  await led.store.put("files", f);

  const p = await bodyPacket(led, rules, "isc2", { asOf: "2026-10-21" });
  const cover = coverSheet(p);
  assert.ok(cover.includes("CPE group: Group A"));
  assert.ok(cover.includes("CPE group: Group B"));
  assert.ok(cover.includes("Date completed: 03/12/2026"), "ISC2 map's date format");
  assert.ok(cover.includes("never asks for, stores or uses your portal login"));
  const { zip, manifest } = await packetZip(led, p);
  const files = readZip(zip);
  const ev = files.filter((x) => x.name.startsWith("evidence/"));
  assert.equal(ev.length, 1);
  assert.equal(await sha256Hex(ev[0].data), await sha256Hex(good));
  assert.equal(crc32(ev[0].data), ev[0].crc);
  assert.ok(manifest.gaps.some((g) => /no longer matches/.test(g.problem)));
});

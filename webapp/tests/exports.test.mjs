// CSV, XLSX and ICS exports: each is read back and checked, not just written.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Ledger, MemoryStore, CONFIRMED } from "../src/ledger.js";
import { loadSample } from "../src/sample.js";
import { readZip } from "../src/zip.js";
import { ledgerCsv, ledgerXlsx, cycleEndCalendar, makeXlsx, colName, uuid5, icsFold, ACTIVITY_COLUMNS } from "../src/exports.js";
import * as E from "../src/engine.js";

const here = dirname(fileURLToPath(import.meta.url));
const repo = dirname(dirname(here));
const rules = E.loadRules(JSON.parse(readFileSync(join(here, "..", "src", "data.json"), "utf8")));
const TODAY = "2026-10-21";

async function sampleLedger() {
  const led = new Ledger(new MemoryStore());
  await loadSample(led, { today: TODAY });
  // an awkward entry: quotes, commas, a newline, a formula-looking title, non-ASCII
  const u = await led.addEntry({ activity_date: "2026-07-07", title: '=HYPERLINK("x") "quoted", with comma\nand a newline — café', provider: "Example, Inc.", hours: 1.25, tags: ["training"], status: CONFIRMED });
  await led.addEvidence(u, "odd name, 1.pdf", new TextEncoder().encode("%PDF-1.4 odd"), "application/pdf");
  return { led, odd: u };
}

// ---------------------------------------------------------------- CSV

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (c === '"') q = false; else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\r" && text[i + 1] === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; i++; }
    else if (c === "\n") throw new Error("bare LF outside quotes");
    else cell += c;
  }
  if (cell || row.length) throw new Error("no CRLF after the last row");
  return rows;
}

test("CSV: RFC 4180, UTF-8 BOM, every activity with its evidence files, formulas neutralised", async () => {
  const { led, odd } = await sampleLedger();
  const text = await ledgerCsv(led);
  assert.ok(text.startsWith("﻿"));
  const rows = parseCsv(text.slice(1));
  assert.deepEqual(rows[0], ACTIVITY_COLUMNS.map(([, h]) => h));
  assert.equal(rows.length, 1 + 10);
  assert.ok(rows.every((r) => r.length === ACTIVITY_COLUMNS.length));
  const col = (name) => ACTIVITY_COLUMNS.findIndex(([k]) => k === name);
  const r = rows.find((x) => x[col("uuid")] === odd);
  assert.equal(r[col("title")], `'=HYPERLINK("x") "quoted", with comma\nand a newline — café`);
  assert.equal(r[col("provider")], "Example, Inc.");
  assert.equal(r[col("hours")], "1.25");
  assert.equal(r[col("evidence")], "odd_name__1.pdf", "the stored (sanitised) file name");
  const withFiles = rows.slice(1).filter((x) => x[col("evidence")].endsWith(".pdf"));
  assert.equal(withFiles.length, 9, "8 sample certificates + the odd one");
});

// ---------------------------------------------------------------- XLSX

function readXlsx(bytes) {
  const files = Object.fromEntries(readZip(bytes).map((f) => [f.name, new TextDecoder().decode(f.data)]));
  const unesc = (s) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
  const names = [...files["xl/workbook.xml"].matchAll(/<sheet name="([^"]*)" sheetId="(\d+)"/g)].map((m) => unesc(m[1]));
  const sheets = {};
  names.forEach((name, i) => {
    const xml = files[`xl/worksheets/sheet${i + 1}.xml`];
    const cells = {};
    for (const m of xml.matchAll(/<c r="([A-Z]+\d+)"([^>]*)>([\s\S]*?)<\/c>/g)) {
      const inline = /<t[^>]*>([\s\S]*?)<\/t>/.exec(m[3]);
      cells[m[1]] = /t="inlineStr"/.test(m[2]) ? unesc(inline[1]) : Number(/<v>(.*?)<\/v>/.exec(m[3])[1]);
    }
    sheets[name] = cells;
  });
  return { files, sheets };
}

test("XLSX: a valid package that reads back cell for cell", async () => {
  const { led, odd } = await sampleLedger();
  const bytes = await ledgerXlsx(led, rules, TODAY);
  const { files, sheets } = readXlsx(bytes);
  for (const part of ["[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml"]) {
    assert.ok(files[part], `missing ${part}`);
    assert.ok(files[part].startsWith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'), part);
  }
  assert.deepEqual(Object.keys(sheets), ["Activities", "Evidence", "Totals"]);

  const act = sheets.Activities;
  ACTIVITY_COLUMNS.forEach(([, h], i) => assert.equal(act[`${colName(i)}1`], h));
  const idCol = colName(ACTIVITY_COLUMNS.findIndex(([k]) => k === "uuid"));
  const row = Object.entries(act).find(([ref, v]) => ref.startsWith(idCol) && v === odd)[0].slice(idCol.length);
  assert.equal(act[`B${row}`], '=HYPERLINK("x") "quoted", with comma\nand a newline — café', "inline string, never a formula");
  assert.equal(act[`D${row}`], 1.25, "hours is a number cell");
  assert.equal(act[`L${row}`], "odd_name__1.pdf");
  assert.ok(!/<f>/.test(files["xl/worksheets/sheet1.xml"]), "no formulas anywhere");

  const ev = sheets.Evidence;
  assert.equal(ev.A1, "Date");
  assert.equal(Object.keys(ev).filter((k) => /^C\d+$/.test(k)).length, 1 + 9);

  const tot = sheets.Totals;
  assert.equal(tot.A1, "Credential");
  const entries = await led.engineEntries();
  const states = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  for (const r of [2, 3, 4]) {
    const cred = rules.credentials.find((c) => c.name === tot[`A${r}`]);
    const a = E.assess(cred, entries, { anchor: states[cred.id].anchor_date, asOf: TODAY });
    assert.equal(tot[`F${r}`], a.required, `${cred.name} required`);
    assert.equal(tot[`G${r}`], a.total_with_carryover, `${cred.name} counted`);
    assert.equal(tot[`I${r}`], a.forfeited, `${cred.name} forfeited`);
    assert.equal(tot[`E${r}`], E.addDays(a.cycle_end, -1));
  }
});

test("XLSX: an independent reader (openpyxl) agrees, when it is installed", async (t) => {
  try {
    execFileSync("python3", ["-c", "import openpyxl"], { stdio: "ignore" });
  } catch {
    t.skip("openpyxl not installed");
    return;
  }
  const { led } = await sampleLedger();
  const dir = mkdtempSync(join(tmpdir(), "registrar-xlsx-"));
  try {
    const f = join(dir, "x.xlsx");
    writeFileSync(f, await ledgerXlsx(led, rules, TODAY));
    const out = JSON.parse(execFileSync("python3", ["-c", `
import json, sys, openpyxl
wb = openpyxl.load_workbook(sys.argv[1])
print(json.dumps({ws.title: [[c for c in row] for row in ws.iter_rows(values_only=True)] for ws in wb.worksheets}, default=str))
`, f]).toString());
    assert.deepEqual(Object.keys(out), ["Activities", "Evidence", "Totals"]);
    assert.equal(out.Activities.length, 1 + 10);
    assert.equal(out.Activities[0][0], "Date");
    assert.ok(out.Activities.slice(1).some((r) => r[3] === 1.25));
    assert.equal(out.Totals.length, 4);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("XLSX: column names and XML-hostile text", () => {
  assert.deepEqual([0, 25, 26, 27, 51, 52, 701, 702].map(colName), ["A", "Z", "AA", "AB", "AZ", "BA", "ZZ", "AAA"]);
  const bytes = makeXlsx([{ name: "S&P <1>", columns: [["a", "A"]], rows: [{ a: "x\u0001y<&>" }] }]);
  const { sheets } = readXlsx(bytes);
  assert.deepEqual(sheets["S&P <1>"], { A1: "A", A2: "xy<&>" });
});

// ---------------------------------------------------------------- ICS

function parseIcs(text) {
  assert.ok(!/\r(?!\n)|(?<!\r)\n/.test(text), "CRLF line ends only");
  for (const line of text.split("\r\n")) assert.ok(new TextEncoder().encode(line).length <= 75, `line over 75 octets: ${line}`);
  const lines = text.replace(/\r\n /g, "").split("\r\n").filter(Boolean);
  const stack = [];
  const root = { name: "ROOT", props: [], children: [] };
  let cur = root;
  for (const l of lines) {
    if (l.startsWith("BEGIN:")) {
      const c = { name: l.slice(6), props: [], children: [] };
      cur.children.push(c);
      stack.push(cur);
      cur = c;
    } else if (l.startsWith("END:")) {
      assert.equal(l.slice(4), cur.name, "BEGIN/END nesting");
      cur = stack.pop();
    } else {
      const m = /^([A-Z-]+)((?:;[^:]*)?):(.*)$/.exec(l);
      assert.ok(m, `not a content line: ${l}`);
      cur.props.push({ name: m[1], params: m[2], value: m[3] });
    }
  }
  assert.equal(stack.length, 0);
  assert.equal(root.children.length, 1);
  return root.children[0];
}
const prop = (c, n) => c.props.find((p) => p.name === n);

test("ICS: one VEVENT per held credential's cycle end, each with 90- and 30-day alarms", async () => {
  const { led } = await sampleLedger();
  await led.setCredentialState("cfe", { anchor_date: null, active: 1 }); // held, no start: skipped and reported
  await led.setCredentialState("ceh", { anchor_date: "2025-01-01", active: 0 }); // not held any more: not included
  const cal = await cycleEndCalendar(led, rules, { asOf: TODAY, stamp: new Date("2026-10-21T12:00:00Z"), version: "1.2.0" });
  const vcal = parseIcs(cal.text);
  assert.equal(vcal.name, "VCALENDAR");
  assert.equal(prop(vcal, "VERSION").value, "2.0");
  assert.ok(prop(vcal, "PRODID").value);
  assert.deepEqual(cal.skipped, ["CFE"]);
  const events = vcal.children.filter((c) => c.name === "VEVENT");
  assert.equal(events.length, 2);
  const states = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  const uids = new Set();
  for (const ev of events) {
    for (const n of ["UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "DESCRIPTION"]) assert.ok(prop(ev, n), n);
    assert.equal(prop(ev, "DTSTAMP").value, "20261021T120000Z");
    assert.equal(prop(ev, "DTSTART").params, ";VALUE=DATE");
    const cred = rules.credentials.find((c) => prop(ev, "SUMMARY").value === `${c.name}: cycle ends`);
    const [, end] = E.cycleWindow(cred, states[cred.id].anchor_date, TODAY);
    assert.equal(prop(ev, "DTSTART").value, E.addDays(end, -1).replace(/-/g, ""), "the last day of the cycle");
    assert.equal(prop(ev, "DTEND").value, end.replace(/-/g, ""));
    uids.add(prop(ev, "UID").value);
    const alarms = ev.children.filter((c) => c.name === "VALARM");
    assert.deepEqual(alarms.map((a) => prop(a, "TRIGGER").value), ["-P90D", "-P30D"]);
    for (const a of alarms) {
      assert.equal(prop(a, "ACTION").value, "DISPLAY");
      assert.ok(prop(a, "DESCRIPTION").value);
    }
  }
  assert.equal(uids.size, 2);
});

test("ICS: UIDs are the self-hosted Registrar's, so the two calendars merge", async () => {
  const anchor = "2025-09-01";
  const led = new Ledger(new MemoryStore());
  await led.setCredentialState("cissp", { anchor_date: anchor, active: 1 });
  const cal = await cycleEndCalendar(led, rules, { asOf: TODAY });
  const py = execFileSync("python3", ["-c", `
import sys
sys.path.insert(0, sys.argv[1])
from cpebot import bodies as B, ics
import datetime
rules = B.load(sys.argv[1] + "/bodies.yaml")
cred = [c for c in rules.credentials if c.id == "cissp"][0]
ev = [e for e in ics.credential_events(cred, datetime.date.fromisoformat(sys.argv[2]), datetime.date.fromisoformat(sys.argv[3])) if e["kind"] == "cycle_end"][0]
print(ev["uid"], ev["date"].isoformat(), ev["summary"])
`, repo, anchor, TODAY]).toString().trim().split(" ");
  assert.equal(cal.events[0].uid, py[0]);
  assert.equal(cal.events[0].date, py[1]);
  assert.equal(await uuid5("python.org", "6ba7b810-9dad-11d1-80b4-00c04fd430c8"), "886313e1-3b8a-5372-9b90-0c9aee199e5d", "RFC 4122 test vector");
});

test("ICS folding never splits a UTF-8 sequence", () => {
  const line = `DESCRIPTION:${"é".repeat(60)}`;
  const folded = icsFold(line);
  for (const part of folded.split("\r\n")) assert.ok(new TextEncoder().encode(part).length <= 75);
  assert.equal(folded.replace(/\r\n /g, ""), line);
});

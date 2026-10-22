// Spreadsheet and calendar exports, all made in the browser.
//
//   CSV   one row per activity, with its evidence file names
//   XLSX  three sheets: Activities, Evidence, Totals (per held credential).
//         A minimal SpreadsheetML workbook written with zip.js: inline
//         strings, no shared-strings table, one bold header style. No library.
//   ICS   one all-day VEVENT per held credential's cycle end, each with a
//         90-day and a 30-day alarm. The UID is the self-hosted Registrar's
//         (uuid5 of credential|cycle_end|date|summary), so importing both
//         calendars updates one event instead of adding a second.
//
// These are reports. The JSON Export in the banner stays the backup format.
import { makeZip } from "./zip.js";
import * as E from "./engine.js";

// ------------------------------------------------------------ the rows

export const ACTIVITY_COLUMNS = [
  ["activity_date", "Date"], ["title", "Title"], ["provider", "Provider"], ["hours", "Hours"], ["count", "Count"],
  ["status", "State"], ["tags", "Kind"], ["markers", "Topics"], ["applies_to", "Counts toward"],
  ["submitted_to", "Submitted to"], ["cost_usd", "Cost (USD)"], ["evidence", "Evidence files"], ["notes", "Notes"],
  ["capture_source", "Source"], ["uuid", "ID"],
];
const NUMERIC = new Set(["hours", "count", "cost_usd"]);

/** Every activity (all states, newest first) as plain values, evidence filenames joined. */
export async function activityRows(led) {
  const ev = await led.listEvidence();
  return (await led.listEntries()).map((e) => {
    const files = ev.filter((r) => r.entry_uuid === e.uuid).map((r) => r.filename);
    const row = {};
    for (const [k] of ACTIVITY_COLUMNS) {
      let v = k === "evidence" ? files : e[k];
      if (Array.isArray(v)) v = v.join("; ");
      if (k === "submitted_to" && e.submitted_to.length) v = e.submitted_to.map((b) => `${b}${e.submitted_dates[b] ? ` ${e.submitted_dates[b]}` : ""}`).join("; ");
      row[k] = v == null ? "" : v;
    }
    return row;
  });
}

export async function evidenceRows(led) {
  const entries = Object.fromEntries((await led.listEntries()).map((e) => [e.uuid, e]));
  return (await led.listEvidence()).map((r) => ({
    activity_date: entries[r.entry_uuid]?.activity_date || "",
    title: entries[r.entry_uuid]?.title || "",
    filename: r.filename,
    bytes: r.bytes,
    sha256: r.sha256,
    added_at: r.added_at,
    entry: r.entry_uuid,
  }));
}

/** One row per held credential with a cycle start: the same figures as the Credentials screen. */
export async function totalsRows(led, rules, asOf = E.todayISO()) {
  const entries = await led.engineEntries();
  const out = [];
  for (const s of await led.allCredentialState()) {
    const cred = rules.byId[s.credential];
    if (!cred || !Number(s.active ?? 1)) continue;
    if (!s.anchor_date) {
      out.push({ credential: cred.name, body: cred.body_name, unit: cred.unit_label, cycle_start: "", last_day: "", required: cred.cycle_total, counted: "", naive: "", forfeited: "", verdict: "no cycle start recorded", unverified: cred.unverified_reasons().length ? "yes" : "" });
      continue;
    }
    const a = E.assess(cred, entries, { anchor: s.anchor_date, carryoverIn: s.carryover_in || 0, asOf });
    out.push({
      credential: cred.name, body: cred.body_name, unit: cred.unit_label,
      cycle_start: a.cycle_start, last_day: E.addDays(a.cycle_end, -1),
      required: a.required, counted: a.total_with_carryover, naive: a.naive_total + a.carryover_in, forfeited: a.forfeited,
      verdict: a.verdict.replace(/_/g, " ") + (a.advisory_only ? " (advisory only)" : ""),
      unverified: cred.unverified_reasons().length ? "yes" : "",
    });
  }
  return out;
}

const EVIDENCE_COLUMNS = [["activity_date", "Date"], ["title", "Activity"], ["filename", "File"], ["bytes", "Bytes"], ["sha256", "SHA-256"], ["added_at", "Added"], ["entry", "Activity ID"]];
const TOTALS_COLUMNS = [["credential", "Credential"], ["body", "Body"], ["unit", "Unit"], ["cycle_start", "Cycle start"], ["last_day", "Last day of cycle"],
  ["required", "Required"], ["counted", "Counted"], ["naive", "Naive sum of hours"], ["forfeited", "Forfeited to caps"], ["verdict", "Verdict"], ["unverified", "Uses unverified rules"]];

// ------------------------------------------------------------ CSV

function csvCell(v) {
  let s = String(v ?? "");
  // A cell a spreadsheet would read as a formula is prefixed with ' (CSV injection).
  if (/^[=+\-@\t\r]/.test(s) && !(typeof v === "number")) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** RFC 4180 CSV with a UTF-8 byte-order mark (so spreadsheet apps read it as UTF-8). */
export async function ledgerCsv(led) {
  const rows = await activityRows(led);
  const lines = [ACTIVITY_COLUMNS.map(([, h]) => csvCell(h)).join(",")];
  for (const r of rows) lines.push(ACTIVITY_COLUMNS.map(([k]) => csvCell(r[k])).join(","));
  return `\ufeff${lines.join("\r\n")}\r\n`;
}

// ------------------------------------------------------------ XLSX

const xmlEsc = (s) => String(s)
  // characters XML 1.0 cannot carry at all
  .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g, "")
  .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function colName(i) {
  let s = "";
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}

function sheetXml(columns, rows, numeric) {
  const cell = (ref, v, header) => {
    if (!header && numeric.has(columns[ref.col][0]) && v !== "" && v != null && Number.isFinite(Number(v))) return `<c r="${ref.a}"><v>${Number(v)}</v></c>`;
    if (v === "" || v == null) return "";
    return `<c r="${ref.a}" t="inlineStr"${header ? ' s="1"' : ""}><is><t xml:space="preserve">${xmlEsc(v)}</t></is></c>`;
  };
  const all = [columns.map(([, h]) => h), ...rows.map((r) => columns.map(([k]) => r[k]))];
  const body = all.map((vals, ri) => `<row r="${ri + 1}">${vals.map((v, ci) => cell({ a: `${colName(ci)}${ri + 1}`, col: ci }, v, ri === 0)).join("")}</row>`).join("");
  const widths = columns.map(([, h], ci) => Math.min(60, Math.max(h.length, ...all.slice(1).map((r) => String(r[ci] ?? "").length)) + 2));
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${widths.map((w, i) => `<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`).join("")}</cols><sheetData>${body}</sheetData></worksheet>`;
}

/** The workbook as .xlsx bytes. sheets: [{name, columns, rows, numeric}] */
export function makeXlsx(sheets) {
  const files = [
    { name: "[Content_Types].xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${sheets.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>` },
    { name: "_rels/.rels", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>` },
    { name: "xl/workbook.xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${sheets.map((s, i) => `<sheet name="${xmlEsc(s.name)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("")}</sheets></workbook>` },
    { name: "xl/_rels/workbook.xml.rels", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${sheets.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("")}<Relationship Id="rId${sheets.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>` },
    { name: "xl/styles.xml", data: `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>` },
    ...sheets.map((s, i) => ({ name: `xl/worksheets/sheet${i + 1}.xml`, data: sheetXml(s.columns, s.rows, s.numeric || new Set()) })),
  ];
  return makeZip(files);
}

export async function ledgerXlsx(led, rules, asOf = E.todayISO()) {
  return makeXlsx([
    { name: "Activities", columns: ACTIVITY_COLUMNS, rows: await activityRows(led), numeric: NUMERIC },
    { name: "Evidence", columns: EVIDENCE_COLUMNS, rows: await evidenceRows(led), numeric: new Set(["bytes"]) },
    { name: "Totals", columns: TOTALS_COLUMNS, rows: await totalsRows(led, rules, asOf), numeric: new Set(["required", "counted", "naive", "forfeited"]) },
  ]);
}

// ------------------------------------------------------------ ICS

// The self-hosted Registrar's UID namespace (its ics module), so UIDs match.
const UID_NAMESPACE = "6f1d3b0e-8c4e-5a7b-9d21-4e0c2f6a9b13";
export const ALARM_DAYS = [90, 30];

/** RFC 4122 version-5 UUID (SHA-1), as Python's uuid.uuid5. */
export async function uuid5(name, namespace = UID_NAMESPACE) {
  const ns = Uint8Array.from(namespace.replace(/-/g, "").match(/../g).map((h) => parseInt(h, 16)));
  const data = new Uint8Array([...ns, ...new TextEncoder().encode(name)]);
  const h = new Uint8Array(await crypto.subtle.digest("SHA-1", data)).slice(0, 16);
  h[6] = (h[6] & 0x0f) | 0x50;
  h[8] = (h[8] & 0x3f) | 0x80;
  const hex = [...h].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const icsText = (v) => String(v).replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r\n?/g, "\n").replace(/\n/g, "\\n");

/** RFC 5545 3.1: fold at 75 octets, never inside a UTF-8 sequence. */
export function icsFold(line, limit = 75) {
  const enc = new TextEncoder();
  if (enc.encode(line).length <= limit) return line;
  const parts = [];
  let chunk = "";
  let size = 0;
  let budget = limit;
  for (const ch of line) {
    const n = enc.encode(ch).length;
    if (size + n > budget) {
      parts.push(chunk);
      chunk = "";
      size = 0;
      budget = limit - 1;
    }
    chunk += ch;
    size += n;
  }
  parts.push(chunk);
  return parts.join("\r\n ");
}

const icsDate = (iso) => iso.replace(/-/g, "");

/**
 * The calendar: one event per held credential with a cycle start recorded.
 * Returns {text, events, skipped}; skipped lists held credentials with no start.
 */
export async function cycleEndCalendar(led, rules, { asOf = E.todayISO(), stamp = new Date(), version = "" } = {}) {
  const events = [];
  const skipped = [];
  for (const s of await led.allCredentialState()) {
    const cred = rules.byId[s.credential];
    if (!cred || !Number(s.active ?? 1)) continue;
    if (!s.anchor_date) {
      skipped.push(cred.name);
      continue;
    }
    const [start, end] = E.cycleWindow(cred, s.anchor_date, asOf);
    const last = E.addDays(end, -1);
    const summary = `${cred.name}: cycle ends`;
    const desc = [
      `Last day of the ${cred.name} (${cred.body_name}) cycle ${start} to ${last}. Anything earned after this counts toward the next cycle.`,
      `Requirement: ${cred.cycle_total} ${cred.unit_label} for the cycle.`,
    ];
    if (cred.unverified_reasons().length) desc.push(`UNVERIFIED: some of ${cred.name}'s rules could not be confirmed against a primary source. Check the date in the body's portal.`);
    desc.push("Computed by Registrar from your recorded cycle start and bodies.yaml. The body's portal is authoritative.");
    events.push({ uid: await uuid5([cred.id, "cycle_end", last, summary].join("|")), date: last, summary, description: desc.join("\n"), name: cred.name });
  }
  const dtstamp = stamp.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", `PRODID:-//ceu.tools//Registrar browser ${version}//EN`, "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
    "NAME:Registrar cycle ends", "X-WR-CALNAME:Registrar cycle ends"];
  for (const e of events) {
    lines.push("BEGIN:VEVENT", `UID:${e.uid}`, `DTSTAMP:${dtstamp}`, `DTSTART;VALUE=DATE:${icsDate(e.date)}`, `DTEND;VALUE=DATE:${icsDate(E.addDays(e.date, 1))}`,
      `SUMMARY:${icsText(e.summary)}`, `DESCRIPTION:${icsText(e.description)}`, `CATEGORIES:Registrar,${icsText(e.name)},cycle_end`, "TRANSP:TRANSPARENT");
    for (const d of ALARM_DAYS) lines.push("BEGIN:VALARM", "ACTION:DISPLAY", `DESCRIPTION:${icsText(`${e.summary} in ${d} days`)}`, `TRIGGER:-P${d}D`, "END:VALARM");
    lines.push("END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return { text: lines.map((l) => `${icsFold(l)}\r\n`).join(""), events, skipped };
}

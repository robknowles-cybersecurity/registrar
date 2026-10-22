// Sample data: three credentials and nine activities with synthetic evidence,
// so a first-time visitor can see what Registrar does before typing anything.
//
// Everything here is invented and says so: titles start "SAMPLE —", the
// providers are "Example ..." or "Placeholder ...", the certificate PDFs are
// generated on the spot and carry "SAMPLE CERTIFICATE - NOT A REAL RECORD".
// Sample rows are marked capture_source "fixture" and capture_ref
// SAMPLE_REF, and "Remove sample data" deletes exactly those rows (and any
// correction of them) and the evidence attached to them. The sample records
// each credential setting it wrote, as written; removing it undoes only the
// settings that still read exactly that way. A setting you changed after
// loading is kept. Your own activities are never touched.
//
// Dates are relative to the day you load the sample, so the activities always
// fall inside the current cycle.
import { CAPTURED, CONFIRMED } from "./ledger.js";
import { addDays, addMonths, todayISO } from "./engine.js";

export const SAMPLE_REF = "registrar-sample";
export const SAMPLE_CONFIG_KEY = "sample_data";

/** The credentials the sample holds, and how long ago each cycle began. */
export const SAMPLE_CREDENTIALS = [
  { id: "cissp", monthsAgo: 14 },
  { id: "cysa_plus", monthsAgo: 16 },
  { id: "cfe", monthsAgo: 8 },
];

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** The nine activities. daysAgo is from the day the sample is loaded. */
export const SAMPLE_ACTIVITIES = [
  { daysAgo: 360, title: "Zero Trust Architecture in Practice", provider: "Example Security Institute", hours: 1.5, kind: "webinar", tags: ["training"], markers: ["domain_security"], submitted: "isc2" },
  { daysAgo: 300, title: "Regional Security Summit", provider: "Example Conference Group", hours: 14, kind: "two-day conference", tags: ["conference"], markers: ["domain_security"] },
  { daysAgo: 240, title: "Cloud Incident Response Workshop", provider: "Placeholder Training Academy", hours: 8, kind: "workshop", tags: ["training"], markers: ["domain_security"] },
  { daysAgo: 200, title: "Leading Technical Teams", provider: "Example Leadership School", hours: 20, kind: "course", tags: ["professional_dev"], markers: [] },
  { daysAgo: 150, title: "Project Management Fundamentals", provider: "Placeholder Business College", hours: 16, kind: "course", tags: ["professional_dev"], markers: [] },
  { daysAgo: 100, title: "Threat Hunting with Packet Analysis", provider: "Placeholder Training Academy", hours: 6, kind: "lab course", tags: ["training"], markers: ["domain_security"] },
  { daysAgo: 60, title: "Detection Engineering Meetup", provider: "Example Security Chapter", hours: 2, kind: "meetup", tags: ["conference"], markers: ["domain_security"], noEvidence: true },
  { daysAgo: 45, title: "Fraud Risk and Professional Ethics", provider: "Example Fraud Institute", hours: 4, kind: "course", tags: ["training"], markers: ["fraud", "ethics"] },
  { daysAgo: 12, title: "Secure Code Review Lab", provider: "Example Security Institute", hours: 3, kind: "lab", tags: ["training"], markers: ["domain_security"], captured: true },
];

// ------------------------------------------------------------ a tiny PDF writer

function pdfString(s) {
  return `(${String(s).replace(/[^\x20-\x7e]/g, "?").replace(/[()\\]/g, (c) => `\\${c}`)})`;
}

/**
 * A one-page text PDF (Helvetica, US Letter). lines: [[x | "c", y, size, bold, text]].
 * Enough for pdf.js and the certificate parser; no dependency.
 */
export function textPdf(lines) {
  const W = 612;
  let content = "";
  for (const [x, y, size, bold, text] of lines) {
    const xx = x === "c" ? (W - String(text).length * size * 0.5) / 2 : x;
    content += `BT /${bold ? "F2" : "F1"} ${size} Tf ${xx.toFixed(1)} ${y} Td ${pdfString(text)} Tj ET\n`;
  }
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${W} 792] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    `<< /Length ${content.length} >>\nstream\n${content}endstream`,
  ];
  let out = "%PDF-1.4\n";
  const offsets = [];
  objs.forEach((body, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R /Info << /Producer (Registrar sample data) >> >>\nstartxref\n${xref}\n%%EOF\n`;
  return new TextEncoder().encode(out); // ASCII only, so one byte per character
}

function longDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

/** The synthetic certificate for one sample activity. */
export function sampleCertificate(a, date, holder = "Jordan Sample") {
  return textPdf([
    ["c", 740, 10, true, "SAMPLE CERTIFICATE - NOT A REAL RECORD"],
    ["c", 640, 26, true, "Certificate of Completion"],
    ["c", 596, 13, false, "This certifies that"],
    ["c", 566, 20, true, holder],
    ["c", 530, 13, false, `has completed the ${a.kind}`],
    ["c", 506, 15, true, `"${a.title}"`],
    ["c", 476, 13, false, `on ${longDate(date)}`],
    ["c", 452, 13, false, `and is awarded ${a.hours} contact hours of continuing education.`],
    ["c", 380, 12, true, a.provider],
    ["c", 362, 10, false, "Provider of continuing professional education"],
  ]);
}

// ------------------------------------------------------------ load / remove

export async function sampleState(led) {
  const raw = await led.getConfig(SAMPLE_CONFIG_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** Whether there is sample data to remove: the record of a load, or sample rows (an import brings rows, never the record). */
export async function sampleLoaded(led) {
  if (await sampleState(led)) return true;
  return (await led.store.getAll("entries")).some((e) => e.capture_ref === SAMPLE_REF);
}

/** Seed the sample. Refuses if it is already loaded. Returns what it added. */
export async function loadSample(led, { today = todayISO() } = {}) {
  if (await sampleState(led)) throw new Error("the sample data is already loaded");
  // wrote: the whole row as the sample left it. before: the fields it
  // changed, as they were (null when the sample created the row).
  const settings = [];
  for (const c of SAMPLE_CREDENTIALS) {
    const st = await led.getCredentialState(c.id);
    const anchor = addMonths(today, -c.monthsAgo);
    let fields = null;
    if (!st) fields = { anchor_date: anchor, carryover_in: 0, active: 1, note: "sample data" };
    else if (!st.anchor_date) fields = { anchor_date: anchor, active: 1 };
    if (!fields) continue;
    const before = st ? Object.fromEntries(Object.keys(fields).map((k) => [k, st[k] ?? null])) : null;
    const wrote = rowOf(await led.setCredentialState(c.id, fields));
    settings.push({ credential: c.id, before, wrote });
  }
  let files = 0;
  for (const a of SAMPLE_ACTIVITIES) {
    const date = addDays(today, -a.daysAgo);
    const uuid = await led.addEntry({
      activity_date: date,
      title: `SAMPLE — ${a.title}`,
      provider: a.provider,
      hours: a.hours,
      tags: a.tags,
      markers: a.markers,
      notes: "Sample data: invented, not a real record. Remove it on the Credentials screen.",
      capture_source: "fixture",
      capture_ref: SAMPLE_REF,
      status: a.captured ? CAPTURED : CONFIRMED,
      submitted_to: a.submitted ? [a.submitted] : [],
      submitted_dates: a.submitted ? { [a.submitted]: addDays(date, 7) } : {},
    });
    if (!a.noEvidence) {
      const slug = a.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      await led.addEvidence(uuid, `sample-${slug}.pdf`, sampleCertificate(a, date), "application/pdf", "sample data");
      files += 1;
    }
  }
  await led.setConfig(SAMPLE_CONFIG_KEY, JSON.stringify({ loaded_on: today, settings }));
  return { activities: SAMPLE_ACTIVITIES.length, files, credentials: settings.length };
}

const STATE_FIELDS = ["anchor_date", "carryover_in", "portal_credits", "portal_checked", "active", "note"];

function rowOf(st) {
  return Object.fromEntries(STATE_FIELDS.map((k) => [k, st[k] ?? null]));
}

function sameRow(a, b, fields = STATE_FIELDS) {
  return fields.every((k) => (a[k] ?? null) === (b[k] ?? null));
}

// A sample loaded by v1.2.1 or earlier recorded only which credentials it
// created or gave a start date. What it wrote then is known from loaded_on;
// for a start date it filled in, only the two fields it set are compared.
function settingsOf(state) {
  if (!state) return [];
  if (Array.isArray(state.settings)) return state.settings;
  const months = Object.fromEntries(SAMPLE_CREDENTIALS.map((c) => [c.id, c.monthsAgo]));
  const anchor = (id) => (months[id] != null && state.loaded_on ? addMonths(state.loaded_on, -months[id]) : undefined);
  return [
    ...(state.created || []).map((id) => ({ credential: id, before: null, wrote: { anchor_date: anchor(id), carryover_in: 0, portal_credits: null, portal_checked: null, active: 1, note: "sample data" } })),
    ...(state.anchored || []).map((id) => ({ credential: id, before: { anchor_date: null }, wrote: { anchor_date: anchor(id), active: 1 }, fields: ["anchor_date", "active"] })),
  ];
}

/**
 * Remove every sample row: entries carrying SAMPLE_REF (corrections of them
 * carry it too), their evidence rows, files no other evidence uses, and the
 * credential settings the sample wrote, but only those still exactly as it
 * wrote them: a created row is deleted, a filled-in start date is put back
 * the way it was. A setting changed since is kept and counted in `kept`.
 * Nothing else is touched.
 */
export async function removeSample(led) {
  const state = await sampleState(led);
  const entries = (await led.store.getAll("entries")).filter((e) => e.capture_ref === SAMPLE_REF);
  const gone = new Set(entries.map((e) => e.uuid));
  const evidence = await led.store.getAll("evidence");
  const dropped = evidence.filter((r) => gone.has(r.entry_uuid));
  for (const r of dropped) await led.store.delete("evidence", r.id);
  const stillUsed = new Set(evidence.filter((r) => !gone.has(r.entry_uuid)).map((r) => r.sha256));
  for (const sha of new Set(dropped.map((r) => r.sha256))) if (!stillUsed.has(sha)) await led.store.delete("files", sha);
  for (const uuid of gone) await led.store.delete("entries", uuid);
  let kept = 0;
  for (const s of settingsOf(state)) {
    const cur = await led.getCredentialState(s.credential);
    if (!cur) continue;
    if (!sameRow(cur, s.wrote, s.fields)) {
      kept += 1;
      continue;
    }
    if (s.before) await led.setCredentialState(s.credential, s.before);
    else await led.store.delete("credential_state", s.credential);
  }
  await led.store.delete("config", SAMPLE_CONFIG_KEY);
  return { activities: gone.size, files: dropped.length, kept };
}

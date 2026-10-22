// Submission packets: a zip per certifying body with a cover sheet and the
// evidence. Field layouts come from packetmaps/ (the same files the Python app
// uses, exported into data.json), resolved the same way as the Python packets.py.
//
// Registrar never signs in to a body's portal. The cover sheet lists each
// activity's fields in the form's order, ready to copy; you submit yourself.
import * as E from "./engine.js";
import { makeZip } from "./zip.js";
import { blobBytes, sha256Hex } from "./ledger.js";

const GENERIC = "generic";

export function mapFor(cred, maps) {
  for (const m of Object.values(maps)) if ((m.credentials || []).includes(cred.id)) return m;
  for (const m of Object.values(maps)) {
    if (m.id !== GENERIC && m.body_id === cred.body_id && !(m.credentials || []).length) return m;
  }
  return maps[GENERIC];
}

function strftime(iso, fmt) {
  const [y, m, d] = iso.split("-");
  return String(fmt || "%Y-%m-%d").replace(/%Y/g, y).replace(/%m/g, m).replace(/%d/g, d);
}

function n(v) {
  const s = Number(v).toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  return s === "-0" ? "0" : s;
}

/** The evidence file's name inside the zip: one flat name, no path separators, no "..". */
export function bundleName(entry, ev) {
  const name = String(ev.filename || "file").replace(/\.\.+/g, "").replace(/[\\/]+/g, "_") || "file";
  return `${entry.activity_date}_${entry.uuid.slice(0, 8)}_${name}`;
}

/** One packet-map field's value for one entry (Python packets._resolve). */
export function resolveField(field, ctx, pmap) {
  const { entry: e, cred, bucket, groups, rules } = ctx;
  const src = field.source;
  let raw;
  switch (src) {
    case "literal": raw = field.value; break;
    case "holder_choice": return "";
    case "entry.activity_date": raw = strftime(e.activity_date, field.format || pmap.date_format); break;
    case "entry.title": raw = e.title; break;
    case "entry.provider": raw = e.provider; break;
    case "entry.hours": raw = e.hours ? n(e.hours) : ""; break;
    case "entry.count": raw = String(e.count); break;
    case "entry.notes": raw = e.notes || ""; break;
    case "entry.uuid": raw = e.uuid; break;
    case "entry.tags": raw = [...e.tags]; break;
    case "entry.tag_labels": raw = e.tags.map((t) => rules.tag_labels[t] || t); break;
    case "entry.markers": raw = [...e.markers]; break;
    case "entry.marker_labels": raw = e.markers.map((m) => rules.marker_labels[m] || m); break;
    case "credit.amount": raw = n(ctx.credits); break;
    case "credit.unit": raw = cred.unit_label; break;
    case "bucket.id": raw = bucket.id; break;
    case "bucket.name": raw = bucket.name || bucket.id; break;
    case "group.id": raw = [...groups]; break;
    case "group.name": raw = groups.map((g) => (cred.group(g) || {}).name || g); break;
    case "holder.name": raw = ctx.holder || ""; break;
    case "credential.name": raw = cred.name; break;
    case "body.name": raw = cred.body_name; break;
    case "evidence.filenames": raw = ctx.evidence.map((ev) => bundleName(e, ev)); break;
    case "evidence.count": raw = String(ctx.evidence.length); break;
    default: throw new Error(`unknown packet-map source '${src}'`);
  }
  const table = field.map;
  let values = Array.isArray(raw) ? raw : [raw];
  if (table != null) values = values.map((v) => (v in table ? table[v] : field.default ?? v));
  values = values.filter((v) => v != null && v !== "").map(String);
  let value = values.join(", ");
  if (!value && field.default != null && table == null) value = String(field.default);
  return value;
}

function groupsOf(cred, bucket, markers) {
  // Mirrors engine group assignment, for the cover sheet's Group column.
  if (!cred.groups.length) return [];
  if (cred.buckets.some((b) => b.group)) return bucket.group ? [bucket.group] : [];
  const map = cred.group_marker_map || {};
  const ms = new Set(markers || []);
  const partition = Object.values(map).some((v) => v == null);
  if (!partition) return Object.entries(map).filter(([, m]) => m && ms.has(m)).map(([g]) => g);
  let residual = null;
  for (const [g, m] of Object.entries(map)) {
    if (m == null) residual = g;
    else if (ms.has(m)) return [g];
  }
  return residual ? [residual] : [];
}

/**
 * Everything a body's packet needs: for each tracked credential of the body,
 * its cycle, its assessment and the per-entry form fields.
 */
export async function bodyPacket(led, rules, bodyId, { asOf = E.todayISO(), unsubmittedOnly = false } = {}) {
  const holder = (await led.getConfig("attendee_name")) || "";
  const states = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  const all = await led.listEntries({ status: "confirmed" });
  const evidence = await led.listEvidence();
  const engineEntries = await led.engineEntries();
  const creds = rules.credentials.filter((c) => c.body_id === bodyId && states[c.id] && states[c.id].anchor_date && Number(states[c.id].active ?? 1));
  const sections = [];
  for (const cred of creds) {
    const st = states[cred.id];
    const a = E.assess(cred, engineEntries, { anchor: st.anchor_date, asOf, carryoverIn: st.carryover_in || 0 });
    const pmap = mapFor(cred, rules.packetmaps);
    const rows = [];
    for (const e of [...all].sort((x, y) => (x.activity_date + x.created_at).localeCompare(y.activity_date + y.created_at))) {
      if (!(a.cycle_start <= e.activity_date && e.activity_date < a.cycle_end)) continue;
      if (e.applies_to != null && !e.applies_to.includes(cred.id)) continue;
      const m = cred.buckets_for(e.tags, e.markers);
      if (!m.length) continue;
      if (unsubmittedOnly && (e.submitted_to || []).includes(cred.body_id)) continue;
      const ev = evidence.filter((r) => r.entry_uuid === e.uuid);
      const ctx = { entry: e, cred, bucket: m[0], groups: groupsOf(cred, m[0], e.markers), rules, holder, evidence: ev, credits: E.creditsForEntry(cred, e) };
      rows.push({ entry: e, evidence: ev, fields: pmap.fields.map((f) => ({ label: f.label, value: resolveField(f, ctx, pmap), required: Boolean(f.required), note: f.note || "" })) });
    }
    sections.push({ cred, assessment: a, pmap, rows });
  }
  const bodyName = (rules.bodies().find((b) => b.id === bodyId) || { name: bodyId }).name;
  return { bodyId, bodyName, holder, asOf, sections };
}

export function coverSheet(p) {
  const L = [];
  L.push(`${p.bodyName}: submission packet`);
  L.push("=".repeat(L[0].length));
  L.push(`Prepared ${p.asOf} by Registrar (https://ceu.tools) for ${p.holder || "(holder name not set: Settings)"}.`);
  L.push("");
  L.push("Registrar never asks for, stores or uses your portal login. Sign in to");
  L.push(`${p.bodyName}'s portal yourself and copy each field below into its form.`);
  L.push("The evidence files are in evidence/ in this zip; manifest.json lists");
  L.push("each file's SHA-256, checked when this packet was built.");
  if (!p.sections.length) {
    L.push("");
    L.push("No tracked credential for this body has a cycle start date. Set one in Settings.");
  }
  for (const s of p.sections) {
    const a = s.assessment;
    L.push("");
    L.push(`## ${s.cred.name} (${s.cred.long_name || s.cred.name})`);
    L.push(`Cycle ${a.cycle_start} to ${a.cycle_end} (grace to ${a.grace_end}).`);
    L.push(`Counted ${n(a.total_with_carryover)} of ${n(a.required)} ${s.cred.unit_label}; naive sum ${n(a.naive_total)}; forfeited to caps ${n(a.forfeited)}. Verdict: ${a.verdict}.`);
    for (const g of a.groups) L.push(`  ${g.name}: ${n(g.awarded)}${g.floor ? ` (floor ${n(g.floor)}${g.shortfall ? `, SHORT BY ${n(g.shortfall)}` : ""})` : ""}${g.cap != null ? ` (cap ${n(g.cap)})` : ""}`);
    for (const r of s.cred.unverified_reasons()) L.push(`  UNVERIFIED: ${r}`);
    L.push(`Form layout: ${s.pmap.form_name}${s.pmap.verified ? "" : " (UNVERIFIED layout: " + (s.pmap.verified_note || "labels not confirmed against the live form") + ")"}.`);
    if (s.pmap.holder_note) L.push(s.pmap.holder_note);
    if (!s.rows.length) L.push("No confirmed activities in this cycle.");
    s.rows.forEach((r, i) => {
      L.push("");
      L.push(`### ${i + 1}. ${r.entry.activity_date}  ${r.entry.title}`);
      for (const f of r.fields) L.push(`  ${f.label}: ${f.value || (f.required ? "(required — choose in portal)" : "—")}`);
      if (!r.evidence.length) L.push("  Evidence: NONE ATTACHED");
    });
  }
  L.push("");
  return L.join("\n");
}

/** The packet as a zip: COVER.txt, manifest.json and evidence/<files>. */
export async function packetZip(led, packet) {
  const files = [{ name: "COVER.txt", data: coverSheet(packet) }];
  const manifest = { body: packet.bodyId, prepared: packet.asOf, holder: packet.holder, files: [], gaps: [] };
  const seen = new Set();
  for (const s of packet.sections) {
    for (const r of s.rows) {
      if (!r.evidence.length) manifest.gaps.push({ entry: r.entry.uuid, credential: s.cred.id, problem: "no evidence attached" });
      for (const ev of r.evidence) {
        const name = `evidence/${bundleName(r.entry, ev)}`;
        if (seen.has(name)) continue;
        seen.add(name);
        const blob = await led.evidenceFile(ev);
        if (!blob) {
          manifest.gaps.push({ entry: r.entry.uuid, file: ev.filename, problem: "file not in this browser" });
          continue;
        }
        const bytes = new Uint8Array(await blobBytes(blob));
        if ((await sha256Hex(bytes)) !== ev.sha256) {
          manifest.gaps.push({ entry: r.entry.uuid, file: ev.filename, problem: "file no longer matches its recorded SHA-256; left out" });
          continue;
        }
        files.push({ name, data: bytes });
        manifest.files.push({ path: name, sha256: ev.sha256, bytes: bytes.length, entry: r.entry.uuid, credential: s.cred.id });
      }
    }
  }
  files.push({ name: "manifest.json", data: JSON.stringify(manifest, null, 1) + "\n" });
  return { zip: makeZip(files), manifest };
}

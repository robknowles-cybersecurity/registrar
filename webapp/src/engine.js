// The cap-and-floor-aware credit engine: a line-by-line port of
// the Python engine.py. Same rules data (bodies.yaml, exported to data.json), same
// arithmetic, same verdicts. tests/engine_parity.test.mjs runs both engines on
// the same entries and fails on any difference, so a change here or there that
// is not mirrored on the other side cannot pass unnoticed.
//
// Dates are "YYYY-MM-DD" strings throughout; they compare correctly as strings.

export const COMPLIANT = "COMPLIANT";
export const ON_PACE = "ON_PACE";
export const BEHIND = "BEHIND";
export const SHORT = "SHORT";
export const UNVERIFIED_CANNOT_ASSERT = "UNVERIFIED_CANNOT_ASSERT";

const GROUP_NONE = "none";
const GROUP_BUCKET = "bucket";
const GROUP_MARKER_PARTITION = "marker_partition";
const GROUP_MARKER_OVERLAP = "marker_overlap";

export class EngineError extends Error {}

// ---------------------------------------------------------------- dates

const DAY_MS = 86400000;

function ymd(y, m, d) {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

function parts(s) {
  const [y, m, d] = String(s).slice(0, 10).split("-").map(Number);
  return [y, m, d];
}

export function isRealDate(s) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(s))) return false;
  const [y, m, d] = parts(s);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

export function todayISO(now = new Date()) {
  return ymd(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

function daysInMonth(y, m) {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** Date `months` later, clamped to the end of a short month. */
export function addMonths(s, months) {
  const [y0, m0, d0] = parts(s);
  const total = y0 * 12 + (m0 - 1) + months;
  const y = Math.floor(total / 12);
  const m = (total % 12) + 1;
  return ymd(y, m, Math.min(d0, daysInMonth(y, m)));
}

export function addDays(s, days) {
  const [y, m, d] = parts(s);
  const t = new Date(Date.UTC(y, m - 1, d) + days * DAY_MS);
  return ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
}

export function daysBetween(a, b) {
  const [y1, m1, d1] = parts(a);
  const [y2, m2, d2] = parts(b);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / DAY_MS);
}

function yearOf(s) {
  return parts(s)[0];
}

/**
 * Python's round(x, n): decided on the float's exact decimal value, ties to
 * even. Math.round(x * 100) / 100 differs on exact ties such as 37.625, and
 * the parity test catches that.
 */
export function pyRound(x, n) {
  if (!Number.isFinite(x)) return x;
  const neg = x < 0;
  const exact = Math.abs(x).toFixed(70); // exact for any double in this domain
  const [ip, fp] = exact.split(".");
  const keep = fp.slice(0, n);
  const rest = fp.slice(n);
  let digits = (ip + keep).replace(/^0+(?=\d)/, "");
  const first = rest[0];
  const tail = rest.slice(1);
  let up = false;
  if (first > "5") up = true;
  else if (first === "5") up = /[1-9]/.test(tail) || Number(digits[digits.length - 1]) % 2 === 1;
  let v = BigInt(digits) + (up ? 1n : 0n);
  const out = Number(v) / 10 ** n;
  return neg ? -out : out;
}

export function round4(x) {
  return pyRound(x, 4);
}

function round2(x) {
  return pyRound(x, 2);
}

// ---------------------------------------------------------------- rules

/** Wrap one exported credential with the helpers engine.py's Credential has. */
export function credential(raw, rules) {
  const buckets = (raw.buckets || []).map((b) => ({
    id: b.id,
    name: b.name,
    group: b.group ?? null,
    per_hour: b.per_hour ?? null,
    per_item: b.per_item ?? null,
    cap: b.cap ?? null,
    cap_note: b.cap_note,
    cap_verified: b.cap_verified,
    tags: b.tags || [],
    requires_markers: b.requires_markers || [],
    value_is_override: Boolean(b.value_is_override),
  }));
  const c = {
    raw,
    rules,
    id: raw.id,
    name: raw.name,
    long_name: raw.long_name,
    body_id: raw.body_id,
    body_name: raw.body_name,
    unit_label: raw.unit_label || "credit",
    unit_note: raw.unit_note,
    verified: Boolean(raw.verified),
    source_url: raw.source_url,
    verified_date: raw.verified_date,
    verification_note: raw.verification_note,
    caps_known: raw.caps_known,
    caps_unknown_note: raw.caps_unknown_note,
    cycle: raw.cycle || {},
    requirements: raw.requirements || {},
    groups: raw.groups || [],
    buckets,
    group_marker_map: raw.group_marker_map || {},
    carryover: raw.carryover || {},
    submission: raw.submission || {},
    grace_period_days: raw.grace_period_days || 0,
    grace_note: raw.grace_note,
    fees: raw.fees || [],
  };
  c.cycle_months = Number(c.cycle.length_months || 12);
  c.cycle_total = Number(c.requirements.cycle_total || 0);
  c.annual_minimum = c.requirements.annual_minimum == null ? null : Number(c.requirements.annual_minimum);
  c.annual_target = c.requirements.annual_target == null ? null : Number(c.requirements.annual_target);
  c.group = (gid) => c.groups.find((g) => g.id === gid) || null;
  c.bucket = (bid) => c.buckets.find((b) => b.id === bid) || null;
  c.buckets_for = (tags, markers) => bucketsFor(c, tags, markers);
  c.unverified_reasons = () => unverifiedReasons(c);
  c.unverified_value = (kind, bucket) => unverifiedValue(c, kind, bucket);
  return c;
}

/** Load the exported data.json into the shape the app and engine use. */
export function loadRules(data) {
  const rules = {
    data,
    sweep_date: data.sweep_date,
    tags: (data.tag_vocabulary || []).map((t) => t.id),
    tag_labels: Object.fromEntries((data.tag_vocabulary || []).map((t) => [t.id, t.label])),
    markers: (data.topic_markers || []).map((m) => m.id),
    marker_labels: Object.fromEntries((data.topic_markers || []).map((m) => [m.id, m.label])),
    programs: data.programs || [],
    packetmaps: data.packetmaps || {},
  };
  rules.credentials = (data.credentials || []).map((c) => credential(c, rules));
  rules.byId = Object.fromEntries(rules.credentials.map((c) => [c.id, c]));
  rules.get = (id) => {
    const c = rules.byId[id];
    if (!c) throw new EngineError(`no such credential: ${id}`);
    return c;
  };
  rules.bodies = () => {
    const seen = [];
    for (const c of rules.credentials) {
      if (!seen.some((b) => b.id === c.body_id)) seen.push({ id: c.body_id, name: c.body_name });
    }
    return seen;
  };
  return rules;
}

function bucketsFor(cred, tags, markers) {
  const t = new Set(tags || []);
  const m = new Set(markers || []);
  const out = [];
  for (const b of cred.buckets) {
    if (!b.tags.some((x) => t.has(x))) continue;
    const need = b.requires_markers || [];
    if (need.length && !need.every((x) => m.has(x))) continue;
    out.push(b);
  }
  return out;
}

export function creditsFor(bucket, hours, count = 1, override = undefined) {
  if (override !== undefined && override !== null) return Number(override);
  if (bucket.value_is_override) return 0;
  if (bucket.per_hour != null) return Number(hours || 0) * Number(bucket.per_hour);
  if (bucket.per_item != null) return Number(count || 1) * Number(bucket.per_item);
  return 0;
}

function unverifiedReasons(c) {
  const reasons = [];
  if (!c.verified) reasons.push(`credential-level rules UNVERIFIED (${c.verification_note || "no note"})`);
  if (c.caps_known === false) reasons.push(`per-category caps NOT KNOWN (${c.caps_unknown_note || "no note"})`);
  for (const b of c.buckets) {
    if (b.cap_verified === false) reasons.push(`bucket '${b.id}' cap UNVERIFIED (${b.cap_note || "no note"})`);
  }
  if (c.carryover && c.carryover.verified === false) {
    reasons.push(`carryover rule UNVERIFIED (${c.carryover.verified_note || c.carryover.window_note || "no note"})`);
  }
  return reasons;
}

function unverifiedValue(c, kind = "rules", bucket = null) {
  let note = null;
  if (!c.verified) note = c.verification_note || "credential-level rules UNVERIFIED";
  else if (kind === "cap" && bucket && bucket.cap_verified === false) note = bucket.cap_note || "category cap UNVERIFIED";
  else if (kind === "cap" && c.caps_known === false) note = c.caps_unknown_note || "category caps NOT KNOWN";
  else if (kind === "carryover" && c.carryover && c.carryover.verified === false) {
    note = c.carryover.verified_note || "carryover rule UNVERIFIED";
  }
  return note == null ? null : { note, source_url: c.source_url };
}

// ---------------------------------------------------------------- cycles

export function cycleWindow(cred, anchor, asOf) {
  if (!anchor) throw new EngineError(`${cred.id}: no cycle anchor date`);
  asOf = asOf || todayISO();
  const basis = String(cred.cycle.basis || "certification_anniversary").toLowerCase();
  const months = cred.cycle_months;
  if (basis === "calendar_year" && months === 12) {
    const y = yearOf(asOf);
    return [ymd(y, 1, 1), ymd(y + 1, 1, 1)];
  }
  let start = String(anchor).slice(0, 10);
  if (asOf < start) return [start, addMonths(start, months)];
  for (;;) {
    const end = addMonths(start, months);
    if (asOf < end) return [start, end];
    start = end;
  }
}

export function normalize(entry) {
  return {
    id: entry.id ?? entry.uuid ?? null,
    date: entry.date || entry.activity_date ? String(entry.date || entry.activity_date).slice(0, 10) : null,
    title: entry.title || "",
    provider: entry.provider || "",
    hours: Number(entry.hours || 0),
    count: parseInt(entry.count || 1, 10) || 1,
    tags: [...(entry.tags || [])],
    markers: [...(entry.markers || [])],
    overrides: { ...(entry.overrides || {}) },
    applies_to: entry.applies_to ?? null,
    status: entry.status || "confirmed",
    submitted_to: [...(entry.submitted_to || [])],
    evidence_count: parseInt(entry.evidence_count || 0, 10) || 0,
  };
}

function groupMode(cred) {
  if (!cred.groups.length) return GROUP_NONE;
  if (cred.buckets.some((b) => b.group)) return GROUP_BUCKET;
  const map = cred.group_marker_map;
  if (Object.keys(map).length) {
    if (Object.values(map).some((v) => v == null)) return GROUP_MARKER_PARTITION;
    return GROUP_MARKER_OVERLAP;
  }
  return GROUP_NONE;
}

function groupsFor(cred, mode, bucket, markers) {
  if (mode === GROUP_NONE) return [];
  if (mode === GROUP_BUCKET) return bucket.group ? [bucket.group] : [];
  const m = new Set(markers || []);
  const map = cred.group_marker_map;
  if (mode === GROUP_MARKER_OVERLAP) {
    return Object.entries(map).filter(([, marker]) => marker && m.has(marker)).map(([gid]) => gid);
  }
  let residual = null;
  for (const [gid, marker] of Object.entries(map)) {
    if (marker == null) {
      residual = gid;
      continue;
    }
    if (m.has(marker)) return [gid];
  }
  return residual ? [residual] : [];
}

// ---------------------------------------------------------------- assessment

export function assess(cred, entries, { anchor = null, asOf = null, cycleStart = null, carryoverIn = 0, countPending = false } = {}) {
  asOf = asOf || todayISO();
  const a = {
    credential: cred.id,
    name: cred.name,
    body: cred.body_name,
    unit_label: cred.unit_label,
    cycle_start: null,
    cycle_end: null,
    grace_end: null,
    as_of: asOf,
    required: cred.cycle_total,
    awarded: 0,
    naive_total: 0,
    forfeited: 0,
    carryover_in: Number(carryoverIn || 0),
    carryover_out: 0,
    buckets: [],
    groups: [],
    unmet_floors: [],
    annual: [],
    submission: {},
    fees: [...cred.fees],
    verdict: null,
    advisory_only: false,
    unverified_reasons: cred.unverified_reasons(),
    entries_counted: 0,
    entries_ignored: 0,
    entries_pending: 0,
    notes: [],
  };
  let start, end;
  if (cycleStart) {
    start = String(cycleStart).slice(0, 10);
    end = addMonths(start, cred.cycle_months);
  } else {
    [start, end] = cycleWindow(cred, anchor, asOf);
  }
  a.cycle_start = start;
  a.cycle_end = end;
  a.grace_end = addDays(end, Number(cred.grace_period_days || 0));

  const mode = groupMode(cred);
  a.notes.push(`group semantics: ${mode}`);

  const rawByBucket = {};
  const awards = [];
  for (const row of entries) {
    const e = normalize(row);
    if (e.status !== "confirmed") {
      if (e.status === "captured" || e.status === "pending") {
        a.entries_pending += 1;
        if (!countPending) continue;
      } else {
        a.entries_ignored += 1;
        continue;
      }
    }
    if (e.date == null || !(start <= e.date && e.date < end)) continue;
    if (e.applies_to != null && !e.applies_to.includes(cred.id)) continue;
    const matches = cred.buckets_for(e.tags, e.markers);
    if (!matches.length) {
      a.entries_ignored += 1;
      continue;
    }
    const bucket = matches[0];
    const credits = creditsFor(bucket, e.hours, e.count, e.overrides[cred.id]);
    if (bucket.value_is_override && !(cred.id in e.overrides)) {
      a.notes.push(`entry ${e.id} matched bucket '${bucket.id}', which has no computable rate and no override — counted as 0`);
    }
    rawByBucket[bucket.id] = (rawByBucket[bucket.id] || 0) + credits;
    awards.push([bucket, credits, e]);
    a.entries_counted += 1;
  }

  const keptRatio = {};
  for (const bucket of cred.buckets) {
    const raw = rawByBucket[bucket.id] || 0;
    const cap = bucket.cap == null ? null : Number(bucket.cap);
    const awarded = cap == null ? raw : Math.min(raw, cap);
    keptRatio[bucket.id] = raw > 0 ? awarded / raw : 1;
    if (raw || awarded) {
      a.buckets.push({
        id: bucket.id,
        name: bucket.name || bucket.id,
        raw: round4(raw),
        awarded: round4(awarded),
        forfeited: round4(raw - awarded),
        cap,
        cap_verified: bucket.cap_verified ?? null,
        group: bucket.group,
        at_cap: cap != null && raw >= cap,
      });
    }
  }
  const bucketAwardedTotal = a.buckets.reduce((s, b) => s + b.awarded, 0);
  a.naive_total = round4(awards.reduce((s, [, c]) => s + c, 0));

  const groupRaw = Object.fromEntries(cred.groups.map((g) => [g.id, 0]));
  for (const [bucket, credits, e] of awards) {
    const surviving = credits * (keptRatio[bucket.id] ?? 1);
    for (const gid of groupsFor(cred, mode, bucket, e.markers)) {
      if (gid in groupRaw) groupRaw[gid] += surviving;
    }
  }
  const partition = mode === GROUP_BUCKET || mode === GROUP_MARKER_PARTITION;
  let groupForfeit = 0;
  for (const g of cred.groups) {
    const raw = groupRaw[g.id] || 0;
    const cap = g.cap == null ? null : Number(g.cap);
    const floor = g.floor == null ? null : Number(g.floor);
    const awarded = cap == null ? raw : Math.min(raw, cap);
    if (cap != null && !partition) {
      throw new EngineError(`${cred.id}: group '${g.id}' has a cap under overlapping-marker semantics; the rules file needs a partition to make that well-defined`);
    }
    groupForfeit += raw - awarded;
    const row = {
      id: g.id,
      name: g.name || g.id,
      awarded: round4(awarded),
      raw: round4(raw),
      floor,
      cap,
      shortfall: floor ? round4(Math.max(0, floor - awarded)) : 0,
      at_cap: cap != null && raw >= cap,
    };
    a.groups.push(row);
    if (row.shortfall > 0) a.unmet_floors.push(g.id);
  }
  a.awarded = round4(bucketAwardedTotal - groupForfeit);
  a.forfeited = round4(a.naive_total - a.awarded);

  if (cred.annual_minimum != null || cred.annual_target != null) {
    const perYear = {};
    for (const [bucket, credits, e] of awards) {
      const y = yearOf(e.date);
      perYear[y] = (perYear[y] || 0) + credits * (keptRatio[bucket.id] ?? 1);
    }
    for (const y of Object.keys(perYear).map(Number).sort((p, q) => p - q)) {
      const need = cred.annual_minimum;
      a.annual.push({
        year: y,
        awarded: round4(perYear[y]),
        minimum: need,
        target: cred.annual_target,
        target_advisory: Boolean(cred.requirements.annual_target_advisory),
        met: need == null || perYear[y] >= need,
        year_complete: y < yearOf(asOf),
      });
    }
  }

  a.submission = submissionState(cred, awards, asOf);

  if (cred.carryover.allowed) {
    const cap = cred.carryover.max;
    const excess = Math.max(0, a.awarded + a.carryover_in - a.required);
    let out = cap == null ? excess : Math.min(excess, Number(cap));
    if (cred.carryover.conditional_on_floors && a.unmet_floors.length) {
      out = 0;
      a.notes.push("carryover withheld: floors unmet, and this body only lets you carry a surplus once the minimums are satisfied");
    }
    if (cred.carryover.verified === false) a.notes.push("carryover figure is UNVERIFIED — treat as advisory");
    a.carryover_out = round4(out);
  }

  a.gap = Math.max(0, a.required - (a.awarded + a.carryover_in));
  a.total_with_carryover = a.awarded + a.carryover_in;
  [a.verdict, a.advisory_only] = verdict(cred, a, asOf);
  return a;
}

function submissionState(cred, awards, asOf) {
  const sub = cred.submission || {};
  const cadence = sub.cadence || "continuous";
  const state = { cadence, self_certified: Boolean(sub.self_certified) };
  if (cadence !== "annual_window") {
    state.deadlines = [];
    return state;
  }
  const win = sub.window || {};
  const reg = win.register_by_mmdd || "12-31";
  const offset = parseInt(win.register_by_year_offset || 0, 10);
  const perYear = {};
  for (const [, credits, e] of awards) {
    const y = yearOf(e.date);
    perYear[y] = perYear[y] || { credits: 0, unsubmitted: 0 };
    perYear[y].credits += credits;
    if (!(e.submitted_to || []).includes(cred.body_id)) perYear[y].unsubmitted += 1;
  }
  const [mm, dd] = reg.split("-").map(Number);
  state.deadlines = Object.keys(perYear).map(Number).sort((p, q) => p - q).map((y) => {
    const due = ymd(y + offset, mm, dd);
    return {
      earned_year: y,
      credits: round4(perYear[y].credits),
      unsubmitted_entries: perYear[y].unsubmitted,
      register_by: due,
      days_left: daysBetween(asOf, due),
      overdue: asOf > due && perYear[y].unsubmitted > 0,
    };
  });
  return state;
}

export function pace(a) {
  const span = daysBetween(a.cycle_start, a.cycle_end);
  const elapsed = Math.max(0, Math.min(span, daysBetween(a.cycle_start, a.as_of)));
  const frac = span ? elapsed / span : 1;
  const expected = a.required * frac;
  const daysLeft = Math.max(0, daysBetween(a.as_of, a.cycle_end));
  const monthsLeft = daysLeft / 30.44;
  return {
    elapsed_days: elapsed,
    total_days: span,
    elapsed_fraction: round4(frac),
    expected_by_now: round2(expected),
    actual: a.total_with_carryover,
    delta: round2(a.total_with_carryover - expected),
    on_pace: a.total_with_carryover >= expected,
    days_left: daysLeft,
    gap: round2(a.gap),
    per_month_needed: monthsLeft >= 1 ? round2(a.gap / monthsLeft) : a.gap,
  };
}

function verdict(cred, a, asOf) {
  if (cred.caps_known === false) {
    a.notes.push(`verdict withheld: per-category caps for ${cred.name} are not encoded, so the totals below are an UPPER BOUND, not a compliance position`);
    return [UNVERIFIED_CANNOT_ASSERT, true];
  }
  const floorsMet = !a.unmet_floors.length;
  const annualMet = a.annual.filter((r) => r.year_complete).every((r) => r.met);
  const totalMet = a.total_with_carryover >= a.required;
  if (totalMet && floorsMet && annualMet) return [COMPLIANT, false];
  if (asOf >= a.cycle_end) return [SHORT, false];
  return [pace(a).on_pace && floorsMet ? ON_PACE : BEHIND, false];
}

/** Credits one entry claims for a credential before caps (packets, CSV). */
export function creditsForEntry(cred, entry) {
  const e = normalize(entry);
  const m = cred.buckets_for(e.tags, e.markers);
  if (!m.length) return 0;
  return creditsFor(m[0], e.hours, e.count, e.overrides[cred.id]);
}

export function naiveAssess(cred, entries, { anchor = null, asOf = null, cycleStart = null } = {}) {
  asOf = asOf || todayISO();
  let start, end;
  if (cycleStart) {
    start = String(cycleStart).slice(0, 10);
    end = addMonths(start, cred.cycle_months);
  } else {
    [start, end] = cycleWindow(cred, anchor, asOf);
  }
  let total = 0;
  for (const row of entries) {
    const e = normalize(row);
    if (e.status !== "confirmed" || e.date == null) continue;
    if (!(start <= e.date && e.date < end)) continue;
    if (e.applies_to != null && !e.applies_to.includes(cred.id)) continue;
    const m = cred.buckets_for(e.tags, e.markers);
    if (!m.length) continue;
    total += creditsFor(m[0], e.hours, e.count, e.overrides[cred.id]);
  }
  return round4(total);
}

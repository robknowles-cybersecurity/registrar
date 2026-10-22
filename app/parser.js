// Heuristic certificate parser. Text in, a pre-filled activity form out, with
// a confidence per field. It never saves anything: the form shows each guess
// and its confidence, and a person confirms or corrects it.
//
// Confidence: "high" (an explicit label such as "Completion Date:" or "CPE
// credits:" next to the value), "medium" (a recognisable phrase or a single
// unambiguous candidate), "low" (a fallback guess), or null (nothing found).
// Each field also carries `why`, the phrase that produced it, so the form can
// show where a value came from.

const MONTHS = {
  jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3, apr: 4, april: 4, may: 5,
  jun: 6, june: 6, jul: 7, july: 7, aug: 8, august: 8, sep: 9, sept: 9, september: 9,
  oct: 10, october: 10, nov: 11, november: 11, dec: 12, december: 12,
};
const MON = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\\.?";
const ORD = "(?:st|nd|rd|th)?";

const CONF_RANK = { high: 3, medium: 2, low: 1 };

function iso(y, m, d) {
  y = Number(y);
  m = Number(m);
  d = Number(d);
  if (y < 100) y += 2000;
  if (!(m >= 1 && m <= 12 && d >= 1 && d <= 31)) return null;
  const t = new Date(Date.UTC(y, m - 1, d));
  if (t.getUTCMonth() !== m - 1) return null;
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Every date in a string, with its position: [{iso, index, end, text}]. */
export function findDates(s, { dayFirst = false } = {}) {
  const out = [];
  const push = (value, m) => {
    if (value && !out.some((o) => m.index < o.end && o.index < m.index + m[0].length)) {
      out.push({ iso: value, index: m.index, end: m.index + m[0].length, text: m[0] });
    }
  };
  let m;
  // Month-name ranges first, so "September 15–17, 2026" yields the 17th.
  const rangeMD = new RegExp(`\\b${MON}\\s+(\\d{1,2})${ORD}\\s*[-–—]\\s*(\\d{1,2})${ORD},?\\s+(\\d{4})\\b`, "gi");
  while ((m = rangeMD.exec(s))) push(iso(m[4], MONTHS[m[1].toLowerCase().replace(".", "")], m[3]), m);
  const rangeDM = new RegExp(`\\b(\\d{1,2})${ORD}\\s*[-–—]\\s*(\\d{1,2})${ORD}\\s+${MON},?\\s+(\\d{4})\\b`, "gi");
  while ((m = rangeDM.exec(s))) push(iso(m[4], MONTHS[m[3].toLowerCase().replace(".", "")], m[2]), m);
  const isoRe = /\b(\d{4})-(\d{2})-(\d{2})\b/g;
  while ((m = isoRe.exec(s))) push(iso(m[1], m[2], m[3]), m);
  const mdy = new RegExp(`\\b${MON}\\s+(\\d{1,2})${ORD},?\\s+(\\d{4})\\b`, "gi");
  while ((m = mdy.exec(s))) push(iso(m[3], MONTHS[m[1].toLowerCase().replace(".", "")], m[2]), m);
  const dmy = new RegExp(`\\b(\\d{1,2})${ORD}\\s+(?:of\\s+)?${MON},?\\s+(\\d{4})\\b`, "gi");
  while ((m = dmy.exec(s))) push(iso(m[3], MONTHS[m[2].toLowerCase().replace(".", "")], m[1]), m);
  const num = /\b(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})\b/g;
  while ((m = num.exec(s))) {
    let a = Number(m[1]);
    let b = Number(m[2]);
    // Day-first when the first number cannot be a month, or when a dot
    // separator is used (European style); otherwise US month-first.
    let df = dayFirst || a > 12 || m[0].includes(".");
    if (b > 12) df = false;
    push(df ? iso(m[3], b, a) : iso(m[3], a, b), m);
  }
  return out.sort((x, y) => x.index - y.index);
}

const DATE_GOOD = /(complet|award|attend|held|event|session|date|on\b|conducted|took place|earned|presented|issued)/i;
const DATE_BAD = /(expir|valid\s+(until|through|thru)|renew|due|birth|printed|generated|exam\s+date|next)/i;
const DATE_LABEL = /\b(completion\s+date|date\s+(of\s+)?(completion|attendance|award|event)|date\s+completed|completed(\s+on)?|event\s+date|session\s+date|date\s+awarded|awarded\s+on|date)\s*[:\-–]?\s*$/i;

function bestDate(lines) {
  const cands = [];
  lines.forEach((line, li) => {
    for (const d of findDates(line)) {
      const before = line.slice(Math.max(0, d.index - 40), d.index);
      const ctx = (lines[li - 1] || "") + " " + line;
      if (DATE_BAD.test(before) || (DATE_BAD.test(line) && !DATE_GOOD.test(before))) continue;
      let conf = "low";
      let why = d.text;
      if (DATE_LABEL.test(before.replace(/\t/g, " ").trimEnd()) || DATE_LABEL.test(before)) {
        conf = "high";
        why = (before.trim() + " " + d.text).trim();
      } else if (DATE_GOOD.test(before) || DATE_GOOD.test(ctx)) {
        conf = "medium";
        why = (before.trim().split(/\s+/).slice(-3).join(" ") + " " + d.text).trim();
      }
      cands.push({ value: d.iso, conf, why, li });
    }
  });
  if (!cands.length) return null;
  cands.sort((a, b) => CONF_RANK[b.conf] - CONF_RANK[a.conf] || a.li - b.li);
  const top = cands[0];
  const distinct = new Set(cands.map((c) => c.value));
  // One date and nothing to choose between: medium even without a label.
  if (top.conf === "low" && distinct.size === 1) top.conf = "medium";
  return { value: top.value, confidence: top.conf, why: top.why };
}

// ------------------------------------------------------------- hours / credits

const UNIT = "(cpe(?:\\s*(?:credits?|hours?|hrs?|units?))?s?|ceus?|eces?|pdus?|contact\\s+hours?|credit\\s+hours?|learning\\s+hours?|training\\s+hours?|hours?|hrs?\\.?|credits?|points?)";
const NUMBER = "(\\d{1,3}(?:[.,]\\d{1,2})?)";

function num(s) {
  return Number(String(s).replace(",", "."));
}

function unitKind(u) {
  u = u.toLowerCase();
  if (/contact|learning|training|hour|hrs?/.test(u) && !/cpe|credit/.test(u)) return "hours";
  if (/cpe/.test(u)) return "cpe";
  if (/ceu/.test(u)) return "ceu";
  if (/ece/.test(u)) return "ece";
  if (/pdu/.test(u)) return "pdu";
  if (/credit/.test(u)) return "credit";
  return "points";
}

function bestHours(lines) {
  const cands = [];
  lines.forEach((line, li) => {
    const flat = line.replace(/\t/g, "  ");
    let m;
    // Label first: "CPE credits: 8", "Contact hours: 2.0", "Credits: 5 ECE".
    const label = new RegExp(`\\b${UNIT}\\s*(?:awarded|earned|granted)?\\s*[:=]\\s*${NUMBER}\\b`, "gi");
    while ((m = label.exec(flat))) cands.push({ value: num(m[2]), kind: unitKind(m[1]), conf: "high", why: m[0].trim(), li, pos: m.index });
    // Number then unit: "1.5 CPE credits", "14 CPE hours", "2.5 hrs".
    const after = new RegExp(`\\b${NUMBER}\\s*${UNIT}(?![a-z])`, "gi");
    while ((m = after.exec(flat))) {
      const kind = unitKind(m[2]);
      const pre = flat.slice(Math.max(0, m.index - 30), m.index);
      if (/\b(page|slide|chapter|module|lesson|day|week|minute|room|suite|street|id|no\.?)\s*$/i.test(pre)) continue;
      const strong = /(award|earn|grant|eligible|total|for a total|qualif|credits?|cpe)/i.test(pre) || kind !== "hours";
      cands.push({ value: num(m[1]), kind, conf: strong ? "high" : "medium", why: m[0].trim(), li, pos: m.index });
    }
    // Durations: "Duration: 3h 30m", "Course length: 8 hours".
    const dur = /\b(?:duration|length|course\s+length|time)\s*[:=]?\s*(\d{1,3})\s*h(?:ours?|rs?)?\s*(?:(\d{1,2})\s*m(?:in(?:utes?)?)?)?/gi;
    while ((m = dur.exec(flat))) cands.push({ value: Number(m[1]) + (m[2] ? Number(m[2]) / 60 : 0), kind: "hours", conf: "high", why: m[0].trim(), li, pos: m.index });
    // Labels with the value in a separate column: "CPE Hours<TAB>6".
    const col = /^\s*([A-Za-z][A-Za-z .]*?)\s*[:]?\t+\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*([A-Za-z]*)\s*$/.exec(line);
    if (col && new RegExp(`^${UNIT}$`, "i").test(col[1].trim().replace(/\s+/g, " "))) {
      cands.push({ value: num(col[2]), kind: unitKind(col[1]), conf: "high", why: `${col[1].trim()} ${col[2]}`, li, pos: 0 });
    }
  });
  if (!cands.length) return null;
  // A CEU figure next to a contact-hours figure is the 10-hour CEU; the
  // hours are what a body's per-hour rate applies to.
  const hasHours = cands.some((c) => c.kind === "hours" || c.kind === "cpe");
  const pref = { cpe: 5, hours: 4, ece: 3, pdu: 3, credit: 2, ceu: hasHours ? 0 : 3, points: 1 };
  cands.sort((a, b) => CONF_RANK[b.conf] - CONF_RANK[a.conf] || pref[b.kind] - pref[a.kind] || a.li - b.li);
  const top = cands[0];
  if (top.value <= 0 || top.value > 400) return null;
  return { value: Math.round(top.value * 100) / 100, confidence: top.conf, why: top.why, unit: top.kind };
}

// ------------------------------------------------------------- names

const NAME_WORD = "[A-Z][a-zA-Z'’\\-]+";
const NAME_RE = new RegExp(`^(?:(?:Dr|Mr|Ms|Mrs|Mx)\\.?\\s+)?${NAME_WORD}(?:\\s+[A-Z]\\.)?(?:\\s+${NAME_WORD}){1,3}$`);
const NOT_NAME = /\b(certificate|completion|attendance|participation|achievement|award|course|training|institute|academy|university|council|chapter|forum|network|conference|workshop|webinar|program|security|association|society|inc|llc|ltd|record|letter|concern|sincerely|regards|date|credits?|hours?|cpe|ceu|pdu|ece|the|of|and|for)\b/i;

function looksLikeName(s) {
  s = s.trim().replace(/[,.]$/, "");
  return NAME_RE.test(s) && !NOT_NAME.test(s) && s.length <= 60;
}

const HOLDER_LABELS = /\b((?:this letter |we )?(?:confirms?|verif(?:y|ies)) that|participant|attendee|student|recipient|name|learner|awarded to|presented to|granted to|issued to|this (?:is to )?certif(?:y|ies) that|we hereby certify that|certifies that|dear)\s*[:,]?\s*/i;

function bestHolder(lines, hint) {
  if (hint) {
    const h = hint.trim().toLowerCase();
    for (const line of lines) {
      if (h && line.toLowerCase().includes(h)) return { value: hint.trim(), confidence: "high", why: "matches the holder name in Settings" };
    }
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/\t+/g, " ");
    const m = HOLDER_LABELS.exec(line);
    if (!m) continue;
    const rest = line.slice(m.index + m[0].length).trim().replace(/[,:]$/, "");
    const firstWords = rest.split(/\s+(?:attended|has|completed|participated|successfully|for|is)\b/)[0].trim();
    if (firstWords && looksLikeName(firstWords)) return { value: firstWords, confidence: "high", why: `${m[0].trim()} ${firstWords}` };
    const next = (lines[i + 1] || "").trim();
    if (!rest && looksLikeName(next)) return { value: next, confidence: "high", why: `${m[0].trim()} / ${next}` };
  }
  // "Alex Example attended ..." at the start of a sentence.
  for (const line of lines) {
    const m = new RegExp(`^((?:${NAME_WORD}\\s+){1,3}${NAME_WORD})\\s+(?:attended|has\\s+(?:successfully\\s+)?completed|completed|participated)\\b`).exec(line.trim());
    if (m && looksLikeName(m[1])) return { value: m[1], confidence: "medium", why: m[0] };
  }
  const solo = lines.map((l) => l.trim()).filter(looksLikeName);
  if (solo.length === 1) return { value: solo[0], confidence: "low", why: "the only line shaped like a name" };
  return null;
}

// ------------------------------------------------------------- provider

const ORG_SUFFIX = /\b(inc\.?|llc|ltd\.?|gmbh|institute|academy|association|university|college|council|chapter|society|foundation|forum|network|alliance|guild|platform|group|school|center|centre|conference|seccon|extension|corporation|corp\.?)\b/i;
// "by" phrases stand alone; bare nouns ("Provider", "Sponsor") need a colon
// or a column gap, or "Provider of continuing education" would be read as one.
const PROVIDER_LABEL = /\b(?:(presented by|provided by|sponsored by|issued by|hosted by|offered by|organi[sz]ed by|delivered by)\s*[:\-–]?\s*|(training provider|provider|sponsor|organi[sz](?:ation|er)|issuer)\s*(?::|\t)\s*)/i;

function cleanOrg(s) {
  s = s.replace(/\s*[·|•]\s*.*$/, "").trim();
  // Keep the full stop of an abbreviation (Inc., Ltd., Corp.); drop others.
  if (!/\b(inc|ltd|corp|co)\.$/i.test(s)) s = s.replace(/[,.;:]+$/, "");
  // "Example SecCon 2026" is an event; the organiser is "Example SecCon".
  s = s.replace(/\s+(19|20)\d{2}$/, "");
  return s.trim();
}

function bestProvider(lines, holder) {
  for (const line of lines) {
    const m = PROVIDER_LABEL.exec(line.replace(/\t+/g, " "));
    if (!m) continue;
    const rest = cleanOrg(line.replace(/\t+/g, " ").slice(m.index + m[0].length));
    if (/^(of|for|to|in)\b/i.test(rest)) continue;
    if (rest && rest.length <= 80 && !/^(the\s+)?(holder|participant)/i.test(rest)) {
      return { value: rest, confidence: "high", why: `${m[0].trim()} ${rest}` };
    }
  }
  // Lines that look like an organisation: a known suffix, short, not a sentence.
  const orgs = [];
  lines.forEach((raw, li) => {
    for (const piece of raw.split(/\t+|,\s+(?=[A-Z])/)) {
      let s = cleanOrg(piece);
      s = s.replace(/^(registrar of courses|program chair|chapter secretary|conference committee)\s*,?\s*/i, "");
      if (!s || s.length > 70 || s.split(/\s+/).length > 8) continue;
      if (holder && s.toLowerCase().includes(holder.toLowerCase())) continue;
      if (/^(certificate|record|attendance|course|cpe certificate)/i.test(s)) continue;
      if (/[.!?]$/.test(piece.trim()) && piece.trim().split(/\s+/).length > 6) continue;
      if (/\b(attended|completed|presenting|awarded|earned|certif|thank|participated|for a total)\b/i.test(s)) continue;
      const m = ORG_SUFFIX.exec(s);
      if (!m) continue;
      if (/^\s*(this|we|our|at|in)\b/i.test(s)) continue;
      orgs.push({ value: s, li });
    }
  });
  if (orgs.length) {
    const counts = {};
    for (const o of orgs) counts[o.value.toLowerCase()] = (counts[o.value.toLowerCase()] || 0) + 1;
    // An organisation named twice (header and signature) is the issuer.
    orgs.sort((a, b) => counts[b.value.toLowerCase()] - counts[a.value.toLowerCase()] || a.li - b.li);
    const top = orgs[0];
    const conf = counts[top.value.toLowerCase()] > 1 || orgs.length === 1 ? "medium" : "low";
    return { value: top.value, confidence: conf, why: "organisation name on the certificate" };
  }
  return null;
}

// ------------------------------------------------------------- title

const TITLE_LABEL = /\b(course\s+title|program\s+title|session\s+title|title|course|program|event|session|webinar|workshop|meeting|activity|topic)\s*[:\t]\s*/i;
const TITLE_PHRASES = [
  /\bfor\s+(?:successfully\s+)?(?:completing|completion\s+of|attending|participating\s+in|attendance\s+at|presenting)\s+(?:the\s+)?(?:course\s+|webinar\s+|workshop\s+|session\s+)?(.+)$/i,
  /\b(?:has\s+)?(?:successfully\s+)?completed\s+(?:the\s+)?(?:course\s+|webinar\s+|workshop\s+|program\s+)?(.+)$/i,
  /\b(?:attended|participated\s+in)\s+(?:the\s+)?(.+)$/i,
  /\bthank\s+you\s+for\s+presenting\s+(.+)$/i,
];
const TITLE_STOP = /(,\s*(?:a|an|the)\b.*$|,?\s+(?:on|held|from)\s+(?:\d|[A-Z][a-z]+\s+\d).*$|,?\s+at\s+our\b.*$|\s+and\s+(?:is|earned|was)\b.*$|,?\s+(?:on|dated)\s+\d.*$|,\s*for\s+a\s+total.*$|,\s*\d.*$)/i;

function cleanTitle(s) {
  // A title ends where its date begins: "SecCon 2026, September 15–17, 2026".
  const ds = findDates(s);
  if (ds.length && ds[0].index > 0) s = s.slice(0, ds[0].index).replace(/[,\s]*(?:\b(?:on|held|from|dated))?\s*$/i, "");
  s = s.replace(TITLE_STOP, "");
  s = s.replace(/^[“"'‘]+|[”"'’.,;:]+$/g, "").trim();
  s = s.replace(/^(?:the\s+)?(?:webinar|course|workshop|session)\s*$/i, "");
  return s;
}

function bestTitle(lines, provider, holder) {
  for (const line of lines) {
    const m = TITLE_LABEL.exec(line);
    if (!m || m.index > 2) continue;
    const label = m[1].toLowerCase();
    if (label === "topic" && lines.some((l) => /^\s*(course|program|meeting|event|session|webinar)\b/i.test(l))) continue;
    const v = cleanTitle(line.slice(m.index + m[0].length).replace(/\t+/g, " "));
    if (v && v.length >= 4 && !/^(virtual|in person|online)$/i.test(v)) return { value: v, confidence: "high", why: `${m[1]}: ${v}` };
  }
  // A quoted title anywhere.
  for (const line of lines) {
    const m = /[“"]([^”"]{4,120})[”"]/.exec(line);
    if (m) return { value: cleanTitle(m[1]), confidence: "high", why: `quoted: ${m[1]}` };
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/\t+/g, " ");
    for (const re of TITLE_PHRASES) {
      const m = re.exec(line);
      if (!m) continue;
      let v = cleanTitle(m[1]);
      // "...completed the" or "attended and completed" with the title on
      // the next line.
      if (!v || /^(the|of)$/i.test(v) || /^(and|or)\s+\w+$/i.test(v)) {
        v = cleanTitle((lines[i + 1] || "").replace(/\t+/g, " "));
      }
      if (v && v.length >= 4 && v !== holder) return { value: v, confidence: "medium", why: m[0].trim() };
    }
    if (/\b(completed|completion of|attending|for)\s*(the)?\s*(webinar|course|workshop)?\s*$/i.test(line) && lines[i + 1]) {
      const v = cleanTitle(lines[i + 1]);
      if (v && v !== holder) return { value: v, confidence: "medium", why: `${line.trim()} / ${v}` };
    }
  }
  // Fallback: the most prominent line that is not boilerplate.
  for (const raw of lines) {
    const l = raw.trim();
    if (!l || l.length < 6 || l.length > 90) continue;
    if (/^(certificate|this|we|awarded|presented|for|on|date|dear|to whom)/i.test(l)) continue;
    if (provider && l.toLowerCase() === provider.toLowerCase()) continue;
    if (holder && l.toLowerCase().includes(holder.toLowerCase())) continue;
    if (findDates(l).length || /\d+\s*(cpe|ceu|hours?|hrs)/i.test(l)) continue;
    return { value: l, confidence: "low", why: "first line that is not boilerplate" };
  }
  return null;
}

// ------------------------------------------------------------- suggestions

const CRED_HINTS = [
  ["cissp", /\b(isc2|isc²|\(isc\)2|cissp|group a|group b)\b/i],
  ["cysa_plus", /\b(comptia|cysa\+?|ceus?)\b/i],
  ["ceh", /\b(ec-council|eccouncil|ceh|eces?)\b/i],
  ["gcitp", /\b(insider threat|c-int|gcitp|counter-insider|pdus?)\b/i],
  ["ctm", /\b(atap|threat assessment|ctm)\b/i],
  ["cfe", /\b(acfe|fraud|cfe)\b/i],
];

function suggest(text) {
  const credentials = CRED_HINTS.filter(([, re]) => re.test(text)).map(([id]) => id);
  const tags = [];
  if (/\b(conference|summit|seccon|symposium|convention)\b/i.test(text)) tags.push("conference");
  else if (/\b(chapter meeting|chapter)\b/i.test(text) && /\bmeeting\b/i.test(text)) tags.push("chapter_meeting");
  else if (/\b(presenting|presenter|speaker|instructor of record|taught)\b/i.test(text)) tags.push("teaching");
  else if (/\b(course|webinar|training|workshop|bootcamp|lab|class|roundtable|session)\b/i.test(text)) tags.push("training");
  const markers = [];
  if (/\b(security|cyber|threat|malware|zero trust|penetration|cryptograph|incident|soc|detection|cloud security|gdpr|breach)\b/i.test(text)) markers.push("domain_security");
  if (/\bfraud\b/i.test(text)) markers.push("fraud");
  if (/\bethics?\b/i.test(text)) markers.push("ethics");
  if (/\b(insider threat|c-int|counter-insider)\b/i.test(text)) markers.push("cint");
  if (/\bthreat assessment\b/i.test(text)) markers.push("threat_management");
  return { credentials, tags, markers };
}

// ------------------------------------------------------------- entry point

export function toLines(text) {
  return String(text || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((l) => l.replace(/[  ]+/g, " ").replace(/ *\t */g, "\t").trim())
    .filter((l) => l.length);
}

/**
 * Parse certificate text into form fields.
 * Returns {fields: {activity_date, hours, provider, title, holder_name}, suggestions}
 * where each field is {value, confidence, why} or {value: null, confidence: null}.
 */
export function parseCertificate(text, { holderHint = "" } = {}) {
  const lines = toLines(text);
  const none = { value: null, confidence: null, why: "" };
  const holder = bestHolder(lines, holderHint) || none;
  const provider = bestProvider(lines, holder.value) || none;
  const title = bestTitle(lines, provider.value, holder.value) || none;
  return {
    fields: {
      activity_date: bestDate(lines) || none,
      hours: bestHours(lines) || none,
      provider,
      title,
      holder_name: holder,
    },
    suggestions: suggest(lines.join("\n")),
    lines,
  };
}

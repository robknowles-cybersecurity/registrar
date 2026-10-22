// Registrar in the browser: screens and wiring. No framework; each screen is
// a function that renders HTML into <main> and binds its handlers.
import * as E from "./engine.js";
import { Ledger, LedgerError, SoftDuplicate, validateUpload, CONFIRMED, CAPTURED, STATES } from "./ledger.js";
import { IdbStore, requestPersistence } from "./db.js";
import { exportLedger, importLedger } from "./schema.js";
import { parseCertificate } from "./parser.js";
import { extractText } from "./ingest.js";
import { bodyPacket, packetZip, coverSheet } from "./packets.js";
import * as LLM from "./llm.js";
import { loadSample, removeSample, sampleState } from "./sample.js";
import { ledgerCsv, ledgerXlsx, cycleEndCalendar } from "./exports.js";

const view = document.getElementById("view");
let led;
let rules;
let build = { version: "", build: "" };
const RELEASES = "https://github.com/robknowles-cybersecurity/registrar/releases";

// ---------------------------------------------------------------- helpers

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v) => {
  const n = Number(v || 0);
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
};
const $ = (sel, root = view) => root.querySelector(sel);
const $$ = (sel, root = view) => [...root.querySelectorAll(sel)];

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 4000);
}

function download(name, bytes, type) {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function unverifiedBadge(cred, kind = "rules", bucket = null) {
  const u = cred.unverified_value(kind, bucket);
  if (!u) return "";
  const link = /^https?:\/\//i.test(u.source_url || "") ? ` <a href="${esc(u.source_url)}" rel="noreferrer noopener" target="_blank">source</a>` : "";
  return ` <span class="badge unv" title="Not confirmed on the body's own page: ${esc(u.note)}">UNVERIFIED</span>${link}`;
}

function bar(value, target, frac, klass = "") {
  const pct = target ? Math.min(100, (100 * value) / target) : 0;
  const tick = frac == null ? "" : `<u data-left="${Math.min(100, frac * 100).toFixed(1)}"></u>`;
  return `<div class="bar" role="img" aria-label="${fmt(value)} of ${fmt(target)}"><i class="${klass}" data-w="${pct.toFixed(1)}"></i>${tick}</div>`;
}

async function tracked() {
  const states = await led.allCredentialState();
  return states.filter((s) => s.anchor_date && Number(s.active ?? 1) && rules.byId[s.credential]);
}

/** Credentials the person holds (ticked at first run or in Settings), cycle start or not. */
async function held() {
  const states = await led.allCredentialState();
  return states.filter((s) => Number(s.active ?? 1) && rules.byId[s.credential]);
}

/** Rules' credentials with the ones you hold first. */
async function heldFirst() {
  const mine = new Set((await held()).map((s) => s.credential));
  return [...rules.credentials].sort((a, b) => Number(mine.has(b.id)) - Number(mine.has(a.id))).map((c) => ({ c, mine: mine.has(c.id) }));
}

/** A fresh browser: never answered the first-run question, holds nothing, has no activities. */
let settled = false; // once false, a first run cannot come back until a wipe
async function firstRun() {
  if (settled) return false;
  const fresh = (await led.getConfig("onboarding")) == null && !(await led.allCredentialState()).length && !(await led.store.getAll("entries")).length;
  settled = !fresh;
  return fresh;
}

async function assessAll() {
  const entries = await led.engineEntries();
  const out = [];
  for (const s of await tracked()) {
    const cred = rules.get(s.credential);
    const a = E.assess(cred, entries, { anchor: s.anchor_date, carryoverIn: s.carryover_in || 0 });
    out.push({ cred, state: s, a, pace: E.pace(a) });
  }
  return out;
}

function setNav(hash) {
  for (const a of document.querySelectorAll(".top nav a")) {
    const on = a.getAttribute("href") === hash || (hash.startsWith("#/entry/") && a.getAttribute("href") === "#/entries");
    if (on) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
}

// ---------------------------------------------------------------- screens

async function screenHome() {
  const rows = await assessAll();
  const stats = await led.stats();
  const pending = stats.entries[CAPTURED] || 0;
  const nEntries = Object.values(stats.entries).reduce((x, y) => x + y, 0);
  const sample = await sampleState(led);
  const noStart = (await held()).filter((s) => !s.anchor_date);
  let html = `<h1>Credentials</h1>`;
  if (sample) html += `<div class="banner sample"><p class="m0"><b>Sample data is loaded.</b> The activities titled “SAMPLE —” and their certificates are invented, to show how Registrar counts. Remove them before you log your own, or keep them while you look around.</p>
    <div class="actions"><button type="button" class="secondary" id="sample-remove">Remove sample data</button></div></div>`;
  if (pending) html += `<div class="banner">${pending} captured ${pending === 1 ? "activity is" : "activities are"} awaiting confirmation: nothing counts until you confirm it. <a href="#/entries?status=captured">Review</a></div>`;
  for (const s of noStart) {
    const c = rules.get(s.credential);
    html += `<section class="card" aria-labelledby="start-${esc(c.id)}"><h2 class="mt0" id="start-${esc(c.id)}">${esc(c.name)} <span class="small mute">${esc(c.body_name)}</span>${unverifiedBadge(c)}</h2>
      <form class="start-form" data-start="${esc(c.id)}" novalidate><label for="start-date-${esc(c.id)}">When did your current ${esc(c.name)} cycle begin?</label>
      <p class="small mute m0">Usually your certification or renewal date. Progress, pace and the calendar need it.</p>
      <div class="flexrow"><input type="date" id="start-date-${esc(c.id)}" required><button type="submit">Save</button></div></form></section>`;
  }
  if (!rows.length && !noStart.length) {
    html += `<div class="card"><p class="mt0"><b>Nothing tracked yet.</b> Open <a href="#/settings">Settings</a>, tick the credentials you hold and record the date each one's current cycle began.
      Then <a href="#/log">log an activity</a>, or <b>Import</b> an export from the self-hosted Registrar.</p></div>`;
  }
  if (!nEntries) {
    html += `<section class="card empty" aria-labelledby="try-h"><h2 class="mt0" id="try-h">New here? Try it with sample data</h2>
      <p>Load three sample credentials (CISSP, CySA+ and CFE) and nine invented activities with sample certificates, to see caps, minimums, pace and packets at work. Everything is marked SAMPLE, and one click removes it.</p>
      <div class="actions"><button type="button" id="sample-load">Load sample data</button></div>
      <p class="small">Or try evidence capture: <a href="sample/try-capture-certificate.pdf" download="sample-certificate.pdf" id="try-capture">try capture with this sample certificate</a>, then drop it on <a href="#/log">Log activity</a>. It is read on this device.</p></section>`;
  }
  html += `<div class="grid">`;
  for (const { cred, a, pace } of rows) {
    const klass = a.verdict === "COMPLIANT" ? "ok" : a.verdict === "BEHIND" || a.verdict === "SHORT" ? "bad" : "";
    html += `<section class="card" aria-labelledby="c-${esc(cred.id)}">
      <h2 id="c-${esc(cred.id)}" class="mt0"><a href="#/credential/${esc(cred.id)}">${esc(cred.name)}</a> <span class="small mute">${esc(cred.body_name)}</span>${unverifiedBadge(cred)}</h2>
      <p class="verdict v-${esc(a.verdict)}">${esc(a.verdict.replace(/_/g, " "))}${a.advisory_only ? " (advisory only)" : ""}</p>
      ${bar(a.total_with_carryover, a.required, pace.elapsed_fraction, klass)}
      <dl class="figs">
        <dt>Counted</dt><dd>${fmt(a.total_with_carryover)} of ${fmt(a.required)} ${esc(cred.unit_label)}</dd>
        <dt>Naive sum</dt><dd>${fmt(a.naive_total + a.carryover_in)} <span class="small mute">what a spreadsheet would say</span></dd>
        <dt>Forfeited</dt><dd>${fmt(a.forfeited)} <span class="small mute">to caps</span></dd>
        ${a.groups.filter((g) => g.floor).map((g) => `<dt>${esc(g.name.split(" — ")[0])}</dt><dd>${fmt(g.awarded)} of ${fmt(g.floor)}${g.shortfall ? ` <b class="v-SHORT">SHORT BY ${fmt(g.shortfall)}</b>` : ""}</dd>`).join("")}
        <dt>Cycle</dt><dd>${esc(a.cycle_start)} → ${esc(a.cycle_end)} <span class="small mute">(${pace.days_left} days left)</span></dd>
      </dl>
    </section>`;
  }
  html += `</div>`;
  if (nEntries) html += reportsCard();
  view.innerHTML = html;
  bindReports();
  if ($("#sample-load")) $("#sample-load").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try {
      const r = await loadSample(led);
      toast(`Sample data loaded: ${r.activities} activities, ${r.files} certificates.`);
    } catch (err) {
      toast(err.message);
    }
    route();
  });
  if ($("#sample-remove")) $("#sample-remove").addEventListener("click", async () => {
    const r = await removeSample(led);
    toast(`Sample data removed: ${r.activities} activities. Your own data was not touched.`);
    route();
  });
  $$(".start-form").forEach((f) => f.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = f.querySelector('input[type="date"]');
    if (!input.value) {
      input.focus();
      toast("Enter the date the cycle began.");
      return;
    }
    try {
      await led.setCredentialState(f.dataset.start, { anchor_date: input.value, active: 1 });
      route();
    } catch (err) {
      toast(err.message);
    }
  }));
}

// -- reports: CSV, .xlsx and .ics, made here in the browser

function reportsCard() {
  return `<section class="card reports" aria-labelledby="reports-h"><h2 class="mt0" id="reports-h">Reports</h2>
    <p class="small mute">Made in this browser from your ledger. For a backup you can import again, use <b>Export</b> at the top.</p>
    <div class="actions"><button type="button" class="secondary" id="dl-csv">Activities (.csv)</button>
    <button type="button" class="secondary" id="dl-xlsx">Workbook (.xlsx)</button>
    <button type="button" class="secondary" id="dl-ics">Cycle ends calendar (.ics)</button></div></section>`;
}

function bindReports() {
  if (!$("#dl-csv")) return;
  const day = E.todayISO();
  $("#dl-csv").addEventListener("click", async () => {
    download(`registrar-activities-${day}.csv`, new TextEncoder().encode(await ledgerCsv(led)), "text/csv;charset=utf-8");
  });
  $("#dl-xlsx").addEventListener("click", async () => {
    download(`registrar-${day}.xlsx`, await ledgerXlsx(led, rules), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
  });
  $("#dl-ics").addEventListener("click", async () => {
    const cal = await cycleEndCalendar(led, rules, { version: build.version });
    if (!cal.events.length) {
      toast("No cycle start recorded yet: add one for each credential you hold.");
      return;
    }
    download(`registrar-cycle-ends-${day}.ics`, new TextEncoder().encode(cal.text), "text/calendar;charset=utf-8");
    toast(`Calendar: ${cal.events.length} cycle end(s), each with alerts 90 and 30 days before${cal.skipped.length ? `; no start date for ${cal.skipped.join(", ")}` : ""}.`);
  });
}

// -- first run

/** To the Credentials screen, rendering it once (a hash change routes by itself). */
function goHome() {
  if ((location.hash || "#/") === "#/") route();
  else location.hash = "#/";
}

async function screenWelcome() {
  view.innerHTML = `<h1>Which credentials do you hold?</h1>
    <p>Registrar counts each credential by its own body's rules. Tick the ones you hold: your progress, the activity form, packets, the calendar and the reports all follow from this. You can change it any time in Settings.</p>
    <form id="welcome-form" novalidate>
      <fieldset><legend>Credentials Registrar can count</legend>
      ${rules.credentials.map((c) => `<div class="hold">
        <label class="plain"><input type="checkbox" name="hold" value="${esc(c.id)}"> <b>${esc(c.name)}</b> <span class="small mute">${esc(c.long_name || "")} · ${esc(c.body_name)}</span>${unverifiedBadge(c)}</label>
        <div class="when" id="when-${esc(c.id)}" hidden><label for="w-${esc(c.id)}">Current ${esc(c.name)} cycle began <span class="small mute">(optional: you can add it later)</span></label>
        <input type="date" id="w-${esc(c.id)}" name="anchor-${esc(c.id)}"></div></div>`).join("")}
      </fieldset>
      <div id="welcome-error" role="alert"></div>
      <div class="actions"><button type="submit">Continue</button><button type="button" class="secondary" id="welcome-skip">Skip for now</button></div>
      <p class="small mute">Just looking? Skip, then press <b>Load sample data</b>. Have a backup? Use <b>Import</b> at the top.</p>
    </form>`;
  $$('input[name="hold"]').forEach((cb) => cb.addEventListener("change", () => {
    $(`#when-${cb.value}`).hidden = !cb.checked;
  }));
  $("#welcome-skip").addEventListener("click", async () => {
    await led.setConfig("onboarding", "skipped");
    goHome();
  });
  $("#welcome-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    try {
      for (const id of fd.getAll("hold")) {
        await led.setCredentialState(id, { anchor_date: fd.get(`anchor-${id}`) || null, carryover_in: 0, active: 1 });
      }
      await led.setConfig("onboarding", "done");
      toast(fd.getAll("hold").length ? "Saved. Log an activity, or load the sample data to look around." : "Saved.");
      goHome();
    } catch (err) {
      $("#welcome-error").innerHTML = `<div class="banner bad">${esc(err.message)}</div>`;
    }
  });
}

async function screenCredential(id) {
  const cred = rules.byId[id];
  if (!cred) return notFound();
  const st = await led.getCredentialState(id);
  if (!st || !st.anchor_date) {
    view.innerHTML = `<h1>${esc(cred.name)}</h1><p>No cycle start date recorded. Set it in <a href="#/settings">Settings</a>.</p>`;
    return;
  }
  const entries = await led.engineEntries();
  const a = E.assess(cred, entries, { anchor: st.anchor_date, carryoverIn: st.carryover_in || 0 });
  const pace = E.pace(a);
  const all = await led.listEntries({ status: CONFIRMED });
  const inCycle = all.filter((e) => a.cycle_start <= e.activity_date && e.activity_date < a.cycle_end && (e.applies_to == null || e.applies_to.includes(id)) && cred.buckets_for(e.tags, e.markers).length);
  const ev = await led.listEvidence();
  view.innerHTML = `<h1>${esc(cred.name)} <span class="small mute">${esc(cred.long_name || "")} · ${esc(cred.body_name)}</span>${unverifiedBadge(cred)}</h1>
    <p class="verdict v-${esc(a.verdict)}">${esc(a.verdict.replace(/_/g, " "))}</p>
    ${bar(a.total_with_carryover, a.required, pace.elapsed_fraction)}
    <dl class="figs">
      <dt>Counted</dt><dd>${fmt(a.total_with_carryover)} of ${fmt(a.required)} ${esc(cred.unit_label)} (carried in ${fmt(a.carryover_in)})</dd>
      <dt>Naive sum</dt><dd>${fmt(a.naive_total + a.carryover_in)}</dd>
      <dt>Forfeited</dt><dd>${fmt(a.forfeited)}</dd>
      <dt>Pace</dt><dd>${fmt(pace.expected_by_now)} expected by today; ${pace.delta >= 0 ? "ahead by" : "behind by"} ${fmt(Math.abs(pace.delta))}; ${fmt(pace.per_month_needed)}/month needed</dd>
      <dt>Cycle</dt><dd>${esc(a.cycle_start)} → ${esc(a.cycle_end)}, grace to ${esc(a.grace_end)}</dd>
      ${a.carryover_out ? `<dt>Carry out</dt><dd>${fmt(a.carryover_out)}${unverifiedBadge(cred, "carryover")}</dd>` : ""}
    </dl>
    ${a.notes.filter((n) => !n.startsWith("group semantics")).map((n) => `<div class="banner">${esc(n)}</div>`).join("")}
    <h2>Categories</h2>
    <div class="scroll"><table><thead><tr><th>Category</th><th>Claimed</th><th>Counted</th><th>Forfeited</th><th>Cap</th></tr></thead><tbody>
    ${a.buckets.map((b) => `<tr><td>${esc(b.name)}</td><td>${fmt(b.raw)}</td><td>${fmt(b.awarded)}</td><td>${fmt(b.forfeited)}</td><td>${b.cap == null ? "none" : fmt(b.cap)}${unverifiedBadge(cred, "cap", cred.bucket(b.id))}</td></tr>`).join("") || `<tr><td colspan=5 class="mute">No counted activity yet.</td></tr>`}
    </tbody></table></div>
    ${a.groups.length ? `<h2>Groups</h2><div class="scroll"><table><thead><tr><th>Group</th><th>Counted</th><th>Floor</th><th>Cap</th><th>Short by</th></tr></thead><tbody>
      ${a.groups.map((g) => `<tr><td>${esc(g.name)}</td><td>${fmt(g.awarded)}</td><td>${g.floor == null ? "—" : fmt(g.floor)}</td><td>${g.cap == null ? "—" : fmt(g.cap)}</td><td>${g.shortfall ? `<b class="v-SHORT">${fmt(g.shortfall)}</b>` : "—"}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${a.annual.length ? `<h2>By year</h2><div class="scroll"><table><thead><tr><th>Year</th><th>Counted</th><th>Minimum</th><th>Target</th></tr></thead><tbody>
      ${a.annual.map((r) => `<tr><td>${r.year}</td><td>${fmt(r.awarded)}</td><td>${r.minimum == null ? "—" : fmt(r.minimum)}${r.minimum != null && !r.met ? " <b class='v-SHORT'>not met</b>" : ""}</td><td>${r.target == null ? "—" : fmt(r.target)}${r.target_advisory ? " <span class='small mute'>(pacing only)</span>" : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}
    ${a.submission.deadlines && a.submission.deadlines.length ? `<h2>Registration deadlines</h2><ul>${a.submission.deadlines.map((d) => `<li>${d.earned_year}: register ${fmt(d.credits)} by <b>${esc(d.register_by)}</b>; ${d.unsubmitted_entries} not yet submitted${d.overdue ? ` <b class="v-SHORT">OVERDUE</b>` : ""}</li>`).join("")}</ul>` : ""}
    <h2>Activities in this cycle (${inCycle.length})</h2>
    <div class="scroll"><table><thead><tr><th>Date</th><th>Activity</th><th>Credits</th><th>Evidence</th><th>Submitted</th></tr></thead><tbody>
    ${inCycle.map((e) => `<tr><td>${esc(e.activity_date)}</td><td><a href="#/entry/${esc(e.uuid)}">${esc(e.title || "(untitled)")}</a><div class="small mute">${esc(e.provider)}</div></td><td>${fmt(E.creditsForEntry(cred, e))}</td><td>${ev.filter((x) => x.entry_uuid === e.uuid).length || '<span class="badge bad">none</span>'}</td><td>${e.submitted_to.includes(cred.body_id) ? esc(e.submitted_dates[cred.body_id] || "yes") : "—"}</td></tr>`).join("") || `<tr><td colspan=5 class="mute">None.</td></tr>`}
    </tbody></table></div>`;
}

// -- log / edit an activity, with evidence capture

const FIELD_LABELS = { activity_date: "Date", hours: "Hours", provider: "Provider", title: "Title", holder_name: "Certificate holder" };

async function screenLog({ uuid = null, correct = false } = {}) {
  const current = uuid ? await led.getEntry(uuid) : null;
  if (uuid && !current) return notFound();
  const holder = (await led.getConfig("attendee_name")) || "";
  const tr = await tracked();
  const e = current || { activity_date: E.todayISO(), title: "", provider: "", hours: "", count: 1, tags: [], markers: [], applies_to: null, notes: "", cost_usd: null };
  const editingConfirmed = current && current.status === CONFIRMED;
  const llm = LLM.status();
  const heading = !current ? "Log an activity" : editingConfirmed ? "Correct a confirmed activity" : "Edit activity";
  view.innerHTML = `<h1>${heading}</h1>
    ${editingConfirmed ? `<div class="banner">This activity is confirmed. Saving writes a corrected copy that supersedes it; the original is kept on record.</div>` : ""}
    <form id="entry-form" novalidate>
      <section class="drop" id="drop" aria-labelledby="drop-h">
        <h2 id="drop-h" class="mt0">Evidence</h2>
        <p class="small mute m0">Drop a certificate (PDF, PNG or JPEG), choose a file, or take a photo. It is read on this device and the form is filled in for you to check. Nothing is saved until you press Save.</p>
        <div class="actions">
          <button type="button" class="secondary" id="pick-file">Choose file</button>
          <button type="button" class="secondary" id="pick-photo">Take photo</button>
        </div>
        <input type="file" id="file-input" accept="application/pdf,image/png,image/jpeg,.pdf,.png,.jpg,.jpeg" multiple hidden aria-label="Evidence file">
        <input type="file" id="photo-input" accept="image/*" capture="environment" hidden aria-label="Photo of a certificate">
        <ul class="files" id="file-list"></ul>
        <p id="ingest-status" class="small" role="status" aria-live="polite"></p>
      </section>
      <div id="holder-note"></div>
      <div class="row">
        <div><label for="f-activity_date">Date</label><input type="date" id="f-activity_date" name="activity_date" value="${esc(e.activity_date)}" required><p class="why" id="why-activity_date"></p></div>
        <div><label for="f-hours">Hours</label><input type="number" id="f-hours" name="hours" min="0" step="0.25" value="${esc(e.hours)}"><p class="why" id="why-hours"></p></div>
        <div><label for="f-count">Count <span class="small mute">(per-item categories)</span></label><input type="number" id="f-count" name="count" min="1" step="1" value="${esc(e.count || 1)}"></div>
      </div>
      <label for="f-title">Title</label><input type="text" id="f-title" name="title" value="${esc(e.title)}"><p class="why" id="why-title"></p>
      <label for="f-provider">Provider</label><input type="text" id="f-provider" name="provider" value="${esc(e.provider)}"><p class="why" id="why-provider"></p>
      <fieldset><legend>Kind of activity</legend><div class="checks">
        ${rules.data.tag_vocabulary.map((t) => `<label><input type="checkbox" name="tags" value="${esc(t.id)}" ${e.tags.includes(t.id) ? "checked" : ""}> ${esc(t.label)}</label>`).join("")}
      </div></fieldset>
      <fieldset><legend>Topics</legend><div class="checks">
        ${rules.data.topic_markers.map((m) => `<label><input type="checkbox" name="markers" value="${esc(m.id)}" ${e.markers.includes(m.id) ? "checked" : ""}> ${esc(m.label)}</label>`).join("")}
      </div></fieldset>
      ${tr.length ? `<fieldset><legend>Counts toward <span class="small mute">(none ticked = every credential it is eligible for)</span></legend><div class="checks">
        ${tr.map((s) => `<label><input type="checkbox" name="applies_to" value="${esc(s.credential)}" ${e.applies_to && e.applies_to.includes(s.credential) ? "checked" : ""}> ${esc(rules.get(s.credential).name)}</label>`).join("")}
      </div></fieldset>` : ""}
      <label for="f-notes">Notes</label><textarea id="f-notes" name="notes">${esc(e.notes)}</textarea>
      <div class="row"><div><label for="f-cost">Cost (USD, optional)</label><input type="number" id="f-cost" name="cost_usd" min="0" step="0.01" value="${e.cost_usd == null ? "" : esc(e.cost_usd)}"></div></div>
      <div class="byok" id="byok">${llm.on
        ? `<p class="m0"><b>Your own API key is set for this session</b> (${esc(LLM.PROVIDERS[llm.provider].label)}, ${esc(llm.model)}).</p>
           <p class="small">Pressing the button sends the <b>extracted text only</b> (never the file) to ${esc(llm.baseUrl)}. This leaves your browser.</p>
           <button type="button" class="secondary" id="ask-llm" disabled>Ask the model to fill the form</button>`
        : `<p class="small m0">Optional: set your own API key in <a href="#/settings">Settings</a> to ask a model to read the extracted text. Off by default; nothing leaves this browser unless you turn it on.</p>`}</div>
      <details id="text-details" hidden><summary>Text read from the evidence</summary><pre class="text" id="extracted"></pre></details>
      <div id="form-error" role="alert"></div>
      <div class="actions">
        <button type="submit" name="save" value="captured">${editingConfirmed ? "Save correction" : "Save (captured)"}</button>
        ${editingConfirmed ? "" : `<button type="submit" name="save" value="confirm" class="secondary">Save and confirm</button>`}
      </div>
      <p class="small mute">Captured activities count for nothing until you confirm them. Confirming checks for a likely double entry.</p>
    </form>`;

  const pending = []; // {name, bytes, mime}
  const dirty = new Set();
  let lastText = "";
  for (const f of ["activity_date", "hours", "title", "provider"]) $(`#f-${f}`).addEventListener("input", () => dirty.add(f));
  if (current) ["activity_date", "hours", "title", "provider"].forEach((f) => dirty.add(f));

  const status = (msg) => ($("#ingest-status").textContent = msg);
  const renderFiles = () => {
    $("#file-list").innerHTML = pending.map((p, i) => `<li>${esc(p.name)} <span class="small mute">${(p.bytes.length / 1024).toFixed(0)} KB</span> <button type="button" class="secondary" data-rm="${i}" aria-label="Remove ${esc(p.name)}">Remove</button></li>`).join("");
    $$("[data-rm]").forEach((b) => b.addEventListener("click", () => {
      pending.splice(Number(b.dataset.rm), 1);
      renderFiles();
    }));
  };

  const fill = (field, value, confidence, why) => {
    if (value == null || value === "") return;
    const input = $(`#f-${field}`);
    if (!input || dirty.has(field)) {
      if (input && String(input.value) !== String(value)) $(`#why-${field}`).innerHTML = `Evidence says <b>${esc(value)}</b> <span class="conf conf-${esc(confidence)}">${esc(confidence)}</span> — kept your entry`;
      return;
    }
    input.value = value;
    $(`#why-${field}`).innerHTML = `<span class="conf conf-${esc(confidence)}">${esc(confidence)}${confidence === "llm" ? "" : " confidence"}</span> ${why ? `from “${esc(why)}”` : ""} — check before saving`;
  };

  const applyParse = (parsed, method, ocrConfidence) => {
    const ocr = method !== "pdf-text";
    for (const [field, r] of Object.entries(parsed.fields)) {
      if (field === "holder_name" || r.value == null) continue;
      let conf = r.confidence;
      if (ocr && ocrConfidence != null && ocrConfidence < 85 && conf === "high") conf = "medium";
      fill(field, r.value, conf, r.why);
    }
    const noTags = !$$('input[name="tags"]:checked').length;
    if (noTags) for (const t of parsed.suggestions.tags) { const c = $(`input[name="tags"][value="${t}"]`); if (c) c.checked = true; }
    if (!$$('input[name="markers"]:checked').length) for (const m of parsed.suggestions.markers) { const c = $(`input[name="markers"][value="${m}"]`); if (c) c.checked = true; }
    const h = parsed.fields.holder_name;
    const note = $("#holder-note");
    if (h.value && holder && h.value.toLowerCase() !== holder.toLowerCase()) {
      note.innerHTML = `<div class="banner bad">This certificate names <b>${esc(h.value)}</b>, but your name in Settings is <b>${esc(holder)}</b>. Check it is yours.</div>`;
    } else if (h.value && !holder) {
      note.innerHTML = `<div class="banner">Certificate holder: <b>${esc(h.value)}</b> <span class="conf conf-${esc(h.confidence)}">${esc(h.confidence)}</span>. <button type="button" class="secondary" id="use-name">Use as my name in packets</button></div>`;
      $("#use-name").addEventListener("click", async () => {
        await led.setConfig("attendee_name", h.value);
        note.innerHTML = `<div class="banner ok">Saved ${esc(h.value)} as the holder name.</div>`;
      });
    } else if (h.value) {
      note.innerHTML = `<div class="banner ok">Certificate holder <b>${esc(h.value)}</b> matches your name.</div>`;
    } else note.innerHTML = "";
  };

  const ingest = async (files) => {
    for (const file of files) {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let mime;
      try {
        mime = validateUpload(file.name || "photo.jpg", bytes);
      } catch (err) {
        status(`${file.name}: ${err.message}`);
        continue;
      }
      pending.push({ name: file.name || "photo.jpg", bytes, mime });
      renderFiles();
      status(`Reading ${file.name}…`);
      try {
        const r = await extractText(file, bytes, {
          onProgress: (m) => status(`${file.name}: ${m.status}${m.progress ? ` ${Math.round(m.progress * 100)}%` : ""}`),
        });
        lastText = r.text;
        $("#extracted").textContent = r.text || "(no text found)";
        $("#text-details").hidden = false;
        const parsed = parseCertificate(r.text, { holderHint: holder });
        applyParse(parsed, r.method, r.ocrConfidence);
        const found = Object.values(parsed.fields).filter((f) => f.value != null).length;
        status(`${file.name}: read by ${r.method === "pdf-text" ? "PDF text" : "OCR"}${r.ocrConfidence != null ? ` (OCR confidence ${Math.round(r.ocrConfidence)}%)` : ""}; ${found} of 5 fields found. Check the form, then save.`);
        if ($("#ask-llm")) $("#ask-llm").disabled = !lastText.trim();
      } catch (err) {
        status(`${file.name}: could not read it (${err.message}). It will still be attached when you save.`);
      }
    }
  };

  $("#pick-file").addEventListener("click", () => $("#file-input").click());
  $("#pick-photo").addEventListener("click", () => $("#photo-input").click());
  $("#file-input").addEventListener("change", (ev) => ingest([...ev.target.files]));
  $("#photo-input").addEventListener("change", (ev) => ingest([...ev.target.files]));
  const drop = $("#drop");
  drop.addEventListener("dragover", (ev) => { ev.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (ev) => { ev.preventDefault(); drop.classList.remove("over"); ingest([...ev.dataTransfer.files]); });
  if ($("#ask-llm")) {
    $("#ask-llm").addEventListener("click", async () => {
      $("#ask-llm").disabled = true;
      status("Sending the extracted text to the model…");
      try {
        const r = await LLM.extractWithLlm(lastText);
        for (const [f, v] of Object.entries(r)) if (f !== "holder_name") fill(f, v, "llm", "your model");
        status("The model's answer is in the form, marked “llm”. Check it before saving.");
      } catch (err) {
        status(`Model request failed: ${err.message}`);
      } finally {
        $("#ask-llm").disabled = false;
      }
    });
  }

  $("#entry-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const mode = ev.submitter ? ev.submitter.value : "captured";
    const fd = new FormData(ev.target);
    const applies = fd.getAll("applies_to");
    const payload = {
      activity_date: fd.get("activity_date"),
      title: String(fd.get("title") || "").trim(),
      provider: String(fd.get("provider") || "").trim(),
      hours: Number(fd.get("hours") || 0),
      count: parseInt(fd.get("count") || "1", 10) || 1,
      tags: fd.getAll("tags"),
      markers: fd.getAll("markers"),
      applies_to: applies.length ? applies : null,
      notes: String(fd.get("notes") || ""),
      cost_usd: fd.get("cost_usd") === "" || fd.get("cost_usd") == null ? null : Number(fd.get("cost_usd")),
    };
    const err = $("#form-error");
    err.innerHTML = "";
    if (!payload.tags.length) {
      err.innerHTML = `<div class="banner bad">Tick at least one kind of activity: it decides which category the hours count in.</div>`;
      return;
    }
    let id;
    try {
      if (editingConfirmed) id = await led.supersede(current.uuid, payload);
      else if (current) {
        await led.updateEntry(current.uuid, payload);
        id = current.uuid;
      } else id = await led.addEntry({ ...payload, capture_source: "web", capture_ref: "browser" });
      for (const p of pending) await led.addEvidence(id, p.name, p.bytes, p.mime);
    } catch (e2) {
      err.innerHTML = `<div class="banner bad">${esc(e2.message)}</div>`;
      return;
    }
    if (mode === "confirm") {
      try {
        await led.confirm(id);
      } catch (e3) {
        if (e3 instanceof SoftDuplicate) {
          location.hash = `#/entry/${id}?dupe=1`;
          return;
        }
        throw e3;
      }
    }
    toast(mode === "confirm" ? "Saved and confirmed." : editingConfirmed ? "Correction saved; the original is kept." : "Saved. It counts once you confirm it.");
    location.hash = `#/entry/${id}`;
  });
}

async function screenEntry(uuid, query) {
  const e = await led.getEntry(uuid);
  if (!e) return notFound();
  const ev = await led.verifyEvidence(uuid);
  const newer = await led.supersededBy(uuid);
  let dupes = [];
  if (e.status === CAPTURED) dupes = await led.softDuplicates(e);
  const credits = rules.credentials.map((c) => ({ c, n: (e.applies_to == null || e.applies_to.includes(c.id)) && c.buckets_for(e.tags, e.markers).length ? E.creditsForEntry(c, e) : null })).filter((x) => x.n != null);
  view.innerHTML = `<h1>${esc(e.title || "(untitled)")} <span class="pill ${esc(e.status)}">${esc(e.status)}</span></h1>
    ${query.get("dupe") && dupes.length ? `<div class="banner bad">Not confirmed: this looks like ${dupes.length === 1 ? "an activity" : "activities"} already on file (same date and provider, similar hours): ${dupes.map((d) => `<a href="#/entry/${esc(d.uuid)}">${esc(d.title)}</a>`).join(", ")}. Confirm anyway only if it is genuinely separate.</div>` : ""}
    ${newer ? `<div class="banner">Superseded by a correction: <a href="#/entry/${esc(newer)}">view the current version</a>.</div>` : ""}
    ${e.supersedes ? `<div class="banner">This corrects <a href="#/entry/${esc(e.supersedes)}">an earlier version</a>, which is kept on record.</div>` : ""}
    <dl class="figs">
      <dt>Date</dt><dd>${esc(e.activity_date)}</dd>
      <dt>Provider</dt><dd>${esc(e.provider || "—")}</dd>
      <dt>Hours</dt><dd id="entry-hours">${fmt(e.hours)}</dd>
      <dt>Count</dt><dd>${e.count}</dd>
      <dt>Kind</dt><dd>${e.tags.map((t) => esc(rules.tag_labels[t] || t)).join(", ") || "—"}</dd>
      <dt>Topics</dt><dd>${e.markers.map((m) => esc(rules.marker_labels[m] || m)).join(", ") || "—"}</dd>
      <dt>Counts toward</dt><dd>${credits.map(({ c, n }) => `${esc(c.name)}: ${fmt(n)} ${esc(c.unit_label)}`).join("; ") || "nothing (no matching category)"}</dd>
      <dt>Submitted</dt><dd>${e.submitted_to.map((b) => `${esc(b)} ${esc(e.submitted_dates[b] || "")}`).join(", ") || "—"}</dd>
      <dt>Notes</dt><dd>${esc(e.notes || "—")}</dd>
      <dt>Recorded</dt><dd class="small">${esc(e.created_at)}${e.confirmed_at ? `; confirmed ${esc(e.confirmed_at)}` : ""}${e.voided_at ? `; voided ${esc(e.voided_at)}` : ""}</dd>
    </dl>
    <h2>Evidence</h2>
    <ul class="files">${ev.map((r) => `<li>${r.verify === "OK" ? `<a href="#" data-dl="${r.id}">${esc(r.filename)}</a>` : esc(r.filename)} <span class="badge ${r.verify === "OK" ? "ok" : "bad"}">${r.verify === "OK" ? "hash OK" : r.verify === "MISSING" ? "file not in this browser" : "ALTERED: refused"}</span> <span class="small mute">sha256 ${esc(r.sha256.slice(0, 12))}…</span></li>`).join("") || `<li class="mute">No evidence attached.</li>`}</ul>
    <div class="actions">
      ${e.status === CAPTURED ? `<button type="button" id="act-confirm">Confirm</button>${query.get("dupe") ? `<button type="button" class="danger" id="act-force">Confirm anyway</button>` : ""}<a class="button" href="#/entry/${esc(uuid)}/edit">Edit</a><button type="button" class="secondary" id="act-reject">Reject</button>` : ""}
      ${e.status === CONFIRMED ? `<a class="button" href="#/entry/${esc(uuid)}/edit">Correct</a>` : ""}
      ${e.status === "rejected" ? `<button type="button" class="secondary" id="act-reopen">Re-open</button>` : ""}
      ${[CAPTURED, CONFIRMED, "rejected"].includes(e.status) ? `<button type="button" class="danger" id="act-void">Void</button>` : ""}
    </div>
    <p class="small mute">Nothing is deleted. Void retires a mistake; it stays on record and counts for nothing.</p>`;
  const act = async (fn, msg) => {
    try {
      await fn();
      toast(msg);
      route();
    } catch (err) {
      if (err instanceof SoftDuplicate) location.hash = `#/entry/${uuid}?dupe=1`;
      else toast(err.message);
    }
  };
  const on = (id, fn) => { const b = $(`#${id}`); if (b) b.addEventListener("click", fn); };
  on("act-confirm", () => act(() => led.confirm(uuid), "Confirmed."));
  on("act-force", () => act(() => led.confirm(uuid, true), "Confirmed."));
  on("act-reject", () => act(() => led.setStatus(uuid, "rejected"), "Rejected."));
  on("act-reopen", () => act(() => led.setStatus(uuid, CAPTURED), "Re-opened."));
  on("act-void", () => { if (confirm("Void this activity? It stays on record and counts for nothing.")) act(() => led.void(uuid), "Voided."); });
  $$("[data-dl]").forEach((a) => a.addEventListener("click", async (evt) => {
    evt.preventDefault();
    const row = ev.find((r) => String(r.id) === a.dataset.dl);
    const blob = await led.evidenceFile(row);
    if (blob) download(row.filename, new Uint8Array(await blob.arrayBuffer()), row.mime || "application/octet-stream");
  }));
}

async function screenEntries(query) {
  const status = query.get("status") || "";
  const rows = await led.listEntries(status ? { status } : {});
  const ev = await led.listEvidence();
  const sample = await sampleState(led);
  view.innerHTML = `<h1>Activities</h1>
    ${sample ? `<div class="banner sample">Sample data is loaded: activities titled “SAMPLE —” are invented. Remove them on the <a href="#/">Credentials</a> screen.</div>` : ""}
    <p><label for="status-filter" class="inline">Show</label>
    <select id="status-filter" class="auto"><option value="">every state</option>${STATES.map((s) => `<option ${s === status ? "selected" : ""}>${s}</option>`).join("")}</select></p>
    <div class="scroll"><table><thead><tr><th>Date</th><th>Activity</th><th>Hours</th><th>State</th><th>Evidence</th></tr></thead><tbody>
    ${rows.map((e) => `<tr><td>${esc(e.activity_date)}</td><td><a href="#/entry/${esc(e.uuid)}">${esc(e.title || "(untitled)")}</a><div class="small mute">${esc(e.provider)}</div></td><td>${fmt(e.hours)}</td><td><span class="pill ${esc(e.status)}">${esc(e.status)}</span></td><td>${ev.filter((x) => x.entry_uuid === e.uuid).length}</td></tr>`).join("") || `<tr><td colspan=5 class="mute">No activities${status ? ` in state ${esc(status)}` : ""}.</td></tr>`}
    </tbody></table></div>
    ${rows.length ? reportsCard() : ""}`;
  bindReports();
  $("#status-filter").addEventListener("change", (ev2) => { location.hash = ev2.target.value ? `#/entries?status=${ev2.target.value}` : "#/entries"; });
}

async function screenSubmissions() {
  const rows = await assessAll();
  const all = await led.listEntries({ status: CONFIRMED });
  let html = `<h1>Submissions</h1><p class="mute">Confirmed activities in each current cycle that you have not yet recorded as submitted to the body. Submit in the body's portal yourself (a <a href="#/packets">packet</a> has every field ready to copy), then record the date here.</p>`;
  if (!rows.length) html += `<p>Nothing tracked yet: see <a href="#/settings">Settings</a>.</p>`;
  for (const { cred, a } of rows) {
    const todo = all.filter((e) => a.cycle_start <= e.activity_date && e.activity_date < a.cycle_end && (e.applies_to == null || e.applies_to.includes(cred.id)) && cred.buckets_for(e.tags, e.markers).length && !e.submitted_to.includes(cred.body_id));
    html += `<h2>${esc(cred.name)} <span class="small mute">${esc(cred.body_name)} · ${esc(a.submission.cadence.replace(/_/g, " "))}${a.submission.self_certified ? " · self-certified" : ""}</span></h2>`;
    html += todo.length ? `<div class="scroll"><table><thead><tr><th>Date</th><th>Activity</th><th>Credits</th><th>Submitted on</th></tr></thead><tbody>${todo.map((e) => `<tr><td>${esc(e.activity_date)}</td><td><a href="#/entry/${esc(e.uuid)}">${esc(e.title)}</a></td><td>${fmt(E.creditsForEntry(cred, e))}</td><td><span class="flexrow"><input type="date" value="${E.todayISO()}" aria-label="Date submitted to ${esc(cred.body_name)}: ${esc(e.title)}" data-date="${esc(e.uuid)}|${esc(cred.body_id)}"><button type="button" class="secondary" data-mark="${esc(e.uuid)}|${esc(cred.body_id)}">Mark</button></span></td></tr>`).join("")}</tbody></table></div>` : `<p class="mute">Nothing waiting.</p>`;
  }
  view.innerHTML = html;
  $$("[data-mark]").forEach((b) => b.addEventListener("click", async () => {
    const [uuid, body] = b.dataset.mark.split("|");
    const d = $(`[data-date="${b.dataset.mark}"]`).value;
    try {
      await led.markSubmitted(uuid, body, d);
      toast("Recorded as submitted.");
      route();
    } catch (err) {
      toast(err.message);
    }
  }));
}

async function screenPackets() {
  const rows = await assessAll();
  const bodies = [...new Map(rows.map(({ cred }) => [cred.body_id, cred.body_name])).entries()];
  view.innerHTML = `<h1>Submission packets</h1>
    <p>One zip per certifying body: a cover sheet with each activity's fields in the body's form order, a manifest with every file's SHA-256, and your evidence files.
    <b>Registrar never signs in for you</b>: open the body's portal yourself and copy the fields across.</p>
    ${bodies.length ? "" : `<p>Nothing tracked yet: see <a href="#/settings">Settings</a>.</p>`}
    ${bodies.map(([id, name]) => `<section class="card"><h2 class="mt0">${esc(name)}</h2>
      <label class="plain"><input type="checkbox" data-unsub="${esc(id)}"> Only activities not yet submitted</label>
      <div class="actions"><button type="button" data-zip="${esc(id)}">Download packet (.zip)</button><button type="button" class="secondary" data-preview="${esc(id)}">Preview cover sheet</button></div>
      <pre class="text" id="cover-${esc(id)}" hidden></pre></section>`).join("")}`;
  const opts = (id) => ({ unsubmittedOnly: $(`[data-unsub="${id}"]`).checked });
  $$("[data-preview]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.preview;
    const pre = $(`#cover-${id}`);
    pre.textContent = coverSheet(await bodyPacket(led, rules, id, opts(id)));
    pre.hidden = false;
  }));
  $$("[data-zip]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.zip;
    const p = await bodyPacket(led, rules, id, opts(id));
    const { zip, manifest } = await packetZip(led, p);
    download(`registrar-packet-${id}-${p.asOf}.zip`, zip, "application/zip");
    toast(`Packet: ${manifest.files.length} file(s)${manifest.gaps.length ? `, ${manifest.gaps.length} gap(s) listed in the manifest` : ""}.`);
  }));
}

async function screenBodies() {
  const progs = rules.programs;
  view.innerHTML = `<h1>Rules</h1>
    <p>The same rules file as the self-hosted Registrar (<code>bodies.yaml</code>, swept ${esc(rules.sweep_date)}). Every figure carries its source.
    Registrar does not read certifying bodies' websites or portals on your behalf: submission is assisted fill only, and it never asks for a portal login.</p>
    <h2>Tracked credentials</h2>
    ${(await heldFirst()).map(({ c, mine }) => `<section class="card"><h3 class="mt0">${esc(c.name)} <span class="small mute">${esc(c.long_name || "")} · ${esc(c.body_name)}</span>${unverifiedBadge(c)}${mine ? ' <span class="badge held">you hold this</span>' : ""}</h3>
      <p class="small">${fmt(c.cycle_total)} ${esc(c.unit_label)} per ${c.cycle_months} months (${esc(String(c.cycle.basis || "").replace(/_/g, " "))}); grace ${c.grace_period_days} days.
      ${/^https?:/i.test(c.source_url || "") ? `<a href="${esc(c.source_url)}" rel="noreferrer noopener" target="_blank">Source</a>` : ""} ${c.verified_date ? `verified ${esc(c.verified_date)}` : ""}</p>
      ${c.caps_known === false ? `<div class="banner">Category caps are not known for this credential: totals are an upper bound and no compliance verdict is given.</div>` : ""}
      <div class="scroll"><table><thead><tr><th>Category</th><th>Rate</th><th>Cap</th><th>Group</th></tr></thead><tbody>
      ${c.buckets.map((b) => `<tr><td>${esc(b.name || b.id)}</td><td>${b.per_hour != null ? `${fmt(b.per_hour)}/hour` : b.per_item != null ? `${fmt(b.per_item)}/item` : "—"}${b.value_is_override ? " (enter the awarded value)" : ""}</td><td>${b.cap == null ? "none" : fmt(b.cap)}${unverifiedBadge(c, "cap", b)}</td><td>${esc(b.group || "—")}</td></tr>`).join("")}
      </tbody></table></div>
      ${c.groups.length ? `<p class="small">${c.groups.map((g) => `${esc(g.name)}: ${g.floor ? `at least ${fmt(g.floor)}` : ""}${g.floor && g.cap != null ? ", " : ""}${g.cap != null ? `at most ${fmt(g.cap)}` : ""}`).join("; ")}</p>` : ""}
      ${c.unverified_reasons().length ? `<details><summary class="small">Why some values are unverified</summary><ul class="small">${c.unverified_reasons().map((r) => `<li>${esc(r)}</li>`).join("")}</ul></details>` : ""}
    </section>`).join("")}
    <h2>Catalogue (reference only)</h2>
    <p class="small mute">Maintenance totals for other certifications, from each body's published pages. Not scored: the engine scores only the tracked credentials above.</p>
    ${progs.map((p) => `<details class="card"><summary><b>${esc(p.body_name)}</b> <span class="small mute">${(p.certifications || []).length} certifications · ${esc(p.unit_label || "")}</span>${p.verified ? "" : ' <span class="badge unv">UNVERIFIED</span>'}</summary>
      <div class="scroll"><table><thead><tr><th>Certification</th><th>Per cycle</th><th>Months</th><th>Status</th></tr></thead><tbody>
      ${(p.certifications || []).map((c) => `<tr><td>${esc(c.name)} <span class="small mute">${esc(c.long_name || "")}</span></td><td>${c.cycle_total == null ? "—" : fmt(c.cycle_total)}${c.verified ? "" : ' <span class="badge unv">UNVERIFIED</span>'}</td><td>${esc(c.cycle_months ?? p.cycle_months ?? "—")}</td><td>${esc(c.status || "")}${c.status_note ? ` <span class="small mute">${esc(c.status_note)}</span>` : ""}</td></tr>`).join("")}
      </tbody></table></div></details>`).join("")}`;
}

async function screenSettings() {
  const states = Object.fromEntries((await led.allCredentialState()).map((s) => [s.credential, s]));
  const holder = (await led.getConfig("attendee_name")) || "";
  const llm = LLM.status();
  view.innerHTML = `<h1>Settings</h1>
    <form id="settings-form" novalidate>
      <label for="s-holder">Your name as it appears on certificates</label>
      <input type="text" id="s-holder" name="attendee_name" value="${esc(holder)}" autocomplete="name">
      <p class="small mute">Used to check that a certificate is yours and to fill the attendee field in packets.</p>
      <h2>Credentials you hold</h2>
      <p class="small mute">Tick each one you hold and enter the date its current cycle began (certification or membership date).</p>
      ${(await heldFirst()).map(({ c }) => {
        const s = states[c.id] || {};
        const on = s.credential && Number(s.active ?? 1);
        return `<fieldset><legend>${esc(c.name)} <span class="small mute">${esc(c.body_name)}</span>${unverifiedBadge(c)}</legend>
          <label class="plain"><input type="checkbox" name="track-${esc(c.id)}" ${on ? "checked" : ""}> I hold ${esc(c.name)}</label>
          <div class="row"><div><label for="a-${esc(c.id)}">Cycle start</label><input type="date" id="a-${esc(c.id)}" name="anchor-${esc(c.id)}" value="${esc(s.anchor_date || "")}"></div>
          <div><label for="co-${esc(c.id)}">Carried in from last cycle (${esc(c.unit_label)})</label><input type="number" min="0" step="0.25" id="co-${esc(c.id)}" name="carry-${esc(c.id)}" value="${esc(s.carryover_in || 0)}"></div></div>
        </fieldset>`;
      }).join("")}
      <div class="actions"><button type="submit">Save settings</button></div>
      <div id="settings-error" role="alert"></div>
    </form>
    <h2>Optional: use my own API key</h2>
    <div class="byok">
      <p class="mt0"><b>Off by default.</b> When set, the Log screen offers a button that sends the <b>text already read from a certificate</b> (never the file, never your ledger) to the provider below, to fill the form. That text leaves your browser.
      The key is kept in memory for this tab only: it is never saved, exported or sent anywhere else, and reloading the page forgets it.</p>
      <p class="small">Status: <b id="llm-status">${llm.on ? `on (${esc(LLM.PROVIDERS[llm.provider].label)}, ${esc(llm.model)})` : "off"}</b></p>
      <form id="llm-form" novalidate autocomplete="off">
        <div class="row">
          <div><label for="l-provider">Provider</label><select id="l-provider" name="provider">${Object.entries(LLM.PROVIDERS).map(([k, p]) => `<option value="${k}">${esc(p.label)}</option>`).join("")}</select></div>
          <div><label for="l-model">Model</label><input type="text" id="l-model" name="model" value="${esc(LLM.PROVIDERS.anthropic.defaultModel)}"></div>
        </div>
        <label for="l-base">API base URL</label><input type="url" id="l-base" name="baseUrl" value="${esc(LLM.PROVIDERS.anthropic.baseUrl)}">
        <p class="small mute">This site's security policy allows only api.anthropic.com, api.openai.com and localhost. Other OpenAI-compatible hosts need a self-hosted copy with an edited policy.</p>
        <label for="l-key">API key</label><input type="password" id="l-key" name="key" autocomplete="off" spellcheck="false">
        <div class="actions"><button type="submit">Use for this session</button><button type="button" class="secondary" id="llm-forget">Forget key</button></div>
      </form>
    </div>`;
  $("#l-provider").addEventListener("change", (ev) => {
    const p = LLM.PROVIDERS[ev.target.value];
    $("#l-model").value = p.defaultModel;
    $("#l-base").value = p.baseUrl;
  });
  $("#llm-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    try {
      LLM.configure({ provider: fd.get("provider"), key: fd.get("key"), model: fd.get("model"), baseUrl: fd.get("baseUrl") });
      $("#l-key").value = "";
      toast("Key set for this session only.");
      route();
    } catch (err) {
      toast(err.message);
    }
  });
  $("#llm-forget").addEventListener("click", () => { LLM.forget(); toast("Key forgotten."); route(); });
  $("#settings-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    const errBox = $("#settings-error");
    errBox.innerHTML = "";
    try {
      const name = String(fd.get("attendee_name") || "").trim();
      if (name || holder) await led.setConfig("attendee_name", name);
      for (const c of rules.credentials) {
        const on = fd.get(`track-${c.id}`) === "on";
        const anchor = fd.get(`anchor-${c.id}`) || null;
        if (on || states[c.id]) {
          await led.setCredentialState(c.id, { anchor_date: anchor, carryover_in: Number(fd.get(`carry-${c.id}`) || 0), active: on ? 1 : 0 });
        }
      }
      toast("Settings saved.");
      location.hash = "#/";
    } catch (err) {
      errBox.innerHTML = `<div class="banner bad">${esc(err.message)}</div>`;
    }
  });
}

function notFound() {
  view.innerHTML = `<h1>Not found</h1><p><a href="#/">Back to your credentials</a></p>`;
}

// ---------------------------------------------------------------- banner actions

async function doExport() {
  const doc = await exportLedger(led, { includeFiles: true });
  download(`registrar-ledger-${E.todayISO()}.json`, new TextEncoder().encode(JSON.stringify(doc, null, 1)), "application/json");
  toast(`Exported ${doc.entries.length} activities and ${Object.keys(doc.registrar.files).length} evidence file(s).`);
}

async function doImport(file) {
  let doc;
  try {
    doc = JSON.parse(await file.text());
  } catch {
    toast("That file is not JSON.");
    return;
  }
  try {
    const r = await importLedger(led, doc);
    toast(`Imported ${r.entries_added} activities (${r.entries_skipped} already here), ${r.files_stored} file(s)${r.files_refused.length ? `; ${r.files_refused.length} altered file(s) refused` : ""}${r.files_absent ? `; ${r.files_absent} file(s) not in the export` : ""}.`);
    route();
  } catch (err) {
    toast(`Import refused: ${err.message}`);
  }
}

async function doWipe() {
  if (!confirm("Wipe everything Registrar holds in this browser: every activity, evidence file and setting? This cannot be undone. Export first if you want a copy.")) return;
  await led.wipe();
  LLM.forget();
  settled = false;
  toast("Wiped. This browser holds no Registrar data now.");
  location.hash = "#/";
  route();
}

async function storageNote() {
  try {
    const est = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : null;
    const persisted = navigator.storage && navigator.storage.persisted ? await navigator.storage.persisted() : false;
    const s = await led.stats();
    const n = Object.values(s.entries).reduce((x, y) => x + y, 0);
    document.getElementById("storage-note").textContent = `${n} activities, ${s.evidence_files} evidence file(s)${est ? `, ${(est.usage / 1048576).toFixed(1)} MB used` : ""}${persisted ? "" : " · storage not marked persistent: export backups"}`;
  } catch {
    /* informational only */
  }
}

// ---------------------------------------------------------------- router

async function route() {
  const hash = location.hash || "#/";
  const [path, qs] = hash.slice(1).split("?");
  const query = new URLSearchParams(qs || "");
  setNav(`#${path}`);
  let m;
  try {
    if (await firstRun()) await screenWelcome();
    else if (path === "/" || path === "" || path === "/welcome") await screenHome();
    else if ((m = /^\/credential\/([\w-]+)$/.exec(path))) await screenCredential(m[1]);
    else if (path === "/log") await screenLog();
    else if ((m = /^\/entry\/([0-9a-f]{32})\/edit$/.exec(path))) await screenLog({ uuid: m[1] });
    else if ((m = /^\/entry\/([0-9a-f]{32})$/.exec(path))) await screenEntry(m[1], query);
    else if (path === "/entries") await screenEntries(query);
    else if (path === "/submissions") await screenSubmissions();
    else if (path === "/packets") await screenPackets();
    else if (path === "/bodies") await screenBodies();
    else if (path === "/settings") await screenSettings();
    else notFound();
  } catch (err) {
    view.innerHTML = `<div class="banner bad">Something went wrong: ${esc(err.message)}</div>`;
    console.error(err);
  }
  // Widths go through the CSSOM: the CSP forbids inline style attributes.
  $$("[data-w]").forEach((el) => (el.style.width = `${el.dataset.w}%`));
  $$("[data-left]").forEach((el) => (el.style.left = `${el.dataset.left}%`));
  storageNote();
  document.title = `${(view.querySelector("h1") || {}).textContent || "Registrar"} · Registrar`;
}

async function main() {
  const data = await (await fetch("./data.json")).json();
  rules = E.loadRules(data);
  try {
    build = await (await fetch("./version.json")).json();
  } catch {
    /* a source checkout served as-is has no version.json */
  }
  const ver = document.getElementById("version");
  if (ver && build.version) {
    ver.innerHTML = `Registrar <a href="${RELEASES}/tag/v${esc(build.version)}">v${esc(build.version)} release notes</a> · build <code title="SHA-256 of the built files, first 16 hex digits">${esc(build.build)}</code>`;
  }
  led = new Ledger(await IdbStore.open());
  window.__registrar = { led, rules }; // for the automated check in e2e/verify.mjs
  document.getElementById("btn-export").addEventListener("click", doExport);
  document.getElementById("btn-import").addEventListener("click", () => document.getElementById("import-file").click());
  document.getElementById("import-file").addEventListener("change", (ev) => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (f) doImport(f);
  });
  document.getElementById("btn-wipe").addEventListener("click", doWipe);
  window.addEventListener("hashchange", () => {
    route();
    view.focus({ preventScroll: true });
  });
  requestPersistence();
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  }
  await route();
}

main().catch((err) => {
  view.innerHTML = `<div class="banner bad">Registrar could not start: ${esc(err.message)}. It needs a browser with IndexedDB (a private window may not provide it).</div>`;
});

// Build the landing page's "How each body counts" section from the rules
// data the browser app uses (data.json, exported from bodies.yaml), so the
// page and the app cannot disagree.
//
//   renderRules(data)  -> { html, css }   pure; used by the build and the tests
//   writeRules(data, siteDir)             rewrites the marked blocks in
//                                         site/index.html and site/style.css
//
// The landing page runs no scripts (its Content-Security-Policy has no
// script-src), so the body filter is plain HTML and CSS: one checkbox per
// body, a reset button for "All", one generated CSS rule per body that
// hides its card while other bodies are ticked and it is not, and generated
// rules that pick the "N of M" number to show.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

export const HTML_BEGIN = "<!-- rules:begin (built from bodies.yaml by npm run build; do not edit by hand) -->";
export const HTML_END = "<!-- rules:end -->";
export const CSS_BEGIN = "/* rules-filter:begin (built from bodies.yaml by npm run build; do not edit by hand) */";
export const CSS_END = "/* rules-filter:end */";

const BADGE = '<span class="badge">UNVERIFIED</span>';

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Escaped text with each {unverified} marker turned into the badge; a
// full stop or comma right after it stays on the badge's line.
function text(s) {
  return esc(s).replace(/\s*\{unverified\}([.,;:)])?/g, (_, p) => (p ? ` <span class="nowrap">${BADGE}${p}</span>` : ` ${BADGE}`));
}

function years(months) {
  if (!Number.isInteger(months) || months <= 0 || months % 12) throw new Error(`cycle of ${months} months is not a whole number of years`);
  const y = months / 12;
  return `${y} year${y === 1 ? "" : "s"}`;
}

function slug(id) {
  if (!/^[a-z0-9_-]+$/.test(id)) throw new Error(`body_id ${JSON.stringify(id)} is not usable as an HTML id`);
  return id;
}

const FIELDS = [
  ["cycle", "Cycle"],
  ["cycle_total", "Cycle total"],
  ["annual_minimum", "Annual minimum"],
  ["categories", "Categories and caps"],
  ["submission", "How you submit"],
  ["policy", "Policy"],
];

export function bodiesOf(data) {
  const bodies = (data.programs || []).filter((p) => p.landing);
  if (!bodies.length) throw new Error("no program in the data has a landing: block");
  for (const p of bodies) {
    for (const k of ["cycle_total", "annual_minimum", "categories", "submission"]) {
      if (typeof p.landing[k] !== "string" || !p.landing[k].trim()) throw new Error(`${p.body_id}: landing.${k} is missing`);
    }
  }
  const ids = bodies.map((p) => p.body_id);
  if (new Set(ids).size !== ids.length) throw new Error(`duplicate body_id among ${ids.join(", ")}`);
  return bodies;
}

function card(p) {
  const id = slug(p.body_id);
  const creds = (p.certifications || []).filter((c) => c.status === "offered").map((c) => c.name);
  const value = {
    cycle: `${years(p.cycle_months)}${p.landing.cycle_unverified ? ` ${BADGE}` : ""}`,
    cycle_total: text(p.landing.cycle_total),
    annual_minimum: text(p.landing.annual_minimum),
    categories: text(p.landing.categories),
    submission: text(p.landing.submission),
    policy: `<a href="${esc(p.source_url)}">${esc(p.body_name)} policy page</a>`,
  };
  const facts = FIELDS.map(([k, label]) => `          <div class="rb-${k.replace(/_/g, "-")}"><dt>${label}</dt><dd>${value[k]}</dd></div>`).join("\n");
  return `      <li class="rb rb-${id}">
        <article aria-labelledby="rb-${id}">
          <header class="rb-head">
            <h3 id="rb-${id}">${esc(p.body_name)}</h3>
            <p class="rb-unit">Unit: ${esc(p.unit_label)}</p>
            ${creds.length ? `<p class="rb-creds"><span class="vh">${creds.length === 1 ? "Credential" : "Credentials"}: </span>${creds.map(esc).join(", ")}</p>` : ""}
          </header>
          <dl class="rb-facts">
${facts}
          </dl>
        </article>
      </li>`;
}

export function renderRules(data) {
  const bodies = bodiesOf(data);
  const n = bodies.length;
  const chips = bodies.map((p) => `        <input type="checkbox" class="chip-input" id="rf-${slug(p.body_id)}"><label class="chip" for="rf-${p.body_id}">${esc(p.body_name)}</label>`).join("\n");
  // The count is real text, so assistive technology reads it: one number
  // per possible count, and the CSS below shows exactly one of them.
  const nums = [`<span class="rn rn-0">${n}</span>`, ...bodies.map((_, i) => `<span class="rn rn-${i + 1}">${i + 1}</span>`)].join("");
  const heads = FIELDS.map(([, label]) => `<span>${label}</span>`).join("");
  const html = `${HTML_BEGIN}
    <div class="rules-board">
    <form class="rules-filter">
      <fieldset>
        <legend>Show bodies</legend>
        <button type="reset" class="chip chip-all">All<span class="vh"> bodies</span></button>
${chips}
      </fieldset>
      <p class="rules-count" aria-live="polite">${nums} of ${n} bodies shown</p>
    </form>
    <p class="small rules-badge-key">${BADGE} means that value
    could not be confirmed on the body's own published pages: it is shown,
    but you should check it before you rely on it.</p>
    <div class="rules-table">
    <div class="rb-colheads" aria-hidden="true"><span>Body</span>${heads}</div>
    <ul class="rules-cards" role="list">
${bodies.map(card).join("\n")}
    </ul>
    </div>
    </div>
    ${HTML_END}`;
  // Ticking any body hides every card whose own box is not ticked. One rule
  // per body, so the card keeps whatever display the layout gives it.
  const rules = bodies.map((p) => `.rules-board:has(.chip-input:checked):not(:has(#rf-${p.body_id}:checked)) .rb-${p.body_id}`);
  // "At least k ticked" is k ticked boxes in sibling order; exactly k is
  // that and not k + 1. rn-0 (every body) is the default.
  const atLeast = (k) => `.rules-filter:has(${Array(k).fill(".chip-input:checked").join(" ~ ")})`;
  const counts = bodies.map((_, i) => `${atLeast(i + 1)}${i + 1 < n ? `:not(:has(${atLeast(i + 2).slice(".rules-filter:has(".length, -1)}))` : ""} .rn-${i + 1} { display: inline; }`);
  const css = `${CSS_BEGIN}
${rules.join(",\n")} { display: none; }
${atLeast(1)} .rn-0 { display: none; }
${counts.join("\n")}
${CSS_END}`;
  return { html, css, count: n };
}

function replaceBetween(src, begin, end, block, what) {
  const a = src.indexOf(begin);
  const b = src.indexOf(end);
  if (a < 0 || b < a || src.indexOf(begin, a + 1) >= 0) throw new Error(`${what}: expected exactly one ${begin} ... ${end} block`);
  return src.slice(0, a) + block + src.slice(b + end.length);
}

export function applyRules(indexHtml, styleCss, data) {
  const { html, css, count } = renderRules(data);
  return {
    count,
    indexHtml: replaceBetween(indexHtml, HTML_BEGIN, HTML_END, html, "site/index.html"),
    styleCss: replaceBetween(styleCss, CSS_BEGIN, CSS_END, css, "site/style.css"),
  };
}

export function writeRules(data, siteDir) {
  const ip = join(siteDir, "index.html");
  const sp = join(siteDir, "style.css");
  const out = applyRules(readFileSync(ip, "utf8"), readFileSync(sp, "utf8"), data);
  writeFileSync(ip, out.indexHtml);
  writeFileSync(sp, out.styleCss);
  return out;
}

// The landing page's "How each body counts" section is built from
// bodies.yaml. These tests export bodies.yaml afresh, render the section and
// require site/index.html and site/style.css to match it, so a rules change
// that was not rebuilt fails here.
import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { applyRules, renderRules, HTML_BEGIN, HTML_END } from "../tools/site_rules.mjs";

const webapp = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(webapp);

function freshData() {
  const dir = mkdtempSync(join(tmpdir(), "registrar-rules-"));
  try {
    const out = join(dir, "data.json");
    execFileSync("python3", [join(webapp, "tools", "export_data.py"), out], { stdio: "pipe" });
    return JSON.parse(readFileSync(out, "utf8"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const data = freshData();
const indexHtml = readFileSync(join(repo, "site", "index.html"), "utf8");
const styleCss = readFileSync(join(repo, "site", "style.css"), "utf8");

test("site/index.html and site/style.css carry the section built from bodies.yaml", () => {
  const built = applyRules(indexHtml, styleCss, data);
  assert.equal(built.indexHtml, indexHtml, "site/index.html is stale: run npm run build");
  assert.equal(built.styleCss, styleCss, "site/style.css is stale: run npm run build");
});

test("the committed app data.json is the same export", () => {
  const app = JSON.parse(readFileSync(join(repo, "site", "app", "data.json"), "utf8"));
  assert.deepEqual(app.programs.map((p) => p.landing), data.programs.map((p) => p.landing));
});

test("one card and one chip per body, in bodies.yaml order, with its policy link", () => {
  const { html, count } = renderRules(data);
  assert.equal(count, 10);
  const cards = [...html.matchAll(/<h3 id="rb-([a-z0-9_-]+)">([^<]+)<\/h3>/g)].map((m) => m[1]);
  const chips = [...html.matchAll(/id="rf-([a-z0-9_-]+)"/g)].map((m) => m[1]);
  const ids = data.programs.map((p) => p.body_id);
  assert.deepEqual(cards, ids);
  assert.deepEqual(chips, ids);
  for (const p of data.programs) assert.ok(html.includes(`<a href="${p.source_url}">${p.body_name} policy page</a>`), p.body_id);
});

test("a changed value in bodies.yaml data changes the page", () => {
  const changed = structuredClone(data);
  changed.programs[0].landing.cycle_total = "999 {unverified}";
  const { html } = renderRules(changed);
  assert.ok(html.includes('<dd>999 <span class="badge">UNVERIFIED</span></dd>'));
  assert.notEqual(applyRules(indexHtml, styleCss, changed).indexHtml, indexHtml);
});

test("badges: every {unverified} marker and cycle_unverified flag becomes one badge", () => {
  const { html } = renderRules(data);
  const markers = data.programs.reduce((n, p) => n + Object.values(p.landing).filter((v) => typeof v === "string").join(" ").split("{unverified}").length - 1 + (p.landing.cycle_unverified ? 1 : 0), 0);
  const section = html.slice(html.indexOf('<ul class="rules-cards"'));
  assert.equal(section.split('<span class="badge">UNVERIFIED</span>').length - 1, markers);
  assert.ok(!html.includes("{unverified}"));
});

test("text is escaped", () => {
  const changed = structuredClone(data);
  changed.programs[0].landing.categories = '<img src=x> & "q"';
  assert.ok(renderRules(changed).html.includes("&lt;img src=x&gt; &amp; &quot;q&quot;"));
});

test("the page has exactly one generated block", () => {
  assert.equal(indexHtml.split(HTML_BEGIN).length, 2);
  assert.equal(indexHtml.split(HTML_END).length, 2);
});

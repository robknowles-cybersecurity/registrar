// The browser engine must give the same answers as cpebot/engine.py.
// Random (seeded) ledgers go through both; any difference fails the test.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as E from "../src/engine.js";

const here = dirname(fileURLToPath(import.meta.url));
const data = JSON.parse(readFileSync(join(here, "..", "src", "data.json"), "utf8"));
const rules = E.loadRules(data);

function rng(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function pick(r, xs) {
  return xs[Math.floor(r() * xs.length)];
}

function makeEntries(r, n) {
  const statuses = ["confirmed", "confirmed", "confirmed", "confirmed", "captured", "void", "superseded", "rejected"];
  const out = [];
  for (let i = 0; i < n; i++) {
    const y = 2023 + Math.floor(r() * 4);
    const m = 1 + Math.floor(r() * 12);
    const d = 1 + Math.floor(r() * 28);
    const tags = [pick(r, rules.tags)];
    if (r() < 0.2) tags.push(pick(r, rules.tags));
    const markers = rules.markers.filter(() => r() < 0.3);
    const e = {
      uuid: `e${i}`,
      activity_date: `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`,
      title: `Activity ${i}`,
      provider: pick(r, ["Example Institute", "Sample Academy", ""]),
      hours: Math.round(r() * 160) / 8,
      count: 1 + Math.floor(r() * 3),
      tags,
      markers,
      overrides: {},
      applies_to: null,
      status: pick(r, statuses),
      submitted_to: r() < 0.3 ? [pick(r, ["isc2", "eccouncil", "acfe", "comptia"])] : [],
    };
    if (r() < 0.1) e.overrides[pick(r, rules.credentials).id] = Math.round(r() * 40);
    if (r() < 0.1) e.applies_to = [pick(r, rules.credentials).id];
    out.push(e);
  }
  return out;
}

test("JS engine matches cpebot/engine.py on 140 random ledgers", () => {
  const r = rng(20261021);
  const cases = [];
  for (let k = 0; k < 20; k++) {
    const entries = makeEntries(r, 10 + Math.floor(r() * 120));
    for (const cred of rules.credentials) {
      cases.push({
        credential: cred.id,
        entries,
        anchor: `${2022 + Math.floor(r() * 4)}-${String(1 + Math.floor(r() * 12)).padStart(2, "0")}-${String(1 + Math.floor(r() * 28)).padStart(2, "0")}`,
        as_of: `2026-${String(1 + Math.floor(r() * 12)).padStart(2, "0")}-15`,
        carryover_in: r() < 0.2 ? Math.round(r() * 30) : 0,
      });
    }
  }
  const py = JSON.parse(execFileSync("python3", [join(here, "py_assess.py")], {
    input: JSON.stringify(cases), maxBuffer: 256 * 1024 * 1024,
  }).toString());
  assert.equal(py.length, cases.length);
  const close = (a, b, path) => assert.ok(Math.abs(a - b) < 1e-6, `${path}: js ${a} vs py ${b}`);
  let compared = 0;
  cases.forEach((c, i) => {
    const cred = rules.get(c.credential);
    const p = py[i];
    let js;
    try {
      js = E.assess(cred, c.entries, { anchor: c.anchor, asOf: c.as_of, carryoverIn: c.carryover_in });
    } catch (err) {
      assert.ok(p.error, `case ${i}: js threw ${err.message}, python did not`);
      return;
    }
    const where = `case ${i} ${c.credential}`;
    for (const k of ["cycle_start", "cycle_end", "grace_end", "verdict", "advisory_only",
      "entries_counted", "entries_ignored", "entries_pending"]) {
      assert.deepEqual(js[k], p[k], `${where}.${k}`);
    }
    for (const k of ["awarded", "naive_total", "forfeited", "carryover_out", "gap", "total_with_carryover"]) {
      close(js[k], p[k], `${where}.${k}`);
    }
    close(E.naiveAssess(cred, c.entries, { anchor: c.anchor, asOf: c.as_of }), p.naive, `${where}.naive`);
    assert.deepEqual(js.unmet_floors, p.unmet_floors, `${where}.unmet_floors`);
    assert.deepEqual(js.buckets, p.buckets, `${where}.buckets`);
    assert.deepEqual(js.groups, p.groups, `${where}.groups`);
    assert.deepEqual(js.annual, p.annual, `${where}.annual`);
    assert.deepEqual(js.submission, p.submission, `${where}.submission`);
    assert.deepEqual(js.unverified_reasons, p.unverified_reasons, `${where}.unverified_reasons`);
    const jp = E.pace(js);
    for (const k of Object.keys(p.pace)) {
      if (typeof p.pace[k] === "number") close(jp[k], p.pace[k], `${where}.pace.${k}`);
      else assert.equal(jp[k], p.pace[k], `${where}.pace.${k}`);
    }
    compared++;
  });
  assert.ok(compared > 100, `only ${compared} cases compared`);
});

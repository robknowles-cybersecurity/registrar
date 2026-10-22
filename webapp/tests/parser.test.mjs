// The evidence parser against 16 synthetic certificates (tests/fixtures/):
// text PDFs in varied layouts covering every v1 body. Text is extracted with
// the same pdf.js the app ships (its Node build) and the same line builder.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as pdfjs from "../node_modules/pdfjs-dist/legacy/build/pdf.mjs";
import { pdfText } from "../src/pdftext.js";
import { parseCertificate, findDates } from "../src/parser.js";

const here = dirname(fileURLToPath(import.meta.url));
const dir = join(here, "fixtures", "certificates");
const expected = JSON.parse(readFileSync(join(here, "fixtures", "expected.json"), "utf8"));
const FIELDS = ["activity_date", "hours", "provider", "title", "holder_name"];

const results = {};
const NAMES_BODY = new Set(["01-isc2-webinar-centred.pdf", "02-comptia-ceu-letter.pdf", "03-eccouncil-table.pdf",
  "04-gcitp-pdu-insider.pdf", "05-atap-chapter-meeting.pdf", "06-acfe-fraud-ethics.pdf", "12-ceu-conversion.pdf",
  "15-acfe-table-fraud.pdf", "16-portrait-award.pdf"]);

for (const name of readdirSync(dir).filter((f) => f.endsWith(".pdf")).sort()) {
  test(`parses ${name}`, async () => {
    const text = await pdfText(pdfjs, new Uint8Array(readFileSync(join(dir, name))));
    const got = parseCertificate(text);
    const exp = expected[name];
    assert.ok(exp, `no expectation for ${name}`);
    results[name] = got;
    for (const f of FIELDS) {
      const g = got.fields[f];
      assert.ok(g.confidence, `${name}: ${f} not found\n--- text ---\n${text}`);
      if (f === "hours") assert.equal(g.value, exp.hours, `${name}: hours (${g.why})`);
      else assert.equal(g.value, exp[f], `${name}: ${f} (${g.why})\n--- text ---\n${text}`);
    }
    // Only certificates that name the body (or its unit) can hint at it.
    if (NAMES_BODY.has(name)) {
      assert.ok(got.suggestions.credentials.includes(exp.credential), `${name}: credential hint ${exp.credential} missing from ${got.suggestions.credentials}`);
    }
  });
}

test("at least 15 certificates, every v1 body covered", () => {
  const names = Object.keys(expected);
  assert.ok(names.length >= 15, `${names.length} fixtures`);
  const creds = new Set(Object.values(expected).map((e) => e.credential));
  for (const c of ["cissp", "cysa_plus", "ceh", "gcitp", "ctm", "cfe"]) assert.ok(creds.has(c), c);
});

test("an expiry date is never taken as the activity date", () => {
  const r = parseCertificate("Certificate\nValid until: 2029-05-30\nJordan Sample\nZero Trust 101");
  assert.notEqual(r.fields.activity_date.value, "2029-05-30");
});

test("dates: US, day-first, ISO, written, ranges", () => {
  const iso = (s) => findDates(s).map((d) => d.iso);
  assert.deepEqual(iso("on 03/04/2026"), ["2026-03-04"]);
  assert.deepEqual(iso("on 24/03/2026"), ["2026-03-24"]);
  assert.deepEqual(iso("on 04.03.2026"), ["2026-03-04"]);
  assert.deepEqual(iso("2026-02-30"), []);
  assert.deepEqual(iso("5th of March, 2026"), ["2026-03-05"]);
  assert.deepEqual(iso("Sept. 9, 2026"), ["2026-09-09"]);
  assert.deepEqual(iso("September 15–17, 2026"), ["2026-09-17"]);
  assert.deepEqual(iso("15-17 September 2026"), ["2026-09-17"]);
});

test("nothing found means null values and null confidence, not guesses", () => {
  const r = parseCertificate("");
  for (const f of FIELDS) {
    assert.equal(r.fields[f].value, null);
    assert.equal(r.fields[f].confidence, null);
  }
});

test("the holder name from Settings wins when it appears", () => {
  const r = parseCertificate("Awarded to\nJ. Q. Sample-Holder\nfor Something", { holderHint: "J. Q. Sample-Holder" });
  assert.equal(r.fields.holder_name.value, "J. Q. Sample-Holder");
  assert.equal(r.fields.holder_name.confidence, "high");
});

// Held-out certificates, written AFTER the parser was tuned on fixtures/ and
// never tuned against. This is a score, not a pass/fail gate: it measures how
// the heuristics do on wording they were not fitted to.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseCertificate } from "../../src/parser.js";

const here = dirname(fileURLToPath(import.meta.url));
const cases = JSON.parse(readFileSync(join(here, "cases.json"), "utf8"));
let right = 0;
let total = 0;
for (const c of cases) {
  const r = parseCertificate(c.text);
  for (const [f, want] of Object.entries(c.expect)) {
    const got = r.fields[f];
    const ok = got.value === want;
    total++;
    if (ok) right++;
    else console.log(`MISS ${c.name} ${f}: got ${JSON.stringify(got.value)} (${got.confidence}) want ${JSON.stringify(want)}`);
  }
}
console.log(`held-out: ${right}/${total} fields correct across ${cases.length} certificates`);

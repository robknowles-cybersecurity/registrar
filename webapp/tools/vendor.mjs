// pdf.js ships as its "legacy" build: the modern build needs very new JS
// (Map.prototype.getOrInsertComputed) that current Chromium and Safari lack.
// Copy the pinned front-end libraries out of node_modules into src/vendor/,
// and record each file's SHA-256 and SRI (sha384) in src/vendor/MANIFEST.json.
// Run after `npm ci --ignore-scripts`. The app never loads anything from a CDN:
// these copies are what ships.
import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const nm = join(root, "node_modules");
const out = join(root, "src", "vendor");
const lock = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8"));

const FILES = [
  ["pdfjs-dist", "legacy/build/pdf.min.mjs", "pdfjs/pdf.min.mjs"],
  ["pdfjs-dist", "legacy/build/pdf.worker.min.mjs", "pdfjs/pdf.worker.min.mjs"],
  ["pdfjs-dist", "LICENSE", "pdfjs/LICENSE"],
  ["tesseract.js", "dist/tesseract.min.js", "tesseract/tesseract.min.js"],
  ["tesseract.js", "dist/worker.min.js", "tesseract/worker.min.js"],
  ["tesseract.js", "LICENSE.md", "tesseract/LICENSE.md"],
  ["tesseract.js-core", "tesseract-core-relaxedsimd-lstm.wasm.js", "tesseract/core/tesseract-core-relaxedsimd-lstm.wasm.js"],
  ["tesseract.js-core", "tesseract-core-simd-lstm.wasm.js", "tesseract/core/tesseract-core-simd-lstm.wasm.js"],
  ["tesseract.js-core", "tesseract-core-lstm.wasm.js", "tesseract/core/tesseract-core-lstm.wasm.js"],
  ["tesseract.js-core", "LICENSE", "tesseract/core/LICENSE"],
  ["@tesseract.js-data/eng", "4.0.0_best_int/eng.traineddata.gz", "tesseract/lang/eng.traineddata.gz"],
];

rmSync(out, { recursive: true, force: true });
const manifest = { note: "Vendored by tools/vendor.mjs. Do not edit by hand.", files: [] };
for (const [pkg, src, dst] of FILES) {
  const from = join(nm, pkg, src);
  const to = join(out, dst);
  mkdirSync(dirname(to), { recursive: true });
  copyFileSync(from, to);
  const buf = readFileSync(to);
  const meta = lock.packages[`node_modules/${pkg}`];
  manifest.files.push({
    path: `vendor/${dst}`,
    package: pkg,
    version: meta.version,
    package_integrity: meta.integrity,
    source: `${meta.resolved}#${src}`,
    bytes: buf.length,
    sha256: createHash("sha256").update(buf).digest("hex"),
    sri: "sha384-" + createHash("sha384").update(buf).digest("base64"),
  });
}
writeFileSync(join(out, "MANIFEST.json"), JSON.stringify(manifest, null, 1) + "\n");
console.log(`vendored ${manifest.files.length} files into src/vendor/`);

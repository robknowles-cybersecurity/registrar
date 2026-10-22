// Build the browser app into site/app/ (deployed with the rest of site/).
//
//   1. data.json from bodies.yaml + packetmaps/ via the Python loaders
//   2. copy src/ (app code + vendored libraries) to site/app/
//   3. write the service worker with a precache list and a content hash
//
// No bundler, no minifier, no network: the output is the source, as shipped.
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(root);
const src = join(root, "src");
const out = join(repo, "site", "app");

execFileSync("python3", [join(root, "tools", "export_data.py"), join(src, "data.json")], { stdio: "inherit" });

const manifest = JSON.parse(readFileSync(join(src, "vendor", "MANIFEST.json"), "utf8"));
for (const f of manifest.files) {
  const buf = readFileSync(join(src, f.path));
  const sha = createHash("sha256").update(buf).digest("hex");
  if (sha !== f.sha256) throw new Error(`${f.path}: SHA-256 ${sha} does not match MANIFEST.json ${f.sha256}`);
}

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
cpSync(src, out, { recursive: true, filter: (p) => !p.endsWith(`${sep}sw.js`) });

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
const files = walk(out).map((p) => relative(out, p).split(sep).join("/")).sort();
const shell = files.filter((f) => !f.startsWith("vendor/tesseract/") && f !== "vendor/MANIFEST.json");
const hash = createHash("sha256");
for (const f of files) hash.update(f).update(readFileSync(join(out, f)));
const version = hash.digest("hex").slice(0, 16);
const sw = readFileSync(join(src, "sw.js"), "utf8")
  .replace("__BUILD_VERSION__", version)
  .replace("__PRECACHE__", JSON.stringify(["./", ...shell.map((f) => `./${f}`), "./version.json"], null, 1));
writeFileSync(join(out, "sw.js"), sw);
// What the footer shows: the release version and this build's content hash.
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
writeFileSync(join(out, "version.json"), `${JSON.stringify({ version: pkg.version, build: version })}\n`);
writeFileSync(join(out, "BUILD.txt"), `Built from webapp/src by webapp/tools/build.mjs. Do not edit here.\nversion ${version}\n${files.length + 2} files\n`);
console.log(`built site/app: ${files.length + 2} files, version ${pkg.version}, build ${version}`);
if (!existsSync(join(out, "index.html"))) throw new Error("no index.html in the build");

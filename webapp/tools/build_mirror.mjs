// Build a mirror of site/ for GitHub Pages, for networks whose proxies block
// newly registered domains like ceu.tools. A GitHub Pages project site lives
// under a path prefix (https://<owner>.github.io/registrar/), so every
// root-absolute URL is rewritten under BASE_PATH.
//
//   BASE_PATH=/registrar/ node tools/build_mirror.mjs [outDir]   (default dist/mirror)
//
// site/ is read and never written: the ceu.tools build stays byte for byte as
// it was. The mirror is the same files at the same version, plus:
//
//   - <link rel="canonical"> on every page, pointing at the ceu.tools page,
//     so search engines do not split the two
//   - a one-line static banner on the landing page and the app saying what
//     this is, linking to ceu.tools. One banner per page: a page that already
//     carries site/'s alias banner (the landing page, which names the mirror
//     among its addresses) gets no second one
//   - the Content-Security-Policy from site/_headers as a <meta> in each
//     page, because GitHub Pages sends no custom headers. A meta policy
//     cannot carry frame-ancestors, so that directive is dropped; nor can
//     the other _headers lines (X-Frame-Options, Permissions-Policy,
//     Cross-Origin-Opener-Policy, X-Clacks-Overhead) be sent at all.
//     Referrer-Policy is carried as <meta name="referrer">.
//   - .nojekyll (serve files as they are) and a 404.html that links back
//     to the mirror (it replaces site/404.html, which links to ceu.tools paths)
//   - no _headers (Cloudflare Pages only; GitHub Pages would serve it as a file)
//
// The app, its manifest and its service worker already use relative paths,
// so they work under any prefix unchanged: the worker registers as
// ./sw.js and its scope is <BASE_PATH>app/. The build checks that, and fails
// if any root-absolute URL is left in the output.
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { parseHeaders, headersFor } from "../e2e/serve.mjs";

export const PUBLIC = "https://ceu.tools";
export const BANNER_TEXT = "Mirror of ceu.tools for networks that block new domains. Same files, same version.";
const BANNER_CSS = `
/* mirror:begin (GitHub Pages mirror build only, tools/build_mirror.mjs) */
.mirror-banner { margin: 0; padding: .45rem 16px; background: var(--note-soft); color: var(--ink);
  border-bottom: 1px solid var(--line); font-size: .9rem; line-height: 1.4; text-align: center; }
.mirror-banner p { margin: 0; }
.mirror-banner a { color: var(--accent); }
/* mirror:end */
`;

// One site page -> its address on ceu.tools (what Cloudflare Pages serves).
function publicPath(rel) {
  if (rel === "index.html" || rel === "404.html") return "/"; // a 404 page's canonical is the front page
  if (rel.endsWith("/index.html")) return `/${rel.slice(0, -"index.html".length)}`;
  return `/${rel.replace(/\.html$/, "")}`;
}

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function normalizeBase(base) {
  const b = `/${String(base || "/registrar/").replace(/^\/+|\/+$/g, "")}/`.replace(/\/+/g, "/");
  if (!/^\/[A-Za-z0-9._~\-/]*$/.test(b)) throw new Error(`BASE_PATH ${base}: only a plain path like /registrar/`);
  return b;
}

// The CSP that site/_headers gives a path, minus frame-ancestors (ignored in
// a meta policy, and Chromium warns about it).
export function metaPolicy(rules, path) {
  const csp = headersFor(rules, path)["content-security-policy"];
  if (!csp) throw new Error(`${path}: no Content-Security-Policy in _headers`);
  // Two policies set without a detach would be joined with ", " and both enforced.
  if (/,\s*(default|script|style|img|connect)-src\b/.test(csp)) throw new Error(`${path}: two Content-Security-Policies in _headers: ${csp}`);
  return csp.split(";").map((d) => d.trim()).filter((d) => d && !/^frame-ancestors\b/.test(d)).join("; ");
}

// Root-absolute URL -> the same file under the prefix. A page named without
// .html ("/privacy", "../privacy": Cloudflare Pages serves privacy.html) gets
// the extension, so the mirror never depends on a host resolving it.
function withHtml(path, onDisk) {
  return path && !path.endsWith("/") && !existsSync(onDisk) && existsSync(`${onDisk}.html`) ? `${path}.html` : path;
}

function rewriteUrl(url, { rel, base, siteDir }) {
  if (/^([a-z][a-z0-9+.-]*:|\/\/|#|$)/i.test(url)) return url;
  const [, path, rest] = /^([^?#]*)(.*)$/.exec(url);
  if (path.startsWith("/")) return `${base}${withHtml(path.slice(1), join(siteDir, path))}${rest}`;
  return `${withHtml(path, join(siteDir, dirname(rel), path))}${rest}`;
}

const ATTR_URL = /\b(href|src|action|poster)="([^"]*)"/g;

function rewriteHtml(html, { rel, base, siteDir, policy, banner }) {
  let out = html.replace(ATTR_URL, (all, attr, url) => `${attr}="${rewriteUrl(url, { rel, base, siteDir })}"`);

  const canonical = `${PUBLIC}${publicPath(rel)}`;
  const tag = `<link rel="canonical" href="${canonical}">`;
  const had = /<link rel="canonical" href="([^"]*)">/.exec(out);
  if (had && had[1] !== canonical) throw new Error(`${rel}: canonical ${had[1]}, expected ${canonical}`);
  const og = /<meta property="og:url" content="([^"]*)">/.exec(out);
  if (og && og[1] !== canonical) throw new Error(`${rel}: og:url ${og[1]} differs from canonical ${canonical}`);

  // The policy goes first in <head>, before anything it governs.
  const meta = `<meta http-equiv="Content-Security-Policy" content="${esc(policy)}">`;
  const hadMeta = /<meta http-equiv="Content-Security-Policy" content="([^"]*)">\n?/.exec(out);
  if (hadMeta) out = out.replace(hadMeta[0], "");
  const head = [meta];
  if (!/<meta name="referrer"/.test(out)) head.push('<meta name="referrer" content="no-referrer">');
  if (!had) head.push(tag);
  if (!/<meta charset="utf-8">\n/.test(out)) throw new Error(`${rel}: no <meta charset="utf-8"> line to anchor the policy`);
  out = out.replace(/<meta charset="utf-8">\n/, (c) => `${c}${head.join("\n")}\n`);

  // One banner per page: site/'s alias banner already names the mirror.
  if (banner && !/<aside class="alias-banner"/.test(out)) {
    const p = `<aside class="mirror-banner" aria-label="About this mirror"><p>${BANNER_TEXT.replace("ceu.tools", `<a href="${banner}">ceu.tools</a>`)}</p></aside>`;
    // After the skip link, so the skip link stays the first thing a keyboard reaches.
    const skip = /<body>\n(<a class="skip"[^>]*>[^<]*<\/a>\n)?/.exec(out);
    if (!skip) throw new Error(`${rel}: no <body> to put the banner under`);
    out = out.replace(skip[0], `${skip[0]}${p}\n`);
  }
  return out;
}

function notFoundPage(base, policy) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${esc(policy)}">
<meta name="referrer" content="no-referrer">
<link rel="canonical" href="${PUBLIC}/">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not found · ceu.tools mirror</title>
<link rel="icon" href="${base}favicon.ico" sizes="48x48">
<link rel="icon" href="${base}favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="${base}style.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<aside class="mirror-banner" aria-label="About this mirror"><p>${BANNER_TEXT.replace("ceu.tools", `<a href="${PUBLIC}/">ceu.tools</a>`)}</p></aside>
<div class="wrap">
<header>
  <p class="brand"><a href="${base}">ceu.tools</a></p>
  <h1>Page not found</h1>
</header>
<main id="main">
  <p>There is nothing at this address on the mirror.</p>
  <p><a href="${base}">Go to the mirror's front page</a> or <a href="${base}app/">open Registrar</a>.</p>
</main>
</div>
</body>
</html>
`;
}

// Root-absolute URLs outside the prefix left in built HTML, CSS or the
// manifest: must be none (on GitHub Pages they would leave the project site).
export function leftoverAbsolute(outDir, base) {
  const hits = [];
  const outside = (u) => u.startsWith("/") && !u.startsWith("//") && !u.startsWith(base);
  for (const p of walk(outDir)) {
    const rel = relative(outDir, p).split(sep).join("/");
    const text = () => readFileSync(p, "utf8");
    if (rel.endsWith(".html")) {
      for (const m of text().matchAll(/\b(href|src|srcset|action|poster|content)="(\/[^"]*)"/g)) if (outside(m[2])) hits.push(`${rel}: ${m[1]}="${m[2]}"`);
    } else if (rel.endsWith(".css")) {
      for (const m of text().matchAll(/url\(\s*["']?(\/[^"')]*)/g)) if (outside(m[1])) hits.push(`${rel}: url(${m[1]})`);
    } else if (rel.endsWith(".webmanifest")) {
      const walkJson = (v) => (typeof v === "string" ? (outside(v) ? [v] : [])
        : v && typeof v === "object" ? Object.values(v).flatMap(walkJson) : []);
      for (const v of walkJson(JSON.parse(text()))) hits.push(`${rel}: "${v}"`);
    }
  }
  return hits;
}

export function buildMirror(siteDir, outDir, baseIn) {
  const base = normalizeBase(baseIn);
  if (relative(siteDir, outDir) === "" || !relative(siteDir, outDir).startsWith("..")) throw new Error(`refusing to build into ${outDir}: it is inside site/`);
  const rules = parseHeaders(readFileSync(join(siteDir, "_headers"), "utf8"));
  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });
  cpSync(siteDir, outDir, { recursive: true, filter: (p) => relative(siteDir, p) !== "_headers" });

  const pages = [];
  for (const p of walk(outDir)) {
    const rel = relative(outDir, p).split(sep).join("/");
    if (!rel.endsWith(".html")) continue;
    const path = publicPath(rel);
    const banner = rel === "index.html" ? `${PUBLIC}/` : rel === "app/index.html" ? `${PUBLIC}/app/` : null;
    const policy = metaPolicy(rules, path);
    writeFileSync(p, rewriteHtml(readFileSync(p, "utf8"), { rel, base, siteDir, policy, banner }));
    pages.push({ rel, path, policy });
  }
  for (const css of ["style.css", "app/app.css"]) writeFileSync(join(outDir, css), readFileSync(join(outDir, css), "utf8") + BANNER_CSS);
  writeFileSync(join(outDir, "404.html"), notFoundPage(base, metaPolicy(rules, "/")));
  pages.push({ rel: "404.html", path: null, policy: metaPolicy(rules, "/") });
  writeFileSync(join(outDir, ".nojekyll"), "");

  // The app must keep working under the prefix without being rewritten.
  const app = readFileSync(join(outDir, "app", "app.js"), "utf8");
  if (!app.includes('navigator.serviceWorker.register("./sw.js")')) throw new Error("app.js no longer registers ./sw.js; check the worker's scope under the prefix");
  const sw = readFileSync(join(outDir, "app", "sw.js"), "utf8");
  const precache = JSON.parse(/const SHELL = (\[[\s\S]*?\]);/.exec(sw)[1]);
  const bad = precache.filter((u) => !u.startsWith("./"));
  if (bad.length) throw new Error(`sw.js precache has non-relative entries: ${bad.join(", ")}`);
  const left = leftoverAbsolute(outDir, base);
  if (left.length) throw new Error(`root-absolute URLs left in the mirror:\n  ${left.join("\n  ")}`);
  return { base, pages, files: walk(outDir).length };
}

if (process.argv[1] && process.argv[1].endsWith("build_mirror.mjs")) {
  const webapp = dirname(dirname(fileURLToPath(import.meta.url)));
  const out = process.argv[2] || join(webapp, "dist", "mirror");
  const { base, pages, files } = buildMirror(join(dirname(webapp), "site"), out, process.env.BASE_PATH);
  const version = JSON.parse(readFileSync(join(out, "app", "version.json"), "utf8"));
  console.log(`built mirror: ${out}, base ${base}, ${files} files, ${pages.length} pages, version ${version.version}, build ${version.build}`);
}

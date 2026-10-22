// A static server for site/ that applies site/_headers the way Cloudflare
// Pages does (every matching rule's headers), so tests run under the real
// Content-Security-Policy. Local testing only.
import { createServer } from "node:http";
import { readFileSync, existsSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";

export const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
  ".gz": "application/gzip", ".wasm": "application/wasm", ".txt": "text/plain; charset=utf-8", ".pdf": "application/pdf", ".png": "image/png",
  ".gif": "image/gif", ".ico": "image/x-icon" };

export function parseHeaders(text) {
  const rules = [];
  let cur = null;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (!/^\s/.test(line)) {
      cur = { path: line.trim(), headers: [], unset: [] };
      rules.push(cur);
    } else if (cur && /^\s*!\s/.test(line)) {
      cur.unset.push(line.trim().slice(1).trim());
    } else if (cur) {
      const i = line.indexOf(":");
      cur.headers.push([line.slice(0, i).trim(), line.slice(i + 1).trim()]);
    }
  }
  return rules;
}

export function matches(pattern, path) {
  const re = new RegExp("^" + pattern.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$");
  return re.test(path);
}

/**
 * The headers _headers gives a request path, the way Cloudflare Pages
 * applies them: matching rules in file order; within a rule, "! Name"
 * detaches first, then its headers are set. The first rule to set a name
 * replaces any value; a later rule setting it again appends (", ").
 */
export function headersFor(rules, path) {
  const out = {};
  const seen = new Set();
  for (const r of rules) {
    if (!matches(r.path, path)) continue;
    for (const k of r.unset || []) delete out[k.toLowerCase()];
    for (const [k, v] of r.headers) {
      const key = k.toLowerCase();
      out[key] = seen.has(key) && key in out ? `${out[key]}, ${v}` : v;
      seen.add(key);
    }
  }
  return out;
}

export function serve(siteDir, port = 0) {
  const rules = parseHeaders(readFileSync(join(siteDir, "_headers"), "utf8"));
  const log = [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    log.push(url.pathname);
    let rel = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, "");
    let file = join(siteDir, rel);
    if (existsSync(file) && statSync(file).isDirectory()) {
      if (!url.pathname.endsWith("/")) {
        res.writeHead(308, { Location: url.pathname + "/" });
        return res.end();
      }
      file = join(file, "index.html");
    } else if (!existsSync(file) && existsSync(`${file}.html`)) {
      file = `${file}.html`; // Cloudflare Pages serves /privacy from privacy.html
    }
    let status = 200;
    if (!file.startsWith(siteDir) || !existsSync(file)) {
      // Cloudflare Pages serves a top-level 404.html, with status 404, for
      // any address with no file (and then does no single-page fallback).
      file = join(siteDir, "404.html");
      status = 404;
      if (!existsSync(file)) {
        res.writeHead(404, { "content-type": "text/plain" });
        return res.end("not found");
      }
    }
    const headers = { "content-type": TYPES[extname(file)] || "application/octet-stream", ...headersFor(rules, url.pathname) };
    res.writeHead(status, headers);
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve({ server, port: server.address().port, log })));
}

if (process.argv[1] && process.argv[1].endsWith("serve.mjs")) {
  const { port } = await serve(new URL("../../site", import.meta.url).pathname, Number(process.argv[2] || 8765));
  console.log(`serving site/ on http://127.0.0.1:${port}/  (app at /app/)`);
}

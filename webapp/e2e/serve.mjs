// A static server for site/ that applies site/_headers the way Cloudflare
// Pages does (every matching rule's headers), so tests run under the real
// Content-Security-Policy. Local testing only.
import { createServer } from "node:http";
import { readFileSync, existsSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";

const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json",
  ".gz": "application/gzip", ".wasm": "application/wasm", ".txt": "text/plain; charset=utf-8", ".pdf": "application/pdf", ".png": "image/png" };

function parseHeaders(text) {
  const rules = [];
  let cur = null;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (!/^\s/.test(line)) {
      cur = { path: line.trim(), headers: [] };
      rules.push(cur);
    } else if (cur) {
      const i = line.indexOf(":");
      cur.headers.push([line.slice(0, i).trim(), line.slice(i + 1).trim()]);
    }
  }
  return rules;
}

function matches(pattern, path) {
  const re = new RegExp("^" + pattern.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*") + "$");
  return re.test(path);
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
    }
    if (!file.startsWith(siteDir) || !existsSync(file)) {
      res.writeHead(404, { "content-type": "text/plain" });
      return res.end("not found");
    }
    const headers = { "content-type": TYPES[extname(file)] || "application/octet-stream" };
    for (const r of rules) {
      if (!matches(r.path, url.pathname)) continue;
      for (const [k, v] of r.headers) {
        const key = k.toLowerCase();
        headers[key] = headers[key] && key !== "content-type" ? `${headers[key]}, ${v}` : v;
      }
    }
    res.writeHead(200, headers);
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve({ server, port: server.address().port, log })));
}

if (process.argv[1] && process.argv[1].endsWith("serve.mjs")) {
  const { port } = await serve(new URL("../../site", import.meta.url).pathname, Number(process.argv[2] || 8765));
  console.log(`serving site/ on http://127.0.0.1:${port}/  (app at /app/)`);
}

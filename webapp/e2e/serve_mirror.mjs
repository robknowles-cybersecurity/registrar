// A static server for the mirror build (tools/build_mirror.mjs) that behaves
// like GitHub Pages for a project site: files under BASE_PATH only, no custom
// headers (so the pages' <meta> policies are what applies), a directory
// serves its index.html, and anything missing gets 404.html with status 404.
// Local testing only.
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, normalize, resolve } from "node:path";
import { TYPES } from "./serve.mjs";
import { normalizeBase } from "../tools/build_mirror.mjs";

export function serveMirror(dir, base = "/registrar/", port = 0) {
  base = normalizeBase(base);
  dir = resolve(dir);
  const log = [];
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    log.push(url.pathname);
    const notFound = () => {
      res.writeHead(404, { "content-type": TYPES[".html"] });
      res.end(readFileSync(join(dir, "404.html")));
    };
    if (`${url.pathname}/` === base) {
      res.writeHead(301, { Location: base });
      return res.end();
    }
    if (!url.pathname.startsWith(base)) return notFound();
    const rel = normalize(decodeURIComponent(url.pathname.slice(base.length))).replace(/^(\.\.[/\\])+/, "");
    let file = join(dir, rel);
    if (existsSync(file) && statSync(file).isDirectory()) {
      if (!url.pathname.endsWith("/")) {
        res.writeHead(301, { Location: `${url.pathname}/` });
        return res.end();
      }
      file = join(file, "index.html");
    } else if (!existsSync(file) && existsSync(`${file}.html`)) {
      file = `${file}.html`;
    }
    if (!file.startsWith(dir) || !existsSync(file)) return notFound();
    res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" });
    res.end(readFileSync(file));
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve({ server, port: server.address().port, base, log })));
}

if (process.argv[1] && process.argv[1].endsWith("serve_mirror.mjs")) {
  const dir = process.argv[3] || new URL("../dist/mirror", import.meta.url).pathname;
  const { port, base } = await serveMirror(dir, process.env.BASE_PATH, Number(process.argv[2] || 8766));
  console.log(`serving ${dir} on http://127.0.0.1:${port}${base}  (app at ${base}app/)`);
}

// The GitHub Pages mirror (tools/build_mirror.mjs): built into a temporary
// directory under two prefixes, it must leave site/ untouched, keep every URL
// under the prefix, carry each page's policy as a <meta>, point search at
// ceu.tools, and otherwise ship site/'s files byte for byte.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { BANNER_TEXT, buildMirror, leftoverAbsolute, metaPolicy, normalizeBase } from "../tools/build_mirror.mjs";
import { parseHeaders } from "../e2e/serve.mjs";

const webapp = dirname(dirname(fileURLToPath(import.meta.url)));
const site = join(dirname(webapp), "site");
const rules = parseHeaders(readFileSync(join(site, "_headers"), "utf8"));

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
const rel = (dir, p) => relative(dir, p).split(sep).join("/");
const treeHash = (dir) => {
  const h = createHash("sha256");
  for (const p of walk(dir).sort()) h.update(rel(dir, p)).update(readFileSync(p));
  return h.digest("hex");
};

for (const base of ["/registrar/", "/a/b/"]) {
  test(`mirror under ${base}`, async (t) => {
    const out = mkdtempSync(join(tmpdir(), "registrar-mirror-"));
    t.after(() => rmSync(out, { recursive: true, force: true }));
    const before = treeHash(site);
    buildMirror(site, out, base);
    assert.equal(treeHash(site), before, "site/ changed");

    await t.test("GitHub Pages files: .nojekyll and 404.html; no _headers", () => {
      assert.ok(existsSync(join(out, ".nojekyll")));
      assert.ok(!existsSync(join(out, "_headers")));
      const nf = readFileSync(join(out, "404.html"), "utf8");
      assert.match(nf, new RegExp(`<a href="${base}">`));
      assert.match(nf, new RegExp(`<link rel="stylesheet" href="${base}style.css">`));
    });

    await t.test("every other file is site/'s, byte for byte (the two stylesheets only gain the banner rule)", () => {
      for (const p of walk(site)) {
        const r = rel(site, p);
        if (r === "_headers" || r.endsWith(".html")) continue;
        const a = readFileSync(p), b = readFileSync(join(out, r));
        if (r === "style.css" || r === "app/app.css") {
          assert.ok(b.subarray(0, a.length).equals(a), `${r}: not site/'s with a rule appended`);
          assert.match(b.subarray(a.length).toString(), /^\n\/\* mirror:begin[\s\S]*\.mirror-banner[\s\S]*\/\* mirror:end \*\/\n$/);
        } else assert.ok(a.equals(b), `${r} differs from site/`);
      }
      assert.deepEqual(JSON.parse(readFileSync(join(out, "app", "version.json"))), JSON.parse(readFileSync(join(site, "app", "version.json"))));
    });

    await t.test("no root-absolute URL outside the prefix, in HTML, CSS or the manifest", () => {
      assert.deepEqual(leftoverAbsolute(out, base), []);
      for (const page of ["index.html", "privacy.html", "404.html"]) {
        const html = readFileSync(join(out, page), "utf8");
        for (const m of html.matchAll(/\b(?:href|src)="(\/[^"]*)"/g)) assert.ok(m[1].startsWith(base), `${page}: ${m[1]}`);
      }
      const index = readFileSync(join(out, "index.html"), "utf8");
      assert.match(index, new RegExp(`href="${base}app/"`));
      assert.match(index, new RegExp(`href="${base}privacy.html"`));
      assert.match(readFileSync(join(out, "app", "index.html"), "utf8"), /href="\.\.\/privacy\.html"/);
    });

    await t.test("each page: the _headers policy as a <meta> first in <head>, minus frame-ancestors; a canonical on ceu.tools", () => {
      const want = { "index.html": ["/", "https://ceu.tools/"], "privacy.html": ["/privacy", "https://ceu.tools/privacy"],
        "app/index.html": ["/app/", "https://ceu.tools/app/"], "404.html": ["/", "https://ceu.tools/"] };
      const pages = walk(out).map((p) => rel(out, p)).filter((r) => r.endsWith(".html")).sort();
      assert.deepEqual(pages, Object.keys(want).sort());
      for (const [page, [path, canonical]] of Object.entries(want)) {
        const html = readFileSync(join(out, page), "utf8");
        const policy = metaPolicy(rules, path);
        assert.doesNotMatch(policy, /frame-ancestors/);
        const head = html.split("<head>\n")[1].split("\n");
        assert.equal(head[0], '<meta charset="utf-8">', page);
        assert.equal(head[1], `<meta http-equiv="Content-Security-Policy" content="${policy}">`, page);
        assert.equal(html.match(/http-equiv="Content-Security-Policy"/g).length, 1, page);
        assert.equal(html.match(/<link rel="canonical"/g).length, 1, page);
        assert.ok(html.includes(`<link rel="canonical" href="${canonical}">`), page);
        assert.ok(html.includes('<meta name="referrer" content="no-referrer">'), page);
      }
      assert.doesNotMatch(readFileSync(join(out, "index.html"), "utf8"), /<script/i);
    });

    await t.test("one banner per page: the app gets the static mirror banner, linking to ceu.tools", () => {
      const html = readFileSync(join(out, "app", "index.html"), "utf8");
      const banners = html.match(/<aside class="(?:alias|mirror)-banner"[^]*?<\/aside>/g);
      assert.equal(banners.length, 1);
      assert.equal(banners[0].replace(/<[^>]+>/g, ""), BANNER_TEXT);
      assert.ok(banners[0].includes('<a href="https://ceu.tools/app/">ceu.tools</a>'));
      assert.match(html, /<body>\n<a class="skip"[^\n]*\n<aside class="mirror-banner"/, "banner not right after the skip link");
      assert.doesNotMatch(readFileSync(join(out, "privacy.html"), "utf8"), /-banner/);
    });

    await t.test("one banner per page: the landing page keeps site/'s alias banner, which names the mirror, and gains no mirror banner", () => {
      const html = readFileSync(join(out, "index.html"), "utf8");
      const banners = html.match(/<aside class="(?:alias|mirror)-banner"[^]*?<\/aside>/g);
      assert.equal(banners.length, 1);
      assert.doesNotMatch(html, /mirror-banner/);
      assert.match(banners[0].replace(/<[^>]+>/g, ""), /^Served at ceu\.tools, cpe\.insiderthreatanalyst\.com and a GitHub Pages mirror\. /);
      assert.match(html, /<body>\n<a class="skip"[^\n]*\n<aside class="alias-banner"/);
    });

    await t.test("the service worker: registered relative, precache relative, so its scope is the prefix's app/", () => {
      assert.ok(readFileSync(join(out, "app", "app.js"), "utf8").includes('navigator.serviceWorker.register("./sw.js")'));
      const sw = readFileSync(join(out, "app", "sw.js"), "utf8");
      const shell = JSON.parse(/const SHELL = (\[[\s\S]*?\]);/.exec(sw)[1]);
      assert.ok(shell.length > 10 && shell.every((u) => u.startsWith("./")));
      const manifest = JSON.parse(readFileSync(join(out, "app", "manifest.webmanifest"), "utf8"));
      assert.equal(manifest.scope, "./");
      assert.equal(manifest.start_url, "./");
    });
  });
}

test("BASE_PATH is normalised to /a/b/ and refused when it is not a plain path", () => {
  assert.equal(normalizeBase(undefined), "/registrar/");
  assert.equal(normalizeBase("registrar"), "/registrar/");
  assert.equal(normalizeBase("/a/b"), "/a/b/");
  assert.equal(normalizeBase("//a//b//"), "/a/b/");
  assert.throws(() => normalizeBase('/a"b/'));
  assert.throws(() => normalizeBase("/a b/"));
});

test("the mirror refuses to build into site/", () => {
  assert.throws(() => buildMirror(site, site, "/registrar/"), /inside site/);
  assert.throws(() => buildMirror(site, join(site, "mirror"), "/registrar/"), /inside site/);
});

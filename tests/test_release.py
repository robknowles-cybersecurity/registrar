"""Release surface: the ceu.tools landing page and the public docs.

The page's promises are checkable, so they are checked: no scripts, no
third-party requests, no trackers, the Clacks header in the Cloudflare
Pages headers file. The docs' promises likewise: where to report a
vulnerability, that outside code is not being taken, that the commercial
note stays one paragraph with no prices in it.
"""

import os
import json
import re
import sys
import unittest
from html.parser import HTMLParser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SITE = os.path.join(REPO, "site")
SELF_HOST = "https://github.com/robknowles-cybersecurity/registrar"
REPORT_URL = SELF_HOST + "/security/advisories/new"
# Links a visitor may follow away from the site. Navigation only: nothing the
# browser fetches on its own may be off-site.
GUIDE_SOURCE = "https://insiderthreatanalyst.com/cpe"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"
# Where the guide says to earn credits, carried over from the source page.
PROVIDERS = ("https://www.nationalinsiderthreatsig.org/",
             "https://www.cisa.gov/topics/physical-security/insider-threat-mitigation",
             "https://www.sei.cmu.edu/",
             "https://www.cinrmcoe-cdecgrin.ca/solutions",
             "https://www.secureworld.io/",
             "https://www.guidepointsecurity.com/gpsec/",
             "https://www.gartner.com/en/webinars",
             "https://www.fsisac.com/")


def _program_urls():
    """Each body's policy page, as bodies.yaml records it."""
    from cpebot import bodies as B
    return tuple(p.raw["source_url"]
                 for p in B.load(os.path.join(REPO, "bodies.yaml")).programs)


NAV_LINKS = (SELF_HOST, SELF_HOST + "/releases", REPORT_URL,
             SELF_HOST + "/blob/main/webapp/README.md#threat-model",
             GUIDE_SOURCE, CC_BY) + PROVIDERS + _program_urls() + (
             # canonical URLs of the site's own pages
             "https://ceu.tools/", "https://ceu.tools/privacy")
TRACKERS = ("google-analytics", "googletagmanager", "gtag(", "plausible",
            "cloudflareinsights", "facebook", "doubleclick", "segment.",
            "hotjar", "matomo", "clarity.ms", "umami")


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def prose(*parts):
    """A Markdown file as one line of words, emphasis markers dropped."""
    return " ".join(read(*parts).replace("**", "").split())


class Links(HTMLParser):

    def __init__(self):
        HTMLParser.__init__(self)
        self.urls, self.tags, self.handlers = [], [], []
        self.fetched, self.meta = [], {}

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        a = dict(attrs)
        for k, v in attrs:
            if k in ("src", "href", "action", "srcset", "data", "poster"):
                self.urls.append(v or "")
                # everything but a link to follow is fetched by the browser
                if tag != "a" and a.get("rel") != "canonical":
                    self.fetched.append(v or "")
            if k.startswith("on"):
                self.handlers.append(k)
        if tag == "meta" and (a.get("property") or a.get("name")):
            self.meta[a.get("property") or a.get("name")] = a.get("content")


class TestLandingPage(unittest.TestCase):

    def setUp(self):
        self.html = read("site", "index.html")
        self.p = Links()
        self.p.feed(self.html)

    def test_no_scripts_frames_or_inline_handlers(self):
        for tag in ("script", "iframe", "object", "embed"):
            self.assertNotIn(tag, self.p.tags, tag)
        self.assertEqual(self.p.handlers, [])

    def test_the_only_form_cannot_submit(self):
        # One form, the body filter: it exists so "All" can be a reset
        # button. It has no action or method, no control that submits (a
        # button is a submit button unless typed otherwise) and no text field
        # (Enter in one submits implicitly). The CSP's form-action 'none'
        # would block a submission anyway.
        forms = re.findall(r"<form\b([^>]*)>(.*?)</form>", self.html, re.S)
        self.assertEqual(len(forms), 1)
        attrs, body = forms[0]
        self.assertEqual(attrs.strip(), 'class="rules-filter"')
        self.assertEqual(self.html.count("<form"), 1)
        for b in re.findall(r"<button\b([^>]*)>", body):
            self.assertRegex(b, r'type="(reset|button)"', b)
        for i in re.findall(r"<input\b([^>]*)>", body):
            self.assertRegex(i, r'type="checkbox"', i)
        self.assertNotIn("formaction", self.html)
        self.assertIn("form-action 'none'", read("site", "_headers"))

    def test_no_third_party_requests(self):
        # Anything the browser would FETCH must be same-origin. Absolute URLs
        # are allowed only as links a visitor follows, from a fixed list.
        for url in self.p.fetched:
            self.assertFalse(url.startswith(("http://", "https://", "//")), url)
        for url in self.p.urls:
            if url.startswith(("http://", "https://", "//")):
                self.assertIn(url, NAV_LINKS)
        css = read("site", "style.css")
        self.assertNotIn("@import", css)
        self.assertIsNone(re.search(r"url\(\s*['\"]?(https?:)?//", css))

    def test_no_trackers(self):
        blob = (self.html + read("site", "style.css")).lower()
        for t in TRACKERS:
            self.assertNotIn(t, blob, t)

    def test_required_content(self):
        self.assertIn("ceu.tools", self.html)
        self.assertIn("Tool #1", self.html)
        self.assertIn("Registrar", self.html)
        self.assertIn("Track your CPEs, CEUs and PDUs in one place", self.html)
        self.assertIn("Early release", self.html)
        self.assertIn(SELF_HOST, self.p.urls)

    def test_explains_the_problem_before_the_tool(self):
        self.assertLess(self.html.index('id="problem"'),
                        self.html.index("Tool #1"))
        for section in ('id="who"', 'id="bodies"', 'id="local"', 'id="see"'):
            self.assertIn(section, self.html)
        self.assertIn('<a href="%s">More on CPE rules</a>' % GUIDE_SOURCE,
                      self.html)

    def test_guide_sections_come_before_the_tool(self):
        for section in ("basics", "habits", "rules", "audit", "surprises",
                        "first90", "earn", "glossary"):
            self.assertIn('id="%s"' % section, self.html)
            self.assertLess(self.html.index('id="%s"' % section),
                            self.html.index("Tool #1"), section)
            self.assertIn('href="#%s"' % section, self.html)
        # every guide section ends by pointing at the app
        self.assertEqual(self.html.count('<p class="bridge">'), 9)
        for bridge in re.findall(r'<p class="bridge">(.*?)</p>', self.html, re.S):
            self.assertIn('<a href="/app/">', bridge)
        self.assertIn("Rules change; confirm with your body before you rely",
                      self.html)

    def test_guide_credits_its_source(self):
        text = re.sub(r"<[^>]+>", "", self.html)
        text = " ".join(text.split())
        self.assertIn("Adapted from the CPE Resources page at "
                      "insiderthreatanalyst.com (CC BY 4.0)", text)
        self.assertIn('<a href="%s">CPE Resources page at '
                      'insiderthreatanalyst.com</a>' % GUIDE_SOURCE, self.html)
        self.assertIn('<a href="%s">CC BY 4.0</a>' % CC_BY, self.html)
        for url in PROVIDERS:
            self.assertIn('href="%s"' % url, self.html)

    def test_every_body_in_bodies_yaml_has_a_linked_card(self):
        # The per-body section is built from bodies.yaml (npm run build): one
        # card per program, named for the body, linking to the policy page
        # bodies.yaml cites, and an unverified program badged in its card.
        from cpebot import bodies as B
        rules = B.load(os.path.join(REPO, "bodies.yaml"))
        cards = re.findall(r'<h3 id="rb-([^"]+)">([^<]+)</h3>(.*?)</article>',
                           self.html, re.S)
        table = {name: rest for _, name, rest in cards}
        self.assertEqual(len(table), len(rules.programs))
        for p in rules.programs:
            self.assertIn(p.body_name, table)
            self.assertIn('<a href="%s">%s policy page</a>'
                          % (p.raw["source_url"].replace("&", "&amp;"), p.body_name),
                          table[p.body_name])
            if not p.verified:
                self.assertIn("UNVERIFIED", table[p.body_name], p.body_name)

    def test_every_credential_in_bodies_yaml_is_listed(self):
        # The coverage table is written by hand from bodies.yaml; it must
        # name every credential with its body, and badge the unverified ones.
        from cpebot import bodies as B
        rules = B.load(os.path.join(REPO, "bodies.yaml"))
        rows = re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td>(.*?)</tr>",
                          self.html)
        table = {name: (body, rest) for name, body, rest in rows}
        self.assertEqual(len(table), len(rules.credentials))
        for c in rules.credentials:
            self.assertIn(c.name, table)
            self.assertEqual(table[c.name][0], c.body_name)
            if not c.raw.get("verified"):
                self.assertIn("UNVERIFIED", table[c.name][1], c.name)

    def test_share_tags_point_at_the_card(self):
        for page in ("index.html", "privacy.html"):
            p = Links()
            p.feed(read("site", page))
            self.assertEqual(p.meta.get("og:image"), "https://ceu.tools/og.png")
            self.assertEqual(p.meta.get("twitter:card"), "summary_large_image")
            self.assertTrue(p.meta.get("og:title"))
        with open(os.path.join(SITE, "og.png"), "rb") as fh:
            head = fh.read(24)
        self.assertEqual(head[1:4], b"PNG")
        self.assertEqual((int.from_bytes(head[16:20], "big"),
                          int.from_bytes(head[20:24], "big")), (1200, 630))

    def test_privacy_page(self):
        html = read("site", "privacy.html")
        p = Links()
        p.feed(html)
        for tag in ("script", "iframe", "object", "embed", "form"):
            self.assertNotIn(tag, p.tags, tag)
        self.assertEqual(p.handlers, [])
        for url in p.fetched:
            self.assertFalse(url.startswith(("http://", "https://", "//")), url)
        for url in p.urls:
            if url.startswith(("http://", "https://", "//")):
                self.assertIn(url, NAV_LINKS)
        text = " ".join(html.split())
        for must in ("api.anthropic.com", "api.openai.com", "20,000 characters",
                     "never asks for, stores or uses a certifying body's portal login"):
            self.assertIn(must, text)
        csp = [v for n, v in self._header_rules()["/privacy"]
               if n == "Content-Security-Policy"]
        self.assertEqual(len(csp), 1)
        self.assertNotIn("script-src", csp[0])

    def test_security_txt(self):
        txt = read("site", ".well-known", "security.txt")
        self.assertIn("Contact: " + REPORT_URL + "\n", txt)
        self.assertRegex(txt, r"(?m)^Expires: \d{4}-\d\d-\d\dT")
        self.assertNotIn("@", txt)
        self.assertNotIn("mailto:", txt)

    def test_no_pricing(self):
        self.assertIsNone(re.search(r"[$€£]\s?\d", self.html))

    def _header_rules(self):
        """site/_headers as {path: [(name, value)]}, Cloudflare Pages format."""
        rules, cur = {}, None
        for line in read("site", "_headers").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line[0].isspace():
                cur = rules.setdefault(line.strip(), [])
                continue
            self.assertRegex(line, r"^\s+[A-Za-z-]+: \S")
            name, value = line.strip().split(": ", 1)
            cur.append((name, value))
        return rules

    def test_clacks_header_in_cloudflare_pages_format(self):
        rules = self._header_rules()
        self.assertIn(("X-Clacks-Overhead", "GNU Terry Pratchett"), rules["/*"])

    def test_csp_header_forbids_scripts(self):
        # Every matching rule applies on Cloudflare Pages, so a CSP on /*
        # would stack with the app's. The landing page's own policy must
        # allow no scripts; only /app/* may.
        rules = self._header_rules()
        self.assertNotIn("Content-Security-Policy", [n for n, _ in rules["/*"]])
        for path in ("/", "/index.html"):
            csp = [v for n, v in rules[path] if n == "Content-Security-Policy"]
            self.assertEqual(len(csp), 1, path)
            self.assertIn("default-src 'none'", csp[0])
            self.assertNotIn("script-src", csp[0])
        for path, hdrs in rules.items():
            for n, v in hdrs:
                if n == "Content-Security-Policy" and "script-src" in v:
                    self.assertTrue(path.startswith("/app/"), path)

    def test_nothing_the_site_ships_is_git_ignored(self):
        # .gitignore drops any path containing "credential", "token" or
        # "secret". A site file named that way exists locally, passes every
        # check, and never reaches the repository or the deploy.
        import subprocess
        try:
            out = subprocess.run(
                ["git", "ls-files", "--others", "--ignored", "--exclude-standard",
                 "site", "webapp/src"], cwd=REPO, capture_output=True,
                text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        ignored = [l for l in out.splitlines() if "__pycache__" not in l]
        self.assertEqual(ignored, [])

    def test_every_local_reference_exists(self):
        for url in self.p.urls:
            if url.startswith("/"):
                path = os.path.join(SITE, url[1:])
                if url.endswith("/"):
                    path = os.path.join(path, "index.html")
                elif not os.path.splitext(url)[1]:
                    path += ".html"   # Cloudflare Pages serves /privacy from privacy.html
                self.assertTrue(os.path.isfile(path), url)


class TestPublicDocs(unittest.TestCase):

    def test_security_routes_reports_privately(self):
        s = prose("SECURITY.md")
        self.assertIn("private vulnerability reporting", s)
        self.assertIn("rob@robknowles.com", s)

    def test_contributing_states_the_policy(self):
        s = prose("CONTRIBUTING.md")
        self.assertIn("Issues are always welcome", s)
        self.assertIn("not being accepted", s)

    def test_commercial_is_one_short_paragraph_without_prices(self):
        s = read("COMMERCIAL.md")
        body = [b for b in s.strip().split("\n\n") if not b.startswith("#")]
        self.assertEqual(len(body), 1)
        self.assertIn("hosted edition", body[0].lower())
        self.assertIn("is planned", body[0])
        self.assertIn("rob@robknowles.com", body[0])
        self.assertIsNone(re.search(r"[$€£]\s?\d|pric|tier|feature", s,
                                    re.I))

    def test_readme_leads_with_the_browser_then_self_host_then_trust(self):
        s = read("README.md")
        heads = [l for l in s.splitlines() if l.startswith("## ")]
        order = [heads.index(h) for h in ("## Try it in your browser",
                                          "## Self-host it",
                                          "## Why you can trust it")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(order[0], 0, "the browser section comes first")
        trust = prose("README.md").split("## Why you can trust it")[1].split("## ")[0]
        for claim in ("No accounts, no server, your data in your browser",
                      "pinned by hash, and the build refuses on a mismatch",
                      "Private vulnerability reporting is on"):
            self.assertIn(claim, trust)
        self.assertEqual(s.rstrip().splitlines()[-1], "Built with AI-assisted development.")

    def test_readme_images_exist(self):
        for path in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", read("README.md")):
            self.assertTrue(os.path.isfile(os.path.join(REPO, path)), path)

    def test_readme_units_table_matches_bodies_yaml(self):
        from cpebot import bodies as B
        rules = B.load(os.path.join(REPO, "bodies.yaml"))
        rows = {}
        for line in read("README.md").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 6 and cells[0] not in ("Credential", "---"):
                rows[cells[0]] = cells
        for c in rules.credentials:
            self.assertIn(c.name, rows)
            self.assertEqual(rows[c.name][1], c.body_name)
            self.assertEqual(rows[c.name][2], c.unit_label, c.name)
            self.assertEqual(rows[c.name][5].startswith("**no**"),
                             not c.raw.get("verified"), c.name)

    def test_changelog_and_release_notes(self):
        log = read("CHANGELOG.md")
        for head in ("## v1.2.1", "## v1.2.0", "## v1.1.0", "## v1.0.0"):
            self.assertIn(head, log)
        self.assertLess(log.index("## v1.2.1"), log.index("## v1.2.0"))
        self.assertLess(log.index("v1.2.0"), log.index("## v1.1.0"))
        notes = read("docs", "release-notes", "v1.2.0.md")
        self.assertIn("# Registrar v1.2.0", notes)
        self.assertIn("private vulnerability reporting", notes)

    def test_trademarks_and_thirdparty_exist(self):
        self.assertIn("Registrar", read("TRADEMARKS.md"))
        self.assertIn("The Python app: none.", read("THIRDPARTY.md"))

    def test_every_vendored_package_is_declared(self):
        # The browser app vendors front-end libraries; each must be named in
        # THIRDPARTY.md and pinned with its hash in docs/DEPENDENCIES.md.
        manifest = json.loads(read("webapp", "src", "vendor", "MANIFEST.json"))
        third, deps = read("THIRDPARTY.md"), read("docs", "DEPENDENCIES.md")
        for f in manifest["files"]:
            self.assertIn(f["package"].split("/")[-1], third, f["package"])
            self.assertIn(f["sha256"], deps, f["path"])

    def test_default_bind_is_still_loopback(self):
        self.assertIn('os.environ.get("CPEBOT_BIND", "127.0.0.1")',
                      read("cpebot", "web.py"))


if __name__ == "__main__":
    unittest.main()

"""Release surface: the ceu.tools landing page and the public docs.

The page's promises are checkable, so they are checked: no scripts, no
third-party requests, no trackers, the Clacks header in the Cloudflare
Pages headers file. The docs' promises likewise: where to report a
vulnerability, that outside code is not being taken, that the commercial
note stays one paragraph with no prices in it.
"""

import os
import re
import sys
import unittest
from html.parser import HTMLParser

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SITE = os.path.join(REPO, "site")
SELF_HOST = "https://github.com/robknowles-cybersecurity/registrar"
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

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for k, v in attrs:
            if k in ("src", "href", "action", "srcset", "data", "poster"):
                self.urls.append(v or "")
            if k.startswith("on"):
                self.handlers.append(k)


class TestLandingPage(unittest.TestCase):

    def setUp(self):
        self.html = read("site", "index.html")
        self.p = Links()
        self.p.feed(self.html)

    def test_no_scripts_frames_or_inline_handlers(self):
        for tag in ("script", "iframe", "object", "embed", "form"):
            self.assertNotIn(tag, self.p.tags, tag)
        self.assertEqual(self.p.handlers, [])

    def test_no_third_party_requests(self):
        # Anything the browser would FETCH must be same-origin. The one
        # absolute URL allowed is the self-host link, which is navigation.
        for url in self.p.urls:
            if url.startswith(("http://", "https://", "//")):
                self.assertEqual(url, SELF_HOST)
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

    def test_no_pricing(self):
        self.assertIsNone(re.search(r"[$€£]\s?\d", self.html))

    def test_clacks_header_in_cloudflare_pages_format(self):
        lines = read("site", "_headers").splitlines()
        lines = [l for l in lines if l.strip() and not l.startswith("#")]
        self.assertEqual(lines[0], "/*")
        headers = [l for l in lines[1:]]
        for h in headers:
            self.assertRegex(h, r"^\s+[A-Za-z-]+: \S")
        self.assertIn("X-Clacks-Overhead: GNU Terry Pratchett",
                      [h.strip() for h in headers])

    def test_csp_header_forbids_scripts(self):
        csp = [l.strip() for l in read("site", "_headers").splitlines()
               if l.strip().startswith("Content-Security-Policy:")]
        self.assertEqual(len(csp), 1)
        self.assertIn("default-src 'none'", csp[0])
        self.assertNotIn("script-src", csp[0])

    def test_every_local_reference_exists(self):
        for url in self.p.urls:
            if url.startswith("/"):
                self.assertTrue(os.path.isfile(os.path.join(SITE, url[1:])),
                                url)


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

    def test_trademarks_and_thirdparty_exist(self):
        self.assertIn("Registrar", read("TRADEMARKS.md"))
        self.assertIn("None", read("THIRDPARTY.md"))

    def test_default_bind_is_still_loopback(self):
        self.assertIn('os.environ.get("CPEBOT_BIND", "127.0.0.1")',
                      read("cpebot", "web.py"))


if __name__ == "__main__":
    unittest.main()

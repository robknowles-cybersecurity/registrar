"""End-to-end through the live HTTP path.

These tests drive a real server over a real socket with real form posts,
because that is the only thing that proves the app works. A test that calls
`App.dashboard()` directly proves the renderer works; it does not prove the
form field names match what the handler reads, and that mismatch is the
defect that would survive every other test in this repo.

The full journey is here in one test: capture → confirm → dashboard →
report → packet.
"""

import contextlib
import html.parser
import http.client
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.request
import uuid as _uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B          # noqa: E402
from cpebot import ledger as L          # noqa: E402
from cpebot import web as W             # noqa: E402


def encode_multipart(fields, files=None):
    """Build a multipart body the way a browser would."""
    boundary = "----cpebot%s" % _uuid.uuid4().hex
    buf = io.BytesIO()
    for key, values in fields.items():
        if not isinstance(values, (list, tuple)):
            values = [values]
        for v in values:
            buf.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n"
                       "\r\n%s\r\n" % (boundary, key, v)).encode("utf-8"))
    for key, (filename, data, ctype) in (files or {}).items():
        buf.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; "
                   "filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                   % (boundary, key, filename, ctype)).encode("utf-8"))
        buf.write(data)
        buf.write(b"\r\n")
    buf.write(("--%s--\r\n" % boundary).encode("utf-8"))
    return buf.getvalue(), "multipart/form-data; boundary=%s" % boundary


class WebCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cpebot-web-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))
        self.app = W.App(B.load(), self.led)
        self.srv = W.build_server("127.0.0.1", 0, self.app)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever,
                                       daemon=True)
        self.thread.start()
        # Every POST needs the per-install anti-forgery token; the harness
        # injects it the way a browser carrying the form field would.
        self.csrf = self.app.csrf()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def get(self, path, expect=200):
        try:
            with urllib.request.urlopen(self.url(path)) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            body = e.read()
            if expect is not None and e.code != expect:
                raise AssertionError("GET %s -> %d\n%s"
                                     % (path, e.code, body[:2000].decode(
                                         "utf-8", "replace")))
            return e.code, body, dict(e.headers)

    def html(self, path):
        status, body, _ = self.get(path)
        self.assertEqual(status, 200, path)
        return body.decode("utf-8")

    def post(self, path, fields, files=None, expect=(200, 303)):
        fields = dict(fields)
        fields.setdefault("csrf", self.csrf)
        data, ctype = encode_multipart(fields, files)
        req = urllib.request.Request(self.url(path), data=data,
                                     headers={"Content-Type": ctype})
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req) as r:
                return r.status, r.read().decode("utf-8", "replace"), \
                    dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TestPagesRender(WebCase):

    def test_every_page_answers(self):
        for path in ("/", "/entries", "/entry/new", "/bodies", "/report",
                     "/backfill", "/settings"):
            status, body, _ = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(b"Registrar", body)
            # The package keeps its old name; nothing a user reads does.
            self.assertNotIn(b"cpebot", body.lower(), path)

    def test_every_page_starts_with_a_skip_link(self):
        for path in ("/", "/entries", "/entry/new", "/settings"):
            html = self.html(path)
            body = html.split("<body>", 1)[1]
            self.assertTrue(
                body.startswith('<a class=skip href=#main>Skip to content</a>'),
                path)
            self.assertIn("<main id=main", html)

    def test_health_is_json_and_names_unverified_rules(self):
        status, body, headers = self.get("/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", headers["Content-Type"])
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["credentials"], 7)
        # The health endpoint must not claim health it cannot vouch for: the
        # unverified rule set is part of the status, not a footnote.
        self.assertTrue(data["unverified"])
        self.assertIn("ctm", data["unverified"])

    def test_health_does_not_reveal_filesystem_paths(self):
        status, body, _ = self.get("/health")
        data = json.loads(body)
        self.assertNotIn("rules", data)
        self.assertNotIn(self.dir.encode(), body)
        self.assertNotIn(os.path.dirname(W.__file__).encode(), body)
        self.assertNotIn(b"bodies.yaml", body)

    def test_health_and_server_header_report_v1(self):
        status, body, headers = self.get("/health")
        self.assertEqual(json.loads(body)["version"], "1.0.0")
        self.assertTrue(headers["Server"].startswith("Registrar/1.0.0"),
                        headers["Server"])

    def test_rules_page_surfaces_unverified_in_the_ui(self):
        page = self.html("/bodies")
        self.assertIn("UNVERIFIED", page)
        self.assertIn("CTM", page)
        self.assertIn("GCITP", page)
        # The UMD/GIAC correction must survive into what the holder actually reads.
        self.assertNotIn("GIAC", page.replace("NOT GIAC", ""))

    def test_empty_state_is_clean_not_broken(self):
        page = self.html("/")
        self.assertIn("No cycle anchor recorded", page)
        self.assertNotIn("Traceback", page)
        self.assertIn("BELOW THRESHOLD", page)      # 0 real captures, honestly

    def test_unknown_paths_404(self):
        status, _, _ = self.get("/nope", expect=404)
        self.assertEqual(status, 404)
        status, _, _ = self.get("/credential/not_a_cred", expect=404)
        self.assertEqual(status, 404)


class LabelAudit(html.parser.HTMLParser):
    """Collect every label, every id and which labels wrap their control."""

    CONTROLS = ("input", "select", "textarea")

    def __init__(self):
        super().__init__()
        self.labels = []         # [for target or None, wraps a control]
        self.ids = []
        self.control_ids = set()
        self.open_label = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if tag == "label":
            self.open_label = [a.get("for"), False]
            self.labels.append(self.open_label)
        elif tag in self.CONTROLS:
            if a.get("type") != "hidden" and "id" in a:
                self.control_ids.add(a["id"])
            if self.open_label is not None:
                self.open_label[1] = True

    def handle_endtag(self, tag):
        if tag == "label":
            self.open_label = None


class TestFormLabels(WebCase):
    """Every form label is tied to its field, so a screen reader announces
    it and a click on it focuses the field."""

    PAGES = ("/entries", "/entry/new", "/report", "/backfill", "/settings",
             "/whatif")

    def audit(self, path):
        a = LabelAudit()
        a.feed(self.html(path))
        return a

    def test_every_label_names_a_field_on_the_page(self):
        for path in self.PAGES:
            a = self.audit(path)
            self.assertTrue(a.labels, path)
            for target, wraps in a.labels:
                if target is None:
                    self.assertTrue(wraps, "%s: label with no for= and no "
                                    "field inside it" % path)
                else:
                    self.assertIn(target, a.control_ids, path)

    def test_ids_are_unique_on_each_page(self):
        for path in self.PAGES:
            ids = self.audit(path).ids
            self.assertEqual(len(ids), len(set(ids)), path)

    def test_spot_checks(self):
        # Three fields a holder touches most, checked by hand.
        page = self.html("/entry/new")
        self.assertIn('<label for=f-title>Title</label>', page)
        self.assertIn('<input id=f-title name=title', page)
        page = self.html("/settings")
        self.assertIn('<label for=f-cissp-anchor_date>Anchor date</label>', page)
        self.assertIn('<input id=f-cissp-anchor_date type=date name=anchor_date',
                      page)
        page = self.html("/entries")
        self.assertIn('<label for=f-status>Status</label>', page)
        self.assertIn('<select id=f-status name=status>', page)


class TestTheWholeJourney(WebCase):
    """capture → confirm → dashboard → report → packet, over the wire."""

    def test_journey(self):
        # -- settings: give CISSP a cycle to live in ------------------------
        status, _, _ = self.post("/settings", {
            "credential": "cissp", "anchor_date": "2024-09-01",
            "carryover_in": "", "portal_credits": "", "portal_checked": "",
            "active": "1"})
        self.assertEqual(status, 303)
        self.post("/settings", {"config": "1", "attendee_name": "Sample Holder"})

        # -- capture: a form post with an evidence file ---------------------
        status, _, headers = self.post(
            "/entry/new",
            {"activity_date": "2025-03-14", "title": "Threat modelling workshop",
             "provider": "SANS", "hours": "80", "count": "1",
             "tags": ["training"], "markers": ["domain_security"],
             "notes": "web capture", "cost_usd": ""},
            files={"evidence": ("certificate.pdf", b"%PDF-1.4 cert", "application/pdf")})
        self.assertEqual(status, 303)
        entry_url = headers["Location"]
        uuid = entry_url.rsplit("/", 1)[1]

        # It is captured, NOT counted. Nothing auto-commits.
        self.assertEqual(self.led.get_entry(uuid)["status"], L.CAPTURED)
        self.assertIn("0 of 120", self.html("/"))

        detail = self.html(entry_url)
        self.assertIn("captured", detail)
        self.assertIn("Nothing counts until it is confirmed", detail)
        self.assertIn("certificate.pdf", detail)
        self.assertIn("OK", detail)                 # hash verified on read

        # -- confirm --------------------------------------------------------
        status, _, _ = self.post(entry_url, {"action": "status",
                                             "to": L.CONFIRMED})
        self.assertEqual(status, 303)
        self.assertEqual(self.led.get_entry(uuid)["status"], L.CONFIRMED)

        # -- dashboard now shows capped maths -------------------------------
        # Add 40 Group B hours: naive 120 (done!), real 110 with Group A short.
        status, _, h2 = self.post(
            "/entry/new",
            {"activity_date": "2025-06-10", "title": "Management course",
             "provider": "Coursera", "hours": "40", "count": "1",
             "tags": ["training"], "notes": "", "cost_usd": ""})
        self.post(h2["Location"], {"action": "status", "to": L.CONFIRMED})

        dash = self.html("/")
        self.assertIn("110 of 120", dash)
        self.assertIn("A naive total would have said 120", dash)
        self.assertIn("BEHIND", dash)

        # -- report ---------------------------------------------------------
        rep = self.html("/report?credential=cissp")
        self.assertIn("Naive sum would say", rep)
        self.assertIn("Forfeited to caps", rep)
        # The total is ahead of the pace line and the credential still fails.
        # The report has to say why, or those two rows read as a bug.
        self.assertIn("The total is ahead of pace, but this credential is "
                      "BEHIND anyway", rep)
        self.assertIn("General hours do not fix a category shortfall", rep)

        # -- packet ---------------------------------------------------------
        status, blob, headers = self.get("/packet/cissp")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertIn("cissp-packet", headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = zf.namelist()
            manifest = zf.read("manifest.md").decode("utf-8")
        self.assertIn("manifest.md", names)
        self.assertTrue(any(n.endswith("certificate.pdf") for n in names))
        self.assertIn("Sample Holder", manifest)
        self.assertIn("Gaps in this packet", manifest)
        # The unevidenced second entry must be named as a gap, not omitted.
        self.assertIn("no evidence attached", manifest)
        self.assertLess(manifest.index("Gaps in this packet"),
                        manifest.index("## Activities"))


class TestConfirmSemantics(WebCase):

    def setUp(self):
        WebCase.setUp(self)
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")

    def capture(self, **kw):
        base = {"activity_date": "2025-03-14", "title": "Workshop",
                "provider": "SANS", "hours": "8", "count": "1",
                "tags": ["training"], "notes": "", "cost_usd": ""}
        base.update(kw)
        _s, _b, h = self.post("/entry/new", base)
        return h["Location"].rsplit("/", 1)[1]

    def test_soft_duplicate_blocks_confirm_and_shows_the_match(self):
        first = self.capture()
        self.post("/entry/%s" % first, {"action": "status", "to": L.CONFIRMED})
        second = self.capture(hours="9")        # same date+provider, within 25%
        status, body, _ = self.post("/entry/%s" % second,
                                    {"action": "status", "to": L.CONFIRMED})
        self.assertEqual(status, 409)
        self.assertIn("Possible duplicate", body)
        self.assertIn(first[:8], body)
        self.assertEqual(self.led.get_entry(second)["status"], L.CAPTURED)

    def test_the_override_is_available_and_works(self):
        first = self.capture()
        self.post("/entry/%s" % first, {"action": "status", "to": L.CONFIRMED})
        second = self.capture(hours="9")
        _s, body, _h = self.post("/entry/%s" % second,
                                 {"action": "status", "to": L.CONFIRMED})
        self.assertIn("save_force", body)
        status, _, _ = self.post("/entry/%s" % second, {
            "action": "save_force", "activity_date": "2025-03-14",
            "title": "Workshop", "provider": "SANS", "hours": "9",
            "count": "1", "tags": ["training"], "notes": "", "cost_usd": ""})
        self.assertEqual(status, 303)
        self.assertEqual(self.led.get_entry(second)["status"], L.CONFIRMED)

    def test_an_illegal_transition_is_refused_with_a_reason(self):
        u = self.capture()
        self.post("/entry/%s" % u, {"action": "status", "to": L.CONFIRMED})
        self.led.set_status(u, L.SUPERSEDED)
        status, body, _ = self.post("/entry/%s" % u,
                                    {"action": "status", "to": L.CONFIRMED})
        self.assertEqual(status, 400)
        self.assertIn("superseded", body)

    def test_a_bad_date_is_rejected_not_stored(self):
        status, body, _ = self.post("/entry/new", {
            "activity_date": "2026-02-30", "title": "Impossible",
            "hours": "1", "count": "1", "tags": ["training"],
            "notes": "", "cost_usd": ""})
        self.assertEqual(status, 400)
        self.assertIn("2026-02-30", body)
        self.assertEqual(self.led.list_entries(), [])


class TestEvidenceIntegrityOverHTTP(WebCase):

    def test_an_altered_file_is_not_served(self):
        _s, _b, h = self.post(
            "/entry/new",
            {"activity_date": "2026-01-05", "title": "Course", "hours": "4",
             "count": "1", "tags": ["training"], "notes": "", "cost_usd": ""},
            files={"evidence": ("cert.pdf", b"%PDF-1.4 original", "application/pdf")})
        uuid = h["Location"].rsplit("/", 1)[1]
        row = self.led.list_evidence(uuid)[0]
        with open(self.led.evidence_path(row), "wb") as fh:
            fh.write(b"substituted after the fact")
        status, body, _ = self.get("/evidence/%d" % row["id"], expect=409)
        self.assertEqual(status, 409)
        self.assertIn(b"does not match the hash", body)
        self.assertNotIn(b"substituted after the fact", body)

    def test_a_good_file_downloads_intact(self):
        _s, _b, h = self.post(
            "/entry/new",
            {"activity_date": "2026-01-05", "title": "Course", "hours": "4",
             "count": "1", "tags": ["training"], "notes": "", "cost_usd": ""},
            files={"evidence": ("cert.pdf", b"%PDF-1.4 original", "application/pdf")})
        row = self.led.list_evidence(h["Location"].rsplit("/", 1)[1])[0]
        status, body, headers = self.get("/evidence/%d" % row["id"])
        self.assertEqual(status, 200)
        self.assertEqual(body, b"%PDF-1.4 original")
        self.assertIn("cert.pdf", headers["Content-Disposition"])


class TestBackfillOverHTTP(WebCase):

    CSV = ("date,title,provider,hours,tags,markers\n"
           "2025-01-05,Conference day one,ATAP,8,conference,domain_security\n"
           "2025-01-06,Conference day two,ATAP,8,conference,domain_security\n")

    def test_dry_run_writes_nothing(self):
        status, body, _ = self.post("/backfill",
                                    {"action": "dry", "csv": self.CSV})
        self.assertEqual(status, 200)
        self.assertIn("Dry run", body)
        self.assertIn("2 row(s) parsed", body)
        self.assertEqual(self.led.list_entries(), [])

    def test_import_lands_captured_and_uncounted_by_the_falsifier(self):
        status, body, _ = self.post("/backfill",
                                    {"action": "import", "csv": self.CSV})
        self.assertEqual(status, 200)
        self.assertIn("Imported", body)
        rows = self.led.list_entries()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["status"] == L.CAPTURED for r in rows))
        self.assertTrue(all(r["capture_source"] == "backfill" for r in rows))
        # A spreadsheet import must not be able to satisfy the kill-switch.
        self.assertEqual(self.led.capture_counts()["real"], 0)

    def test_a_bad_column_is_refused_with_the_column_named(self):
        status, body, _ = self.post("/backfill", {
            "action": "import", "csv": "date,title,tags,wat\n2025-01-01,x,training,y\n"})
        self.assertEqual(status, 400)
        self.assertIn("wat", body)
        self.assertEqual(self.led.list_entries(), [])

    def test_a_bad_row_is_reported_per_line_and_the_rest_import(self):
        csv = ("date,title,tags\n"
               "2025-01-05,Good,training\n"
               "2025-01-06,Bad tag,not_a_real_tag\n")
        status, body, _ = self.post("/backfill", {"action": "import", "csv": csv})
        self.assertEqual(status, 200)
        self.assertIn("not_a_real_tag", body)
        self.assertEqual(len(self.led.list_entries()), 1)


class TestFilters(WebCase):

    def setUp(self):
        WebCase.setUp(self)
        self.led.add_entry("2025-01-05", "Alpha training", provider="SANS",
                           hours=4, tags=["training"])
        u = self.led.add_entry("2026-05-05", "Beta conference", provider="ATAP",
                               hours=8, tags=["conference"])
        self.led.set_status(u, L.CONFIRMED)

    def test_status_filter(self):
        page = self.html("/entries?status=confirmed")
        self.assertIn("Beta conference", page)
        self.assertNotIn("Alpha training", page)

    def test_date_and_text_filters(self):
        self.assertIn("Alpha", self.html("/entries?until=2025-12-31"))
        self.assertNotIn("Beta", self.html("/entries?until=2025-12-31"))
        self.assertIn("Beta", self.html("/entries?q=conference"))

    def test_tag_and_provider_filters(self):
        self.assertNotIn("Alpha", self.html("/entries?tag=conference"))
        self.assertIn("Alpha", self.html("/entries?provider=sans"))


class TestReminderEndpoint(WebCase):

    def test_quiet_by_default(self):
        _s, body, _ = self.get("/reminders")
        data = json.loads(body)
        self.assertEqual(data["send"], [])
        self.assertIn("quiet by design", data["suppress"][0]["reason"])


PDF = b"%PDF-1.4 test evidence bytes"
PNG = b"\x89PNG\r\n\x1a\n" + b"fake png payload"


class AnchoredCase(WebCase):
    """A WebCase with a CISSP cycle anchored, plus entry helpers."""

    def setUp(self):
        super().setUp()
        self.post("/settings", {
            "credential": "cissp", "anchor_date": "2024-09-01",
            "carryover_in": "", "portal_credits": "", "portal_checked": "",
            "active": "1"})

    def capture(self, title="Workshop", files=None, **over):
        fields = {"activity_date": "2025-03-14", "title": title,
                  "provider": "SANS", "hours": "10", "count": "1",
                  "tags": ["training"], "markers": ["domain_security"],
                  "notes": "", "cost_usd": ""}
        fields.update(over)
        status, body, h = self.post("/entry/new", fields, files=files)
        if status != 303:
            raise AssertionError("capture -> %d\n%s" % (status, body[:1500]))
        return h["Location"]

    def confirm(self, url):
        status, body, _ = self.post(url, {"action": "status",
                                          "to": L.CONFIRMED})
        return status


class TestSecurityHeaders(WebCase):

    def test_hardening_headers_on_every_response(self):
        _s, _b, h = self.get("/")
        self.assertIn("default-src 'none'", h["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["X-Clacks-Overhead"], "GNU Terry Pratchett")

    def test_no_scripts_anywhere(self):
        for path in ("/", "/entries", "/charts", "/whatif", "/export"):
            self.assertNotIn("<script", self.html(path), path)

    def test_stylesheet_is_self_hosted(self):
        status, body, h = self.get("/static/registrar.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", h["Content-Type"])
        self.assertIn(b"--accent", body)
        # and pages link it rather than inlining a <style> block
        self.assertIn("/static/registrar.css", self.html("/"))


class TestHostAllowlist(WebCase):
    """A DNS-rebinding page reaches the app under its own name. Every request
    whose Host is not a loopback name, the bind address or a configured extra
    name is refused before any route runs."""

    def raw(self, method, path, host, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest(method, path, skip_host=True,
                            skip_accept_encoding=True)
            if host is not None:
                conn.putheader("Host", host)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders(body)
            r = conn.getresponse()
            return r.status, r.read(), dict(r.getheaders())
        finally:
            conn.close()

    def test_a_foreign_host_is_refused_on_every_kind_of_page(self):
        for path in ("/", "/entry/new", "/settings", "/backfill",
                     "/export/entries.csv", "/export/entries.json", "/health",
                     "/no-such-page"):
            for host in ("evil.example", "evil.example:%d" % self.port):
                status, body, h = self.raw("GET", path, host)
                self.assertEqual(status, 400, (path, host))
                self.assertEqual(body, b"Bad Host header\n", path)
                self.assertTrue(h["Content-Type"].startswith("text/plain"))
                self.assertNotIn(self.csrf.encode(), body)

    def test_a_forged_post_with_a_valid_token_is_still_refused(self):
        data, ctype = encode_multipart({
            "csrf": self.csrf, "activity_date": "2026-01-05",
            "title": "Rebound", "hours": "1", "count": "1", "notes": "",
            "cost_usd": ""})
        status, body, _ = self.raw("POST", "/entry/new", "evil.example",
                                   data, {"Content-Type": ctype})
        self.assertEqual(status, 400)
        self.assertEqual(self.led.list_entries(), [])

    def test_missing_malformed_or_duplicate_host_is_refused(self):
        self.assertEqual(self.raw("GET", "/", None)[0], 400)
        for bad in ("", "::1", "[::1", "127.0.0.1:abc", "127.0.0.1.evil.example",
                    "localhost..", "localhost..:%d" % self.port):
            self.assertEqual(self.raw("GET", "/", bad)[0], 400, bad)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("GET", "/", skip_host=True)
            conn.putheader("Host", "127.0.0.1")
            conn.putheader("Host", "evil.example")
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 400)
        finally:
            conn.close()

    def test_allowed_names_answer(self):
        for host in ("localhost", "LOCALHOST:%d" % self.port, "127.0.0.1",
                     "127.0.0.1:%d" % self.port, "[::1]", "[::1]:%d" % self.port,
                     "localhost."):
            for path in ("/", "/entry/new", "/export/entries.csv",
                         "/export/entries.json"):
                status, _b, _h = self.raw("GET", path, host)
                self.assertEqual(status, 200, (host, path))

    def test_a_raw_socket_request_is_refused_before_routing(self):
        # Bytes on the wire, nothing between the test and the server.
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(b"GET /export/entries.json HTTP/1.1\r\n"
                      b"Host: rebind.evil.example\r\nConnection: close\r\n\r\n")
            data = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        self.assertTrue(data.startswith(b"HTTP/1.0 400 ") or
                        data.startswith(b"HTTP/1.1 400 "), data[:40])
        self.assertTrue(data.endswith(b"\r\n\r\nBad Host header\n"), data[-60:])


class TestAllowedHostsConfig(unittest.TestCase):

    def serve(self, bind, extra):
        d = tempfile.mkdtemp(prefix="cpebot-host-")
        self.addCleanup(shutil.rmtree, d, True)
        app = W.App(B.load(), L.Ledger(os.path.join(d, "t.db"),
                                       os.path.join(d, "evidence")))
        srv = W.build_server(bind, 0, app, allowed_hosts=extra)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()

        def stop():
            srv.shutdown()
            srv.server_close()
            th.join(timeout=5)
        self.addCleanup(stop)
        return srv.server_address[1]

    def status(self, port, host):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.putrequest("GET", "/", skip_host=True)
            conn.putheader("Host", host)
            conn.endheaders()
            return conn.getresponse().status
        finally:
            conn.close()

    def test_an_allowed_hosts_entry_answers_and_others_do_not(self):
        port = self.serve("127.0.0.1", ["registrar.lan", " Box.Example. "])
        self.assertEqual(self.status(port, "registrar.lan:%d" % port), 200)
        self.assertEqual(self.status(port, "box.example"), 200)
        self.assertEqual(self.status(port, "other.lan"), 400)

    def test_the_bind_address_is_allowed(self):
        names = W.allowed_host_names("192.0.2.7", [])
        self.assertIn("192.0.2.7", names)
        self.assertEqual(names, {"localhost", "127.0.0.1", "::1", "192.0.2.7"})
        self.assertIn("fd00::1", W.allowed_host_names("[fd00::1]", []))

    def test_an_entry_with_a_port_matches_its_name(self):
        names = W.allowed_host_names("127.0.0.1", ["registrar.lan:8762",
                                                   "[fd00::2]:8762", "fd00::3"])
        self.assertIn("registrar.lan", names)
        self.assertNotIn("registrar.lan:8762", names)
        self.assertIn("fd00::2", names)
        self.assertIn("fd00::3", names)
        port = self.serve("127.0.0.1", ["registrar.lan:8762"])
        self.assertEqual(self.status(port, "registrar.lan:8762"), 200)
        self.assertEqual(self.status(port, "registrar.lan"), 200)
        self.assertEqual(self.status(port, "other.lan:8762"), 400)

    def test_blank_entries_are_ignored(self):
        self.assertEqual(W.allowed_host_names("127.0.0.1", ["a.lan", "", "  "]),
                         {"localhost", "127.0.0.1", "::1", "a.lan"})

    def test_host_name_parsing(self):
        self.assertEqual(W._host_name("Example.LAN:8762"), "example.lan")
        self.assertEqual(W._host_name("[::1]:8762"), "::1")
        self.assertEqual(W._host_name("[::1]"), "::1")
        self.assertEqual(W._host_name("[::1]x"), "")
        self.assertEqual(W._host_name("::1"), "")
        self.assertEqual(W._host_name(None), "")
        # One trailing dot is the absolute form of the same name; two is not
        # a name at all and must not collapse to "localhost" (N5).
        self.assertEqual(W._host_name("localhost.:8762"), "localhost")
        self.assertNotEqual(W._host_name("localhost.."), "localhost")
        self.assertNotEqual(W._host_name("localhost..:8762"), "localhost")


class TestCSRF(WebCase):

    def test_post_without_token_is_rejected(self):
        status, body, _ = self.post("/entry/new", {
            "csrf": "not-the-token", "activity_date": "2026-01-05",
            "title": "Forged", "hours": "1", "count": "1", "notes": "",
            "cost_usd": ""})
        self.assertEqual(status, 403)
        self.assertIn("cross-site request rejected", body)
        self.assertEqual(self.led.list_entries(), [])

    def test_a_non_ascii_token_is_a_403_not_a_500(self):
        for bad in ("tökén", "✓" * 64, self.csrf[:-1] + "é"):
            status, body, _ = self.post("/entry/new", {
                "csrf": bad, "activity_date": "2026-01-05",
                "title": "Forged", "hours": "1", "count": "1", "notes": "",
                "cost_usd": ""})
            self.assertEqual(status, 403, bad)
            self.assertIn("cross-site request rejected", body)
        self.assertEqual(self.led.list_entries(), [])

    def test_every_post_form_carries_the_token(self):
        for path in ("/entry/new", "/settings", "/backfill"):
            page = self.html(path)
            forms = page.count("method=post")
            self.assertGreaterEqual(forms, 1, path)
            self.assertEqual(page.count("name=csrf"), forms, path)

    def test_token_is_stable_across_requests(self):
        a = self.html("/entry/new")
        b = self.html("/entry/new")
        tok = a.split('name=csrf value="')[1].split('"')[0]
        self.assertIn(tok, b)
        self.assertEqual(tok, self.app.csrf())


class TestUploadValidation(AnchoredCase):

    def _try(self, filename, data, ctype="application/pdf"):
        return self.post("/entry/new", {
            "activity_date": "2026-01-05", "title": "Course", "hours": "4",
            "count": "1", "tags": ["training"], "notes": "", "cost_usd": ""},
            files={"evidence": (filename, data, ctype)})

    def test_wrong_magic_is_rejected_and_nothing_is_written(self):
        status, body, _ = self._try("cert.pdf", b"<html>not a pdf</html>")
        self.assertEqual(status, 400)
        self.assertIn("does not match its .pdf extension", body)
        self.assertEqual(self.led.list_entries(), [])
        self.assertEqual(self.led.list_evidence(), [])

    def test_unsupported_extension_is_rejected(self):
        status, body, _ = self._try("cert.pdf.html", b"<html>x</html>",
                                    "text/html")
        self.assertEqual(status, 400)
        self.assertIn("PDF, PNG or JPEG only", body)
        self.assertEqual(self.led.list_entries(), [])

    def test_oversized_upload_is_rejected(self):
        big = b"%PDF-1.4 " + b"\0" * L.MAX_EVIDENCE_BYTES
        status, body, _ = self._try("cert.pdf", big)
        self.assertEqual(status, 400)
        self.assertIn("too large", body)
        self.assertEqual(self.led.list_entries(), [])


class TestEvidenceServing(AnchoredCase):

    def test_ctype_comes_from_magic_attachment_by_default(self):
        url = self.capture(files={"evidence": ("scan.png", PNG, "image/png")})
        row = self.led.list_evidence(url.rsplit("/", 1)[1])[0]
        status, body, h = self.get("/evidence/%d" % row["id"])
        self.assertEqual(status, 200)
        self.assertEqual(h["Content-Type"], "image/png")
        self.assertTrue(h["Content-Disposition"].startswith("attachment"))
        self.assertEqual(body, PNG)
        # ?view=1 may inline it — only because the magic bytes validated
        _s, _b, h2 = self.get("/evidence/%d?view=1" % row["id"])
        self.assertTrue(h2["Content-Disposition"].startswith("inline"))

    def test_disposition_filename_is_sanitised(self):
        url = self.capture(files={"evidence": ("scan.png", PNG, "image/png")})
        row = self.led.list_evidence(url.rsplit("/", 1)[1])[0]
        _s, _b, h = self.get("/evidence/%d" % row["id"])
        disp = h["Content-Disposition"]
        name = disp.split('filename="')[1].split('"')[0]
        self.assertNotIn("/", name)
        self.assertNotIn('"', name)

    def test_traversal_shaped_ids_404(self):
        for path in ("/evidence/../../etc/passwd",
                     "/evidence/..%2f..%2fetc%2fpasswd",
                     "/evidence/1e1"):
            status, _, _ = self.get(path, expect=None)
            self.assertEqual(status, 404, path)

    def test_stored_path_escaping_the_root_is_refused(self):
        url = self.capture(files={"evidence": ("cert.pdf", PDF,
                                               "application/pdf")})
        row = self.led.list_evidence(url.rsplit("/", 1)[1])[0]
        # Same bytes, parked outside the evidence root: the hash verifies,
        # so only the resolved-path prefix guard stands between this row
        # and serving an arbitrary file.
        outside = os.path.join(self.dir, "outside.pdf")
        with open(outside, "wb") as fh:
            fh.write(PDF)
        with self.led.conn() as c:
            c.execute("UPDATE evidence SET stored_path=? WHERE id=?",
                      ("../outside.pdf", row["id"]))
        status, _, _ = self.get("/evidence/%d" % row["id"], expect=None)
        self.assertEqual(status, 404)


class TestVoidOverHTTP(AnchoredCase):

    def test_void_is_terminal_excluded_and_dupe_transparent(self):
        url = self.capture()
        uuid = url.rsplit("/", 1)[1]
        self.assertEqual(self.confirm(url), 303)
        self.assertIn("10 of 120", self.html("/"))

        status, _, _ = self.post(url, {"action": "status", "to": L.VOID})
        self.assertEqual(status, 303)
        e = self.led.get_entry(uuid)
        self.assertEqual(e["status"], L.VOID)
        self.assertTrue(e["voided_at"])
        # excluded from totals the moment it is void
        self.assertIn("0 of 120", self.html("/"))
        # the capture event survives — voiding never touches the kill-switch
        self.assertEqual(self.led.capture_counts()["real"], 1)

        # the page goes read-only: no edit form, no transitions, no
        # mark-submitted
        page = self.html(url)
        self.assertIn("read-only", page)
        self.assertNotIn("name=title", page)
        self.assertNotIn("value=status", page)
        self.assertNotIn("value=submitted", page)

        # dupe transparency: an identical entry captures AND confirms
        url2 = self.capture()
        self.assertEqual(self.confirm(url2), 303)
        self.assertIn("10 of 120", self.html("/"))

    def test_void_never_transitions_out(self):
        url = self.capture()
        self.post(url, {"action": "status", "to": L.VOID})
        status, _, _ = self.post(url, {"action": "status",
                                       "to": L.CONFIRMED})
        self.assertEqual(status, 400)

    def test_void_rows_still_export(self):
        url = self.capture(title="Voided course")
        uuid = url.rsplit("/", 1)[1]
        self.post(url, {"action": "status", "to": L.VOID})
        _s, body, _ = self.get("/export/entries.csv")
        text = body.decode("utf-8")
        self.assertIn(uuid, text)
        self.assertIn("void", text)


class TestSupersedeViaWeb(AnchoredCase):

    def test_edit_of_confirmed_routes_through_supersede(self):
        url = self.capture(files={"evidence": ("cert.pdf", PDF,
                                               "application/pdf")})
        old_uuid = url.rsplit("/", 1)[1]
        self.confirm(url)

        # the edit page says what saving will do
        page = self.html(url)
        self.assertIn("marked superseded", page)
        self.assertIn("Save as correction", page)

        status, _, h = self.post(url, {
            "activity_date": "2025-03-14", "title": "Workshop (corrected)",
            "provider": "SANS", "hours": "8", "count": "1",
            "tags": ["training"], "markers": ["domain_security"],
            "notes": "hours corrected", "cost_usd": ""})
        self.assertEqual(status, 303)
        new_uuid = h["Location"].rsplit("/", 1)[1]
        self.assertNotEqual(new_uuid, old_uuid)

        old, new = self.led.get_entry(old_uuid), self.led.get_entry(new_uuid)
        self.assertEqual(old["status"], L.SUPERSEDED)
        self.assertEqual(new["status"], L.CONFIRMED)
        self.assertEqual(new["supersedes"], old_uuid)
        self.assertEqual(new["title"], "Workshop (corrected)")
        self.assertEqual(new["hours"], 8.0)
        # evidence carried forward, hash-identical
        old_ev = self.led.list_evidence(old_uuid)
        new_ev = self.led.list_evidence(new_uuid)
        self.assertEqual(len(new_ev), 1)
        self.assertEqual(new_ev[0]["sha256"], old_ev[0]["sha256"])
        # only the correction counts
        self.assertIn("8 of 120", self.html("/"))
        # lineage renders both ways
        self.assertIn(old_uuid[:8], self.html(h["Location"]))
        self.assertIn("superseded by", self.html(url))

    def test_edit_of_captured_updates_in_place(self):
        url = self.capture()
        uuid = url.rsplit("/", 1)[1]
        status, _, h = self.post(url, {
            "activity_date": "2025-03-14", "title": "Renamed",
            "provider": "SANS", "hours": "10", "count": "1",
            "tags": ["training"], "markers": ["domain_security"],
            "notes": "", "cost_usd": ""})
        self.assertEqual(status, 303)
        self.assertEqual(h["Location"].rsplit("/", 1)[1], uuid)
        self.assertEqual(self.led.get_entry(uuid)["title"], "Renamed")
        self.assertEqual(len(self.led.list_entries()), 1)


class TestSubmissionsOverHTTP(AnchoredCase):

    def test_track_mark_and_csv(self):
        url = self.capture(title="Submittable course")
        uuid = url.rsplit("/", 1)[1]
        self.confirm(url)
        body_id = self.app.rules.get("cissp").body_id

        page = self.html("/submissions")
        self.assertIn(uuid[:8], page)
        # The per-row date field has no visible label; it carries a name.
        self.assertIn('<input type=date name=date value=', page)
        self.assertEqual(page.count("<input type=date name=date "),
                         page.count('aria-label="Date submitted"'))

        _s, csv_body, h = self.get("/submissions.csv?credential=cissp")
        self.assertIn("text/csv", h["Content-Type"])
        self.assertIn(uuid, csv_body.decode("utf-8"))

        status, _, _ = self.post("/submissions", {
            "entry": uuid, "body": body_id, "date": "2025-07-01"})
        self.assertEqual(status, 303)
        e = self.led.get_entry(uuid)
        self.assertIn(body_id, e["submitted_to"])
        self.assertEqual(e["submitted_dates"][body_id], "2025-07-01")

        page = self.html("/submissions")
        self.assertNotIn(uuid[:8], page)
        self.assertIn("Everything counted this cycle is marked submitted",
                      page)

    def test_bad_mark_is_a_400_not_a_500(self):
        status, body, _ = self.post("/submissions", {
            "entry": "no-such-uuid", "body": "isc2", "date": ""})
        self.assertEqual(status, 400)
        self.assertIn("Cannot mark submitted", body)


class TestExportsOverHTTP(AnchoredCase):

    def test_full_ledger_csv_and_json(self):
        url = self.capture(title="Export me")
        uuid = url.rsplit("/", 1)[1]
        self.confirm(url)

        _s, body, h = self.get("/export/entries.csv")
        self.assertIn("text/csv", h["Content-Type"])
        text = body.decode("utf-8")
        self.assertTrue(text.startswith("uuid,status,"))
        self.assertIn(uuid, text)

        _s, body, h = self.get("/export/entries.json")
        self.assertIn("application/json", h["Content-Type"])
        data = json.loads(body)
        self.assertEqual(data["format"], "cpebot-ledger-v0.2")
        self.assertEqual(data["entries"][0]["uuid"], uuid)

    def test_per_credential_csv_and_409_without_anchor(self):
        url = self.capture()
        self.confirm(url)
        _s, body, h = self.get("/export/credential/cissp.csv")
        self.assertIn("text/csv", h["Content-Type"])
        self.assertIn("bucket", body.decode("utf-8"))
        status, _, _ = self.get("/export/credential/cfe.csv", expect=409)
        self.assertEqual(status, 409)
        status, _, _ = self.get("/export/credential/nope.csv", expect=404)
        self.assertEqual(status, 404)

    def test_export_page_links_everything(self):
        page = self.html("/export")
        self.assertIn("/export/entries.csv", page)
        self.assertIn("/export/entries.json", page)
        self.assertIn("/export/credential/cissp.csv", page)
        self.assertIn("/packet/cissp", page)


class TestChartsOverHTTP(AnchoredCase):

    def test_charts_are_inline_svg_with_data_tables(self):
        url = self.capture()
        self.confirm(url)
        page = self.html("/charts")
        self.assertIn("<svg", page)
        self.assertNotIn("<script", page)
        self.assertNotIn("http://", page.split("</header>", 1)[1])
        self.assertNotIn("https://", page)
        self.assertIn("Data table", page)
        self.assertIn("No cycle anchor set", page)   # the unanchored creds


class TestWhatIfOverHTTP(AnchoredCase):

    def test_dry_run_shows_landing_and_writes_nothing(self):
        url = self.capture()
        self.confirm(url)
        before = len(self.led.list_entries())
        page = self.html("/whatif?run=1&date=2025-06-01&hours=8&count=1"
                         "&tags=training&markers=domain_security")
        self.assertIn("Where it lands", page)
        self.assertIn("Forfeited", page)
        self.assertIn("no cycle anchor set", page)   # the unanchored creds
        self.assertEqual(len(self.led.list_entries()), before)

    def test_form_renders_without_running(self):
        page = self.html("/whatif")
        self.assertIn("Run the engine", page)
        self.assertNotIn("Where it lands", page)


class TestEntriesNewFilters(AnchoredCase):

    def test_marker_and_evidence_filters(self):
        a = self.capture(title="HasBoth", files={
            "evidence": ("cert.pdf", PDF, "application/pdf")})
        self.capture(title="PlainOne", markers=[])
        self.assertIn("HasBoth",
                      self.html("/entries?marker=domain_security"))
        self.assertNotIn("PlainOne",
                         self.html("/entries?marker=domain_security"))
        self.assertIn("HasBoth", self.html("/entries?evidence=with"))
        self.assertNotIn("PlainOne", self.html("/entries?evidence=with"))
        self.assertIn("PlainOne", self.html("/entries?evidence=without"))
        self.assertNotIn("HasBoth", self.html("/entries?evidence=without"))
        del a


class TestAdvisoryPanel(AnchoredCase):

    def test_panel_renders_rules_lines_and_names_the_seam(self):
        url = self.capture()
        page = self.html(url)
        self.assertIn("Advisories", page)
        self.assertIn("Advisory only", page)
        self.assertIn("LLM sanity check", page)
        self.assertIn("no model, no keys, no network", page)
        # no evidence -> the evidence caution appears
        self.assertIn("No evidence attached", page)


class TestUnverifiedBadge(AnchoredCase):
    """Rule values not confirmed on the body's own page carry a visible
    'unverified' marker whose tooltip says why and whose link is that page."""

    MARK = 'class="pill warn unverified" href="%s"'
    CTM = "https://www.atapworldwide.org/page/CTM"

    def setUp(self):
        super().setUp()
        self.post("/settings", {
            "credential": "ctm", "anchor_date": "2026-01-01",
            "carryover_in": "", "portal_credits": "", "portal_checked": "",
            "active": "1"})

    def test_rules_page_marks_ctm_with_its_source(self):
        page = self.html("/bodies")
        self.assertIn(self.MARK % self.CTM, page)
        self.assertIn("Not confirmed on the body&#x27;s own page", page)

    def test_credential_page_marks_unverified_caps_only_where_unverified(self):
        page = self.html("/credential/ctm")
        self.assertIn(self.MARK % self.CTM, page)
        self.assertNotIn("pill warn unverified", self.html("/credential/cissp"))

    def test_dashboard_card_marks_the_requirement(self):
        page = self.html("/")
        self.assertIn(self.MARK % self.CTM, page)

    def test_mark_is_text_only_for_a_non_http_source(self):
        self.assertEqual(W.unverified_mark(None), "")
        m = W.unverified_mark({"note": "x<y", "source_url": "javascript:1"})
        self.assertNotIn("href", m)
        self.assertIn("x&lt;y", m)


class TestErrorPage(WebCase):
    """An unexpected failure is a plain 500: the traceback goes to the log,
    never to the page."""

    def test_500_page_is_generic_and_the_traceback_is_logged(self):
        def boom(*_a, **_k):
            raise RuntimeError("detail-7f3a /private/path/ledger.db")
        self.app.dashboard = boom
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            status, body, _ = self.get("/", expect=500)
        page = body.decode("utf-8")
        self.assertIn("Something broke", page)
        self.assertIn("details are in the server log", page)
        for leak in ("Traceback", "detail-7f3a", "/private/path", "RuntimeError",
                     "web.py"):
            self.assertNotIn(leak, page)
        self.assertIn("Traceback", log.getvalue())
        self.assertIn("detail-7f3a", log.getvalue())


class TestSourceLinks(AnchoredCase):
    """A rules source renders as a link only for http(s); javascript: and
    data: render as plain text, even if a rules file slipped past the
    loader."""

    def test_source_link_helper(self):
        self.assertEqual(W.source_link("https://example.org/a"),
                         '<a href="https://example.org/a">https://example.org/a</a>')
        for url in ("javascript:alert(1)", "data:text/html,<b>x</b>",
                    " javascript:alert(1)", "JAVASCRIPT:alert(1)"):
            out = W.source_link(url)
            self.assertNotIn("<a", out, url)
            self.assertNotIn("<b>", out, url)

    def test_pages_never_link_a_non_http_source(self):
        cred = self.app.rules.get("cissp")
        real = cred.source_url
        self.assertTrue(real.startswith("https://"))
        for path in ("/bodies", "/credential/cissp"):
            self.assertIn('href="%s"' % real, self.html(path), path)
        for url in ("javascript:alert(1)", "data:text/html,x"):
            cred.source_url = url
            for path in ("/bodies", "/credential/cissp"):
                page = self.html(path)
                self.assertNotIn('href="%s' % url.split(":")[0], page, path)
                self.assertIn(W.esc(url), page, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Submission packets: per-entry copy-ready fields and the evidence bundle.

What must hold:

* the fields come from a mapping FILE, so a new body is a new file — proven
  here by adding one in a temp directory and watching a credential pick it up
  with no code change;
* the mapping format refuses anything shaped like a portal login;
* a value Registrar cannot know is marked "choose in portal", never guessed;
* the bundle carries exactly the bytes that were hashed at upload, and a file
  that no longer matches is left OUT and named as a gap.
"""

import datetime
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cpebot import bodies as B          # noqa: E402
from cpebot import cli                  # noqa: E402
from cpebot import ledger as L          # noqa: E402
from cpebot import packets as P         # noqa: E402
from test_web import WebCase            # noqa: E402

AS_OF = datetime.date(2026, 10, 21)
PDF = b"%PDF-1.4\nSAMPLE certificate. Not a real document.\n"


class PacketCase(unittest.TestCase):

    def setUp(self):
        self.rules = B.load()
        self.dir = tempfile.mkdtemp(prefix="cpebot-packets-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        self.maps = P.load_maps(rules=self.rules)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entry(self, date, title, hours=0.0, tags=("training",), markers=(),
              confirm=True, provider="Example Institute", **kw):
        u = self.led.add_entry(activity_date=date, title=title, hours=hours,
                               tags=list(tags), markers=list(markers),
                               provider=provider, **kw)
        if confirm:
            self.led.set_status(u, L.CONFIRMED)
        return u

    def field(self, entry, label):
        for f in entry["fields"]:
            if f["label"] == label:
                return f
        raise AssertionError("no field %r" % label)

    def write_map(self, directory, name, text):
        with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
            fh.write(text)


GENERIC = '''schema_version: 1
id: "generic"
fields:
  - label: "Title"
    source: "entry.title"
'''


class TestMapsAreData(PacketCase):

    def test_shipped_maps_load_and_isc2_is_honest_about_verification(self):
        self.assertIn("generic", self.maps)
        self.assertIn("isc2", self.maps)
        self.assertFalse(self.maps["isc2"].verified)
        self.assertTrue(self.maps["isc2"].verified_note)

    def test_credential_resolution(self):
        self.assertEqual(P.map_for(self.rules.get("cissp"), self.maps).id,
                         "isc2")
        self.assertEqual(P.map_for(self.rules.get("cfe"), self.maps).id,
                         "generic")

    def test_a_new_body_is_a_new_file_not_new_code(self):
        d = os.path.join(self.dir, "maps")
        os.makedirs(d)
        self.write_map(d, "generic.yaml", GENERIC)
        self.write_map(d, "acfe.yaml", '''schema_version: 1
id: "acfe"
form_name: "Example ACFE form"
body_id: "acfe"
fields:
  - label: "Course date"
    source: "entry.activity_date"
    format: "%d %b %Y"
  - label: "CPE credits earned"
    source: "credit.amount"
''')
        maps = P.load_maps(d, rules=self.rules)
        self.assertEqual(P.map_for(self.rules.get("cfe"), maps).id, "acfe")
        self.led.set_credential_state("cfe", anchor_date="2026-01-01")
        self.entry("2026-03-14", "SAMPLE fraud workshop", hours=4,
                   markers=("fraud",))
        pk = P.entry_packets(self.rules, self.led, "cfe", as_of=AS_OF,
                             maps=maps)
        e = pk["entries"][0]
        self.assertEqual([f["label"] for f in e["fields"]],
                         ["Course date", "CPE credits earned"])
        self.assertEqual(self.field(e, "Course date")["value"], "14 Mar 2026")
        self.assertEqual(self.field(e, "CPE credits earned")["value"], "4")

    def _bad(self, text, needle):
        d = tempfile.mkdtemp(dir=self.dir)
        self.write_map(d, "generic.yaml", GENERIC)
        self.write_map(d, "bad.yaml", text)
        with self.assertRaises(P.PacketMapError) as cm:
            P.load_maps(d, rules=self.rules)
        self.assertIn(needle, str(cm.exception))

    def test_portal_credentials_are_refused_anywhere_in_a_map(self):
        base = 'schema_version: 1\nid: "x"\nbody_id: "isc2"\n'
        self._bad(base + 'password: "hunter2"\nfields:\n  - label: "T"\n'
                  '    source: "entry.title"\n', "portal credential")
        self._bad(base + 'fields:\n  - label: "T"\n    source: "entry.title"\n'
                  '    map:\n      portal_login: "x"\n', "portal credential")

    def test_unknown_source_key_and_credential_are_refused(self):
        base = 'schema_version: 1\nid: "x"\nbody_id: "isc2"\nfields:\n'
        self._bad(base + '  - label: "T"\n    source: "entry.nope"\n',
                  "unknown source")
        self._bad(base + '  - label: "T"\n    source: "entry.title"\n'
                  '    colour: "red"\n', "unknown key")
        self._bad('schema_version: 1\nid: "x"\ncredentials: ["nope"]\n'
                  'fields:\n  - label: "T"\n    source: "entry.title"\n',
                  "unknown credential")
        self._bad(base + '  - label: "T"\n    source: "literal"\n',
                  "no value")
        self._bad(base + '  - label: "T"\n    source: "entry.title"\n'
                  '  - label: "T"\n    source: "entry.provider"\n',
                  "duplicate field")

    def test_a_non_http_source_url_is_refused(self):
        base = ('schema_version: 1\nid: "x"\nbody_id: "isc2"\nfields:\n'
                '  - label: "T"\n    source: "entry.title"\n')
        for url in ("javascript:alert(1)", "data:text/html,x"):
            self._bad(base + 'source_url: "%s"\n' % url, "http(s) URL")

    def test_a_directory_without_generic_is_refused(self):
        d = os.path.join(self.dir, "nogeneric")
        os.makedirs(d)
        with self.assertRaises(P.PacketMapError):
            P.load_maps(d, rules=self.rules)


class TestIsc2Fields(PacketCase):

    def setUp(self):
        PacketCase.setUp(self)
        self.a = self.entry("2025-10-02", "SAMPLE detection webinar", hours=8,
                            markers=("domain_security",), notes="demo row")
        self.b = self.entry("2025-11-06", "SAMPLE negotiation course",
                            hours=40)
        pk = P.entry_packets(self.rules, self.led, "cissp", as_of=AS_OF)
        self.pk = pk
        self.by = dict((e["uuid"], e) for e in pk["entries"])

    def test_groups_map_to_portal_wording(self):
        self.assertEqual(self.field(self.by[self.a], "CPE group")["value"],
                         "Group A")
        self.assertEqual(self.field(self.by[self.b], "CPE group")["value"],
                         "Group B")

    def test_date_uses_the_map_format(self):
        self.assertEqual(self.field(self.by[self.a], "Date completed")["value"],
                         "10/02/2025")

    def test_credits_are_per_activity_before_caps(self):
        # 40 Group B hours: the cycle total caps Group B at 30, but the
        # portal is told what the activity was worth; ISC2 applies the cap.
        self.assertEqual(self.field(self.by[self.b], "CPE credits")["value"],
                         "40")

    def test_unknowable_fields_say_choose_in_portal(self):
        f = self.field(self.by[self.a], "Domain")
        self.assertTrue(f["holder_choice"])
        self.assertEqual(f["value"], "")
        text = P.render_entry_text(self.pk, self.by[self.a])
        self.assertIn("Domain: %s" % P.HOLDER_CHOICE, text)

    def test_required_but_empty_is_flagged_missing(self):
        u = self.entry("2025-12-01", "SAMPLE no provider", hours=1,
                       markers=("domain_security",), provider="")
        pk = P.entry_packets(self.rules, self.led, "cissp", as_of=AS_OF)
        e = [x for x in pk["entries"] if x["uuid"] == u][0]
        self.assertTrue(self.field(e, "Provider")["missing"])
        self.assertIn("Provider: [MISSING]", P.render_entry_text(pk, e))

    def test_packet_text_says_unverified_and_who_submits(self):
        text = P.render_text(self.pk)
        self.assertIn("UNVERIFIED FIELD MAP", text)
        self.assertIn("never asks for or stores portal logins", text)

    def test_only_counted_cycle_entries_appear(self):
        self.entry("2024-01-01", "SAMPLE before the cycle", hours=3,
                   markers=("domain_security",))
        self.entry("2025-12-02", "SAMPLE still captured", hours=3,
                   markers=("domain_security",), confirm=False)
        v = self.entry("2025-12-03", "SAMPLE voided", hours=3,
                       markers=("domain_security",))
        self.led.void(v)
        self.entry("2025-12-04", "SAMPLE ineligible", hours=3,
                   tags=("special_project",))
        pk = P.entry_packets(self.rules, self.led, "cissp", as_of=AS_OF)
        self.assertEqual(sorted(e["uuid"] for e in pk["entries"]),
                         sorted([self.a, self.b]))

    def test_unsubmitted_filter(self):
        self.led.mark_submitted(self.a, "isc2", on_date="2026-01-05")
        pk = P.entry_packets(self.rules, self.led, "cissp", as_of=AS_OF,
                             unsubmitted_only=True)
        self.assertEqual([e["uuid"] for e in pk["entries"]], [self.b])
        pk = P.entry_packets(self.rules, self.led, "cissp", as_of=AS_OF)
        self.assertTrue(self.by[self.a]["uuid"] in
                        [e["uuid"] for e in pk["entries"] if e["submitted"]])

    def test_no_anchor_is_an_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            P.entry_packets(self.rules, self.led, "cfe", as_of=AS_OF)


class TestEvidenceBundle(PacketCase):

    def setUp(self):
        PacketCase.setUp(self)
        self.good = self.entry("2025-10-02", "SAMPLE good evidence", hours=2,
                               markers=("domain_security",))
        self.ev_good = self.led.add_evidence(self.good, "cert.pdf", PDF)
        self.bad = self.entry("2025-10-03", "SAMPLE altered evidence",
                              hours=2, markers=("domain_security",))
        self.ev_bad = self.led.add_evidence(self.bad, "tampered.pdf",
                                            PDF + b"original")
        self.gone = self.entry("2025-10-04", "SAMPLE missing evidence",
                               hours=2, markers=("domain_security",))
        self.ev_gone = self.led.add_evidence(self.gone, "gone.pdf",
                                             PDF + b"gone")
        self.bare = self.entry("2025-10-05", "SAMPLE no evidence", hours=2,
                               markers=("domain_security",))
        with open(self.led.evidence_path(self.ev_bad), "ab") as fh:
            fh.write(b"edited later")
        os.remove(self.led.evidence_path(self.ev_gone))
        self.data, self.manifest = P.bundle(self.rules, self.led, "cissp",
                                            as_of=AS_OF)
        self.zf = zipfile.ZipFile(io.BytesIO(self.data))
        self.root = "cissp-evidence-%s/" % AS_OF

    def test_good_file_is_bundled_byte_for_byte(self):
        name = self.root + "evidence/2025-10-02_%s_cert.pdf" % self.good[:8]
        self.assertEqual(self.zf.read(name), PDF)

    def test_altered_file_is_left_out_and_named(self):
        names = self.zf.namelist()
        self.assertFalse([n for n in names if n.endswith("tampered.pdf")])
        whys = [g["why"] for g in self.manifest["gaps"]
                if g["entry"] == self.bad]
        self.assertTrue(whys and "NOT bundled" in whys[0])
        status = [f["status"] for e in self.manifest["entries"]
                  if e["uuid"] == self.bad for f in e["files"]]
        self.assertEqual(status, ["ALTERED"])

    def test_missing_and_bare_entries_are_gaps(self):
        gaps = dict((g["entry"], g["why"]) for g in self.manifest["gaps"])
        self.assertIn("missing", gaps[self.gone])
        self.assertIn("no evidence", gaps[self.bare])
        self.assertEqual(self.manifest["files_bundled"], 1)

    def test_manifest_json_matches_the_zip(self):
        m = json.loads(self.zf.read(self.root + "manifest.json"))
        self.assertEqual(m["format"], "registrar-evidence-bundle-v1")
        for e in m["entries"]:
            for f in e["files"]:
                if f["status"] != "OK":
                    continue
                import hashlib
                self.assertEqual(
                    hashlib.sha256(self.zf.read(self.root + f["path"]))
                    .hexdigest(), f["sha256"])

    def test_markdown_manifest_leads_with_gaps(self):
        md = self.zf.read(self.root + "MANIFEST.md").decode("utf-8")
        self.assertLess(md.index("## Gaps"), md.index("## Files"))
        self.assertIn("UNVERIFIED field map", md)

    def test_one_copy_ready_text_per_entry(self):
        texts = [n for n in self.zf.namelist() if "/packets/" in n]
        self.assertEqual(len(texts), 4)
        self.assertIn(self.root + "PACKET.txt", self.zf.namelist())

    def test_bundle_is_deterministic(self):
        again, _m = P.bundle(self.rules, self.led, "cissp", as_of=AS_OF)
        self.assertEqual(again, self.data)


class TestPacketCli(PacketCase):

    def test_text_and_zip(self):
        self.entry("2025-10-02", "SAMPLE cli entry", hours=2,
                   markers=("domain_security",))
        base = ["--db", self.led.path, "--evidence", self.led.evidence_root]
        out = os.path.join(self.dir, "p.txt")
        self.assertEqual(cli.main(base + ["packet", "cissp", "--out", out,
                                          "--as-of", "2026-10-21"]), 0)
        with open(out, encoding="utf-8") as fh:
            self.assertIn("SAMPLE cli entry", fh.read())
        z = os.path.join(self.dir, "b.zip")
        self.assertEqual(cli.main(base + ["packet", "cissp", "--zip", z]), 0)
        self.assertEqual(stat.S_IMODE(os.stat(z).st_mode), 0o600)
        self.assertTrue(zipfile.is_zipfile(z))
        self.assertEqual(cli.main(base + ["packet", "cfe"]), 1)   # no anchor
        self.assertEqual(cli.main(base + ["packet", "nope"]), 2)


CTM_SOURCE = "https://www.atapworldwide.org/page/CTM"


class TestUnverifiedRules(PacketCase):
    """A rule value nobody confirmed on the body's own page says so, with
    the page to check, wherever a packet shows it."""

    def setUp(self):
        PacketCase.setUp(self)
        self.led.set_credential_state("ctm", anchor_date="2026-01-01")
        self.entry("2026-03-14", "SAMPLE ctm course", hours=4)

    def test_rule_fields_are_marked_and_entry_fields_are_not(self):
        pk = P.entry_packets(self.rules, self.led, "ctm", as_of=AS_OF)
        self.assertEqual(pk["rules_unverified"]["source_url"], CTM_SOURCE)
        e = pk["entries"][0]
        self.assertTrue(self.field(e, "Credits claimed")["unverified"])
        self.assertTrue(self.field(e, "Category")["unverified"])
        for label in ("Activity date", "Title", "Hours"):
            self.assertIsNone(self.field(e, label)["unverified"], label)

    def test_text_carries_the_marker_and_a_footnote_with_the_source(self):
        pk = P.entry_packets(self.rules, self.led, "ctm", as_of=AS_OF)
        text = P.render_entry_text(pk, pk["entries"][0])
        self.assertIn("Credits claimed: 4 [unverified*]", text)
        self.assertIn("* unverified: this value comes from rules not "
                      "confirmed", text)
        self.assertIn(CTM_SOURCE, text)
        self.assertIn("UNVERIFIED RULES:", P.render_text(pk))

    def test_manifest_names_the_unverified_rules(self):
        data, manifest = P.bundle(self.rules, self.led, "ctm", as_of=AS_OF)
        self.assertEqual(manifest["rules_unverified"]["source_url"],
                         CTM_SOURCE)
        zf = zipfile.ZipFile(io.BytesIO(data))
        md = zf.read("ctm-evidence-%s/MANIFEST.md" % AS_OF).decode("utf-8")
        self.assertIn("Rules: **unverified**", md)
        self.assertIn(CTM_SOURCE, md)
        m = json.loads(zf.read("ctm-evidence-%s/manifest.json" % AS_OF))
        flags = [f["unverified"] for e in m["entries"] for f in e["fields"]]
        self.assertIn(True, flags)

    def test_verified_rules_carry_no_marker(self):
        self.entry("2025-10-02", "SAMPLE cissp course", hours=2,
                   markers=("domain_security",))
        pk = P.entry_packets(self.rules, self.led, "cissp", as_of=AS_OF)
        self.assertIsNone(pk["rules_unverified"])
        self.assertFalse([f for e in pk["entries"] for f in e["fields"]
                          if f["unverified"]])
        self.assertNotIn("unverified*", P.render_text(pk))
        _, manifest = P.bundle(self.rules, self.led, "cissp", as_of=AS_OF)
        self.assertIsNone(manifest["rules_unverified"])


class TestPacketsOverHTTP(WebCase):

    def setUp(self):
        WebCase.setUp(self)
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        u = self.led.add_entry(activity_date="2025-10-02",
                               title="SAMPLE web packet entry", hours=2,
                               tags=["training"], markers=["domain_security"],
                               provider="Example Institute")
        self.led.set_status(u, L.CONFIRMED)
        self.led.add_evidence(u, "cert.pdf", PDF)

    def test_page_renders_fields_and_never_asks_for_a_login(self):
        page = self.html("/packets/cissp")
        self.assertIn("SAMPLE web packet entry", page)
        self.assertIn("choose in portal", page)
        self.assertIn("UNVERIFIED field map", page)
        self.assertNotIn("type=password", page.replace('"', ""))
        self.assertNotIn("<script", page)

    def test_bundle_route(self):
        status, body, headers = self.get("/packets/cissp.zip")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        names = zipfile.ZipFile(io.BytesIO(body)).namelist()
        self.assertTrue(any(n.endswith("_cert.pdf") for n in names))

    def test_unknown_and_unanchored(self):
        self.assertEqual(self.get("/packets/nope", expect=404)[0], 404)
        self.assertEqual(self.get("/packets/cfe", expect=409)[0], 409)
        self.assertEqual(self.get("/packets/cfe.zip", expect=409)[0], 409)

    def test_linked_from_the_credential_page(self):
        page = self.html("/credential/cissp")
        self.assertIn("/packets/cissp.zip", page)
        self.assertIn("/deadlines.ics?credential=cissp", page)

    def test_unverified_rules_marked_on_the_packet_page(self):
        self.led.set_credential_state("ctm", anchor_date="2026-01-01")
        u = self.led.add_entry(activity_date="2026-03-14",
                               title="SAMPLE ctm web entry", hours=4,
                               tags=["training"], markers=[],
                               provider="Example Institute")
        self.led.set_status(u, L.CONFIRMED)
        page = self.html("/packets/ctm")
        self.assertIn('class="pill warn unverified" href="%s"' % CTM_SOURCE,
                      page)
        self.assertNotIn("pill warn unverified", self.html("/packets/cissp"))


if __name__ == "__main__":
    unittest.main()

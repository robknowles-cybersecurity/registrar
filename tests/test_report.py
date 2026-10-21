"""Renewal report and submission packet.

The report is the artefact the holder actually reads at renewal time, so the
tests here are about what it TELLS them, not about whether the functions return. Three
things must survive: the cap-aware arithmetic has to reach the page intact,
a gap has to be visible without reading to the end, and the packet has to
name what is missing rather than quietly shipping an incomplete folder.
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B          # noqa: E402
from cpebot import engine as E          # noqa: E402
from cpebot import ledger as L          # noqa: E402
from cpebot import report as R          # noqa: E402

TODAY = datetime.date(2026, 8, 30)


class ReportCase(unittest.TestCase):

    def setUp(self):
        self.rules = B.load()
        self.dir = tempfile.mkdtemp(prefix="cpebot-report-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entry(self, date, title, hours=0.0, count=1, tags=("training",),
              markers=(), confirm=True, **kw):
        u = self.led.add_entry(activity_date=date, title=title, hours=hours,
                               count=count, tags=list(tags),
                               markers=list(markers), **kw)
        if confirm:
            self.led.set_status(u, L.CONFIRMED)
        return u


class TestIncompleteWithoutAnchor(ReportCase):
    """No anchor date means no cycle, and the report must say so, not guess."""

    def test_missing_anchor_reports_incomplete(self):
        rep = R.credential_report(self.rules, self.led, "cissp", TODAY)
        self.assertTrue(rep["incomplete"])
        self.assertIn("anchor", rep["reason"])
        self.assertNotIn("assessment", rep)

    def test_full_report_covers_every_credential(self):
        rep = R.full_report(self.rules, self.led, TODAY)
        self.assertEqual(len(rep["credentials"]), len(self.rules.credentials))
        self.assertEqual(rep["capture_counts"]["threshold"], 8)


class TestCapAwareMathReachesThePage(ReportCase):
    """The number on the report must be the capped one, with the loss shown."""

    def setUp(self):
        ReportCase.setUp(self)
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        # 80 Group A hours + 40 Group B hours. Group B is capped at 30, so a
        # naive sum says 120 (done!) and the truth is 110 with the Group A
        # floor of 90 also unmet. This is the whole product in one fixture.
        self.entry("2025-02-10", "Domain deep dive", hours=80,
                   markers=("domain_security",))
        self.entry("2025-06-10", "Management course", hours=40,
                   tags=("training",))
        self.rep = R.credential_report(self.rules, self.led, "cissp", TODAY)

    def test_awarded_is_capped_not_naive(self):
        a = self.rep["assessment"]
        self.assertEqual(a["awarded"], 110.0)
        self.assertEqual(a["naive_total"], 120.0)
        self.assertEqual(a["forfeited"], 10.0)

    def test_floor_shortfall_is_named(self):
        a = self.rep["assessment"]
        self.assertEqual(a["unmet_floors"], ["group_a"])
        ga = [g for g in a["groups"] if g["id"] == "group_a"][0]
        self.assertEqual(ga["awarded"], 80.0)
        self.assertEqual(ga["shortfall"], 10.0)

    def test_markdown_shows_the_forfeiture_and_the_naive_trap(self):
        md = R.render_markdown(self.rep)
        self.assertIn("CISSP", md)
        self.assertIn("110", md)          # the real figure
        self.assertIn("120", md)          # what naive summing would have said
        self.assertIn("10", md)           # forfeited / shortfall
        # The report must not congratulate a portfolio that fails a floor.
        self.assertNotIn(E.COMPLIANT, md)

    def test_pace_block_is_present(self):
        self.assertIn("days_left", self.rep["pace"])
        self.assertIn("per_month_needed", self.rep["pace"])


class TestEvidenceCompleteness(ReportCase):

    def setUp(self):
        ReportCase.setUp(self)
        self.led.set_credential_state("cfe", anchor_date="2026-03-01")
        self.a = self.entry("2026-04-02", "Fraud examination refresher",
                            hours=12, markers=("fraud",))
        self.b = self.entry("2026-05-06", "Ethics for examiners", hours=4,
                            markers=("ethics",))
        self.led.add_evidence(self.a, "cert.pdf", b"%PDF-1.4 certificate")

    def test_completeness_counts_only_in_cycle_entries(self):
        rep = R.credential_report(self.rules, self.led, "cfe", TODAY)
        ev = rep["evidence"]
        self.assertEqual(ev["entries_counted"], 2)
        self.assertEqual(ev["entries_with_evidence"], 1)
        self.assertEqual(ev["completeness_pct"], 50.0)
        self.assertEqual(ev["files_checked"], 1)
        self.assertEqual(ev["failures"], [])

    def test_acfe_retention_and_required_fields_are_carried(self):
        rep = R.credential_report(self.rules, self.led, "cfe", TODAY)
        self.assertEqual(rep["evidence"]["retention_years"], 3)
        self.assertIn("attendee name", rep["evidence"]["required_fields"])
        self.assertTrue(rep["submission"]["self_certified"])

    def test_a_tampered_file_becomes_a_report_failure(self):
        row = self.led.list_evidence(self.a)[0]
        with open(self.led.evidence_path(row), "wb") as fh:
            fh.write(b"not the file that was submitted")
        rep = R.credential_report(self.rules, self.led, "cfe", TODAY)
        fails = rep["evidence"]["failures"]
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0]["entry_uuid"], self.a)
        self.assertIn("ALTERED", fails[0]["states"])
        self.assertIn("ALTERED", R.render_markdown(rep))

    def test_a_deleted_file_becomes_a_report_failure(self):
        row = self.led.list_evidence(self.a)[0]
        os.remove(self.led.evidence_path(row))
        rep = R.credential_report(self.rules, self.led, "cfe", TODAY)
        self.assertIn("MISSING", rep["evidence"]["failures"][0]["states"])


class TestSubmittedVersusLogged(ReportCase):

    def setUp(self):
        ReportCase.setUp(self)
        self.led.set_credential_state("ceh", anchor_date="2024-11-15")
        # The 2025 hours are the dangerous ones: EC-Council's window for
        # credits earned in 2025 shut on 2026-02-01, so leaving that one
        # unregistered is a loss that cannot be repaired by studying harder.
        self.x = self.entry("2025-03-01", "Unregistered webinar", hours=10)
        self.y = self.entry("2026-03-01", "Recorded webinar", hours=10)
        self.led.mark_submitted(self.y, "eccouncil")

    def test_counts_split(self):
        rep = R.credential_report(self.rules, self.led, "ceh", TODAY)
        s = rep["submission"]
        self.assertEqual(s["logged"], 2)
        self.assertEqual(s["submitted"], 1)
        self.assertEqual(s["unsubmitted"], 1)
        self.assertEqual(s["cadence"], "annual_window")

    def test_annual_window_deadline_is_surfaced(self):
        rep = R.credential_report(self.rules, self.led, "ceh", TODAY)
        deadlines = rep["submission"]["deadlines"]
        self.assertTrue(deadlines)
        years = dict((d["earned_year"], d) for d in deadlines)
        self.assertIn(2025, years)
        # Credits earned in 2025 had to be registered by 2026-02-01; as of
        # 2026-08-30 that window is shut and the report must say so.
        self.assertTrue(years[2025]["overdue"])
        self.assertIn("2026-02-01", str(years[2025]["register_by"]))


class TestPortalReconciliation(ReportCase):

    def setUp(self):
        ReportCase.setUp(self)
        self.led.set_credential_state("cysa_plus", anchor_date="2024-10-01")
        self.entry("2025-01-05", "Training", hours=30)

    def test_no_portal_figure_is_stated_not_assumed(self):
        rep = R.credential_report(self.rules, self.led, "cysa_plus", TODAY)
        p = rep["portal"]
        self.assertIsNone(p["portal_credits"])
        self.assertIsNone(p["diff"])
        self.assertIn("cannot do for you", p["note"])

    def test_disagreement_is_quantified_and_portal_called_authoritative(self):
        self.led.set_credential_state("cysa_plus", portal_credits=22,
                                      portal_checked="2026-08-01")
        rep = R.credential_report(self.rules, self.led, "cysa_plus", TODAY)
        p = rep["portal"]
        self.assertEqual(p["ledger_credits"], 30.0)
        self.assertEqual(p["diff"], 8.0)
        self.assertIn("portal is authoritative", p["note"])
        self.assertIn("8", R.render_markdown(rep))

    def test_agreement_says_so(self):
        self.led.set_credential_state("cysa_plus", portal_credits=30)
        rep = R.credential_report(self.rules, self.led, "cysa_plus", TODAY)
        self.assertEqual(rep["portal"]["diff"], 0)
        self.assertIn("agree", rep["portal"]["note"])


class TestFeesAndUnverified(ReportCase):

    def test_unpaid_fee_is_isolated(self):
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        paid = self.led.add_fee("cissp", "AMF 2025", 135, "2025-09-01")
        self.led.add_fee("cissp", "AMF 2026", 135, "2026-09-01")
        self.led.mark_fee_paid(paid, "2025-08-20")
        rep = R.credential_report(self.rules, self.led, "cissp", TODAY)
        self.assertEqual(len(rep["fees"]["recorded"]), 2)
        self.assertEqual(len(rep["fees"]["unpaid"]), 1)
        self.assertEqual(rep["fees"]["unpaid"][0]["name"], "AMF 2026")

    def test_ctm_report_is_advisory_and_says_why(self):
        self.led.set_credential_state("ctm", anchor_date="2025-06-01")
        self.entry("2025-09-01", "Threat assessment seminar", hours=40,
                   tags=("training",))
        rep = R.credential_report(self.rules, self.led, "ctm", TODAY)
        self.assertTrue(rep["advisory_only"])
        self.assertEqual(rep["assessment"]["verdict"], E.UNVERIFIED_CANNOT_ASSERT)
        self.assertTrue(rep["unverified_reasons"])
        md = R.render_markdown(rep)
        self.assertIn("UNVERIFIED", md.upper())

    def test_every_credential_carries_its_source_and_date(self):
        for cred in self.rules.credentials:
            self.led.set_credential_state(cred.id, anchor_date="2025-01-01")
        rep = R.full_report(self.rules, self.led, TODAY)
        for c in rep["credentials"]:
            self.assertIn("source_url", c)
            self.assertIn("verified_date", c)


class TestPacket(ReportCase):

    def setUp(self):
        ReportCase.setUp(self)
        self.led.set_config("attendee_name", "Sample Holder")
        self.led.set_credential_state("cfe", anchor_date="2026-03-01")
        self.good = self.entry("2026-04-02", "Fraud examination refresher",
                               hours=12, markers=("fraud",),
                               provider="ACFE Chapter")
        self.bare = self.entry("2026-05-06", "Ethics for examiners", hours=4,
                               markers=("ethics",), provider="ACFE")
        self.led.add_evidence(self.good, "certificate.pdf", b"%PDF certificate")
        self.dest = os.path.join(self.dir, "packet")

    def test_packet_copies_files_and_writes_a_manifest(self):
        s = R.build_packet(self.rules, self.led, "cfe", self.dest, TODAY)
        self.assertEqual(s["entries"], 2)
        self.assertEqual(s["files"], 1)
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "manifest.md")))
        copied = os.listdir(os.path.join(self.dest, "evidence"))
        self.assertEqual(len(copied), 1)
        self.assertTrue(copied[0].startswith("2026-04-02_"))
        self.assertTrue(copied[0].endswith("certificate.pdf"))

    def test_gaps_appear_above_the_activity_table(self):
        s = R.build_packet(self.rules, self.led, "cfe", self.dest, TODAY)
        md = s["manifest"]
        self.assertIn("## Gaps in this packet", md)
        self.assertLess(md.index("## Gaps in this packet"),
                        md.index("## Activities"))
        self.assertIn(self.bare[:8], md)
        self.assertIn("no evidence attached", md)

    def test_manifest_uses_the_bodys_own_field_names(self):
        s = R.build_packet(self.rules, self.led, "cfe", self.dest, TODAY)
        md = s["manifest"]
        for field in R.packet_fields(self.rules.get("cfe")):
            self.assertIn(field.title(), md)
        self.assertIn("Sample Holder", md)

    def test_a_missing_file_is_reported_not_silently_skipped(self):
        row = self.led.list_evidence(self.good)[0]
        os.remove(self.led.evidence_path(row))
        s = R.build_packet(self.rules, self.led, "cfe", self.dest, TODAY)
        whys = [m["why"] for m in s["missing"]]
        self.assertIn("file missing from disk", whys)
        self.assertEqual(s["files"], 0)

    def test_clean_packet_says_no_gaps(self):
        self.led.add_evidence(self.bare, "receipt.pdf", b"%PDF receipt")
        s = R.build_packet(self.rules, self.led, "cfe", self.dest, TODAY)
        self.assertEqual(s["missing"], [])
        self.assertIn("None: every counted entry", s["manifest"])

    def test_packet_without_an_anchor_refuses(self):
        with self.assertRaises(ValueError):
            R.build_packet(self.rules, self.led, "gcitp",
                           os.path.join(self.dir, "p2"), TODAY)

    def test_zip_contains_manifest_and_evidence(self):
        out = os.path.join(self.dir, "cfe.zip")
        s = R.zip_packet(self.rules, self.led, "cfe", out,
                         os.path.join(self.dir, "work"), TODAY)
        self.assertTrue(s["zip_bytes"] > 0)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("manifest.md", names)
            self.assertTrue(any(n.startswith("evidence/") for n in names))
            body = zf.read("manifest.md").decode("utf-8")
        self.assertIn("CFE", body)


class TestCapturedEntriesStayOutOfTheReport(ReportCase):
    """Nothing auto-commits: an unconfirmed capture must not move a number."""

    def test_captured_entry_is_invisible_to_the_report(self):
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-04-01", "Confirmed", hours=10,
                   markers=("domain_security",))
        pending = self.entry("2025-04-02", "Not yet confirmed", hours=90,
                             markers=("domain_security",), confirm=False)
        rep = R.credential_report(self.rules, self.led, "cissp", TODAY)
        self.assertEqual(rep["assessment"]["awarded"], 10.0)
        self.assertEqual(rep["evidence"]["entries_counted"], 1)
        self.led.confirm(pending)
        rep2 = R.credential_report(self.rules, self.led, "cissp", TODAY)
        self.assertEqual(rep2["assessment"]["awarded"], 100.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

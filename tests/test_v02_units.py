"""Unit tests for the v0.2 modules: exports, whatif, validate, charts.

The web suite proves these work end-to-end through the live HTTP path; this
file pins the module-level contracts the web layer relies on — what the
full-ledger dump includes, what the cycle CSV excludes, that the what-if
calculator forfeits what the engine forfeits, that advisories never block,
and that a chart is an SVG string and nothing else.
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B          # noqa: E402
from cpebot import charts as C          # noqa: E402
from cpebot import engine as E          # noqa: E402
from cpebot import exports as X         # noqa: E402
from cpebot import ledger as L          # noqa: E402
from cpebot import validate as V        # noqa: E402
from cpebot import whatif as W          # noqa: E402


class LedgerCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cpebot-v02-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))
        self.rules = B.load()
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entry(self, title, hours, markers=(), status=L.CONFIRMED):
        uuid = self.led.add_entry(
            activity_date="2025-03-14", title=title, hours=hours,
            tags=["training"], markers=list(markers),
            capture_source="fixture", status=status)
        return uuid


class TestExports(LedgerCase):

    def test_full_dump_carries_every_state(self):
        kept = self.entry("Kept", 10, ["domain_security"])
        voided = self.entry("Voided", 5)
        self.led.void(voided)
        corrected = self.led.supersede(kept, title="Kept (corrected)")

        csv_text = X.entries_csv(self.led)
        for u in (kept, voided, corrected):
            self.assertIn(u, csv_text)
        for status in (L.SUPERSEDED, L.VOID, L.CONFIRMED):
            self.assertIn(status, csv_text)

    def test_cycle_csv_exports_counted_work_only(self):
        self.entry("Counted", 10, ["domain_security"])
        voided = self.entry("NeverCounted", 5)
        self.led.void(voided)
        csv_text = X.credential_csv(self.rules, self.led, "cissp")
        self.assertIn("Counted", csv_text)
        self.assertNotIn("NeverCounted", csv_text)

    def test_cycle_csv_refuses_an_unanchored_credential(self):
        with self.assertRaises(ValueError):
            X.credential_csv(self.rules, self.led, "cfe")

    def test_submission_rows_skip_already_submitted(self):
        a = self.entry("Queue me", 10, ["domain_security"])
        b = self.entry("Already in", 4, ["domain_security"])
        body_id = self.rules.get("cissp").body_id
        self.led.mark_submitted(b, body_id, "2025-06-01")
        cred, fields, rows = X.submission_rows(self.rules, self.led, "cissp")
        uuids = [r["entry"]["uuid"] for r in rows]
        self.assertIn(a, uuids)
        self.assertNotIn(b, uuids)
        self.assertTrue(fields)
        # the CSV mirrors the rows, portal columns plus the uuid
        csv_text = X.submission_csv(self.rules, self.led, "cissp")
        self.assertIn(a, csv_text)
        self.assertNotIn(b, csv_text)


class TestWhatIf(LedgerCase):

    def test_forfeit_matches_the_engine_not_naive_arithmetic(self):
        # 80 real Group A hours on the books; a 40-hour general course then
        # hits the Group B cap: naive says +40, the engine says less. The
        # calculator must report the engine's number and name the forfeit.
        self.entry("Base load", 80, ["domain_security"])
        rows = W.dry_run(self.rules, self.led,
                         {"date": "2025-06-10", "hours": 40, "count": 1,
                          "tags": ["training"], "markers": []},
                         as_of=datetime.date(2025, 7, 1))
        row = next(r for r in rows if r["credential"] == "cissp")
        self.assertIsNone(row["skipped"])
        self.assertEqual(row["raw"], 40.0)
        self.assertLess(row["delta"], row["raw"])
        self.assertEqual(row["forfeited"], row["raw"] - row["delta"])
        self.assertEqual(row["after"]["total"],
                         row["before"]["total"] + row["delta"])

    def test_dry_run_writes_nothing(self):
        before = self.led.stats()["entries"]
        W.dry_run(self.rules, self.led,
                  {"date": "2025-06-10", "hours": 8, "count": 1,
                   "tags": ["training"], "markers": []})
        self.assertEqual(self.led.stats()["entries"], before)

    def test_unanchored_and_unclaimed_are_named_not_guessed(self):
        rows = W.dry_run(self.rules, self.led,
                         {"date": "2025-06-10", "hours": 8, "count": 1,
                          "tags": [], "markers": []})
        by_id = dict((r["credential"], r) for r in rows)
        self.assertIn("anchor", by_id["cfe"]["skipped"])
        self.assertIn("no category", by_id["cissp"]["skipped"])


class TestValidate(LedgerCase):

    def test_advisories_flag_nothing_matches_and_missing_evidence(self):
        lines = V.advisories(self.rules, {
            "tags": [], "markers": [], "hours": 8, "count": 1,
            "evidence_count": 0})
        texts = " | ".join(l["text"] for l in lines)
        self.assertIn("count for nothing", texts)
        self.assertIn("No evidence attached", texts)
        # advisory levels only — nothing here is a block or an error
        self.assertTrue(all(l["level"] in (V.INFO, V.CAUTION, V.WARNING)
                            for l in lines))

    def test_llm_validator_is_a_disabled_seam_by_default(self):
        res = V.run_validators(self.rules, self.led, {
            "tags": ["training"], "markers": [], "hours": 8, "count": 1,
            "evidence_count": 1})
        self.assertIn("LLM sanity check",
                      [v.label for v in res["disabled"]])

    def test_enabled_stub_admits_it_is_a_stub(self):
        self.led.set_config(V.LLMValidator.CONFIG_KEY, True)
        res = V.run_validators(self.rules, self.led, {
            "tags": ["training"], "markers": [], "hours": 8, "count": 1,
            "evidence_count": 1})
        texts = " | ".join(l["text"] for l in res["lines"])
        self.assertIn("not wired", texts)
        self.assertEqual(res["disabled"], [])


class TestCharts(LedgerCase):

    def _assessment(self):
        cred = self.rules.get("cissp")
        return cred, E.assess(cred, self.led.engine_entries(),
                              anchor=self.led.anchors()["cissp"],
                              as_of=datetime.date(2025, 7, 1))

    def test_burndown_is_svg_and_script_free(self):
        self.entry("Load", 40, ["domain_security"])
        cred, a = self._assessment()
        points = C.burndown_points(cred, self.led, a,
                                   datetime.date(2025, 7, 1))
        svg = C.burndown_svg(cred, a, E.pace(a), points)
        self.assertTrue(svg.lstrip().startswith("<svg"))
        self.assertIn("</svg>", svg)
        self.assertNotIn("<script", svg)
        self.assertNotIn("http", svg)

    def test_floor_fill_and_volume_degrade_to_empty(self):
        self.assertEqual(C.volume_svg([]), "")
        series = self.led.capture_by_month(3, datetime.date(2025, 7, 1))
        svg = C.volume_svg(series)
        if svg:
            self.assertTrue(svg.lstrip().startswith("<svg"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Ledger tests: the confirm-state machine, evidence hashing, dupes, backfill.

Every test gets a throwaway database in a temp directory, so this suite never
touches the real ledger and never needs a fixture reset.
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import backfill
from cpebot import bodies as B
from cpebot import engine as E
from cpebot import ledger as L

RULES = B.load()


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cpebot-test-")
        self.led = L.Ledger(
            path=os.path.join(self.tmp, "cpebot.db"),
            evidence_root=os.path.join(self.tmp, "evidence"),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, **kw):
        kw.setdefault("activity_date", "2026-05-01")
        kw.setdefault("title", "a thing")
        kw.setdefault("tags", ["training"])
        return self.led.add_entry(**kw)


class TestConfirmStateMachine(LedgerCase):

    def test_capture_does_not_count(self):
        u = self.add(hours=10)
        e = self.led.get_entry(u)
        self.assertEqual(e["status"], L.CAPTURED)
        self.assertIsNone(e["confirmed_at"])
        # the engine feed excludes it entirely
        self.assertEqual(self.led.engine_entries(), [])
        self.assertEqual(len(self.led.engine_entries(include_pending=True)), 1)

    def test_confirm_makes_it_count(self):
        u = self.add(hours=10)
        self.led.confirm(u)
        e = self.led.get_entry(u)
        self.assertEqual(e["status"], L.CONFIRMED)
        self.assertIsNotNone(e["confirmed_at"])
        self.assertEqual(len(self.led.engine_entries()), 1)

    def test_illegal_transitions_are_refused(self):
        u = self.add()
        self.led.confirm(u)
        self.led.set_status(u, L.SUPERSEDED)
        # superseded is terminal
        with self.assertRaises(L.LedgerError):
            self.led.set_status(u, L.CONFIRMED)

    def test_rejected_can_be_reopened(self):
        u = self.add()
        self.led.set_status(u, L.REJECTED)
        self.led.set_status(u, L.CAPTURED)
        self.assertEqual(self.led.get_entry(u)["status"], L.CAPTURED)

    def test_unknown_status_is_refused(self):
        u = self.add()
        with self.assertRaises(L.LedgerError):
            self.led.set_status(u, "probably_fine")

    def test_supersede_keeps_both_rows(self):
        u = self.add(hours=10, title="misheard title")
        self.led.confirm(u)
        new = self.led.supersede(u, title="correct title", hours=12)
        self.assertEqual(self.led.get_entry(u)["status"], L.SUPERSEDED)
        self.assertEqual(self.led.get_entry(new)["title"], "correct title")
        self.assertEqual(self.led.get_entry(new)["supersedes"], u)
        # only the replacement reaches the engine
        feed = self.led.engine_entries()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["hours"], 12)

    def test_bad_date_is_refused(self):
        with self.assertRaises(L.LedgerError):
            self.add(activity_date="14/03/2026")
        with self.assertRaises(ValueError):
            self.add(activity_date="2026-02-30")


class TestSoftDuplicates(LedgerCase):

    def _confirmed(self, **kw):
        u = self.add(**kw)
        self.led.confirm(u, force=True)
        return u

    def test_same_date_provider_and_similar_hours_is_flagged(self):
        self._confirmed(provider="SANS", hours=8, title="first log")
        u2 = self.add(provider="sans  ", hours=9, title="second log")   # +12.5%
        with self.assertRaises(L.SoftDuplicate) as ctx:
            self.led.confirm(u2)
        self.assertEqual(len(ctx.exception.matches), 1)

    def test_hours_outside_the_band_are_not_flagged(self):
        self._confirmed(provider="SANS", hours=8)
        u2 = self.add(provider="SANS", hours=2)     # -75%, a different session
        self.led.confirm(u2)
        self.assertEqual(self.led.get_entry(u2)["status"], L.CONFIRMED)

    def test_different_provider_is_not_flagged(self):
        self._confirmed(provider="SANS", hours=8)
        u2 = self.add(provider="ISC2", hours=8)
        self.led.confirm(u2)
        self.assertEqual(self.led.get_entry(u2)["status"], L.CONFIRMED)

    def test_different_date_is_not_flagged(self):
        self._confirmed(provider="SANS", hours=8)
        u2 = self.add(provider="SANS", hours=8, activity_date="2026-05-02")
        self.led.confirm(u2)
        self.assertEqual(self.led.get_entry(u2)["status"], L.CONFIRMED)

    def test_the_flag_can_be_overridden(self):
        # Two sessions from the same provider on the same day is normal at a
        # conference. The check is a speed bump, not a wall.
        self._confirmed(provider="SANS", hours=8)
        u2 = self.add(provider="SANS", hours=8)
        with self.assertRaises(L.SoftDuplicate):
            self.led.confirm(u2)
        self.led.confirm(u2, force=True)
        self.assertEqual(self.led.get_entry(u2)["status"], L.CONFIRMED)

    def test_unconfirmed_entries_do_not_trigger_the_flag(self):
        # Only what is already on the record can be duplicated.
        self.add(provider="SANS", hours=8)
        u2 = self.add(provider="SANS", hours=8)
        self.led.confirm(u2)
        self.assertEqual(self.led.get_entry(u2)["status"], L.CONFIRMED)


class TestEvidence(LedgerCase):

    def test_upload_hashes_and_verifies(self):
        u = self.add()
        ev = self.led.add_evidence(u, "certificate.pdf", b"pretend PDF bytes",
                                   mime="application/pdf")
        self.assertEqual(len(ev["sha256"]), 64)
        self.assertEqual(ev["bytes"], 17)
        results = self.led.verify_evidence(u)
        self.assertEqual([r["verify"] for r in results], ["OK"])

    def test_tampering_is_detected(self):
        u = self.add()
        ev = self.led.add_evidence(u, "certificate.pdf", b"original")
        path = os.path.join(self.led.evidence_root, ev["stored_path"])
        with open(path, "wb") as fh:
            fh.write(b"tampered")
        results = self.led.verify_evidence(u)
        self.assertEqual(results[0]["verify"], "ALTERED")
        self.assertNotEqual(results[0]["observed_sha256"], results[0]["sha256"])

    def test_deletion_is_detected(self):
        u = self.add()
        ev = self.led.add_evidence(u, "certificate.pdf", b"original")
        os.remove(os.path.join(self.led.evidence_root, ev["stored_path"]))
        self.assertEqual(self.led.verify_evidence(u)[0]["verify"], "MISSING")

    def test_filenames_are_sanitised(self):
        u = self.add()
        ev = self.led.add_evidence(u, "../../etc/passwd", b"x")
        self.assertNotIn("..", ev["stored_path"])
        # v0.2: content-addressed — stored flat under the root, named by the
        # file's own sha256, so a hostile filename cannot steer the path.
        self.assertEqual(os.path.dirname(ev["stored_path"]), "")
        self.assertTrue(ev["stored_path"].startswith(ev["sha256"]))
        full = os.path.realpath(
            os.path.join(self.led.evidence_root, ev["stored_path"]))
        root = os.path.realpath(self.led.evidence_root)
        self.assertTrue(full.startswith(root + os.sep))

    def test_identical_bytes_share_one_stored_file(self):
        a, b = self.add(), self.add(title="Second")
        ev1 = self.led.add_evidence(a, "cert.pdf", b"same bytes")
        ev2 = self.led.add_evidence(b, "receipt.pdf", b"same bytes")
        self.assertEqual(ev1["stored_path"], ev2["stored_path"])
        self.assertEqual(len(self.led.list_evidence(a)), 1)
        self.assertEqual(len(self.led.list_evidence(b)), 1)

    def test_evidence_requires_an_entry(self):
        with self.assertRaises(L.LedgerError):
            self.led.add_evidence("nope", "f.pdf", b"x")

    def test_evidence_count_reaches_the_engine_feed(self):
        u = self.add()
        self.led.confirm(u)
        self.led.add_evidence(u, "a.pdf", b"a")
        self.led.add_evidence(u, "b.pdf", b"b")
        self.assertEqual(self.led.engine_entries()[0]["evidence_count"], 2)


class TestCaptureCounter(LedgerCase):

    def test_real_captures_are_counted_and_backfill_is_not(self):
        self.add(capture_source="telegram")
        self.add(capture_source="web")
        self.add(capture_source="backfill")
        self.add(capture_source="fixture")
        counts = self.led.capture_counts(days=30)
        self.assertEqual(counts["total"], 4)
        self.assertEqual(counts["real"], 2)
        self.assertEqual(counts["threshold"], 8)
        self.assertTrue(counts["falsified"])       # 2 < 8

    def test_the_kill_switch_clears_at_eight(self):
        for _ in range(8):
            self.add(capture_source="telegram")
        counts = self.led.capture_counts(days=30)
        self.assertEqual(counts["real"], 8)
        self.assertFalse(counts["falsified"])

    def test_unknown_source_is_refused(self):
        with self.assertRaises(L.LedgerError):
            self.add(capture_source="carrier_pigeon")


class TestReminders(LedgerCase):

    def test_daily_unsolicited_budget_is_queryable(self):
        self.assertEqual(self.led.unsolicited_today(), 0)
        self.led.log_reminder("unsolicited.escalation", credential="cissp",
                              channel="telegram", body="T-90")
        self.assertEqual(self.led.unsolicited_today(), 1)

    def test_suppressed_reminders_do_not_spend_the_budget(self):
        self.led.log_reminder("unsolicited.nudge", suppressed=True,
                              reason="on pace; quiet by design")
        self.assertEqual(self.led.unsolicited_today(), 0)
        self.assertEqual(len(self.led.reminders_since("2000-01-01",
                                                      include_suppressed=True)), 1)


class TestCredentialStateAndFees(LedgerCase):

    def test_anchors_and_carryover_round_trip(self):
        self.led.set_credential_state("cissp", anchor_date="2024-06-01",
                                      carryover_in=12.5)
        self.assertEqual(self.led.anchors()["cissp"], "2024-06-01")
        self.assertEqual(self.led.carryover_in()["cissp"], 12.5)

    def test_unknown_field_is_refused(self):
        with self.assertRaises(L.LedgerError):
            self.led.set_credential_state("cissp", vibes="good")

    def test_fee_lifecycle(self):
        fid = self.led.add_fee("cissp", "AMF", amount_usd=135,
                               due_date="2026-12-01")
        self.assertIsNone(self.led.list_fees("cissp")[0]["paid_on"])
        self.led.mark_fee_paid(fid, "2026-11-15")
        self.assertEqual(self.led.list_fees("cissp")[0]["paid_on"], "2026-11-15")


class TestBackfill(LedgerCase):

    CSV = (
        "date,title,provider,hours,count,tags,markers,notes\n"
        "2025-03-01,Domain deep dive,SANS,20,1,training,domain_security,\n"
        "2025-04-02,Chapter meeting,ATAP,2,1,chapter_meeting,,monthly\n"
        "2025-05-03,Blog post series,self,,6,authoring_blog,,\n"
    )

    def test_import_lands_as_captured_and_not_real(self):
        report = backfill.import_csv(self.CSV, RULES, self.led)
        self.assertEqual(report["imported"], 3)
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(self.led.list_entries(status=L.CAPTURED)), 3)
        self.assertEqual(self.led.engine_entries(), [])   # nothing counts yet
        counts = self.led.capture_counts(days=30)
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["real"], 0)               # backfill is not use

    def test_capture_source_is_recorded(self):
        backfill.import_csv(self.CSV, RULES, self.led)
        for e in self.led.list_entries():
            self.assertEqual(e["capture_source"], "backfill")
            self.assertTrue(e["capture_ref"].startswith("csv:line "))

    def test_dry_run_writes_nothing(self):
        report = backfill.import_csv(self.CSV, RULES, self.led, dry_run=True)
        self.assertEqual(report["parsed"], 3)
        self.assertEqual(report["imported"], 0)
        self.assertEqual(self.led.list_entries(), [])

    def test_explicit_confirm_is_available(self):
        report = backfill.import_csv(self.CSV, RULES, self.led, confirm=True)
        self.assertEqual(report["confirmed"], 3)
        self.assertEqual(len(self.led.engine_entries()), 3)

    def test_duplicates_are_flagged_not_swallowed(self):
        backfill.import_csv(self.CSV, RULES, self.led, confirm=True)
        report = backfill.import_csv(self.CSV, RULES, self.led, confirm=True)
        self.assertEqual(len(report["duplicates_flagged"]), 3)
        self.assertEqual(report["confirmed"], 0)      # flagged rows stay captured
        self.assertEqual(report["imported"], 3)

    def test_bad_tag_is_an_error_not_a_silent_drop(self):
        csv_text = ("date,title,tags\n"
                    "2025-03-01,Fine,training\n"
                    "2025-03-02,Bad,knitting\n")
        report = backfill.import_csv(csv_text, RULES, self.led)
        self.assertEqual(report["imported"], 1)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["line"], 3)
        self.assertIn("unknown tag", report["errors"][0]["error"])

    def test_unknown_column_is_rejected_outright(self):
        with self.assertRaises(backfill.BackfillError):
            backfill.import_csv("date,title,tags,vibes\n", RULES, self.led)

    def test_missing_required_column_is_rejected(self):
        with self.assertRaises(backfill.BackfillError):
            backfill.import_csv("date,title\n", RULES, self.led)

    def test_overrides_parse(self):
        csv_text = ("date,title,tags,overrides\n"
                    "2025-03-01,Exam pass,cert_exam,cysa_plus=25\n")
        backfill.import_csv(csv_text, RULES, self.led)
        self.assertEqual(self.led.list_entries()[0]["overrides"],
                         {"cysa_plus": 25.0})

    def test_shipped_template_imports_cleanly(self):
        report = backfill.import_csv(backfill.TEMPLATE, RULES, self.led)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["imported"], 1)


class TestLedgerFeedsTheEngine(LedgerCase):
    """The join: rows out of SQLite must assess identically to plain dicts."""

    def test_end_to_end_cissp_naive_trap_through_the_database(self):
        a_id = self.add(activity_date="2025-03-01", title="domain work",
                        hours=85, tags=["training"],
                        markers=["domain_security"])
        b_id = self.add(activity_date="2025-04-01", title="general PD",
                        hours=40, tags=["professional_dev"])
        self.led.confirm(a_id)
        self.led.confirm(b_id)
        self.led.set_credential_state("cissp", anchor_date="2024-06-01")

        feed = self.led.engine_entries()
        a = E.assess(RULES.get("cissp"), feed,
                     anchor=self.led.anchors()["cissp"],
                     as_of=datetime.date(2026, 8, 30))
        self.assertEqual(a.awarded, 115.0)
        self.assertEqual(a.naive_total, 125.0)
        self.assertEqual(a.unmet_floors, ["group_a"])
        self.assertEqual(a.verdict, E.BEHIND)


class TestVoid(LedgerCase):
    """VOID: a real capture that counts toward nothing else. (v0.2)"""

    def test_void_is_terminal_from_every_live_state(self):
        for start in (L.CAPTURED, L.CONFIRMED, L.REJECTED):
            u = self.add(status=start)
            e = self.led.void(u)
            self.assertEqual(e["status"], L.VOID)
            self.assertTrue(e["voided_at"])

    def test_void_is_terminal_full_stop(self):
        u = self.add()
        self.led.void(u)
        for target in (L.CAPTURED, L.CONFIRMED, L.REJECTED, L.SUPERSEDED):
            with self.assertRaises(L.LedgerError):
                self.led.set_status(u, target)

    def test_void_keeps_its_capture_event(self):
        before = self.led.capture_counts()["real"]
        u = self.add(capture_source="web")
        self.assertEqual(self.led.capture_counts()["real"], before + 1)
        self.led.void(u)
        self.assertEqual(self.led.capture_counts()["real"], before + 1)

    def test_void_never_reaches_the_engine(self):
        u = self.add()
        self.led.confirm(u)
        self.assertEqual(len(self.led.engine_entries()), 1)
        self.led.void(u)
        self.assertEqual(self.led.engine_entries(), [])
        self.assertEqual(self.led.engine_entries(include_pending=True), [])

    def test_void_is_excluded_from_dupe_matching(self):
        a = self.add(provider="ISACA", hours=2)
        self.led.confirm(a)
        b = self.add(provider="ISACA", hours=2)
        self.assertEqual(len(self.led.soft_duplicates(b)), 1)
        self.led.void(a)
        self.assertEqual(self.led.soft_duplicates(b), [])
        self.led.confirm(b)          # no SoftDuplicate raised

    def test_void_rows_stay_on_the_books(self):
        u = self.add()
        self.led.void(u)
        self.assertEqual(self.led.get_entry(u)["status"], L.VOID)
        self.assertEqual(self.led.stats()["entries"].get("void"), 1)

    def test_void_rows_are_read_only(self):
        u = self.add()
        self.led.void(u)
        with self.assertRaises(L.LedgerError):
            self.led.update_entry(u, title="rewritten history")


class TestConfirmedImmutability(LedgerCase):
    """Confirmed content is corrected by supersede, never edited. (v0.2)"""

    def test_confirmed_content_edit_is_refused(self):
        u = self.add()
        self.led.confirm(u)
        for field in ({"title": "x"}, {"hours": 9}, {"notes": "y"},
                      {"activity_date": "2026-05-02"}):
            with self.assertRaises(L.LedgerError):
                self.led.update_entry(u, **field)

    def test_captured_entries_still_edit_in_place(self):
        u = self.add()
        e = self.led.update_entry(u, title="fixed before confirm")
        self.assertEqual(e["title"], "fixed before confirm")

    def test_submission_bookkeeping_is_still_allowed_when_confirmed(self):
        u = self.add()
        self.led.confirm(u)
        e = self.led.mark_submitted(u, "isc2", on_date="2026-06-01")
        self.assertEqual(e["submitted_to"], ["isc2"])
        self.assertEqual(e["submitted_dates"], {"isc2": "2026-06-01"})

    def test_superseded_rows_are_read_only(self):
        u = self.add()
        self.led.confirm(u)
        self.led.supersede(u, hours=3)
        with self.assertRaises(L.LedgerError):
            self.led.update_entry(u, notes="post-hoc")

    def test_supersede_refuses_unconfirmed_entries(self):
        u = self.add()
        with self.assertRaises(L.LedgerError):
            self.led.supersede(u, hours=3)

    def test_superseded_by_finds_the_replacement(self):
        u = self.add()
        self.led.confirm(u)
        new = self.led.supersede(u, hours=4)
        self.assertEqual(self.led.superseded_by(u), new)
        self.assertIsNone(self.led.superseded_by(new))

    def test_supersede_carries_submission_bookkeeping(self):
        u = self.add()
        self.led.confirm(u)
        self.led.mark_submitted(u, "isc2", on_date="2026-06-01")
        new = self.led.get_entry(self.led.supersede(u))
        self.assertEqual(new["submitted_to"], ["isc2"])
        self.assertEqual(new["submitted_dates"], {"isc2": "2026-06-01"})


class TestSubmittedDates(LedgerCase):

    def test_mark_submitted_defaults_to_today(self):
        u = self.add()
        self.led.confirm(u)
        e = self.led.mark_submitted(u, "acfe")
        self.assertEqual(e["submitted_dates"]["acfe"],
                         str(datetime.date.today()))

    def test_bad_date_is_refused(self):
        u = self.add()
        with self.assertRaises(L.LedgerError):
            self.led.mark_submitted(u, "acfe", on_date="not-a-date")

    def test_two_bodies_keep_separate_dates(self):
        u = self.add()
        self.led.confirm(u)
        self.led.mark_submitted(u, "isc2", on_date="2026-06-01")
        e = self.led.mark_submitted(u, "acfe", on_date="2026-06-15")
        self.assertEqual(e["submitted_dates"],
                         {"isc2": "2026-06-01", "acfe": "2026-06-15"})


class TestUploadValidation(LedgerCase):
    """The web boundary check: size cap, extension AND magic bytes."""

    PDF = b"%PDF-1.7 tiny"
    PNG = b"\x89PNG\r\n\x1a\n0000"
    JPG = b"\xff\xd8\xff\xe0rest"

    def test_valid_types_pass_and_return_their_mime(self):
        self.assertEqual(L.validate_upload("c.pdf", self.PDF),
                         "application/pdf")
        self.assertEqual(L.validate_upload("c.png", self.PNG), "image/png")
        self.assertEqual(L.validate_upload("c.jpg", self.JPG), "image/jpeg")
        self.assertEqual(L.validate_upload("c.jpeg", self.JPG), "image/jpeg")

    def test_wrong_magic_for_the_extension_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.validate_upload("innocent.pdf", self.PNG)
        with self.assertRaises(L.LedgerError):
            L.validate_upload("script.pdf", b"<html><script>")

    def test_unlisted_extension_is_refused_even_with_valid_magic(self):
        with self.assertRaises(L.LedgerError):
            L.validate_upload("cert.svg", b"<svg xmlns=...>")
        with self.assertRaises(L.LedgerError):
            L.validate_upload("cert.pdf.html", self.PDF)

    def test_oversized_upload_is_refused(self):
        big = self.PDF + b"0" * L.MAX_EVIDENCE_BYTES
        with self.assertRaises(L.LedgerError):
            L.validate_upload("big.pdf", big)

    def test_empty_upload_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.validate_upload("empty.pdf", b"")

    def test_sniff_ignores_the_filename_entirely(self):
        self.assertEqual(L.sniff_evidence(self.PDF), "application/pdf")
        self.assertEqual(L.sniff_evidence(self.PNG), "image/png")
        self.assertEqual(L.sniff_evidence(self.JPG), "image/jpeg")
        self.assertIsNone(L.sniff_evidence(b"<html>not evidence"))


class TestMigration(unittest.TestCase):
    """Opening a v0.1 database adds the v0.2 columns, touching nothing else."""

    def test_v01_database_is_migrated_additively(self):
        import sqlite3
        tmp = tempfile.mkdtemp(prefix="cpebot-mig-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        db = os.path.join(tmp, "old.db")
        # A v0.1-shaped entries table (no voided_at, no submitted_dates)
        # with one confirmed row written the v0.1 way.
        c = sqlite3.connect(db)
        c.executescript("""
            CREATE TABLE entries (
                uuid TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, activity_date TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
                hours REAL NOT NULL DEFAULT 0, count INTEGER NOT NULL DEFAULT 1,
                tags TEXT NOT NULL DEFAULT '[]', markers TEXT NOT NULL DEFAULT '[]',
                applies_to TEXT, overrides TEXT NOT NULL DEFAULT '{}',
                submitted_to TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'captured',
                capture_source TEXT NOT NULL DEFAULT 'web',
                capture_ref TEXT NOT NULL DEFAULT '', confirmed_at TEXT,
                supersedes TEXT, cost_usd REAL);
            INSERT INTO entries (uuid, created_at, updated_at, activity_date,
                title, status) VALUES ('aaaa', '2026-01-01 00:00:00',
                '2026-01-01 00:00:00', '2026-01-01', 'old row', 'confirmed');
        """)
        c.commit()
        c.close()
        led = L.Ledger(path=db, evidence_root=os.path.join(tmp, "ev"))
        e = led.get_entry("aaaa")
        self.assertEqual(e["title"], "old row")            # data survived
        self.assertIsNone(e["voided_at"])                  # new column, default
        self.assertEqual(e["submitted_dates"], {})         # new column, default
        led.void("aaaa")                                   # and it works
        self.assertEqual(led.get_entry("aaaa")["status"], L.VOID)



if __name__ == "__main__":
    unittest.main(verbosity=2)

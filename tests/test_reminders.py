"""Reminder policy.

Most of these tests assert SILENCE. That is the correct emphasis: the failure
mode for this feature is not a missed message, it is a stream of unnecessary
ones that trains the holder to ignore the channel before the one message that
mattered ever arrives.
"""

import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B          # noqa: E402
from cpebot import ledger as L          # noqa: E402
from cpebot import reminders as RM      # noqa: E402

TODAY = datetime.date(2026, 8, 30)


class ReminderCase(unittest.TestCase):

    def setUp(self):
        self.rules = B.load()
        self.dir = tempfile.mkdtemp(prefix="cpebot-rem-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def entry(self, date, title, hours=0.0, tags=("training",), markers=(),
              evidence=True, **kw):
        u = self.led.add_entry(activity_date=date, title=title, hours=hours,
                               tags=list(tags), markers=list(markers), **kw)
        self.led.set_status(u, L.CONFIRMED)
        if evidence:
            # v0.2 nags about evidence-less confirmed entries; the fixture
            # attaches a file by default so every OTHER test keeps asserting
            # what it always asserted. The evidence-gap tests below opt out.
            self.led.add_evidence(u, "cert.pdf", b"%PDF- " + u.encode())
        return u

    def kinds(self, plan):
        return [c["kind"] for c in plan["send"]]


class TestSilenceIsTheDefault(ReminderCase):

    def test_no_anchors_means_nothing_to_say(self):
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(p["send"], [])
        self.assertIn("quiet by design", p["suppress"][0]["reason"])

    def test_on_pace_credential_is_silent(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        self.entry("2026-01-10", "Steady progress", hours=60,
                   markers=("domain_security",))
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(p["send"], [])

    def test_milestone_while_on_pace_passes_in_silence(self):
        # T-365 lands exactly on 2026-08-30 for a 2024-08-30 anchor, and the
        # portfolio is comfortably ahead. The milestone must not speak.
        self.led.set_credential_state("cissp", anchor_date="2024-08-30")
        self.entry("2025-01-10", "Domain work", hours=95,
                   markers=("domain_security",))
        self.entry("2025-02-10", "Other work", hours=25)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), [])

    def test_inactive_credential_is_skipped_entirely(self):
        self.led.set_credential_state("ceh", anchor_date="2024-11-15",
                                      active=0)
        self.entry("2025-03-01", "Unregistered webinar", hours=10)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(p["send"], [])


class TestItSpeaksWhenItShould(ReminderCase):

    def test_overdue_registration_window_always_speaks(self):
        self.led.set_credential_state("ceh", anchor_date="2024-11-15")
        self.entry("2025-03-01", "Never registered", hours=40)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertIn("submission_overdue", self.kinds(p))
        body = [c for c in p["send"] if c["kind"] == "submission_overdue"][0]["body"]
        self.assertIn("2026-02-01", body)
        self.assertIn("lost", body)

    def test_floor_shortfall_names_the_category(self):
        # 40 general hours and nothing in Group A: the total looks healthy
        # right up until the floor fails it.
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-06-10", "Management course", hours=40)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertIn("floor_shortfall", self.kinds(p))
        body = [c for c in p["send"] if c["kind"] == "floor_shortfall"][0]["body"]
        self.assertIn("Group A", body)
        self.assertIn("General hours will not fix this", body)

    def test_milestone_speaks_when_behind(self):
        self.led.set_credential_state("cysa_plus", anchor_date="2024-08-30")
        self.entry("2025-01-10", "One course", hours=5)
        p = RM.plan(self.rules, self.led, TODAY)     # T-365 on a 36mo cycle
        self.assertIn("milestone", self.kinds(p))
        self.assertIn("T-365", p["send"][0]["body"])

    def test_milestone_is_silent_on_a_non_milestone_day(self):
        self.led.set_credential_state("cysa_plus", anchor_date="2024-08-29")
        self.entry("2025-01-10", "One course", hours=5)
        p = RM.plan(self.rules, self.led, TODAY)     # T-364; not a milestone
        self.assertNotIn("milestone", self.kinds(p))

    def test_unpaid_fee_inside_thirty_days_speaks(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-15")
        self.entry("2026-01-10", "Domain work", hours=60,
                   markers=("domain_security",))
        self.led.add_fee("cissp", "AMF 2026", 135, "2026-09-15")
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertIn("fee_unpaid", self.kinds(p))

    def test_a_paid_fee_is_silent(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-15")
        self.entry("2026-01-10", "Domain work", hours=60,
                   markers=("domain_security",))
        fee = self.led.add_fee("cissp", "AMF 2026", 135, "2026-09-15")
        self.led.mark_fee_paid(fee, "2026-08-01")
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(p["send"], [])


class TestDailyBudget(ReminderCase):

    def setUp(self):
        ReminderCase.setUp(self)
        # Two credentials, both with an unmet floor. Only one may speak.
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-06-10", "Management course", hours=40)
        self.led.set_credential_state("cfe", anchor_date="2026-03-01")
        self.entry("2026-04-02", "General CPE", hours=25)

    def test_only_one_unsolicited_message_a_day(self):
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(len(p["send"]), 1)
        self.assertTrue(p["suppress"])
        self.assertIn("budget", p["suppress"][0]["reason"])

    def test_the_suppressed_message_is_still_recorded(self):
        p = RM.plan(self.rules, self.led, TODAY)
        RM.record(self.led, p, channel="telegram")
        rows = self.led.reminders_since("2026-08-30", include_suppressed=True)
        self.assertEqual(len(rows), len(p["send"]) + len(p["suppress"]))
        self.assertEqual(len([r for r in rows if r["suppressed"]]),
                         len(p["suppress"]))

    def test_a_spent_budget_silences_the_next_plan(self):
        first = RM.plan(self.rules, self.led, TODAY)
        RM.record(self.led, first)
        second = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(second["send"], [])
        self.assertEqual(second["budget_spent"], 1)

    def test_an_overdue_window_outranks_the_budget(self):
        self.led.set_credential_state("ceh", anchor_date="2024-11-15")
        self.entry("2025-03-01", "Never registered", hours=40)
        first = RM.plan(self.rules, self.led, TODAY)
        RM.record(self.led, first)
        second = RM.plan(self.rules, self.led, TODAY)
        # Everything discretionary is now silent, but the lost-credits
        # warning still goes out: it is the one failure with no later fix.
        self.assertEqual(self.kinds(second), ["submission_overdue"])

    def test_the_more_urgent_message_wins_the_slot(self):
        # 70 general hours and nothing counter-intelligence: the total looks
        # strong, the 60-PDU CINT floor has had nothing put against it, and a
        # renewal fee is also due. The floor outranks the fee.
        self.led.set_credential_state("gcitp", anchor_date="2025-09-15")
        self.entry("2026-01-05", "General coursework", hours=70)
        self.led.add_fee("gcitp", "Renewal", 100, "2026-09-15")
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), ["floor_shortfall"])
        self.assertIn("fee_unpaid", [c["kind"] for c in p["suppress"]])


class TestRecordDoesNotSend(ReminderCase):
    """v0.1 ships the decision, not the outbound path."""

    def test_record_returns_the_count_it_logged(self):
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-06-10", "Management course", hours=40)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(RM.record(self.led, p), len(p["send"]))

    def test_plan_is_pure(self):
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-06-10", "Management course", hours=40)
        RM.plan(self.rules, self.led, TODAY)
        RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.led.reminders_since("2026-01-01",
                                                  include_suppressed=True), [])


class TestEvidenceGap(ReminderCase):
    """v0.2: confirmed entries with no evidence nag — after a grace week."""

    def test_bare_confirmed_entry_past_grace_is_nagged(self):
        self.entry("2026-08-01", "Conference day", hours=8, evidence=False)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), ["evidence_gap"])
        self.assertIn("Conference day", p["send"][0]["body"])

    def test_the_grace_week_holds_its_tongue(self):
        self.entry(str(TODAY - datetime.timedelta(days=3)), "Fresh entry",
                   hours=2, evidence=False)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), [])

    def test_entries_with_evidence_are_not_nagged(self):
        self.entry("2026-08-01", "Documented work", hours=8)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), [])

    def test_gaps_aggregate_into_one_line(self):
        for i in range(5):
            self.entry("2026-07-0%d" % (i + 1), "Bare %d" % i, hours=1,
                       evidence=False, provider="p%d" % i)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), ["evidence_gap"])
        self.assertIn("5 confirmed entries", p["send"][0]["body"])
        self.assertIn("+2 more", p["send"][0]["body"])

    def test_evidence_gap_loses_the_slot_to_a_floor_shortfall(self):
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-06-10", "Management course", hours=40,
                   evidence=False)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), ["floor_shortfall"])
        self.assertIn("evidence_gap",
                      [c["kind"] for c in p["suppress"]])

    def test_voided_entries_are_not_nagged(self):
        u = self.entry("2026-08-01", "Test message", hours=1, evidence=False)
        self.led.void(u)
        p = RM.plan(self.rules, self.led, TODAY)
        self.assertEqual(self.kinds(p), [])


class TestRecordStampsThePlanDay(ReminderCase):
    """record() must spend the budget for the day plan() read it from."""

    def test_budget_holds_when_wall_clock_differs_from_as_of(self):
        self.led.set_credential_state("cissp", anchor_date="2024-09-01")
        self.entry("2025-06-10", "Management course", hours=40)
        day = datetime.date(2026, 6, 1)          # never the wall-clock date
        first = RM.plan(self.rules, self.led, day)
        self.assertEqual(len(first["send"]), 1)
        RM.record(self.led, first)
        second = RM.plan(self.rules, self.led, day)
        self.assertEqual(second["send"], [])
        self.assertEqual(second["budget_spent"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Golden-portfolio tests: one hand-computed portfolio per body.

Two rules govern this file.

1. Every expected number is computed BY HAND in the comment above the
   assertion, from the rule in bodies.yaml. If a test needs the engine to
   work out what the answer should be, it is not a test.

2. Every capped body gets at least one NEGATIVE case — a portfolio where a
   cap or a floor actually bites — and the suite as a whole contains cases
   where the naive sum says COMPLIANT and the cap-aware engine says it is
   not. Those are marked `naive_trap`. They are the reason the project
   exists; if they ever start passing under naive summing, the engine has
   stopped doing its job.
"""

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B
from cpebot import engine as E

RULES = B.load()
TODAY = datetime.date(2026, 8, 30)   # frozen; these tests must not drift


def entry(date, tags, hours=0.0, count=1, markers=(), **kw):
    e = {
        "id": kw.pop("id", None),
        "date": date,
        "title": kw.pop("title", "fixture"),
        "provider": kw.pop("provider", "fixture"),
        "hours": hours,
        "count": count,
        "tags": list(tags),
        "markers": list(markers),
        "status": kw.pop("status", "confirmed"),
    }
    e.update(kw)
    return e


class GoldenBase(unittest.TestCase):
    cred_id = None
    anchor = None

    def cred(self):
        return RULES.get(self.cred_id)

    def run_case(self, entries, **kw):
        kw.setdefault("as_of", TODAY)
        kw.setdefault("anchor", self.anchor)
        return E.assess(self.cred(), entries, **kw)

    def naive(self, entries):
        return E.naive_assess(
            self.cred(), entries, anchor=self.anchor, as_of=TODAY
        )

    def bucket(self, a, bucket_id):
        for b in a.buckets:
            if b["id"] == bucket_id:
                return b
        self.fail("bucket %r absent from assessment" % bucket_id)

    def group(self, a, group_id):
        for g in a.groups:
            if g["id"] == group_id:
                return g
        self.fail("group %r absent from assessment" % group_id)


# ---------------------------------------------------------------------------
# ISC2 / CISSP — 120 CPE per 36 months, Group A floor 90, Group B cap 30
# ---------------------------------------------------------------------------

class TestCISSP(GoldenBase):
    cred_id = "cissp"
    anchor = datetime.date(2024, 6, 1)   # cycle 2024-06-01 .. 2027-06-01

    def test_cycle_window(self):
        a = self.run_case([])
        self.assertEqual(a.cycle_start, datetime.date(2024, 6, 1))
        self.assertEqual(a.cycle_end, datetime.date(2027, 6, 1))
        # 90-day grace after the cycle closes
        self.assertEqual(a.grace_end, datetime.date(2027, 8, 30))

    def test_compliant_portfolio(self):
        # 95h domain-related -> Group A 95 (floor 90 met)
        # 30h general        -> Group B 30 (cap 30, exactly at it)
        # total 125 >= 120
        entries = [
            entry(datetime.date(2025, 3, 1), ["training"], hours=95,
                  markers=["domain_security"]),
            entry(datetime.date(2025, 4, 1), ["professional_dev"], hours=30),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 125.0)
        self.assertEqual(a.forfeited, 0.0)
        self.assertEqual(self.group(a, "group_a")["awarded"], 95.0)
        self.assertEqual(self.group(a, "group_b")["awarded"], 30.0)
        self.assertEqual(a.unmet_floors, [])
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_naive_trap_group_b_cap_and_group_a_floor(self):
        # THE naive trap for ISC2.
        #   85h domain-related  -> Group A 85   (floor is 90: SHORT BY 5)
        #   40h general         -> Group B 40 raw, capped to 30 (10 lost)
        # naive sum        = 85 + 40 = 125  -> "you have 125 of 120, relax"
        # cap-aware total  = 85 + 30 = 115  -> five short, and Group A short too
        entries = [
            entry(datetime.date(2025, 3, 1), ["training"], hours=85,
                  markers=["domain_security"]),
            entry(datetime.date(2025, 4, 1), ["professional_dev"], hours=40),
        ]
        a = self.run_case(entries)
        naive = self.naive(entries)

        self.assertEqual(naive, 125.0)
        self.assertGreaterEqual(naive, a.required)      # naive says: compliant
        self.assertEqual(a.awarded, 115.0)
        self.assertLess(a.awarded, a.required)          # engine says: not
        self.assertEqual(a.gap, 5.0)
        self.assertEqual(a.forfeited, 10.0)
        self.assertEqual(self.group(a, "group_a")["shortfall"], 5.0)
        self.assertEqual(a.unmet_floors, ["group_a"])
        self.assertEqual(a.verdict, E.BEHIND)

    def test_negative_group_b_cap_bites(self):
        # 40h of purely general activity is worth 30. Not 40. And the
        # assessment must still report the 40, because "you did 40 hours and
        # 10 of them were wasted" is the actionable sentence.
        entries = [entry(datetime.date(2025, 4, 1), ["professional_dev"], hours=40)]
        a = self.run_case(entries)
        self.assertEqual(self.group(a, "group_b")["raw"], 40.0)
        self.assertEqual(self.group(a, "group_b")["awarded"], 30.0)
        self.assertTrue(self.group(a, "group_b")["at_cap"])
        self.assertEqual(a.awarded, 30.0)
        self.assertEqual(a.naive_total, 40.0)
        self.assertEqual(a.forfeited, 10.0)

    def test_domain_marker_routes_to_group_a_not_both(self):
        # A domain-related talk is eligible for both bucket definitions.
        # It must count ONCE, in Group A.
        e = entry(datetime.date(2025, 5, 1), ["teaching"], hours=10,
                  markers=["domain_security"])
        a = self.run_case([e])
        self.assertEqual(a.awarded, 10.0)
        self.assertEqual(self.group(a, "group_a")["awarded"], 10.0)
        self.assertEqual(self.group(a, "group_b")["awarded"], 0.0)

    def test_entries_outside_the_cycle_do_not_count(self):
        entries = [
            entry(datetime.date(2024, 5, 31), ["training"], hours=50,
                  markers=["domain_security"]),   # one day before the cycle
            entry(datetime.date(2027, 6, 1), ["training"], hours=50,
                  markers=["domain_security"]),   # first day of the next one
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 0.0)
        self.assertEqual(a.entries_counted, 0)

    def test_unconfirmed_entries_do_not_count(self):
        entries = [entry(datetime.date(2025, 3, 1), ["training"], hours=40,
                         markers=["domain_security"], status="captured")]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 0.0)
        self.assertEqual(a.entries_pending, 1)
        # ... unless explicitly asked for, which the dashboard uses for the
        # "if you confirm everything in your inbox" projection.
        a2 = self.run_case(entries, count_pending=True)
        self.assertEqual(a2.awarded, 40.0)


# ---------------------------------------------------------------------------
# CompTIA / CySA+ — 60 CEU per 36 months, hard per-category caps
# ---------------------------------------------------------------------------

class TestCySAPlus(GoldenBase):
    cred_id = "cysa_plus"
    anchor = datetime.date(2024, 9, 15)   # .. 2027-09-15

    def test_naive_trap_three_caps_at_once(self):
        #   30h training            -> 30            (uncapped)
        #   20 blog posts @1 ea     -> 20 raw, cap 18 (2 lost)
        #   4 years work experience -> 12 raw, cap  9 (3 lost)
        # naive       = 30 + 20 + 12 = 62 -> "62 of 60, done"
        # cap-aware   = 30 + 18 +  9 = 57 -> three short
        entries = [
            entry(datetime.date(2025, 2, 1), ["training"], hours=30),
            entry(datetime.date(2025, 6, 1), ["authoring_blog"], count=20),
            entry(datetime.date(2025, 7, 1), ["work_experience"], count=4),
        ]
        a = self.run_case(entries)
        naive = self.naive(entries)

        self.assertEqual(naive, 62.0)
        self.assertGreaterEqual(naive, a.required)
        self.assertEqual(a.awarded, 57.0)
        self.assertLess(a.awarded, a.required)
        self.assertEqual(a.gap, 3.0)
        self.assertEqual(a.forfeited, 5.0)
        # Two-thirds through the cycle with 57 of 60, the pace line is at 43,
        # so this portfolio is ON_PACE -- it is just not FINISHED, which is
        # exactly the distinction the naive sum destroyed by claiming 62.
        self.assertEqual(a.verdict, E.ON_PACE)
        self.assertNotEqual(a.verdict, E.COMPLIANT)

    def test_negative_blog_cap(self):
        entries = [entry(datetime.date(2025, 6, 1), ["authoring_blog"], count=20)]
        a = self.run_case(entries)
        b = self.bucket(a, "publishing_blog")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (20.0, 18.0, 2.0))
        self.assertTrue(b["at_cap"])

    def test_negative_article_cap(self):
        # 5 articles at 4 CEU each = 20 raw, capped to 18.
        entries = [entry(datetime.date(2025, 6, 1), ["authoring_article"], count=5)]
        a = self.run_case(entries)
        b = self.bucket(a, "publishing_article")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (20.0, 18.0, 2.0))

    def test_negative_book_cap(self):
        # Two books at 50 each is not 100. It is 50.
        entries = [entry(datetime.date(2025, 6, 1), ["authoring_book"], count=2)]
        a = self.run_case(entries)
        b = self.bucket(a, "publishing_book")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (100.0, 50.0, 50.0))
        self.assertEqual(a.awarded, 50.0)

    def test_negative_work_experience_cap(self):
        entries = [entry(datetime.date(2025, 7, 1), ["work_experience"], count=5)]
        a = self.run_case(entries)
        b = self.bucket(a, "work_experience")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (15.0, 9.0, 6.0))

    def test_cert_exam_needs_an_override_and_says_so(self):
        # CompTIA's cert-exam value depends on which exam. The rules file
        # refuses to guess, so an entry with no override is worth zero and
        # the assessment carries a note explaining why.
        entries = [entry(datetime.date(2025, 8, 1), ["cert_exam"], count=1)]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 0.0)
        self.assertTrue(any("no computable rate" in n for n in a.notes))

        entries2 = [entry(datetime.date(2025, 8, 1), ["cert_exam"], count=1,
                          overrides={"cysa_plus": 25})]
        a2 = self.run_case(entries2)
        self.assertEqual(a2.awarded, 25.0)

    def test_compliant_portfolio(self):
        # 45h training + 12 blog (under the cap of 18) + 1 higher-ed course @10 = 67 >= 60
        entries = [
            entry(datetime.date(2025, 2, 1), ["training"], hours=45),
            entry(datetime.date(2025, 6, 1), ["authoring_blog"], count=12),
            entry(datetime.date(2026, 1, 5), ["higher_education"], count=1),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 67.0)
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_negative_conference_cap(self):
        # Conference attendance is its own category now, capped at 15.
        # 20h conference -> 20 raw, 15 awarded, 5 lost.
        entries = [entry(datetime.date(2025, 3, 1), ["conference"], hours=20)]
        a = self.run_case(entries)
        b = self.bucket(a, "conference")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (20.0, 15.0, 5.0))
        self.assertNotIn("training", [x["id"] for x in a.buckets])

    def test_negative_teaching_cap(self):
        # 35h teaching at 1/h -> 35 raw, cap 30 (5 lost).
        entries = [entry(datetime.date(2025, 3, 1), ["teaching"], hours=35)]
        a = self.run_case(entries)
        b = self.bucket(a, "teaching")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (35.0, 30.0, 5.0))

    def test_materials_two_per_hour_and_capped(self):
        # 10h materials at 2/h -> 20 (under the cap of 30).
        a = self.run_case([entry(datetime.date(2025, 3, 1), ["materials"], hours=10)])
        self.assertEqual(self.bucket(a, "materials")["awarded"], 20.0)
        # 20h materials at 2/h -> 40 raw, cap 30 (10 lost).
        a = self.run_case([entry(datetime.date(2025, 3, 1), ["materials"], hours=20)])
        b = self.bucket(a, "materials")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (40.0, 30.0, 10.0))

    def test_naive_trap_conference_and_teaching_caps(self):
        #   10h training            -> 10            (uncapped)
        #   20h conference          -> 20 raw, cap 15 (5 lost)
        #   35h teaching            -> 35 raw, cap 30 (5 lost)
        # naive     = 10 + 20 + 35 = 65 -> "65 of 60, done"
        # cap-aware = 10 + 15 + 30 = 55 -> five short
        entries = [
            entry(datetime.date(2025, 2, 1), ["training"], hours=10),
            entry(datetime.date(2025, 3, 1), ["conference"], hours=20),
            entry(datetime.date(2025, 4, 1), ["teaching"], hours=35),
        ]
        a = self.run_case(entries)
        self.assertEqual(self.naive(entries), 65.0)
        self.assertGreaterEqual(self.naive(entries), a.required)
        self.assertEqual(a.awarded, 55.0)
        self.assertEqual(a.gap, 5.0)
        self.assertEqual(a.forfeited, 10.0)
        self.assertNotEqual(a.verdict, E.COMPLIANT)


# ---------------------------------------------------------------------------
# EC-Council / CEH — 120 ECE per 36 months, annual registration window
# ---------------------------------------------------------------------------

class TestCEH(GoldenBase):
    cred_id = "ceh"
    anchor = datetime.date(2024, 1, 10)   # .. 2027-01-10

    def _portfolio(self, submitted=False):
        sub = ["eccouncil"] if submitted else []
        return [
            # 60h of courses = 60
            entry(datetime.date(2025, 3, 1), ["training"], hours=60,
                  submitted_to=sub),
            # 2 articles @20 = 40
            entry(datetime.date(2025, 9, 1), ["authoring_article"], count=2,
                  submitted_to=sub),
            # 5 presentations @3 = 15
            entry(datetime.date(2026, 2, 1), ["teaching"], count=5),
            # 1 book review @5 = 5
            entry(datetime.date(2026, 3, 1), ["reading"], count=1),
        ]

    def test_compliant_portfolio(self):
        a = self.run_case(self._portfolio())
        self.assertEqual(a.awarded, 120.0)   # 60 + 40 + 15 + 5
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_negative_unsubmitted_credits_are_overdue(self):
        # EC-Council's trap is not a cap, it is a clock: credits earned in a
        # calendar year must be REGISTERED by 1 February of the next one.
        # 100 ECE sitting unregistered from 2025 is 100 ECE at risk.
        a = self.run_case(self._portfolio(submitted=False))
        by_year = {d["earned_year"]: d for d in a.submission["deadlines"]}

        self.assertEqual(by_year[2025]["register_by"], "2026-02-01")
        self.assertEqual(by_year[2025]["credits"], 100.0)
        self.assertEqual(by_year[2025]["unsubmitted_entries"], 2)
        self.assertTrue(by_year[2025]["overdue"])
        self.assertLess(by_year[2025]["days_left"], 0)

        # 2026's credits are not due until 2027-02-01 and are not overdue.
        self.assertEqual(by_year[2026]["register_by"], "2027-02-01")
        self.assertFalse(by_year[2026]["overdue"])
        self.assertGreater(by_year[2026]["days_left"], 0)

    def test_submitting_clears_the_overdue_flag(self):
        a = self.run_case(self._portfolio(submitted=True))
        by_year = {d["earned_year"]: d for d in a.submission["deadlines"]}
        self.assertEqual(by_year[2025]["unsubmitted_entries"], 0)
        self.assertFalse(by_year[2025]["overdue"])


# ---------------------------------------------------------------------------
# UMD / GCITP — 100 PDU per 48 months, CI floor 60, non-CI cap 40
# ---------------------------------------------------------------------------

class TestGCITP(GoldenBase):
    cred_id = "gcitp"
    anchor = datetime.date(2023, 5, 1)   # .. 2027-05-01

    def test_naive_trap_ci_floor_and_other_cap(self):
        #   58h CI-marked training -> group "cint" 58   (floor 60: SHORT BY 2)
        #   50h general volunteering -> group "other" 50 raw, cap 40 (10 lost)
        # naive      = 58 + 50 = 108 -> "108 of 100, comfortable"
        # cap-aware  = 58 + 40 =  98 -> two short, and the CI floor is missed
        entries = [
            entry(datetime.date(2024, 6, 1), ["training"], hours=58,
                  markers=["cint"]),
            entry(datetime.date(2025, 6, 1), ["volunteer"], hours=50),
        ]
        a = self.run_case(entries)
        naive = self.naive(entries)

        self.assertEqual(naive, 108.0)
        self.assertGreaterEqual(naive, a.required)
        self.assertEqual(a.awarded, 98.0)
        self.assertLess(a.awarded, a.required)
        self.assertEqual(self.group(a, "cint")["awarded"], 58.0)
        self.assertEqual(self.group(a, "cint")["shortfall"], 2.0)
        self.assertEqual(self.group(a, "other")["raw"], 50.0)
        self.assertEqual(self.group(a, "other")["awarded"], 40.0)
        self.assertTrue(self.group(a, "other")["at_cap"])
        self.assertEqual(a.unmet_floors, ["cint"])
        self.assertEqual(a.verdict, E.BEHIND)

    def test_negative_special_projects_cap(self):
        # Special projects top out at 45 PDU no matter how many hours go in.
        entries = [entry(datetime.date(2025, 2, 1), ["special_project"], hours=50)]
        a = self.run_case(entries)
        b = self.bucket(a, "special_projects")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (50.0, 45.0, 5.0))
        self.assertTrue(b["at_cap"])

    def test_compliant_portfolio(self):
        #   65h CI training  -> cint 65 (floor 60 met)
        #   35h general      -> other 35 (under the 40 cap)
        # total 100 >= 100
        entries = [
            entry(datetime.date(2024, 6, 1), ["training"], hours=65,
                  markers=["cint"]),
            entry(datetime.date(2025, 6, 1), ["volunteer"], hours=35),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 100.0)
        self.assertEqual(a.unmet_floors, [])
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_teaching_earns_three_pdus_per_hour(self):
        # 10h CI teaching at 3/h -> 30 PDU, in its own category, into cint.
        entries = [entry(datetime.date(2025, 2, 1), ["teaching"], hours=10,
                         markers=["cint"])]
        a = self.run_case(entries)
        b = self.bucket(a, "teaching_presenting")
        self.assertEqual((b["raw"], b["awarded"]), (30.0, 30.0))
        self.assertNotIn("giving_back", [x["id"] for x in a.buckets])
        self.assertEqual(self.group(a, "cint")["awarded"], 30.0)
        self.assertEqual(a.awarded, 30.0)

    def test_negative_teaching_category_cap(self):
        # 40h CI teaching at 3/h -> 120 raw, category cap 100 (20 lost).
        entries = [entry(datetime.date(2025, 2, 1), ["teaching"], hours=40,
                         markers=["cint"])]
        a = self.run_case(entries)
        b = self.bucket(a, "teaching_presenting")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]),
                         (120.0, 100.0, 20.0))
        self.assertTrue(b["at_cap"])

    def test_naive_trap_teaching_rate_into_the_other_cap(self):
        #   55h CI training           -> cint 55   (floor 60: SHORT BY 5)
        #   15h non-CI teaching @3/h  -> other 45 raw, cap 40 (5 lost)
        # naive     = 55 + 45 = 100 -> "100 of 100, done"
        # cap-aware = 55 + 40 =  95 -> five short, and the CI floor is missed
        entries = [
            entry(datetime.date(2024, 6, 1), ["training"], hours=55,
                  markers=["cint"]),
            entry(datetime.date(2025, 6, 1), ["teaching"], hours=15),
        ]
        a = self.run_case(entries)
        self.assertEqual(self.naive(entries), 100.0)
        self.assertGreaterEqual(self.naive(entries), a.required)
        self.assertEqual(self.group(a, "other")["raw"], 45.0)
        self.assertEqual(self.group(a, "other")["awarded"], 40.0)
        self.assertEqual(a.awarded, 95.0)
        self.assertEqual(a.unmet_floors, ["cint"])
        self.assertNotEqual(a.verdict, E.COMPLIANT)


# ---------------------------------------------------------------------------
# ATAP membership — attendance tracking, calendar year
# ---------------------------------------------------------------------------

class TestATAPMembership(GoldenBase):
    cred_id = "atap_membership"
    anchor = datetime.date(2025, 1, 1)   # calendar basis: anchor only seeds it

    def test_calendar_year_window(self):
        a = self.run_case([])
        self.assertEqual(a.cycle_start, datetime.date(2026, 1, 1))
        self.assertEqual(a.cycle_end, datetime.date(2027, 1, 1))

    def test_one_meeting_satisfies_the_year(self):
        entries = [entry(datetime.date(2026, 3, 1), ["chapter_meeting"], count=1)]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 1.0)
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_negative_no_attendance(self):
        a = self.run_case([])
        self.assertEqual(a.awarded, 0.0)
        self.assertNotEqual(a.verdict, E.COMPLIANT)

    def test_ships_marked_unverified(self):
        # ATAP's own site was unreachable during the rules sweep. The numbers
        # here are placeholders and the UI must say so.
        a = self.run_case([])
        self.assertTrue(a.unverified_reasons)
        self.assertTrue(any("UNVERIFIED" in r for r in a.unverified_reasons))


# ---------------------------------------------------------------------------
# ATAP / CTM — prospective, and the caps are NOT KNOWN
# ---------------------------------------------------------------------------

class TestCTM(GoldenBase):
    cred_id = "ctm"
    anchor = datetime.date(2025, 1, 1)   # .. 2027-01-01

    def test_negative_refuses_to_assert_compliance(self):
        # This is CTM's negative case, and it is a different SHAPE of negative:
        # not "the cap bit", but "cpebot does not know the caps, so it will not
        # tell you that you are compliant". 200 credits of anything must still
        # produce an advisory, never a verdict.
        entries = [entry(datetime.date(2026, 1, 1), ["training"], hours=200)]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 200.0)          # an UPPER BOUND
        self.assertGreater(a.awarded, a.required)
        self.assertEqual(a.verdict, E.UNVERIFIED_CANNOT_ASSERT)
        self.assertTrue(a.advisory_only)
        self.assertTrue(any("UPPER BOUND" in n for n in a.notes))

    def test_caps_known_false_is_in_the_unverified_surface(self):
        a = self.run_case([])
        self.assertEqual(a.verdict, E.UNVERIFIED_CANNOT_ASSERT)
        self.assertTrue(any("caps NOT KNOWN" in r for r in a.unverified_reasons))


# ---------------------------------------------------------------------------
# ACFE / CFE — 20 CPE a year, fraud floor 10, ethics floor 2, floors overlap
# ---------------------------------------------------------------------------

class TestCFE(GoldenBase):
    cred_id = "cfe"
    anchor = datetime.date(2026, 4, 1)   # .. 2027-04-01

    def test_compliant_portfolio(self):
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=12,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 15), ["training"], hours=3,
                  markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=5),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 20.0)
        self.assertEqual(self.group(a, "fraud")["awarded"], 12.0)
        self.assertEqual(self.group(a, "ethics")["awarded"], 3.0)
        self.assertEqual(a.unmet_floors, [])
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_naive_trap_total_met_but_fraud_floor_missed(self):
        # 20 hours is 20 hours -- and it is still non-compliant, because only
        # 9 of them were fraud-related and ACFE wants 10.
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=9,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 15), ["training"], hours=2,
                  markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=9),
        ]
        a = self.run_case(entries)
        naive = self.naive(entries)

        self.assertEqual(naive, 20.0)
        self.assertGreaterEqual(naive, a.required)   # naive: "20 of 20, done"
        self.assertEqual(a.awarded, 20.0)            # the TOTAL is fine...
        self.assertEqual(self.group(a, "fraud")["shortfall"], 1.0)   # ...the mix isn't
        self.assertEqual(a.unmet_floors, ["fraud"])
        self.assertNotEqual(a.verdict, E.COMPLIANT)

    def test_negative_ethics_floor_missed(self):
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=18,
                  markers=["fraud"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=2),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 20.0)
        self.assertEqual(self.group(a, "ethics")["shortfall"], 2.0)
        self.assertEqual(a.unmet_floors, ["ethics"])
        self.assertNotEqual(a.verdict, E.COMPLIANT)

    def test_overlapping_floors_do_not_double_count(self):
        # An hour of fraud ethics training satisfies BOTH floors and is still
        # one hour of the twenty. If the engine ever reports 22 here, the
        # overlap handling has broken.
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=8,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 10), ["training"], hours=2,
                  markers=["fraud", "ethics"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=10),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 20.0)                          # not 22
        self.assertEqual(self.group(a, "fraud")["awarded"], 10.0)  # 8 + 2
        self.assertEqual(self.group(a, "ethics")["awarded"], 2.0)  # the same 2
        self.assertEqual(a.unmet_floors, [])
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_carryover_capped_at_ten(self):
        # 25 earned, 20 needed, surplus 5 -> carry 5 (the cap is 10)
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=15,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 15), ["training"], hours=2,
                  markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=8),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 25.0)
        self.assertEqual(a.carryover_out, 5.0)

        # 40 earned, surplus 20 -> carry 10, not 20
        entries.append(entry(datetime.date(2026, 7, 1), ["training"], hours=15,
                             markers=["fraud"]))
        a2 = self.run_case(entries)
        self.assertEqual(a2.awarded, 40.0)
        self.assertEqual(a2.carryover_out, 10.0)

    def test_negative_carryover_withheld_when_a_floor_is_unmet(self):
        # A surplus you are not entitled to carry. 30 hours, but only 5 of
        # them fraud-related: the floor fails, so nothing rolls forward.
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=5,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 15), ["training"], hours=2,
                  markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=23),
        ]
        a = self.run_case(entries)
        self.assertEqual(a.awarded, 30.0)
        self.assertEqual(a.carryover_out, 0.0)
        self.assertTrue(any("carryover withheld" in n for n in a.notes))

    def test_carryover_in_counts_toward_the_total(self):
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=12,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 15), ["training"], hours=3,
                  markers=["ethics"]),
        ]
        a = self.run_case(entries, carryover_in=5)
        self.assertEqual(a.awarded, 15.0)
        self.assertEqual(a.total_with_carryover, 20.0)
        self.assertEqual(a.gap, 0.0)
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_negative_instructor_cap(self):
        # 15h teaching -> instructor 15 raw, cap 10 (5 lost).
        a = self.run_case([entry(datetime.date(2026, 5, 1), ["teaching"],
                                 hours=15)])
        b = self.bucket(a, "instructor")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (15.0, 10.0, 5.0))
        self.assertNotIn("cpe_hour", [x["id"] for x in a.buckets])

    def test_negative_author_cap(self):
        # 12h authoring -> author 12 raw, cap 10 (2 lost).
        a = self.run_case([entry(datetime.date(2026, 5, 1),
                                 ["authoring_article"], hours=12)])
        b = self.bucket(a, "author")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (12.0, 10.0, 2.0))

    def test_negative_mentor_volunteer_cap(self):
        # 14h volunteering -> mentor/volunteer 14 raw, cap 10 (4 lost).
        a = self.run_case([entry(datetime.date(2026, 5, 1), ["volunteer"],
                                 hours=14)])
        b = self.bucket(a, "mentor_volunteer")
        self.assertEqual((b["raw"], b["awarded"], b["forfeited"]), (14.0, 10.0, 4.0))

    def test_college_course_is_fifteen_per_course(self):
        a = self.run_case([entry(datetime.date(2026, 5, 1),
                                 ["higher_education"], count=1)])
        self.assertEqual(self.bucket(a, "college")["awarded"], 15.0)

    def test_negative_side_caps_forfeit_but_the_year_is_met(self):
        #   10h fraud training  -> cpe_hour 10 (fraud floor 10 met)
        #    2h ethics training -> cpe_hour  2 (ethics floor 2 met)
        #   12h teaching        -> instructor 12 raw, cap 10 (2 lost)
        #   12h authoring       -> author     12 raw, cap 10 (2 lost)
        # naive     = 10 + 2 + 12 + 12 = 36
        # cap-aware = 10 + 2 + 10 + 10 = 32 (still >= 20: the caps bite and
        # 4 are forfeit, but the verdict does not change)
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=10,
                  markers=["fraud"]),
            entry(datetime.date(2026, 5, 2), ["training"], hours=2,
                  markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["teaching"], hours=12),
            entry(datetime.date(2026, 7, 1), ["authoring_article"], hours=12),
        ]
        a = self.run_case(entries)
        self.assertEqual(self.naive(entries), 36.0)
        self.assertEqual(a.awarded, 32.0)
        self.assertEqual(a.forfeited, 4.0)
        self.assertEqual(a.unmet_floors, [])
        self.assertEqual(a.verdict, E.COMPLIANT)

    def test_naive_trap_instructor_cap_leaves_the_year_short(self):
        #    6h training        -> cpe_hour 6
        #    2h ethics training -> cpe_hour 2 (ethics floor 2 met)
        #   15h fraud teaching  -> instructor 15 raw, cap 10 (5 lost)
        # naive     = 6 + 2 + 15 = 23 -> "23 of 20, done"
        # cap-aware = 6 + 2 + 10 = 18 -> two short
        entries = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=6),
            entry(datetime.date(2026, 5, 2), ["training"], hours=2,
                  markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["teaching"], hours=15,
                  markers=["fraud"]),
        ]
        a = self.run_case(entries)
        self.assertEqual(self.naive(entries), 23.0)
        self.assertGreaterEqual(self.naive(entries), a.required)
        self.assertEqual(a.awarded, 18.0)
        self.assertEqual(a.gap, 2.0)
        self.assertNotEqual(a.verdict, E.COMPLIANT)


# ---------------------------------------------------------------------------
# cross-cutting
# ---------------------------------------------------------------------------

class TestPace(unittest.TestCase):
    def test_pace_line_and_runway(self):
        cred = RULES.get("cissp")
        # Halfway through the cycle with 10 CPE: expected ~60, so BEHIND.
        a = E.assess(
            cred,
            [entry(datetime.date(2024, 8, 1), ["training"], hours=10,
                   markers=["domain_security"])],
            anchor=datetime.date(2024, 6, 1),
            as_of=datetime.date(2025, 12, 1),
        )
        p = E.pace(a)
        self.assertLess(p["delta"], 0)
        self.assertFalse(p["on_pace"])
        self.assertEqual(p["gap"], 110.0)
        self.assertGreater(p["per_month_needed"], 0)
        self.assertEqual(a.verdict, E.BEHIND)

    def test_cycle_closed_and_short(self):
        cred = RULES.get("cissp")
        a = E.assess(
            cred, [], anchor=datetime.date(2020, 1, 1),
            as_of=datetime.date(2026, 8, 30),
        )
        # cycles roll forward: 2020, 2023, 2026 -> current cycle is open
        self.assertLessEqual(a.cycle_start, datetime.date(2026, 8, 30))
        a2 = E.assess(
            cred, [], cycle_start=datetime.date(2020, 1, 1),
            as_of=datetime.date(2026, 8, 30),
        )
        self.assertEqual(a2.verdict, E.SHORT)


class TestAssessAll(unittest.TestCase):
    def test_every_credential_assesses_without_error(self):
        anchors = dict((c.id, datetime.date(2025, 1, 1)) for c in RULES.credentials)
        out = E.assess_all(RULES, [], anchors, as_of=TODAY)
        self.assertEqual(sorted(out), sorted(RULES.ids()))
        for cred_id, a in out.items():
            self.assertIsNotNone(a.verdict, cred_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)

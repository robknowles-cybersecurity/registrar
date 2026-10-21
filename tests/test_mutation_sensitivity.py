"""Proof that the golden tests are load-bearing.

A passing test suite means nothing until you have watched it fail for the
right reason. Every case here takes a rule out of bodies.yaml, re-runs the
exact portfolio a golden test uses, and asserts the answer CHANGES — which
is the same thing as asserting that the golden expectation was pinned to
that rule and not to a coincidence.

Read it the other way round and it is a regression alarm: if someone deletes
Group B's cap from bodies.yaml, `test_naive_trap_group_b_cap_and_group_a_floor`
starts reporting 125 instead of 115, and the failure here says which rule
went missing.
"""

import copy
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B
from cpebot import engine as E

TODAY = datetime.date(2026, 8, 30)


def mutated(fn):
    """A fresh Rules object with `fn` applied to the raw dict first."""
    base = copy.deepcopy(B.load().raw)
    fn(base)
    rules = B.Rules(base, path="<mutated>", parser="mutation-test")
    rules.validate()
    return rules


def entry(date, tags, hours=0.0, count=1, markers=()):
    return {
        "date": date, "hours": hours, "count": count,
        "tags": list(tags), "markers": list(markers), "status": "confirmed",
    }


def find(seq, key, value):
    for item in seq:
        if item[key] == value:
            return item
    raise KeyError(value)


class TestCapsAreLoadBearing(unittest.TestCase):

    def test_removing_isc2_group_b_cap_changes_the_answer(self):
        portfolio = [
            entry(datetime.date(2025, 3, 1), ["training"], hours=85,
                  markers=["domain_security"]),
            entry(datetime.date(2025, 4, 1), ["professional_dev"], hours=40),
        ]
        anchor = datetime.date(2024, 6, 1)

        real = E.assess(B.load().get("cissp"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertEqual(real.awarded, 115.0)

        def strip(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["groups"], "id", "group_b")["cap"] = None

        broken = E.assess(mutated(strip).get("cissp"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.awarded, 125.0)      # the ten hours come back
        self.assertNotEqual(broken.awarded, real.awarded)

    def test_removing_isc2_group_a_floor_hides_the_shortfall(self):
        portfolio = [
            entry(datetime.date(2025, 3, 1), ["training"], hours=85,
                  markers=["domain_security"]),
            entry(datetime.date(2025, 4, 1), ["professional_dev"], hours=40),
        ]
        anchor = datetime.date(2024, 6, 1)
        real = E.assess(B.load().get("cissp"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertEqual(real.unmet_floors, ["group_a"])

        def strip(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["groups"], "id", "group_a")["floor"] = None

        broken = E.assess(mutated(strip).get("cissp"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.unmet_floors, [])

    def test_removing_comptia_blog_cap_changes_the_answer(self):
        portfolio = [
            entry(datetime.date(2025, 2, 1), ["training"], hours=30),
            entry(datetime.date(2025, 6, 1), ["authoring_blog"], count=20),
            entry(datetime.date(2025, 7, 1), ["work_experience"], count=4),
        ]
        anchor = datetime.date(2024, 9, 15)
        real = E.assess(B.load().get("cysa_plus"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertEqual(real.awarded, 57.0)

        def strip(raw):
            cysa = find(raw["credentials"], "id", "cysa_plus")
            find(cysa["buckets"], "id", "publishing_blog")["cap"] = None

        broken = E.assess(mutated(strip).get("cysa_plus"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.awarded, 59.0)       # the two capped blog posts return
        self.assertGreater(broken.awarded, real.awarded)

    def test_removing_gcitp_other_cap_changes_the_answer(self):
        portfolio = [
            entry(datetime.date(2024, 6, 1), ["training"], hours=58, markers=["cint"]),
            entry(datetime.date(2025, 6, 1), ["volunteer"], hours=50),
        ]
        anchor = datetime.date(2023, 5, 1)
        real = E.assess(B.load().get("gcitp"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertEqual(real.awarded, 98.0)

        def strip(raw):
            g = find(raw["credentials"], "id", "gcitp")
            find(g["groups"], "id", "other")["cap"] = None

        broken = E.assess(mutated(strip).get("gcitp"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.awarded, 108.0)      # the ten PDU come back
        # The CI floor is a SEPARATE rule and still catches this portfolio,
        # which is the point: GCITP needs both to be right, and losing either
        # one changes a different part of the answer.
        self.assertEqual(broken.unmet_floors, ["cint"])
        self.assertEqual(broken.verdict, E.BEHIND)

    def test_removing_acfe_fraud_floor_turns_a_failure_into_a_pass(self):
        portfolio = [
            entry(datetime.date(2026, 5, 1), ["training"], hours=9, markers=["fraud"]),
            entry(datetime.date(2026, 5, 15), ["training"], hours=2, markers=["ethics"]),
            entry(datetime.date(2026, 6, 1), ["training"], hours=9),
        ]
        anchor = datetime.date(2026, 4, 1)
        real = E.assess(B.load().get("cfe"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertNotEqual(real.verdict, E.COMPLIANT)

        def strip(raw):
            cfe = find(raw["credentials"], "id", "cfe")
            find(cfe["groups"], "id", "fraud")["floor"] = None

        broken = E.assess(mutated(strip).get("cfe"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.verdict, E.COMPLIANT)

    def test_flipping_ctm_caps_known_produces_a_verdict_it_should_not_have(self):
        portfolio = [entry(datetime.date(2026, 1, 1), ["training"], hours=200)]
        anchor = datetime.date(2025, 1, 1)
        real = E.assess(B.load().get("ctm"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertEqual(real.verdict, E.UNVERIFIED_CANNOT_ASSERT)

        def flip(raw):
            find(raw["credentials"], "id", "ctm")["caps_known"] = True

        broken = E.assess(mutated(flip).get("ctm"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.verdict, E.COMPLIANT)   # exactly the guess we refuse

    def test_removing_ceh_registration_window_hides_an_overdue_deadline(self):
        portfolio = [entry(datetime.date(2025, 3, 1), ["training"], hours=60)]
        anchor = datetime.date(2024, 1, 10)
        real = E.assess(B.load().get("ceh"), portfolio, anchor=anchor, as_of=TODAY)
        self.assertTrue(real.submission["deadlines"][0]["overdue"])

        def strip(raw):
            find(raw["credentials"], "id", "ceh")["submission"]["cadence"] = "continuous"

        broken = E.assess(mutated(strip).get("ceh"), portfolio,
                          anchor=anchor, as_of=TODAY)
        self.assertEqual(broken.submission["deadlines"], [])


class TestValidatorRejectsBrokenRules(unittest.TestCase):
    """The loader's own failure modes. A rules file that half-loads is the
    worst outcome available, so each of these must raise, not warn."""

    def _expect_error(self, fn):
        with self.assertRaises(B.RulesError):
            mutated(fn)

    def test_bucket_with_both_rates_is_rejected(self):
        def bad(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["buckets"], "id", "a_activity")["per_item"] = 5
        self._expect_error(bad)

    def test_bucket_with_no_rate_is_rejected(self):
        def bad(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["buckets"], "id", "a_activity")["per_hour"] = None
        self._expect_error(bad)

    def test_unknown_group_reference_is_rejected(self):
        def bad(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["buckets"], "id", "a_activity")["group"] = "group_z"
        self._expect_error(bad)

    def test_tag_outside_the_vocabulary_is_rejected(self):
        def bad(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["buckets"], "id", "a_activity")["tags"] = ["knitting"]
        self._expect_error(bad)

    def test_marker_outside_the_vocabulary_is_rejected(self):
        def bad(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["buckets"], "id", "a_activity")["requires_markers"] = ["vibes"]
        self._expect_error(bad)

    def test_floors_exceeding_the_cycle_total_are_rejected(self):
        # 90 + 200 > 120: an impossible credential, and a typo that would
        # otherwise render as a permanently red dashboard.
        def bad(raw):
            cissp = find(raw["credentials"], "id", "cissp")
            find(cissp["groups"], "id", "group_b")["floor"] = 200
        self._expect_error(bad)

    def test_verified_true_without_provenance_is_rejected(self):
        def bad(raw):
            find(raw["credentials"], "id", "cissp")["source_url"] = None
        self._expect_error(bad)

    def test_duplicate_credential_id_is_rejected(self):
        def bad(raw):
            raw["credentials"].append(copy.deepcopy(raw["credentials"][0]))
        self._expect_error(bad)

    def test_unsupported_schema_version_is_rejected(self):
        def bad(raw):
            raw["schema_version"] = 99
        self._expect_error(bad)


class TestEngineRefusesAmbiguousRules(unittest.TestCase):
    def test_capped_overlapping_group_raises_rather_than_guessing(self):
        # A cap on a group whose members overlap has no defined meaning: the
        # same hour sits in two groups, so there is no fact about which copy
        # is discarded. The engine must refuse instead of picking one.
        def bad(raw):
            cfe = find(raw["credentials"], "id", "cfe")
            find(cfe["groups"], "id", "fraud")["cap"] = 5

        rules = mutated(bad)
        portfolio = [entry(datetime.date(2026, 5, 1), ["training"], hours=9,
                           markers=["fraud"])]
        with self.assertRaises(E.EngineError):
            E.assess(rules.get("cfe"), portfolio,
                     anchor=datetime.date(2026, 4, 1), as_of=TODAY)


if __name__ == "__main__":
    unittest.main(verbosity=2)

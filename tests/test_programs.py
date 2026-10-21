"""The research catalog (`programs:` in bodies.yaml) and its provenance rules.

The catalog holds body-level maintenance programs with per-certification
totals. It is reference data, not engine input — but a number in it is still
a number someone will act on, so the loader enforces the same discipline the
credentials section does: every figure names its source, says whether that
source was actually read, and only a figure read on the body's own page may
call itself verified. These tests pin that, and each refusal is checked
against a mutation of a known-good program so they can fail.
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import bodies as B  # noqa: E402

PROV = {"source_url": "https://example.org/rules", "source_tier": "official_search_snippet",
        "look_status": "NOT_READ", "checked_date": "2026-10-21",
        "verified": False, "verified_date": None}


def _p(**extra):
    d = dict(PROV)
    d.update(extra)
    return d


def good_program():
    sub = dict((k, _p(value="unknown")) for k in B.SUBMISSION_KEYS)
    return _p(body_id="examplebody", body_name="Example Body", unit_label="CPE",
              cycle_months=36,
              facts=[_p(key="annual_fee", value=100)],
              certifications=[_p(id="ex1", name="EX1", status="offered",
                                 cycle_total=60, annual_minimum=20)],
              submission=sub)


def rules_with(program):
    raw = B._raw_load(B.DEFAULT_PATH)[0]
    raw = copy.deepcopy(raw)
    raw["programs"] = [program]
    return B.Rules(raw)


class TestShippedCatalog(unittest.TestCase):

    def setUp(self):
        self.rules = B.load()

    def test_catalog_loads_and_is_not_engine_input(self):
        self.assertTrue(self.rules.programs)
        engine_ids = set(self.rules.ids())
        for p in self.rules.programs:
            for c in p.certifications:
                # Catalog ids never shadow engine credential ids; a link to an
                # engine track is explicit via engine_track.
                self.assertNotIn(c.id, engine_ids)

    def test_no_shipped_figure_claims_verification_it_did_not_earn(self):
        for p in self.rules.programs:
            items = [p.raw] + list(p.facts) + [c.raw for c in p.certifications] \
                + list(p.submission.values())
            for item in items:
                if item.get("verified"):
                    self.assertEqual(item.get("look_status"), "READ_OK")
                    self.assertEqual(item.get("source_tier"), "official_page")

    def test_every_engine_body_has_a_program(self):
        program_bodies = set(p.body_id for p in self.rules.programs)
        for b in self.rules.bodies():
            self.assertIn(b["id"], program_bodies)

    def test_giac_abeyance_is_retired_with_the_bodys_own_term(self):
        giac = [p for p in self.rules.programs if p.body_id == "giac"][0]
        certs = dict((c.id, c) for c in giac.certifications)
        for cid in ("giac_gsna", "giac_gleg"):
            self.assertEqual(certs[cid].status, "retired")
            self.assertEqual(certs[cid].status_note, "in abeyance (GIAC term)")
        # Only the abeyance certs carry the note.
        noted = sorted(c.id for c in giac.certifications if c.status_note)
        self.assertEqual(noted, ["giac_gleg", "giac_gsna"])


class TestProgramValidation(unittest.TestCase):

    def assertRefused(self, mutate):
        prog = good_program()
        mutate(prog)
        with self.assertRaises(B.RulesError):
            rules_with(prog).validate()

    def test_good_program_validates(self):
        self.assertTrue(rules_with(good_program()).validate())

    def test_missing_source_url_refused(self):
        self.assertRefused(lambda p: p["certifications"][0].pop("source_url"))

    def test_non_http_source_url_refused(self):
        for url in ("javascript:alert(1)", "data:text/html,x", "ftp://example.org/"):
            self.assertRefused(lambda p, u=url: p["facts"][0].update(source_url=u))
            self.assertRefused(lambda p, u=url: p.update(source_url=u))

    def test_non_http_credential_source_url_refused(self):
        raw = copy.deepcopy(B._raw_load(B.DEFAULT_PATH)[0])
        self.assertTrue(B.Rules(copy.deepcopy(raw)).validate())
        for url in ("javascript:alert(1)", "data:text/html,x"):
            bad = copy.deepcopy(raw)
            bad["credentials"][0]["source_url"] = url
            with self.assertRaises(B.RulesError):
                B.Rules(bad).validate()

    def test_unknown_look_status_refused(self):
        self.assertRefused(lambda p: p["facts"][0].update(look_status="PROBABLY"))

    def test_verified_without_reading_refused(self):
        self.assertRefused(lambda p: p["certifications"][0].update(
            verified=True, verified_date="2026-10-21"))

    def test_verified_from_secondary_refused(self):
        self.assertRefused(lambda p: p["facts"][0].update(
            verified=True, verified_date="2026-10-21", look_status="READ_OK",
            source_tier="secondary"))

    def test_verified_official_read_accepted(self):
        prog = good_program()
        prog["facts"][0].update(verified=True, verified_date="2026-10-21",
                                look_status="READ_OK", source_tier="official_page")
        self.assertTrue(rules_with(prog).validate())

    def test_missing_submission_key_refused(self):
        self.assertRefused(lambda p: p["submission"].pop("mfa"))

    def test_unknown_submission_key_refused(self):
        self.assertRefused(lambda p: p["submission"].update(extra=_p(value="x")))

    def test_submission_keys_are_the_five_documented_ones(self):
        self.assertEqual(B.SUBMISSION_KEYS, ("channel", "public_api", "bulk_upload",
                                             "mfa", "provider_reported"))

    def test_bad_mfa_value_refused(self):
        self.assertRefused(lambda p: p["submission"]["mfa"].update(value="maybe"))

    def test_annual_minimum_above_total_refused(self):
        self.assertRefused(lambda p: p["certifications"][0].update(annual_minimum=61))

    def test_negative_total_refused(self):
        self.assertRefused(lambda p: p["certifications"][0].update(cycle_total=-1))

    def test_dangling_engine_track_refused(self):
        self.assertRefused(lambda p: p["certifications"][0].update(engine_track="nope"))

    def test_engine_track_to_real_credential_accepted(self):
        prog = good_program()
        prog["certifications"][0]["engine_track"] = "cissp"
        self.assertTrue(rules_with(prog).validate())

    def test_empty_status_note_refused(self):
        self.assertRefused(lambda p: p["certifications"][0].update(status_note="  "))

    def test_status_note_accepted(self):
        prog = good_program()
        prog["certifications"][0].update(status="retired", status_note="in abeyance")
        self.assertTrue(rules_with(prog).validate())

    def test_duplicate_cert_id_refused(self):
        def dup(p):
            p["certifications"].append(copy.deepcopy(p["certifications"][0]))
        self.assertRefused(dup)


if __name__ == "__main__":
    unittest.main(verbosity=2)

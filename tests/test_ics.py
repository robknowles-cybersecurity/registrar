"""Deadline calendar (.ics).

Two kinds of claim, tested separately. The DATES must be right — a calendar
that puts a cycle end on the wrong day is worse than none, because it is
trusted. And the FILE must be RFC 5545, checked here by a stdlib parser
strict about the parts calendar clients are strict about: CRLF, 75-octet
folding, balanced components, required properties, TEXT escaping.
"""

import datetime
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cpebot import bodies as B          # noqa: E402
from cpebot import cli                  # noqa: E402
from cpebot import ics as I             # noqa: E402
from cpebot import ledger as L          # noqa: E402
from test_web import WebCase            # noqa: E402

AS_OF = datetime.date(2026, 10, 21)
STAMP = datetime.datetime(2026, 10, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# a small, strict RFC 5545 reader
# ---------------------------------------------------------------------------

def unfold(text):
    """RFC 5545 3.1: CRLF followed by one space or tab is a continuation."""
    return re.sub(r"\r\n[ \t]", "", text)


def unescape(value):
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": "\n", "N": "\n"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse(text):
    """Return the VCALENDAR as nested dicts; raise on any structural error."""
    if "\n" in text.replace("\r\n", ""):
        raise AssertionError("bare LF line ending")
    if not text.endswith("\r\n"):
        raise AssertionError("file must end with CRLF")
    for raw in text.split("\r\n"):
        if len(raw.encode("utf-8")) > 75:
            raise AssertionError("line over 75 octets: %r" % raw)
    stack, root = [], None
    for line in unfold(text).split("\r\n")[:-1]:
        name, _, value = line.partition(":")
        prop, _, params = name.partition(";")
        if prop == "BEGIN":
            comp = {"type": value, "props": [], "children": []}
            if stack:
                stack[-1]["children"].append(comp)
            stack.append(comp)
        elif prop == "END":
            if not stack or stack[-1]["type"] != value:
                raise AssertionError("unbalanced END:%s" % value)
            done = stack.pop()
            if not stack:
                root = done
        else:
            if not stack:
                raise AssertionError("property outside a component: %r" % line)
            stack[-1]["props"].append((prop, params, value))
    if stack or root is None or root["type"] != "VCALENDAR":
        raise AssertionError("no complete VCALENDAR")
    return root


def props(comp, name):
    return [v for p, _params, v in comp["props"] if p == name]


def events_of(cal):
    return [c for c in cal["children"] if c["type"] == "VEVENT"]


class IcsCase(unittest.TestCase):

    def setUp(self):
        self.rules = B.load()
        self.dir = tempfile.mkdtemp(prefix="cpebot-ics-")
        self.led = L.Ledger(os.path.join(self.dir, "t.db"),
                            os.path.join(self.dir, "evidence"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def events(self, as_of=AS_OF, only=None):
        evs, skipped = I.deadlines(self.rules, self.led, as_of=as_of,
                                   only=only)
        return evs, skipped

    def kinds(self, evs, cred, kind):
        return [e for e in evs if e["credential"] == cred and e["kind"] == kind]


class TestRfc5545(IcsCase):

    def setUp(self):
        IcsCase.setUp(self)
        for cid, anchor in (("cissp", "2025-09-01"), ("ceh", "2025-01-01"),
                            ("cfe", "2026-01-01"), ("ctm", "2025-01-01")):
            self.led.set_credential_state(cid, anchor_date=anchor)
        self.text = I.export(self.rules, self.led, as_of=AS_OF, stamp=STAMP)
        self.cal = parse(self.text)

    def test_calendar_header(self):
        self.assertEqual(props(self.cal, "VERSION"), ["2.0"])
        self.assertEqual(props(self.cal, "PRODID"),
                         ["-//ceu.tools//Registrar 1.2.0//EN"])
        self.assertEqual(props(self.cal, "CALSCALE"), ["GREGORIAN"])
        self.assertEqual(props(self.cal, "METHOD"), ["PUBLISH"])

    def test_every_event_has_required_properties(self):
        evs = events_of(self.cal)
        self.assertTrue(evs)
        for ev in evs:
            for name in ("UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY"):
                self.assertEqual(len(props(ev, name)), 1, name)
            self.assertEqual(props(ev, "DTSTAMP"), ["20261021T120000Z"])

    def test_all_day_dates_are_value_date_and_one_day_long(self):
        for ev in events_of(self.cal):
            params = dict((p, pa) for p, pa, _v in ev["props"])
            self.assertEqual(params["DTSTART"], "VALUE=DATE")
            self.assertEqual(params["DTEND"], "VALUE=DATE")
            start = datetime.datetime.strptime(props(ev, "DTSTART")[0],
                                               "%Y%m%d").date()
            end = datetime.datetime.strptime(props(ev, "DTEND")[0],
                                             "%Y%m%d").date()
            self.assertEqual(end - start, datetime.timedelta(days=1))

    def test_uids_are_unique(self):
        uids = [props(ev, "UID")[0] for ev in events_of(self.cal)]
        self.assertEqual(len(uids), len(set(uids)))

    def test_alarms_are_valid_display_alarms(self):
        for ev in events_of(self.cal):
            for al in ev["children"]:
                self.assertEqual(al["type"], "VALARM")
                self.assertEqual(props(al, "ACTION"), ["DISPLAY"])
                self.assertEqual(len(props(al, "DESCRIPTION")), 1)
                self.assertRegex(props(al, "TRIGGER")[0], r"^-P\d+D$")

    def test_long_descriptions_were_folded(self):
        self.assertIn("\r\n ", self.text)

    def test_text_escaping_round_trips(self):
        nasty = "a, b; c\\d\nline two — ✓"
        self.assertEqual(unescape(I.escape_text(nasty)), nasty)

    def test_fold_never_splits_a_utf8_sequence(self):
        line = "DESCRIPTION:" + "—" * 80
        folded = I.fold(line)
        for part in folded.split("\r\n"):
            self.assertLessEqual(len(part.encode("utf-8")), 75)
            part.encode("utf-8").decode("utf-8")      # raises if split
        self.assertEqual(unfold(folded), line)

    def test_description_survives_the_round_trip(self):
        cissp = [ev for ev in events_of(self.cal)
                 if props(ev, "SUMMARY")[0] == "CISSP: cycle ends"][0]
        desc = unescape(props(cissp, "DESCRIPTION")[0])
        self.assertIn("2025-09-01 to 2028-08-31", desc)
        self.assertIn("https://www.isc2.org/", desc)


class TestDates(IcsCase):

    def test_cycle_end_is_the_last_day_of_the_cycle(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        evs, _ = self.events()
        end = self.kinds(evs, "cissp", "cycle_end")
        self.assertEqual([e["date"] for e in end], [datetime.date(2028, 8, 31)])

    def test_grace_end_counts_from_cycle_end(self):
        # [2025-09-01, 2028-09-01) + 90 days = 2028-11-30 exclusive, so the
        # last day of grace is 2028-11-29.
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        evs, _ = self.events()
        grace = self.kinds(evs, "cissp", "grace_end")
        self.assertEqual([e["date"] for e in grace],
                         [datetime.date(2028, 11, 29)])

    def test_conditional_grace_is_not_promised(self):
        # ACFE's 30 days is an extension you must request.
        self.led.set_credential_state("cfe", anchor_date="2026-01-01")
        evs, _ = self.events()
        grace = self.kinds(evs, "cfe", "grace_end")[0]
        self.assertIn("conditional", grace["summary"])
        self.assertIn("REQUESTED", grace["description"])

    def test_previous_cycle_grace_appears_only_while_it_is_open(self):
        self.led.set_credential_state("cissp", anchor_date="2022-09-01")
        evs, _ = self.events(as_of=datetime.date(2025, 10, 1))
        prev = [e for e in self.kinds(evs, "cissp", "grace_end")
                if "previous cycle" in e["summary"]]
        self.assertEqual([e["date"] for e in prev],
                         [datetime.date(2025, 11, 29)])
        evs, _ = self.events(as_of=datetime.date(2025, 12, 15))
        self.assertFalse([e for e in self.kinds(evs, "cissp", "grace_end")
                          if "previous cycle" in e["summary"]])

    def test_ece_registration_window_per_calendar_year(self):
        # CEH cycle 2025-01-01 .. 2027-12-31: years 2025, 2026, 2027, each
        # due 1 Feb of the next year. 2026-02-01 is past on AS_OF.
        self.led.set_credential_state("ceh", anchor_date="2025-01-01")
        evs, _ = self.events()
        reg = self.kinds(evs, "ceh", "registration")
        self.assertEqual([e["date"] for e in reg],
                         [datetime.date(2027, 2, 1), datetime.date(2028, 2, 1)])
        self.assertIn("2026", reg[0]["summary"])

    def test_past_events_are_not_emitted(self):
        self.led.set_credential_state("ceh", anchor_date="2025-01-01")
        evs, _ = self.events()
        self.assertTrue(all(e["date"] >= AS_OF for e in evs))

    def test_recorded_unpaid_fee_is_an_event_paid_is_not(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        self.led.add_fee("cissp", "AMF 2027", 135, due_date="2027-09-01")
        self.led.add_fee("cissp", "AMF 2026", 135, due_date="2026-11-01",
                         paid_on="2026-10-01")
        evs, _ = self.events()
        fees = self.kinds(evs, "cissp", "fee")
        self.assertEqual([(e["date"], e["summary"]) for e in fees],
                         [(datetime.date(2027, 9, 1),
                           "CISSP: AMF 2027 due ($135)")])

    def test_recorded_fees_replace_derived_ones(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        evs, _ = self.events()
        self.assertTrue(all("estimated" in e["summary"]
                            for e in self.kinds(evs, "cissp", "fee")))
        self.led.add_fee("cissp", "AMF 2027", 135, due_date="2027-09-01")
        evs, _ = self.events()
        self.assertFalse([e for e in self.kinds(evs, "cissp", "fee")
                          if "estimated" in e["summary"]])

    def test_derived_annual_fee_lands_on_cycle_anniversaries(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        evs, _ = self.events()
        fees = self.kinds(evs, "cissp", "fee")
        self.assertEqual([e["date"] for e in fees],
                         [datetime.date(2027, 9, 1)])
        self.assertIn("DERIVED", fees[0]["description"])

    def test_alternative_per_cycle_fees_are_one_deadline(self):
        self.led.set_credential_state("gcitp", anchor_date="2025-08-01")
        evs, _ = self.events()
        fees = self.kinds(evs, "gcitp", "fee")
        self.assertEqual(len(fees), 1)
        self.assertEqual(fees[0]["date"], datetime.date(2029, 7, 31))
        self.assertIn("$500", fees[0]["description"])
        self.assertIn("$650", fees[0]["description"])

    def test_mid_cycle_annual_minimum(self):
        # No shipped credential has an annual minimum inside a multi-year
        # cycle, so build one: CFE's shape stretched to three years.
        raw = dict(self.rules.raw)
        cfe = [c for c in raw["credentials"] if c["id"] == "cfe"][0]
        stretched = dict(cfe, id="cfe3", cycle={"length_months": 36,
                                                "basis": "certification_anniversary"},
                         requirements=dict(cfe["requirements"],
                                           cycle_total=60, annual_minimum=20))
        # Append rather than replace: the programs catalog cross-references
        # the shipped credential ids and must still validate.
        raw["credentials"] = list(raw["credentials"]) + [stretched]
        rules = B.Rules(raw)
        rules.validate()
        self.led.set_credential_state("cfe3", anchor_date="2026-03-01")
        evs, _ = I.deadlines(rules, self.led, as_of=datetime.date(2026, 3, 1))
        mins = [e for e in evs if e["kind"] == "annual_minimum"]
        self.assertEqual([e["date"] for e in mins],
                         [datetime.date(2026, 12, 31),
                          datetime.date(2027, 12, 31),
                          datetime.date(2028, 12, 31)])

    def test_twelve_month_cycle_minimum_is_not_repeated(self):
        self.led.set_credential_state("cfe", anchor_date="2026-01-01")
        evs, _ = self.events()
        self.assertFalse(self.kinds(evs, "cfe", "annual_minimum"))

    def test_unverified_rules_say_so_in_the_event(self):
        self.led.set_credential_state("ctm", anchor_date="2025-01-01")
        evs, _ = self.events()
        self.assertIn("UNVERIFIED", self.kinds(evs, "ctm",
                                               "cycle_end")[0]["description"])

    def test_unanchored_and_inactive_are_skipped_with_a_reason(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01",
                                      active=0)
        evs, skipped = self.events()
        self.assertFalse(evs)
        reasons = dict((s["credential"], s["reason"]) for s in skipped)
        self.assertEqual(reasons["cissp"], "inactive")
        self.assertIn("anchor", reasons["cfe"])

    def test_uids_are_stable_across_exports(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        a, _ = self.events()
        b, _ = self.events(as_of=AS_OF + datetime.timedelta(days=3))
        self.assertEqual([e["uid"] for e in a], [e["uid"] for e in b])

    def test_only_filters_to_one_credential(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        self.led.set_credential_state("ceh", anchor_date="2025-01-01")
        evs, _ = self.events(only=["ceh"])
        self.assertEqual(set(e["credential"] for e in evs), {"ceh"})


class TestCli(IcsCase):

    def test_ics_to_file_is_private_and_parses(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        out = os.path.join(self.dir, "d.ics")
        rc = cli.main(["--db", self.led.path, "--evidence",
                       self.led.evidence_root, "ics", "--out", out,
                       "--as-of", "2026-10-21"])
        self.assertEqual(rc, 0)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        with open(out, "rb") as fh:
            cal = parse(fh.read().decode("utf-8"))
        self.assertTrue(events_of(cal))

    def test_unknown_credential_is_an_error(self):
        rc = cli.main(["--db", self.led.path, "--evidence",
                       self.led.evidence_root, "ics", "--credential", "nope",
                       "--out", os.path.join(self.dir, "x.ics")])
        self.assertEqual(rc, 2)


class TestIcsOverHTTP(WebCase):

    def test_route_serves_a_calendar(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        status, body, headers = self.get("/deadlines.ics")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/calendar"))
        self.assertIn("attachment", headers["Content-Disposition"])
        cal = parse(body.decode("utf-8"))
        self.assertTrue(any(props(ev, "SUMMARY") == ["CISSP: cycle ends"]
                            for ev in events_of(cal)))

    def test_credential_filter_and_404(self):
        self.led.set_credential_state("cissp", anchor_date="2025-09-01")
        self.led.set_credential_state("ceh", anchor_date="2025-01-01")
        _s, body, _h = self.get("/deadlines.ics?credential=ceh")
        cal = parse(body.decode("utf-8"))
        self.assertTrue(all(props(ev, "SUMMARY")[0].startswith("CEH")
                            for ev in events_of(cal)))
        status, _b, _h = self.get("/deadlines.ics?credential=nope", expect=404)
        self.assertEqual(status, 404)

    def test_empty_ledger_is_a_valid_empty_calendar(self):
        _s, body, _h = self.get("/deadlines.ics")
        cal = parse(body.decode("utf-8"))
        self.assertEqual(events_of(cal), [])


if __name__ == "__main__":
    unittest.main()

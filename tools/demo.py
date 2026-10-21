#!/usr/bin/env python3
"""Build a demo ledger, or a clean empty one.

Every activity in `fixtures/sample-history.csv` is invented and every row
says so: titles begin with "SAMPLE —" and providers are named "Example ..."
or "Placeholder ...". **None of it is anyone's real CPE history.** That is not
squeamishness — a demo dataset that looked plausible would eventually get
mistaken for the record, and the record is what a certifying body audits.

    python3 tools/demo.py --db var/demo.db            # sample data
    python3 tools/demo.py --db var/empty.db --empty   # schema only

The empty database is not an afterthought. Most of this app's screens are
easy to make look good with data in them; the state a new user will actually
see first is the one with nothing in it, and `--empty` is how that state gets
looked at before it ships.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpebot import backfill as BF       # noqa: E402
from cpebot import bodies as B          # noqa: E402
from cpebot import ledger as L          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(os.path.dirname(HERE), "fixtures", "sample-history.csv")

# Anchors chosen so the demo shows every state at once: one credential
# comfortably on pace, one behind a category floor while ahead on total
# hours, one with an annual submission window about to close, one advisory
# because its rules could not be verified.
ANCHORS = {
    "cissp": "2025-09-01",
    "cysa_plus": "2025-06-15",
    "ceh": "2025-01-01",
    "gcitp": "2025-08-01",
    "atap_membership": "2026-01-01",
    "ctm": "2025-01-01",
    "cfe": "2026-01-01",
}

FEES = [
    ("cissp", "ISC2 annual maintenance fee", 135.00, "2026-09-01", None),
    ("cfe", "ACFE membership renewal", 250.00, "2026-12-31", None),
    ("atap_membership", "ATAP membership dues", 100.00, "2026-01-15",
     "2026-01-12"),
]

EVIDENCE = b"SAMPLE certificate of completion. Not a real document.\n"


def build(db_path, empty=False, confirm=True):
    evidence_root = os.path.join(os.path.dirname(os.path.abspath(db_path)),
                                 "evidence")
    led = L.Ledger(db_path, evidence_root)
    led.init_schema()
    if empty:
        return led, {"entries": 0, "empty": True}

    rules = B.load()
    for cred, anchor in ANCHORS.items():
        led.set_credential_state(cred, anchor_date=anchor, active=1)
    # A portal figure that deliberately disagrees with the ledger, because
    # the reconciliation row is only worth having if it can be non-zero.
    led.set_credential_state("cissp", portal_credits=96,
                             portal_checked="2026-08-20")
    for cred, name, amount, due, paid in FEES:
        led.add_fee(cred, name, amount_usd=amount, due_date=due, paid_on=paid)

    with open(CSV, encoding="utf-8") as fh:
        result = BF.import_csv(fh.read(), rules, led, confirm=confirm)

    # One capture left deliberately unconfirmed, so the demo opens with
    # something in the queue, and one confirmed entry left without evidence,
    # so the packet has a real gap to report.
    pending = led.add_entry(
        activity_date="2026-08-28",
        title="SAMPLE — Awaiting confirmation: vendor briefing",
        provider="Placeholder Vendor", hours=2, tags=["training"],
        markers=["domain_security"], capture_source="telegram",
        is_real=False, notes="Fixture row; not counted as a real capture.")

    confirmed = [r for r in led.list_entries(status=L.CONFIRMED)]
    for row in confirmed[:6]:
        led.add_evidence(row["uuid"], "sample-certificate.txt", EVIDENCE,
                         mime="text/plain", note="Fixture evidence")
    if confirmed:
        led.mark_submitted(confirmed[-1]["uuid"], "eccouncil")

    return led, {"entries": len(led.list_entries()),
                 "imported": result.get("imported", 0),
                 "pending": pending, "empty": False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="var/demo.db")
    ap.add_argument("--empty", action="store_true",
                    help="schema only — the first-run state")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing database")
    args = ap.parse_args()

    path = os.path.abspath(args.db)
    if os.path.exists(path):
        if not args.force:
            sys.stderr.write("%s exists; pass --force to overwrite\n" % path)
            return 1
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.remove(path + suffix)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    led, info = build(path, empty=args.empty)
    print("built %s" % path)
    for k in sorted(info):
        print("  %-10s %s" % (k, info[k]))
    print("\nrun the app against it with:")
    print("  CPEBOT_DB=%s CPEBOT_BIND=127.0.0.1 python3 -m cpebot.web" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

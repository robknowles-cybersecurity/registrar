"""Build the demo ledger with the Python app and print its JSON export.

Also prints, under "evidence_files", each evidence file's bytes (base64) so a
test can attach them the way a user would. With --check FILE, reads an
export written by the browser app and checks it with the Python side: every
entry has exactly the Python export's keys, values have the Python types,
and the Python engine's totals for those entries are printed for comparison.
"""

import base64
import json
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))

from cpebot import bodies as B     # noqa: E402
from cpebot import engine as E     # noqa: E402
from cpebot import exports as X    # noqa: E402
from cpebot import ledger as L     # noqa: E402
import demo                        # noqa: E402


def build():
    tmp = tempfile.mkdtemp(prefix="registrar-webapp-")
    try:
        led, _ = demo.build(os.path.join(tmp, "demo.db"))
        doc = json.loads(X.entries_json(led))
        files = {}
        for row in led.list_evidence():
            with open(led.evidence_path(row), "rb") as fh:
                files[row["sha256"]] = base64.b64encode(fh.read()).decode()
        state = list(led.all_credential_state().values())
        print(json.dumps({"export": doc, "evidence_files": files,
                          "credential_state": state}))
    finally:
        shutil.rmtree(tmp)


def check(path):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="registrar-webapp-")
    try:
        led = L.Ledger(os.path.join(tmp, "x.db"), os.path.join(tmp, "ev"))
        led.add_entry("2026-01-01", "probe")
        probe = json.loads(X.entries_json(led))["entries"][0]
    finally:
        shutil.rmtree(tmp)
    want = list(probe.keys())
    problems = []
    if doc.get("format") != "cpebot-ledger-v0.2":
        problems.append("format is %r" % doc.get("format"))
    for i, e in enumerate(doc["entries"]):
        if list(e.keys()) != want:
            problems.append("entries[%d] keys %s != %s" % (i, list(e.keys()), want))
        for k, v in e.items():
            pv = probe[k]
            if k in ("applies_to", "confirmed_at", "supersedes", "cost_usd",
                     "voided_at"):
                continue
            if isinstance(pv, float) and isinstance(v, (int, float)):
                continue
            if type(v) is not type(pv):
                problems.append("entries[%d].%s is %s, Python has %s"
                                % (i, k, type(v).__name__, type(pv).__name__))
        for ev in e["evidence"]:
            if sorted(ev) != sorted(["filename", "sha256", "bytes", "mime",
                                     "added_at", "note"]):
                problems.append("entries[%d].evidence keys %s" % (i, sorted(ev)))
    rules = B.load(os.path.join(REPO, "bodies.yaml"))
    anchors = {s["credential"]: s["anchor_date"]
               for s in doc.get("registrar", {}).get("credential_state", [])
               if s.get("anchor_date")}
    totals = {}
    for cid, a in E.assess_all(rules, doc["entries"], anchors,
                               as_of=sys.argv[3]).items():
        totals[cid] = {"awarded": a.awarded, "naive_total": a.naive_total,
                       "verdict": a.verdict}
    print(json.dumps({"problems": problems, "totals": totals}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        check(sys.argv[2])
    else:
        build()

"""Run the Python engine on cases from stdin (JSON) and print results (JSON).

Helper for engine_parity.test.mjs: the same entries go through cpebot/engine.py
and webapp/src/engine.js, and the test fails on any difference.
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from cpebot import bodies as B    # noqa: E402
from cpebot import engine as E    # noqa: E402

rules = B.load(os.path.join(REPO, "bodies.yaml"))
cases = json.load(sys.stdin)
out = []
for case in cases:
    cred = rules.get(case["credential"])
    try:
        a = E.assess(cred, case["entries"], anchor=case["anchor"],
                     as_of=case["as_of"], carryover_in=case["carryover_in"])
    except E.EngineError as exc:
        out.append({"error": str(exc)})
        continue
    d = a.as_dict()
    d["pace"] = E.pace(a)
    d["naive"] = E.naive_assess(cred, case["entries"], anchor=case["anchor"],
                                as_of=case["as_of"])
    out.append(d)
json.dump(out, sys.stdout)

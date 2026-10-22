"""Export bodies.yaml and packetmaps/ as one JSON file for the browser app.

The browser app must use the same rules as the Python app, so it never reads
YAML itself: this script loads both through the Python loaders (which
validate them and refuse anything malformed) and writes what they loaded.

    python3 webapp/tools/export_data.py OUT.json
"""

import datetime
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from cpebot import bodies as B    # noqa: E402
from cpebot import packets as P   # noqa: E402


def _plain(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# The research catalog is reference data in the browser app, as it is in the
# Python app. Only the fields the app shows are exported.
PROGRAM_KEYS = ("body_id", "body_name", "unit_label", "cycle_months",
                "verified", "verified_date", "source_url", "look_status",
                "checked_date", "landing")
CERT_KEYS = ("id", "name", "long_name", "status", "status_note",
             "cycle_total", "annual_minimum", "cycle_months", "engine_track",
             "verified", "verified_date", "source_url", "look_status")


def _program(raw):
    out = {k: raw.get(k) for k in PROGRAM_KEYS}
    out["certifications"] = [{k: c.get(k) for k in CERT_KEYS}
                             for c in raw.get("certifications") or []]
    return _plain(out)


def main(out_path):
    rules = B.load(os.path.join(REPO, "bodies.yaml"))
    maps = P.load_maps(os.path.join(REPO, "packetmaps"), rules)
    raw = rules.raw
    data = {
        "source": "bodies.yaml + packetmaps/ (exported by webapp/tools/export_data.py)",
        "schema_version": raw.get("schema_version"),
        "sweep_date": _plain(raw.get("sweep_date")),
        "tag_vocabulary": _plain(raw.get("tag_vocabulary") or []),
        "topic_markers": _plain(raw.get("topic_markers") or []),
        "credentials": _plain(raw.get("credentials") or []),
        "programs": [_program(p) for p in raw.get("programs") or []],
        "packetmaps": {mid: _plain(m.raw) for mid, m in sorted(maps.items())},
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s: %d credentials, %d programs, %d packet maps"
          % (out_path, len(data["credentials"]), len(data["programs"]),
             len(data["packetmaps"])))


if __name__ == "__main__":
    main(sys.argv[1])

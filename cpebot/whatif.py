"""What-if: where would a hypothetical activity actually land?

The naive answer — "8 hours is 8 CPE everywhere" — is exactly the arithmetic
this project exists to distrust. So the calculator does no arithmetic of its
own: it runs the real engine twice per credential, once without the
hypothetical entry and once with it, and reports the difference. Caps,
floors, group splits and overrides all bite here precisely because the same
code applies them, and the gap between the raw figure and the delta is shown
rather than smoothed over: forfeited credits are the product.
"""

import datetime

from . import engine as E


def dry_run(rules, led, activity, as_of=None):
    """Assess a hypothetical activity against every anchored credential.

    `activity`: {date, hours, count, tags, markers, applies_to, overrides}.
    Pure read — nothing is written to the ledger.
    """
    as_of = as_of or datetime.date.today()
    anchors = led.anchors()
    carry = led.carryover_in()
    feed = led.engine_entries()

    hypo = {
        "id": "hypothetical",
        "date": str(activity.get("date") or as_of),
        "title": activity.get("title") or "(hypothetical)",
        "provider": activity.get("provider") or "",
        "hours": float(activity.get("hours") or 0),
        "count": int(activity.get("count") or 1),
        "tags": list(activity.get("tags") or []),
        "markers": list(activity.get("markers") or []),
        "overrides": dict(activity.get("overrides") or {}),
        "applies_to": activity.get("applies_to"),
        "status": "confirmed",
    }

    rows = []
    for cred in rules.credentials:
        row = {"credential": cred.id, "name": cred.name,
               "unit": cred.unit_label, "skipped": None}
        rows.append(row)

        if hypo["applies_to"] is not None and cred.id not in hypo["applies_to"]:
            row["skipped"] = "excluded by the entry's applies-to list"
            continue
        anchor = anchors.get(cred.id)
        if not anchor:
            row["skipped"] = "no cycle anchor set"
            continue

        matches = cred.buckets_for(hypo["tags"], hypo["markers"])
        bucket = matches[0] if matches else None
        raw = (bucket.credits_for(hypo["hours"], hypo["count"],
                                  hypo["overrides"].get(cred.id))
               if bucket else 0.0)

        kw = dict(anchor=anchor, as_of=as_of,
                  carryover_in=float(carry.get(cred.id) or 0))
        try:
            base = E.assess(cred, feed, **kw)
            plus = E.assess(cred, feed + [hypo], **kw)
        except E.EngineError as exc:
            row["skipped"] = "engine refused: %s" % exc
            continue

        in_cycle = (base.cycle_start <= E.normalize(hypo)["date"]
                    < base.cycle_end)
        delta = round(plus.awarded - base.awarded, 4)
        row.update({
            "bucket": None if bucket is None else {
                "id": bucket.id, "name": bucket.name or bucket.id,
                "cap": bucket.cap,
                "needs_override": bool(bucket.value_is_override
                                       and cred.id not in hypo["overrides"]),
            },
            "in_cycle": in_cycle,
            "raw": round(raw, 4),
            "delta": delta,
            "forfeited": round(max(0.0, raw - delta), 4),
            "before": _pos(base),
            "after": _pos(plus),
            "groups_moved": _moved(base, plus),
            "floors_closed": sorted(set(base.unmet_floors)
                                    - set(plus.unmet_floors)),
            "advisory_only": plus.advisory_only,
        })
        if bucket is None:
            row["skipped"] = "no category claims this activity"
        elif not in_cycle:
            row["skipped"] = ("date falls outside the current cycle "
                              "(%s to %s)" % (base.cycle_start, base.cycle_end))
    return rows


def _pos(a):
    return {"awarded": round(a.awarded, 4),
            "total": round(a.total_with_carryover, 4),
            "required": a.required,
            "gap": round(a.gap, 4),
            "verdict": a.verdict,
            "unmet_floors": list(a.unmet_floors)}


def _moved(base, plus):
    before = dict((g["id"], g) for g in base.groups)
    out = []
    for g in plus.groups:
        b = before.get(g["id"])
        if b and round(g["awarded"] - b["awarded"], 4):
            out.append({"id": g["id"], "name": g["name"],
                        "before": b["awarded"], "after": g["awarded"],
                        "floor": g.get("floor"), "cap": g.get("cap")})
    return out

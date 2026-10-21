"""The cap-and-floor-aware credit engine.

This is the file the project exists for.

The naive way to track continuing education is `sum(hours)`. Every body in
bodies.yaml breaks that sum in a different direction:

  * CompTIA caps each activity category hard. Ten blog posts is 10 CEUs; the
    eleventh through the fortieth are worth exactly nothing.
  * ISC2 splits 120 CPEs into Group A (>= 90, domain-related) and Group B
    (<= 30, general professional). Thirty-five hours of Group B is thirty
    credits and five hours of nothing.
  * UMD's GCITP wants >= 60 of 100 PDUs from counterintelligence activity and
    allows at most 40 from everything else, plus a 45-PDU cap on special
    projects.
  * ACFE's CFE wants 20 CPE a year of which >= 10 must be fraud-related and
    >= 2 must be ethics — floors, not caps, and an hour can satisfy both.
  * EC-Council's CEH doesn't cap much, but credits earned in a calendar year
    must be REGISTERED by 1 February of the next one. Earned is not banked.
  * ATAP's CTM caps five subcategories independently, and cpebot does not
    know those numbers. That case gets its own verdict rather than a guess.

Every one of those failure modes has the same sign: the naive sum says you
are fine when you are not. So the engine reports both numbers — what you
actually earned, and what a naive tracker would have told you — and the gap
between them is the reason to run this instead of a spreadsheet.

Nothing here touches the database. `assess()` takes rules + a list of plain
dicts and returns a plain result object, which makes the golden tests in
tests/ pure arithmetic against hand-computed expectations.
"""

import datetime

# Verdicts.
COMPLIANT = "COMPLIANT"                        # cycle requirements already met
ON_PACE = "ON_PACE"                            # not done, but ahead of the line
BEHIND = "BEHIND"                              # not done, behind the line
SHORT = "SHORT"                                # cycle closed with requirements unmet
UNVERIFIED_CANNOT_ASSERT = "UNVERIFIED_CANNOT_ASSERT"  # rules unknown; advisory only

# Group semantics, derived from the shape of the credential's rules.
GROUP_NONE = "none"
GROUP_BUCKET = "bucket"                # buckets declare their group; a partition
GROUP_MARKER_PARTITION = "marker_partition"  # markers assign, with a residual group
GROUP_MARKER_OVERLAP = "marker_overlap"      # markers assign; an entry may join several


class EngineError(ValueError):
    pass


# --------------------------------------------------------------------------
# date helpers
# --------------------------------------------------------------------------

def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def add_months(d, months):
    """Date `months` later, clamped to the end of a short month."""
    total = (d.year * 12 + (d.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    day = d.day
    while True:
        try:
            return datetime.date(year, month, day)
        except ValueError:
            day -= 1


def cycle_window(cred, anchor, as_of=None):
    """The current cycle's [start, end) for a credential.

    `anchor` is the date the clock starts from: the certification date for an
    anniversary basis, any date inside the first cycle for a calendar basis.
    Cycles repeat from the anchor until one contains `as_of`.
    """
    anchor = _as_date(anchor)
    as_of = _as_date(as_of) or datetime.date.today()
    if anchor is None:
        raise EngineError("%s: no cycle anchor date" % cred.id)
    basis = (cred.cycle.get("basis") or "certification_anniversary").lower()
    months = cred.cycle_months

    if basis == "calendar_year" and months == 12:
        return datetime.date(as_of.year, 1, 1), datetime.date(as_of.year + 1, 1, 1)

    start = anchor
    if as_of < start:
        # An anchor in the future means the first cycle hasn't opened yet.
        return start, add_months(start, months)
    while True:
        end = add_months(start, months)
        if as_of < end:
            return start, end
        start = end


def cycle_end_with_grace(cred, cycle_end):
    return cycle_end + datetime.timedelta(days=int(cred.grace_period_days or 0))


# --------------------------------------------------------------------------
# entry normalisation
# --------------------------------------------------------------------------

def normalize(entry):
    """Coerce a ledger row / fixture dict into the shape the engine expects."""
    return {
        "id": entry.get("id"),
        "date": _as_date(entry.get("date") or entry.get("activity_date")),
        "title": entry.get("title") or "",
        "provider": entry.get("provider") or "",
        "hours": float(entry.get("hours") or 0),
        "count": int(entry.get("count") or 1),
        "tags": list(entry.get("tags") or []),
        "markers": list(entry.get("markers") or []),
        # {credential_id: credits} — forces a value the rules can't compute
        # (CompTIA's cert-exam bucket) or a body-published figure that differs
        # from the generic rate.
        "overrides": dict(entry.get("overrides") or {}),
        # None means "every credential this entry is eligible for". A list
        # restricts it — the holder's call at confirm time, not the engine's.
        "applies_to": entry.get("applies_to"),
        "status": entry.get("status") or "confirmed",
        "submitted_to": list(entry.get("submitted_to") or []),
        "evidence_count": int(entry.get("evidence_count") or 0),
    }


# --------------------------------------------------------------------------
# group semantics
# --------------------------------------------------------------------------

def group_mode(cred):
    if not cred.groups:
        return GROUP_NONE
    if any(b.group for b in cred.buckets):
        return GROUP_BUCKET
    if cred.group_marker_map:
        if any(v is None for v in cred.group_marker_map.values()):
            return GROUP_MARKER_PARTITION
        return GROUP_MARKER_OVERLAP
    return GROUP_NONE


def _groups_for(cred, mode, bucket, markers):
    """Which groups this award lands in. A list, because overlap is legal."""
    if mode == GROUP_NONE:
        return []
    if mode == GROUP_BUCKET:
        return [bucket.group] if bucket.group else []
    markers = set(markers or [])
    if mode == GROUP_MARKER_OVERLAP:
        return [
            gid for gid, marker in cred.group_marker_map.items()
            if marker and marker in markers
        ]
    # partition: first marker-mapped group that matches, else the residual
    residual = None
    for gid, marker in cred.group_marker_map.items():
        if marker is None:
            residual = gid
            continue
        if marker in markers:
            return [gid]
    return [residual] if residual else []


# --------------------------------------------------------------------------
# the assessment
# --------------------------------------------------------------------------

class Assessment(object):
    """Everything the dashboard, the report and the tests need, as plain data."""

    def __init__(self, cred):
        self.credential = cred.id
        self.name = cred.name
        self.body = cred.body_name
        self.unit_label = cred.unit_label
        self.cycle_start = None
        self.cycle_end = None
        self.grace_end = None
        self.as_of = None
        self.required = cred.cycle_total
        self.awarded = 0.0            # what actually counts, after caps
        self.naive_total = 0.0        # what a dumb sum would have said
        self.forfeited = 0.0          # naive_total - awarded, the cost of caps
        self.carryover_in = 0.0
        self.carryover_out = 0.0
        self.buckets = []             # per-bucket raw/awarded/forfeited/cap
        self.groups = []              # per-group awarded/floor/cap/shortfall
        self.unmet_floors = []
        self.annual = []              # per-year totals vs annual minimum/target
        self.submission = {}
        self.fees = list(cred.fees)
        self.verdict = None
        self.advisory_only = False
        self.unverified_reasons = cred.unverified_reasons()
        self.entries_counted = 0
        self.entries_ignored = 0      # eligible for nothing in this credential
        self.entries_pending = 0      # captured but not confirmed
        self.notes = []

    @property
    def gap(self):
        """Credits still needed. Never negative."""
        return max(0.0, self.required - (self.awarded + self.carryover_in))

    @property
    def total_with_carryover(self):
        return self.awarded + self.carryover_in

    def as_dict(self):
        d = dict(self.__dict__)
        d["cycle_start"] = self.cycle_start.isoformat() if self.cycle_start else None
        d["cycle_end"] = self.cycle_end.isoformat() if self.cycle_end else None
        d["grace_end"] = self.grace_end.isoformat() if self.grace_end else None
        d["as_of"] = self.as_of.isoformat() if self.as_of else None
        d["gap"] = self.gap
        d["total_with_carryover"] = self.total_with_carryover
        return d


def assess(cred, entries, anchor=None, as_of=None, cycle_start=None,
           carryover_in=0.0, count_pending=False):
    """Assess one credential against a list of entries.

    `entries` may be raw dicts or already-normalised ones. Entries outside the
    cycle window, or in a non-counting state, are excluded and counted in
    `entries_pending` / `entries_ignored` so the UI can say WHY a number is
    lower than the user expected.
    """
    as_of = _as_date(as_of) or datetime.date.today()
    a = Assessment(cred)
    a.as_of = as_of
    a.carryover_in = float(carryover_in or 0)

    if cycle_start is not None:
        start = _as_date(cycle_start)
        end = add_months(start, cred.cycle_months)
    else:
        start, end = cycle_window(cred, anchor, as_of)
    a.cycle_start, a.cycle_end = start, end
    a.grace_end = cycle_end_with_grace(cred, end)

    mode = group_mode(cred)
    a.notes.append("group semantics: %s" % mode)

    # ---- award each entry to at most one bucket -------------------------
    raw_by_bucket = {}
    awards = []       # (bucket, credits, entry)
    for row in entries:
        e = normalize(row)   # idempotent, so callers may pass either shape
        if e["status"] != "confirmed":
            if e["status"] in ("captured", "pending"):
                a.entries_pending += 1
                if not count_pending:
                    continue
            else:
                a.entries_ignored += 1
                continue
        if e["date"] is None or not (start <= e["date"] < end):
            continue
        if e["applies_to"] is not None and cred.id not in e["applies_to"]:
            continue
        matches = cred.buckets_for(e["tags"], e["markers"])
        if not matches:
            a.entries_ignored += 1
            continue
        bucket = matches[0]          # most specific first; never two at once
        credits = bucket.credits_for(
            e["hours"], e["count"], e["overrides"].get(cred.id)
        )
        if bucket.value_is_override and cred.id not in e["overrides"]:
            a.notes.append(
                "entry %s matched bucket %r, which has no computable rate and "
                "no override — counted as 0" % (e["id"], bucket.id)
            )
        raw_by_bucket[bucket.id] = raw_by_bucket.get(bucket.id, 0.0) + credits
        awards.append((bucket, credits, e))
        a.entries_counted += 1

    # ---- apply per-bucket caps ------------------------------------------
    # Caps are applied to the bucket TOTAL, then pushed back down onto the
    # individual awards pro rata, because group totals need to know which
    # credits survived.
    kept_ratio = {}
    for bucket in cred.buckets:
        raw = raw_by_bucket.get(bucket.id, 0.0)
        cap = None if bucket.cap is None else float(bucket.cap)
        awarded = raw if cap is None else min(raw, cap)
        kept_ratio[bucket.id] = (awarded / raw) if raw > 0 else 1.0
        if raw or awarded:
            a.buckets.append({
                "id": bucket.id,
                "name": bucket.name or bucket.id,
                "raw": round(raw, 4),
                "awarded": round(awarded, 4),
                "forfeited": round(raw - awarded, 4),
                "cap": cap,
                "cap_verified": bucket.cap_verified,
                "group": bucket.group,
                "at_cap": cap is not None and raw >= cap,
            })
    bucket_awarded_total = sum(b["awarded"] for b in a.buckets)
    a.naive_total = round(sum(c for _b, c, _e in awards), 4)

    # ---- group floors and caps ------------------------------------------
    group_raw = dict((g["id"], 0.0) for g in cred.groups)
    for bucket, credits, e in awards:
        surviving = credits * kept_ratio.get(bucket.id, 1.0)
        for gid in _groups_for(cred, mode, bucket, e["markers"]):
            if gid in group_raw:
                group_raw[gid] += surviving

    partition = mode in (GROUP_BUCKET, GROUP_MARKER_PARTITION)
    group_forfeit = 0.0
    for g in cred.groups:
        gid = g["id"]
        raw = group_raw.get(gid, 0.0)
        cap = None if g.get("cap") is None else float(g["cap"])
        floor = None if g.get("floor") is None else float(g["floor"])
        awarded = raw if cap is None else min(raw, cap)
        if cap is not None and not partition:
            # An overlapping group with a cap has no unambiguous meaning: the
            # same credit sits in two groups, so which copy is discarded? None
            # of the seven bodies does this. Refuse rather than invent.
            raise EngineError(
                "%s: group %r has a cap under overlapping-marker semantics; "
                "the rules file needs a partition to make that well-defined"
                % (cred.id, gid)
            )
        group_forfeit += raw - awarded
        row = {
            "id": gid,
            "name": g.get("name") or gid,
            "awarded": round(awarded, 4),
            "raw": round(raw, 4),
            "floor": floor,
            "cap": cap,
            "shortfall": round(max(0.0, floor - awarded), 4) if floor else 0.0,
            "at_cap": cap is not None and raw >= cap,
        }
        a.groups.append(row)
        if row["shortfall"] > 0:
            a.unmet_floors.append(gid)

    a.awarded = round(bucket_awarded_total - group_forfeit, 4)
    a.forfeited = round(a.naive_total - a.awarded, 4)

    # ---- annual minima ---------------------------------------------------
    if cred.annual_minimum is not None or cred.annual_target is not None:
        per_year = {}
        for bucket, credits, e in awards:
            per_year.setdefault(e["date"].year, 0.0)
            per_year[e["date"].year] += credits * kept_ratio.get(bucket.id, 1.0)
        for year in sorted(per_year):
            need = cred.annual_minimum
            target = cred.annual_target
            complete = year < as_of.year
            a.annual.append({
                "year": year,
                "awarded": round(per_year[year], 4),
                "minimum": need,
                "target": target,
                "target_advisory": bool(
                    cred.requirements.get("annual_target_advisory")
                ),
                "met": (need is None) or (per_year[year] >= need),
                "year_complete": complete,
            })

    # ---- submission window ----------------------------------------------
    a.submission = _submission_state(cred, awards, as_of)

    # ---- carryover -------------------------------------------------------
    if cred.carryover.get("allowed"):
        cap = cred.carryover.get("max")
        excess = max(0.0, a.awarded + a.carryover_in - a.required)
        out = excess if cap is None else min(excess, float(cap))
        if cred.carryover.get("conditional_on_floors") and a.unmet_floors:
            out = 0.0
            a.notes.append(
                "carryover withheld: floors unmet, and this body only lets you "
                "carry a surplus once the minimums are satisfied"
            )
        if cred.carryover.get("verified") is False:
            a.notes.append("carryover figure is UNVERIFIED — treat as advisory")
        a.carryover_out = round(out, 4)

    # ---- verdict ---------------------------------------------------------
    a.verdict, a.advisory_only = _verdict(cred, a, as_of)
    return a


def _submission_state(cred, awards, as_of):
    """For bodies with a registration window, what is due and by when."""
    sub = cred.submission or {}
    cadence = sub.get("cadence") or "continuous"
    state = {"cadence": cadence, "self_certified": bool(sub.get("self_certified"))}
    if cadence != "annual_window":
        state["deadlines"] = []
        return state
    window = sub.get("window") or {}
    reg_mmdd = window.get("register_by_mmdd") or "12-31"
    offset = int(window.get("register_by_year_offset") or 0)
    per_year = {}
    for _bucket, credits, e in awards:
        y = per_year.setdefault(e["date"].year, {"credits": 0.0, "unsubmitted": 0})
        y["credits"] += credits
        if cred.body_id not in (e["submitted_to"] or []):
            y["unsubmitted"] += 1
    out = []
    for year in sorted(per_year):
        month, day = (int(p) for p in reg_mmdd.split("-"))
        due = datetime.date(year + offset, month, day)
        out.append({
            "earned_year": year,
            "credits": round(per_year[year]["credits"], 4),
            "unsubmitted_entries": per_year[year]["unsubmitted"],
            "register_by": due.isoformat(),
            "days_left": (due - as_of).days,
            "overdue": as_of > due and per_year[year]["unsubmitted"] > 0,
        })
    state["deadlines"] = out
    return state


def pace(assessment):
    """Runway: where the line is today, and what the remaining rate must be."""
    a = assessment
    span = (a.cycle_end - a.cycle_start).days
    elapsed = max(0, min(span, (a.as_of - a.cycle_start).days))
    frac = (elapsed / float(span)) if span else 1.0
    expected = a.required * frac
    days_left = max(0, (a.cycle_end - a.as_of).days)
    months_left = days_left / 30.44
    return {
        "elapsed_days": elapsed,
        "total_days": span,
        "elapsed_fraction": round(frac, 4),
        "expected_by_now": round(expected, 2),
        "actual": a.total_with_carryover,
        "delta": round(a.total_with_carryover - expected, 2),
        "on_pace": a.total_with_carryover >= expected,
        "days_left": days_left,
        "gap": round(a.gap, 2),
        "per_month_needed": round(a.gap / months_left, 2) if months_left >= 1 else a.gap,
    }


def _verdict(cred, a, as_of):
    """COMPLIANT / ON_PACE / BEHIND / SHORT, or a refusal to assert."""
    if cred.caps_known is False:
        # The rules file admits it doesn't know this body's caps. Any verdict
        # would be a guess dressed as a compliance statement, and the whole
        # point of a compliance tool is that it does not do that.
        a.notes.append(
            "verdict withheld: per-category caps for %s are not encoded, so "
            "the totals below are an UPPER BOUND, not a compliance position"
            % cred.name
        )
        return UNVERIFIED_CANNOT_ASSERT, True

    floors_met = not a.unmet_floors
    annual_met = all(r["met"] for r in a.annual if r["year_complete"])
    total_met = a.total_with_carryover >= a.required

    if total_met and floors_met and annual_met:
        return COMPLIANT, False
    if as_of >= a.cycle_end:
        return SHORT, False
    return (ON_PACE if pace(a)["on_pace"] and floors_met else BEHIND), False


def assess_all(rules, entries, anchors, as_of=None, carryover_in=None,
               only=None):
    """Assess every credential (or `only`) and return {cred_id: Assessment}."""
    carryover_in = carryover_in or {}
    out = {}
    for cred in rules.credentials:
        if only and cred.id not in only:
            continue
        anchor = anchors.get(cred.id)
        if anchor is None:
            continue
        out[cred.id] = assess(
            cred, entries, anchor=anchor, as_of=as_of,
            carryover_in=float(carryover_in.get(cred.id) or 0),
        )
    return out


def naive_assess(cred, entries, anchor=None, as_of=None, cycle_start=None):
    """What a spreadsheet would say: sum the hours, ignore every rule.

    Kept in the shipped code rather than only in the tests because the
    dashboard shows both numbers side by side. The difference IS the product.
    """
    as_of = _as_date(as_of) or datetime.date.today()
    if cycle_start is not None:
        start = _as_date(cycle_start)
        end = add_months(start, cred.cycle_months)
    else:
        start, end = cycle_window(cred, anchor, as_of)
    total = 0.0
    for row in entries:
        e = normalize(row)   # idempotent, so callers may pass either shape
        if e["status"] != "confirmed" or e["date"] is None:
            continue
        if not (start <= e["date"] < end):
            continue
        if e["applies_to"] is not None and cred.id not in e["applies_to"]:
            continue
        matches = cred.buckets_for(e["tags"], e["markers"])
        if not matches:
            continue
        total += matches[0].credits_for(
            e["hours"], e["count"], e["overrides"].get(cred.id)
        )
    return round(total, 4)

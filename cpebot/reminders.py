"""Reminder policy: quiet when on pace, loud only when it matters.

The design constraint is not technical. A reminder system that speaks every
day gets muted, and a muted reminder system is worse than none, because it
looks like it is working. So the policy is subtractive — start from silence
and require a reason to speak:

  * **Quiet when on pace.** If the runway maths says you will make it at the
    current rate, there is nothing to say, and saying it anyway spends the
    credibility you need for the message that matters.
  * **At most one unsolicited message a day**, across every credential. When
    two want to speak, the more urgent one wins and the other is logged as
    suppressed, so the record shows what was withheld and why.
  * **Escalation only at T-365 / T-180 / T-90 / T-30, and only when behind.**
    A milestone that arrives while you are on pace passes in silence.
  * **Overdue submission windows always speak.** EC-Council's February
    registration deadline is the one failure that cannot be fixed later: the
    credits do not come back. That outranks the daily budget.

Everything here decides and logs. v0.1 does not send: `plan()` is pure, and
the only writer is `record()`. Wiring it to a channel is a deferred milestone
precisely so that shipping the maths does not also ship an outbound path.
"""

import datetime

from . import engine as E

MILESTONES = (365, 180, 90, 30)

# Higher wins when two messages compete for the single daily slot.
URGENCY = {
    "submission_overdue": 100,
    "floor_shortfall": 80,
    "milestone": 60,
    "fee_unpaid": 40,
    "evidence_gap": 20,
}

# "Log now, evidence later" is a first-class workflow, so an evidence-less
# confirmed entry is not nagged about immediately — it gets a week before it
# becomes a candidate line.
EVIDENCE_GRACE_DAYS = 7


def plan(rules, led, as_of=None):
    """Decide what WOULD be sent today. Pure: no writes, no sends."""
    as_of = as_of or datetime.date.today()
    anchors = led.anchors()
    carry = led.carryover_in()
    feed = led.engine_entries()
    candidates = []

    for cred in rules.credentials:
        anchor = anchors.get(cred.id)
        if not anchor:
            continue
        state = led.get_credential_state(cred.id) or {}
        if not state.get("active", 1):
            continue
        a = E.assess(cred, feed, anchor=anchor, as_of=as_of,
                     carryover_in=float(carry.get(cred.id) or 0))
        p = E.pace(a)

        # 1. An overdue registration window. Non-negotiable.
        for d in a.submission.get("deadlines", []):
            if d["overdue"]:
                candidates.append(_msg(
                    "submission_overdue", cred, a,
                    "%s %s earned in %d were due at the %s portal on %s and "
                    "are still unregistered. Those credits are lost if the "
                    "window has closed."
                    % (_n(d["credits"]), cred.unit_label, d["earned_year"],
                       cred.body_name, d["register_by"])))

        # 2. A floor that is behind ITS OWN pace line.
        #
        # Every floor is unmet on day one of a cycle, so "unmet" is not a
        # reason to speak — it is the normal state of a credential you have
        # three years to renew. What deserves a message is a floor falling
        # behind the pace it needs to close, because that is the failure the
        # headline total actively hides: you can be comfortably ahead on
        # hours and still be certain to fail on categories.
        behind_floors = [] if a.advisory_only else _behind_floors(a, p)
        if behind_floors:
            names = ", ".join(g["name"] for g in behind_floors)
            short = ", ".join(
                "%s at %s of a required %s" % (g["name"], _n(g["awarded"]),
                                               _n(g["floor"]))
                for g in behind_floors)
            candidates.append(_msg(
                "floor_shortfall", cred, a,
                "%s has a category minimum you are not on track for: %s. "
                "General hours will not fix this — %s specifically."
                % (cred.name, short, names)))

        # 3. Milestones, but only when behind.
        days_left = p["days_left"]
        for m in MILESTONES:
            if days_left == m and not p["on_pace"]:
                candidates.append(_msg(
                    "milestone", cred, a,
                    "T-%d on %s: %s of %s %s, %s short, %s/month needed for "
                    "the remaining %d days."
                    % (m, cred.name, _n(a.total_with_carryover),
                       _n(a.required), cred.unit_label, _n(a.gap),
                       _n(p["per_month_needed"]), days_left)))

        # 4. Unpaid fees inside the renewal window.
        for f in led.list_fees(cred.id):
            if f["paid_on"] or not f["due_date"]:
                continue
            due = datetime.date.fromisoformat(f["due_date"])
            if 0 <= (due - as_of).days <= 30:
                candidates.append(_msg(
                    "fee_unpaid", cred, a,
                    "%s: %s ($%s) is due %s and is not marked paid."
                    % (cred.name, f["name"], _n(f["amount_usd"]), f["due_date"])))

    # 5. Confirmed entries still carrying no evidence, once the grace
    # window has passed. One aggregated line, lowest urgency: ACFE's
    # three-year retention rule makes the gap real, but it never outranks
    # money or deadlines for the daily slot.
    cutoff = str(as_of - datetime.timedelta(days=EVIDENCE_GRACE_DAYS))
    bare = sorted((e for e in feed
                   if not e["evidence_count"] and e["date"] <= cutoff),
                  key=lambda e: e["date"])
    if bare:
        titles = ", ".join("%s (%s)" % (e["title"], e["date"])
                           for e in bare[:3])
        more = "" if len(bare) <= 3 else ", +%d more" % (len(bare) - 3)
        candidates.append({
            "kind": "evidence_gap", "credential": "", "name": "",
            "urgency": URGENCY["evidence_gap"], "verdict": "",
            "body": "%d confirmed entr%s ha%s no evidence attached: %s%s. "
                    "Attach the certificate while you can still find it — "
                    "the audit rule is years of retention, not weeks."
                    % (len(bare), "y" if len(bare) == 1 else "ies",
                       "s" if len(bare) == 1 else "ve", titles, more),
        })

    candidates.sort(key=lambda c: (-URGENCY[c["kind"]], c["credential"]))

    # Budget: submission_overdue bypasses it; everything else shares one slot.
    spent = led.unsolicited_today(as_of)
    send, suppress = [], []
    for c in candidates:
        if c["kind"] == "submission_overdue":
            send.append(c)
            continue
        if spent + len([s for s in send if s["kind"] != "submission_overdue"]) >= 1:
            suppress.append(dict(c, reason="daily unsolicited budget spent"))
        else:
            send.append(c)
    if not candidates:
        suppress.append({
            "kind": "none", "credential": "", "urgency": 0, "body": "",
            "reason": "on pace everywhere, nothing overdue — quiet by design",
        })
    return {"as_of": str(as_of), "send": send, "suppress": suppress,
            "budget_spent": spent}


def record(led, plan_result, channel="telegram"):
    """Write the plan to reminders_log. Still does not SEND anything.

    Rows are stamped with the PLAN's day, not the wall clock: plan() reads
    the daily budget for as_of, so recording under a different date would
    let a second plan for the same as_of speak again. (In production the two
    are the same day; in tests and backdated runs they are not.)
    """
    ts = "%s %s" % (plan_result["as_of"],
                    datetime.datetime.now().strftime("%H:%M:%S"))
    for c in plan_result["send"]:
        led.log_reminder("unsolicited.%s" % c["kind"], credential=c["credential"],
                         channel=channel, body=c["body"], sent_at=ts)
    for c in plan_result["suppress"]:
        led.log_reminder("unsolicited.%s" % c["kind"], credential=c["credential"],
                         channel=channel, body=c.get("body", ""),
                         suppressed=True, reason=c["reason"], sent_at=ts)
    return len(plan_result["send"])


def _behind_floors(assessment, pace):
    """Groups whose own progress is behind the fraction of cycle elapsed.

    The same pace line the headline number gets, applied per group. A group
    sitting at 60 of 90 two-thirds of the way through is fine and silent; a
    group at 0 of 90 at the same point is not, even when the total looks
    healthy — and that gap is the whole reason this tool exists.
    """
    frac = pace["elapsed_fraction"]
    out = []
    for g in assessment.groups:
        if not g["floor"] or not g["shortfall"]:
            continue
        if g["awarded"] < g["floor"] * frac:
            out.append(g)
    return out


def _msg(kind, cred, assessment, body):
    return {"kind": kind, "credential": cred.id, "name": cred.name,
            "urgency": URGENCY[kind], "body": body,
            "verdict": assessment.verdict}


def _n(value):
    if value is None:
        return "—"
    return ("%.2f" % float(value)).rstrip("0").rstrip(".")

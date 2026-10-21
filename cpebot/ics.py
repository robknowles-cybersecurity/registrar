"""Deadline calendar: every date that can cost you a credential, as .ics.

Reminders (reminders.py) decide when to SPEAK and are built to stay quiet.
A calendar is the other half: the dates themselves, sitting in the calendar
you already look at, so the T-30 message is never the first you hear of it.

What goes in, per anchored and active credential, for the current cycle:

  * **cycle end** — the last day of the cycle, the day credits must be in by;
  * **grace end** — where the body publishes one, with the body's own note
    (ACFE's "grace" is an extension you must REQUEST, and the event says so);
  * **registration windows** — EC-Council's credits-earned-in-year-N must be
    registered by 1 February of N+1, one event per calendar year in the cycle;
  * **mid-cycle minimums** — an annual minimum inside a multi-year cycle gets
    a year-end event (a minimum that coincides with cycle end is not repeated);
  * **fees** — unpaid fees recorded in the ledger, by their due date. Where a
    credential has NO recorded fees, the fees in bodies.yaml are placed by
    their cadence (annual: each cycle anniversary; per_cycle: cycle end) and
    marked as derived, because a date the holder never entered is an
    estimate and the event must not pretend otherwise.

Also: the previous cycle's grace end, when today sits inside it — that is
exactly the window in which the old cycle can still be rescued.

Only events on or after `as_of` are emitted. Overdue items are the reminder
system's job; a calendar full of past dates trains you to ignore it.

The output is RFC 5545: CRLF line ends, lines folded at 75 octets without
splitting a UTF-8 sequence, TEXT values escaped, all-day events as
DTSTART/DTEND VALUE=DATE, and a UID per event that is stable across exports
(uuid5 of what the event IS, not when it was exported), so re-importing an
updated file updates events instead of duplicating them.
"""

import datetime
import uuid

from . import __version__ as VERSION
from . import engine as E

# A fixed namespace so the same deadline always gets the same UID.
UID_NAMESPACE = uuid.UUID("6f1d3b0e-8c4e-5a7b-9d21-4e0c2f6a9b13")

PRODID = "-//ceu.tools//Registrar %s//EN" % VERSION
CAL_NAME = "Registrar deadlines"

# Days before the event that the calendar client should alert. One alarm per
# event, on purpose: the reminder policy's rule against nagging applies here.
ALARM_DAYS = {
    "cycle_end": 30,
    "grace_end": 14,
    "registration": 30,
    "annual_minimum": 30,
    "fee": 14,
}


def _last_day(end):
    """Cycles are [start, end); the last day credits count is end - 1."""
    return end - datetime.timedelta(days=1)


def _event(cred, kind, date, summary, lines):
    key = "|".join([cred.id, kind, date.isoformat(), summary])
    desc = list(lines)
    if not cred.is_fully_verified:
        desc.append("UNVERIFIED: some of %s's rules could not be confirmed "
                    "against a primary source. Check the date in the "
                    "body's portal." % cred.name)
    if cred.source_url:
        desc.append("Rules source: %s (verified %s)"
                    % (cred.source_url, cred.verified_date or "—"))
    desc.append("Computed by Registrar from your recorded cycle anchor and "
                "bodies.yaml. The body's portal is authoritative.")
    return {
        "uid": "%s" % uuid.uuid5(UID_NAMESPACE, key),
        "credential": cred.id,
        "name": cred.name,
        "kind": kind,
        "date": date,
        "summary": summary,
        "description": "\n".join(desc),
        "alarm_days": ALARM_DAYS.get(kind),
    }


def _grace_summary(cred):
    # A body note on the grace period means it is conditional (ACFE's is an
    # extension you must ask for), so the title must not promise it.
    if cred.grace_note:
        return "%s: grace window ends (conditional, see note)" % cred.name
    return "%s: grace period ends" % cred.name


def _derived_note(cadence, fees):
    listed = ", ".join(
        "%s%s" % (f.get("name"), "" if f.get("amount_usd") is None
                  else " $%s" % _n(f["amount_usd"])) for f in fees)
    return ("DERIVED, not recorded: placed by the %s cadence in bodies.yaml "
            "(%s). Record the real due date in Settings and this estimate "
            "is replaced." % (cadence, listed))


def _anniversaries(start, end, months=12):
    """start, start+12m, ... strictly before end."""
    out, k = [], 0
    while True:
        d = E.add_months(start, months * k)
        if d >= end:
            return out
        out.append(d)
        k += 1


def credential_events(cred, anchor, as_of, recorded_fees=()):
    """Every deadline for one credential's current cycle (unfiltered by date)."""
    start, end = E.cycle_window(cred, anchor, as_of)
    last = _last_day(end)
    unit = cred.unit_label
    out = []

    out.append(_event(
        cred, "cycle_end", last,
        "%s: cycle ends" % cred.name,
        ["Last day of the %s (%s) cycle %s to %s. Anything earned after "
         "today counts toward the next cycle."
         % (cred.name, cred.body_name, start, last),
         "Requirement: %s %s for the cycle."
         % (_n(cred.cycle_total), unit)]))

    grace = int(cred.grace_period_days or 0)
    if grace:
        lines = ["%d-day grace after the %s cycle ending %s."
                 % (grace, cred.name, last)]
        if cred.grace_note:
            lines.append(cred.grace_note)
        out.append(_event(cred, "grace_end",
                          E.cycle_end_with_grace(cred, end)
                          - datetime.timedelta(days=1),
                          _grace_summary(cred), lines))
        # The previous cycle's grace, if today is still inside it.
        if start > E._as_date(anchor):
            prev_start, prev_end = E.cycle_window(
                cred, anchor, start - datetime.timedelta(days=1))
            prev_grace_last = (E.cycle_end_with_grace(cred, prev_end)
                               - datetime.timedelta(days=1))
            if prev_grace_last >= as_of:
                out.append(_event(
                    cred, "grace_end", prev_grace_last,
                    _grace_summary(cred) + " (previous cycle)",
                    ["The %s cycle %s to %s has closed; its %d-day grace "
                     "runs to this date." % (cred.name, prev_start,
                                             _last_day(prev_end), grace)]
                    + ([cred.grace_note] if cred.grace_note else [])))

    sub = cred.submission or {}
    if sub.get("cadence") == "annual_window":
        window = sub.get("window") or {}
        month, day = (int(p) for p in
                      (window.get("register_by_mmdd") or "12-31").split("-"))
        offset = int(window.get("register_by_year_offset") or 0)
        for year in range(start.year, last.year + 1):
            due = datetime.date(year + offset, month, day)
            out.append(_event(
                cred, "registration", due,
                "%s: register %d %s with %s" % (cred.name, year, unit,
                                                cred.body_name),
                ["%s earned in calendar year %d must be registered by this "
                 "date. Unregistered credits for that year are lost."
                 % (unit, year)]
                + ([sub["note"]] if sub.get("note") else [])))

    if cred.annual_minimum is not None and cred.cycle_months > 12:
        for year in range(start.year, last.year + 1):
            year_end = datetime.date(year, 12, 31)
            if not (start <= year_end < last):
                continue
            out.append(_event(
                cred, "annual_minimum", year_end,
                "%s: %d annual minimum (%s %s)"
                % (cred.name, year, _n(cred.annual_minimum), unit),
                ["At least %s %s must be earned in calendar year %d, inside "
                 "the %s cycle ending %s."
                 % (_n(cred.annual_minimum), unit, year, cred.name, last)]))

    recorded_fees = list(recorded_fees or ())
    if recorded_fees:
        for f in recorded_fees:
            if f.get("paid_on") or not f.get("due_date"):
                continue
            amount = f.get("amount_usd")
            out.append(_event(
                cred, "fee", E._as_date(f["due_date"]),
                "%s: %s due%s" % (cred.name, f["name"],
                                  "" if amount is None
                                  else " ($%s)" % _n(amount)),
                ["Recorded in your ledger and not yet marked paid."]
                + ([f["note"]] if f.get("note") else [])))
    else:
        per_cycle = []
        for f in cred.fees or []:
            cadence = (f.get("cadence") or "").lower()
            if cadence == "per_cycle":
                per_cycle.append(f)
            elif cadence == "annual":
                for d in _anniversaries(start, end):
                    out.append(_event(
                        cred, "fee", d,
                        "%s: %s due (estimated)" % (cred.name, f.get("name")),
                        [_derived_note("annual", [f])]
                        + ([f["due_note"]] if f.get("due_note") else [])))
        if per_cycle:
            # Several per-cycle fees on one credential are alternative routes
            # to the same renewal (UMD: by PDUs, or by re-examination), so
            # they are one deadline, not several bills.
            name = (per_cycle[0].get("name") if len(per_cycle) == 1
                    else "renewal fee")
            out.append(_event(
                cred, "fee", last,
                "%s: %s due (estimated)" % (cred.name, name),
                [_derived_note("per_cycle", per_cycle)]
                + ["%s: %s" % (f.get("name"), f["due_note"])
                   for f in per_cycle if f.get("due_note")]))
    return out


def deadlines(rules, led, as_of=None, only=None):
    """All upcoming deadlines, sorted. Returns (events, skipped)."""
    as_of = E._as_date(as_of) or datetime.date.today()
    anchors = led.anchors()
    events, skipped = [], []
    for cred in rules.credentials:
        if only and cred.id not in only:
            continue
        anchor = anchors.get(cred.id)
        if not anchor:
            skipped.append({"credential": cred.id,
                            "reason": "no cycle anchor date recorded"})
            continue
        state = led.get_credential_state(cred.id) or {}
        if not state.get("active", 1):
            skipped.append({"credential": cred.id, "reason": "inactive"})
            continue
        try:
            evs = credential_events(cred, anchor, as_of,
                                    led.list_fees(cred.id))
        except E.EngineError as exc:
            skipped.append({"credential": cred.id, "reason": str(exc)})
            continue
        events.extend(e for e in evs if e["date"] >= as_of)
    seen, unique = set(), []
    for e in sorted(events, key=lambda e: (e["date"], e["credential"],
                                           e["kind"], e["summary"])):
        if e["uid"] in seen:
            continue
        seen.add(e["uid"])
        unique.append(e)
    return unique, skipped


# ---------------------------------------------------------------------------
# RFC 5545 serialisation
# ---------------------------------------------------------------------------

def escape_text(value):
    """RFC 5545 3.3.11 TEXT escaping."""
    return (str(value).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\n").replace("\r", "\n")
            .replace("\n", "\\n"))


def fold(line, limit=75):
    """RFC 5545 3.1: fold at `limit` octets, never inside a UTF-8 sequence."""
    raw = line.encode("utf-8")
    if len(raw) <= limit:
        return line
    parts, chunk, size = [], [], 0
    budget = limit
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > budget:
            parts.append("".join(chunk))
            chunk, size = [], 0
            budget = limit - 1          # continuation lines start with a space
        chunk.append(ch)
        size += n
    parts.append("".join(chunk))
    return "\r\n ".join(parts)


def _date(d):
    return d.strftime("%Y%m%d")


def render(events, stamp=None, name=CAL_NAME):
    """Serialise events to an iCalendar string (CRLF line endings)."""
    stamp = stamp or datetime.datetime.now(datetime.timezone.utc)
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(datetime.timezone.utc)
    dtstamp = stamp.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:%s" % PRODID,
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "NAME:%s" % escape_text(name),
        "X-WR-CALNAME:%s" % escape_text(name),
    ]
    for e in events:
        lines += [
            "BEGIN:VEVENT",
            "UID:%s" % e["uid"],
            "DTSTAMP:%s" % dtstamp,
            "DTSTART;VALUE=DATE:%s" % _date(e["date"]),
            "DTEND;VALUE=DATE:%s" % _date(e["date"]
                                          + datetime.timedelta(days=1)),
            "SUMMARY:%s" % escape_text(e["summary"]),
            "DESCRIPTION:%s" % escape_text(e["description"]),
            "CATEGORIES:%s" % ",".join(escape_text(c) for c in
                                       ("Registrar", e["name"], e["kind"])),
            "TRANSP:TRANSPARENT",
        ]
        if e.get("alarm_days"):
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                "DESCRIPTION:%s" % escape_text(e["summary"]),
                "TRIGGER:-P%dD" % int(e["alarm_days"]),
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "".join(fold(l) + "\r\n" for l in lines)


def export(rules, led, as_of=None, only=None, stamp=None):
    """deadlines() + render(): the .ics text for a ledger."""
    events, _skipped = deadlines(rules, led, as_of=as_of, only=only)
    return render(events, stamp=stamp)


def _n(value):
    if value is None:
        return "—"
    return ("%.2f" % float(value)).rstrip("0").rstrip(".")

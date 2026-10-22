"""Renewal reports and submission packets.

The renewal report answers the question you actually have in December: "if I
submitted today, what happens?" That is seven questions, not one, and a
number on its own answers none of them:

  * credits earned vs the floors and caps that apply to them
  * the gap, and the runway left to close it
  * whether the evidence behind those credits still exists and still hashes
  * what you have already submitted vs what you have merely logged
  * what the body's portal thinks you have, and where that differs
  * what the fees are and whether they are paid
  * which of the above rests on a rule cpebot could not verify

The packet builder turns one credential's cycle into a folder you can hand to
an auditor: every evidence file, plus a manifest laid out in the fields that
body asks for. `manifest.md` is written for a human reading it under audit
conditions, which is why it leads with what is MISSING rather than burying it.
"""

import datetime
import os
import shutil
import zipfile

from . import engine as E


def _fmt(value, dash="—"):
    if value is None:
        return dash
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def _sub(cred, key, default=None):
    """Read a key out of a credential's `submission:` block.

    ACFE's three-year retention rule and its required-field list are
    properties of HOW you submit, so bodies.yaml keeps them inside
    `submission:` rather than loose at the top of the credential. Reading
    them from the wrong level fails silently as a None — which is exactly how
    a retention obligation would have quietly vanished from the packet.
    """
    return (cred.submission or {}).get(key, cred.raw.get(key, default))


# ---------------------------------------------------------------------------
# renewal report
# ---------------------------------------------------------------------------

def credential_report(rules, led, cred_id, as_of=None):
    """The full per-credential picture. Plain data; renderers do the prose."""
    as_of = as_of or datetime.date.today()
    cred = rules.get(cred_id)
    state = led.get_credential_state(cred_id) or {}
    anchor = state.get("anchor_date")
    if not anchor:
        return {
            "credential": cred_id, "name": cred.name, "body": cred.body_name,
            "incomplete": True,
            "reason": "no cycle anchor date recorded — set the certification "
                      "or membership date before this credential can be "
                      "assessed",
            "unverified_reasons": cred.unverified_reasons(),
        }

    feed = led.engine_entries()
    a = E.assess(cred, feed, anchor=anchor, as_of=as_of,
                 carryover_in=float(state.get("carryover_in") or 0))
    p = E.pace(a)
    counted = _entries_in_cycle(led, a, cred)

    # evidence completeness -------------------------------------------------
    with_evidence = [e for e in counted if e["evidence_count"] > 0]
    verification = led.verify_evidence()
    by_entry = {}
    for row in verification:
        by_entry.setdefault(row["entry_uuid"], []).append(row["verify"])
    in_cycle_uuids = set(e["uuid"] for e in counted)
    problems = [
        {"entry_uuid": u, "states": v}
        for u, v in by_entry.items()
        if u in in_cycle_uuids and any(s != "OK" for s in v)
    ]
    evidence = {
        "entries_counted": len(counted),
        "entries_with_evidence": len(with_evidence),
        "completeness_pct": round(
            100.0 * len(with_evidence) / len(counted), 1) if counted else 0.0,
        "files_checked": len([r for r in verification
                              if r["entry_uuid"] in in_cycle_uuids]),
        "failures": problems,
        "retention_years": _sub(cred, "evidence_retention_years"),
        "required_fields": _sub(cred, "evidence_required_fields") or [],
    }

    # submitted vs logged ---------------------------------------------------
    submitted = [e for e in counted if cred.body_id in (e["submitted_to"] or [])]
    submission = {
        "logged": len(counted),
        "submitted": len(submitted),
        "unsubmitted": len(counted) - len(submitted),
        "self_certified": bool((cred.submission or {}).get("self_certified")),
        "cadence": a.submission.get("cadence"),
        "deadlines": a.submission.get("deadlines", []),
    }

    # portal reconciliation -------------------------------------------------
    portal_credits = state.get("portal_credits")
    portal = {
        "portal_credits": portal_credits,
        "checked": state.get("portal_checked"),
        "ledger_credits": a.total_with_carryover,
        "diff": (None if portal_credits is None
                 else round(a.total_with_carryover - float(portal_credits), 2)),
    }
    if portal_credits is None:
        portal["note"] = ("no portal figure recorded — reconciliation is the "
                          "one check Registrar cannot do for you")
    elif portal["diff"]:
        portal["note"] = (
            "ledger and portal disagree by %s %s; the portal is authoritative "
            "for renewal, so find the missing entries before the deadline"
            % (_fmt(abs(portal["diff"])), cred.unit_label)
        )
    else:
        portal["note"] = "ledger and portal agree"

    # fees ------------------------------------------------------------------
    recorded = led.list_fees(cred_id)
    fees = {
        "defined": cred.fees,
        "recorded": recorded,
        "unpaid": [f for f in recorded if not f["paid_on"]],
    }

    return {
        "credential": cred_id,
        "name": cred.name,
        "body": cred.body_name,
        "unit_label": cred.unit_label,
        "as_of": str(as_of),
        "incomplete": False,
        "assessment": a.as_dict(),
        "pace": p,
        "evidence": evidence,
        "submission": submission,
        "portal": portal,
        "fees": fees,
        "grace_period_days": cred.grace_period_days,
        "grace_note": cred.grace_note,
        "unverified_reasons": a.unverified_reasons,
        "advisory_only": a.advisory_only,
        "source_url": cred.source_url,
        "verified_date": cred.verified_date,
    }


def _entries_in_cycle(led, assessment, cred):
    """The confirmed ledger rows inside this cycle that this credential uses."""
    start, end = assessment.cycle_start, assessment.cycle_end
    out = []
    for e in led.list_entries(status="confirmed", limit=100000):
        d = datetime.date.fromisoformat(e["activity_date"])
        if not (start <= d < end):
            continue
        if e["applies_to"] is not None and cred.id not in e["applies_to"]:
            continue
        if not cred.buckets_for(e["tags"], e["markers"]):
            continue
        out.append(e)
    counts = dict((r["entry_uuid"], 0) for r in led.list_evidence())
    for r in led.list_evidence():
        counts[r["entry_uuid"]] = counts.get(r["entry_uuid"], 0) + 1
    for e in out:
        e["evidence_count"] = counts.get(e["uuid"], 0)
    return out


def full_report(rules, led, as_of=None):
    as_of = as_of or datetime.date.today()
    return {
        "generated": str(as_of),
        "rules_sweep_date": rules.sweep_date,
        "rules_path": rules.path,
        "credentials": [credential_report(rules, led, c.id, as_of)
                        for c in rules.credentials],
        "capture_counts": led.capture_counts(),
    }


def render_markdown(report):
    """The renewal report as Markdown. Same content as the web page.

    Accepts either a `full_report` or a single `credential_report`. The web
    page renders one credential at a time and the export renders all of them;
    routing both through one renderer is what stops the page and the download
    from drifting into disagreement.
    """
    if "credentials" not in report:
        report = {"generated": report.get("as_of"), "rules_sweep_date": None,
                  "credentials": [report], "capture_counts": None}
    out = ["# CPE renewal report", "",
           "Generated %s from rules swept %s." %
           (report["generated"], _fmt(report["rules_sweep_date"])), ""]
    for r in report["credentials"]:
        out.append("## %s (%s)" % (r["name"], r["body"]))
        if r.get("incomplete"):
            out += ["", "**Not assessed.** %s" % r["reason"], ""]
            continue
        a, p = r["assessment"], r["pace"]
        out += [
            "",
            "| | |",
            "|---|---|",
            "| Verdict | **%s** |" % a["verdict"],
            "| Cycle | %s to %s |" % (a["cycle_start"], a["cycle_end"]),
            "| Earned | %s of %s %s |" % (_fmt(a["awarded"]),
                                          _fmt(a["required"]), r["unit_label"]),
            "| Carried in | %s |" % _fmt(a["carryover_in"]),
            "| Gap | %s |" % _fmt(a["gap"]),
            "| Forfeited to caps | %s |" % _fmt(a["forfeited"]),
            "| Naive sum would say | %s |" % _fmt(a["naive_total"]),
            "| Pace | %s (expected %s by now) |" % (
                "on pace" if p["on_pace"] else "behind", _fmt(p["expected_by_now"])),
            "| Runway | %d days, %s %s/month needed |" % (
                p["days_left"], _fmt(p["per_month_needed"]), r["unit_label"]),
            "",
        ]
        # "on pace" and "BEHIND" on adjacent rows reads as a contradiction
        # unless the reason is stated. It is the reason this tool exists: the
        # headline total can be comfortably ahead while a category minimum is
        # quietly failing, and the total is the number people look at.
        if p["on_pace"] and a["unmet_floors"]:
            out += ["> The total is ahead of pace, but this credential is %s "
                    "anyway: %s below its minimum. General hours do not fix a "
                    "category shortfall."
                    % (a["verdict"],
                       ", ".join(g["name"] for g in a["groups"]
                                 if g["id"] in a["unmet_floors"])),
                    ""]
        if a["groups"]:
            out += ["### Floors and caps", "",
                    "| Group | Earned | Floor | Cap | Shortfall |",
                    "|---|---|---|---|---|"]
            for g in a["groups"]:
                out.append("| %s | %s | %s | %s | %s |" % (
                    g["name"], _fmt(g["awarded"]), _fmt(g["floor"]),
                    _fmt(g["cap"]), _fmt(g["shortfall"], "-")))
            out.append("")
        if a["buckets"]:
            out += ["### By category", "",
                    "| Category | Raw | Counted | Lost to cap | Cap |",
                    "|---|---|---|---|---|"]
            for b in a["buckets"]:
                out.append("| %s | %s | %s | %s | %s |" % (
                    b["name"], _fmt(b["raw"]), _fmt(b["awarded"]),
                    _fmt(b["forfeited"], "-"), _fmt(b["cap"])))
            out.append("")
        ev = r["evidence"]
        out += ["### Evidence", "",
                "- %d of %d counted entries have evidence attached (%s%%)"
                % (ev["entries_with_evidence"], ev["entries_counted"],
                   _fmt(ev["completeness_pct"])),
                "- %d files re-hashed; %d failed verification"
                % (ev["files_checked"], len(ev["failures"]))]
        # Naming the failures is the point. "1 failed verification" tells you
        # nothing you can act on: ALTERED means the file on disk is no longer
        # the one whose hash was recorded, MISSING means it is gone entirely,
        # and those are different conversations with an auditor.
        for f in ev["failures"]:
            out.append("  - entry `%s`: %s" % (f["entry_uuid"][:8],
                                               ", ".join(sorted(set(f["states"])))))
        if ev["retention_years"]:
            out.append("- this body requires %s years of retention"
                       % ev["retention_years"])
        sub = r["submission"]
        out += ["", "### Submitted vs logged", "",
                "- %d of %d counted entries have been submitted to %s (%d outstanding)"
                % (sub["submitted"], sub["logged"], r["body"], sub["unsubmitted"])]
        for d in sub["deadlines"]:
            out.append("- %s credits earned in %d must be registered by %s%s"
                       % (_fmt(d["credits"]), d["earned_year"], d["register_by"],
                          " — **OVERDUE**" if d["overdue"] else ""))
        out += ["", "### Portal reconciliation", "",
                "- ledger: %s | portal: %s | difference: %s"
                % (_fmt(r["portal"]["ledger_credits"]),
                   _fmt(r["portal"]["portal_credits"]),
                   _fmt(r["portal"]["diff"])),
                "- %s" % r["portal"]["note"]]
        out += ["", "### Fees", ""]
        if not r["fees"]["defined"] and not r["fees"]["recorded"]:
            out.append("- none recorded")
        for f in r["fees"]["defined"]:
            out.append("- %s: $%s %s" % (f.get("name"), _fmt(f.get("amount_usd")),
                                         f.get("cadence") or ""))
        for f in r["fees"]["recorded"]:
            out.append("- %s: %s" % (f["name"],
                                     "paid %s" % f["paid_on"] if f["paid_on"]
                                     else "**UNPAID**, due %s" % _fmt(f["due_date"])))
        if r["unverified_reasons"]:
            out += ["", "### UNVERIFIED — check these numbers before relying on them", ""]
            for reason in r["unverified_reasons"]:
                out.append("- %s" % reason)
        out.append("")
    cc = report["capture_counts"]
    if cc is None:            # single-credential render; no all-credentials block
        return "\n".join(out)
    out += ["## Instrumentation", "",
            "%d real captures in the last %d days (threshold %d): %s"
            % (cc["real"], cc["window_days"], cc["threshold"],
               "BELOW THRESHOLD" if cc["falsified"] else "above threshold"),
            ""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# submission packet
# ---------------------------------------------------------------------------

DEFAULT_PACKET_FIELDS = ["course date", "course description",
                         "sponsoring organization", "credits earned"]


def packet_fields(cred):
    """The columns this body's portal asks for, when it says so."""
    return _sub(cred, "evidence_required_fields") or DEFAULT_PACKET_FIELDS


def _field_value(field, entry, cred, credits):
    f = field.lower()
    if "date" in f:
        return entry["activity_date"]
    if "description" in f or f in ("activity", "title", "course"):
        return entry["title"]
    if "organiz" in f or "organis" in f or "sponsor" in f or "provider" in f:
        return entry["provider"] or "—"
    if "credit" in f or "cpe" in f or "ceu" in f or "hour" in f:
        return _fmt(credits)
    if "name" in f:
        return "{attendee}"      # filled from config at build time
    return "—"


def build_packet(rules, led, cred_id, dest_dir, as_of=None, attendee=None):
    """Assemble an evidence folder + manifest.md for one credential's cycle.

    Returns the manifest text and a summary. The caller zips it (or not) —
    `zip_packet` does that and is what the web export uses.
    """
    as_of = as_of or datetime.date.today()
    cred = rules.get(cred_id)
    rep = credential_report(rules, led, cred_id, as_of)
    if rep.get("incomplete"):
        raise ValueError(rep["reason"])
    a = rep["assessment"]
    entries = _entries_in_cycle(led, _Rehydrated(a), cred)
    attendee = attendee or led.get_config("attendee_name") or "{attendee}"

    files_dir = os.path.join(dest_dir, "evidence")
    if not os.path.isdir(files_dir):
        os.makedirs(files_dir, mode=0o700)

    fields = packet_fields(cred)
    rows, missing, copied = [], [], 0
    for e in sorted(entries, key=lambda x: x["activity_date"]):
        credits = _credits_for_entry(cred, e)
        ev_rows = led.list_evidence(e["uuid"])
        names = []
        for ev in ev_rows:
            src = led.evidence_path(ev)
            if not os.path.isfile(src):
                missing.append({"entry": e["uuid"], "file": ev["filename"],
                                "why": "file missing from disk"})
                continue
            target_name = "%s_%s_%s" % (e["activity_date"], e["uuid"][:8],
                                        ev["filename"])
            shutil.copy2(src, os.path.join(files_dir, target_name))
            names.append(target_name)
            copied += 1
        if not ev_rows:
            missing.append({"entry": e["uuid"], "file": None,
                            "why": "no evidence attached to a counted entry"})
        row = [_field_value(f, e, cred, credits).replace("{attendee}", attendee)
               for f in fields]
        rows.append((row, names, e))

    manifest = _render_manifest(cred, rep, fields, rows, missing, as_of,
                                attendee, copied)
    with open(os.path.join(dest_dir, "manifest.md"), "w", encoding="utf-8") as fh:
        fh.write(manifest)
    return {"manifest": manifest, "entries": len(rows), "files": copied,
            "missing": missing, "dir": dest_dir}


class _Rehydrated(object):
    """Adapter so _entries_in_cycle can take an already-serialised assessment."""

    def __init__(self, d):
        self.cycle_start = datetime.date.fromisoformat(d["cycle_start"])
        self.cycle_end = datetime.date.fromisoformat(d["cycle_end"])


def _credits_for_entry(cred, entry):
    matches = cred.buckets_for(entry["tags"], entry["markers"])
    if not matches:
        return 0.0
    return matches[0].credits_for(entry["hours"], entry["count"],
                                  (entry["overrides"] or {}).get(cred.id))


def _render_manifest(cred, rep, fields, rows, missing, as_of, attendee, copied):
    a = rep["assessment"]
    out = [
        "# %s — submission packet" % cred.name,
        "",
        "%s | cycle %s to %s | prepared %s"
        % (cred.body_name, a["cycle_start"], a["cycle_end"], as_of),
        "",
        "Attendee: %s" % attendee,
        "",
        "## Position",
        "",
        "- **%s %s counted** against a requirement of %s"
        % (_fmt(a["awarded"]), cred.unit_label, _fmt(a["required"])),
        "- verdict: **%s**" % a["verdict"],
    ]
    if a["carryover_in"]:
        out.append("- carried in from the previous cycle: %s"
                   % _fmt(a["carryover_in"]))
    if a["forfeited"]:
        out.append("- %s %s were earned but not counted, because of this "
                   "body's category caps" % (_fmt(a["forfeited"]), cred.unit_label))
    for g in a["groups"]:
        if g["floor"]:
            out.append("- %s: %s of a required %s%s"
                       % (g["name"], _fmt(g["awarded"]), _fmt(g["floor"]),
                          "  **SHORT BY %s**" % _fmt(g["shortfall"])
                          if g["shortfall"] else ""))
    out.append("")

    # What is missing goes ABOVE the table. An auditor should not have to
    # discover a gap by reading to the end.
    if missing:
        out += ["## Gaps in this packet", "",
                "%d item(s) below are incomplete. Fix these before submitting."
                % len(missing), ""]
        for m in missing:
            out.append("- entry `%s`: %s" % (m["entry"][:8], m["why"]))
        out.append("")
    else:
        out += ["## Gaps in this packet", "",
                "None: every counted entry has at least one evidence file and "
                "every file was found on disk.", ""]

    if rep["unverified_reasons"]:
        out += ["## Rules that could not be verified", "",
                "The figures above depend on the following, which Registrar could "
                "not confirm against a primary source. Check them before you "
                "submit.", ""]
        for reason in rep["unverified_reasons"]:
            out.append("- %s" % reason)
        out.append("")

    out += ["## Activities", "",
            "Laid out in the fields %s asks for." % cred.body_name, "",
            "| " + " | ".join(f.title() for f in fields) + " | Evidence |",
            "|" + "---|" * (len(fields) + 1)]
    for row, names, _e in rows:
        out.append("| " + " | ".join(row) + " | "
                   + (", ".join("`%s`" % n for n in names) or "**none**") + " |")
    out += ["",
            "## Files",
            "",
            "%d file(s) in `evidence/`. Names are `date_entryid_originalname`."
            % copied,
            "",
            "Source: %s (verified %s)" % (cred.source_url or "—",
                                          cred.verified_date or "—"),
            ""]
    return "\n".join(out)


def zip_packet(rules, led, cred_id, out_path, workdir, as_of=None,
               attendee=None):
    """Build a packet and zip it. Returns the build summary."""
    summary = build_packet(rules, led, cred_id, workdir, as_of=as_of,
                           attendee=attendee)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(workdir):
            for name in sorted(files):
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, workdir))
    summary["zip"] = out_path
    summary["zip_bytes"] = os.path.getsize(out_path)
    return summary

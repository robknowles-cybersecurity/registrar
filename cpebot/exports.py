"""Exports: the ledger's contents in shapes other software will take.

Three consumers, three shapes:

* **The full-ledger dumps** (CSV and JSON) are the "walk away" format — every
  row in every state, superseded and void included, because an audit trail
  that exports only its flattering rows is not an audit trail. Status is a
  column; the reader filters.
* **The per-credential CSV** is this cycle's counted work with the bucket and
  credit figures the engine actually awarded.
* **The submission CSV** is shaped for a body's portal: the columns come from
  the body's own `evidence_required_fields` list in bodies.yaml, same as the
  packet manifest, so transcription into the portal is a copy, not a mapping
  exercise.
"""

import csv
import datetime
import io
import json

from . import report as R

ENTRY_COLUMNS = [
    "uuid", "status", "activity_date", "title", "provider", "hours", "count",
    "tags", "markers", "applies_to", "overrides", "notes", "capture_source",
    "capture_ref", "created_at", "confirmed_at", "voided_at", "supersedes",
    "submitted_to", "submitted_dates", "cost_usd", "evidence_count",
    "evidence_sha256s",
]


def entries_csv(led):
    """Every entry, every state. The audit-grade dump."""
    ev_by_entry = {}
    for row in led.list_evidence():
        ev_by_entry.setdefault(row["entry_uuid"], []).append(row["sha256"])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(ENTRY_COLUMNS)
    for e in led.list_entries(limit=1000000,
                              order="activity_date, created_at"):
        shas = ev_by_entry.get(e["uuid"], [])
        w.writerow([
            e["uuid"], e["status"], e["activity_date"], e["title"],
            e["provider"], e["hours"], e["count"],
            " ".join(e["tags"]), " ".join(e["markers"]),
            "" if e["applies_to"] is None else " ".join(e["applies_to"]),
            json.dumps(e["overrides"]) if e["overrides"] else "",
            e["notes"], e["capture_source"], e["capture_ref"],
            e["created_at"], e["confirmed_at"] or "", e["voided_at"] or "",
            e["supersedes"] or "",
            " ".join(e["submitted_to"]),
            json.dumps(e["submitted_dates"]) if e["submitted_dates"] else "",
            "" if e["cost_usd"] is None else e["cost_usd"],
            len(shas), " ".join(shas),
        ])
    return out.getvalue()


def entries_json(led):
    """The same dump, losslessly: entries with their evidence rows inline."""
    ev_by_entry = {}
    for row in led.list_evidence():
        ev_by_entry.setdefault(row["entry_uuid"], []).append({
            "filename": row["filename"], "sha256": row["sha256"],
            "bytes": row["bytes"], "mime": row["mime"],
            "added_at": row["added_at"], "note": row["note"]})
    entries = []
    for e in led.list_entries(limit=1000000,
                              order="activity_date, created_at"):
        d = dict(e)
        d["evidence"] = ev_by_entry.get(e["uuid"], [])
        d.pop("evidence_count", None)
        entries.append(d)
    return json.dumps({
        "exported_at": datetime.datetime.now().replace(
            microsecond=0).isoformat(" "),
        "format": "cpebot-ledger-v0.2",
        "entries": entries,
    }, indent=1)


def credential_csv(rules, led, cred_id, as_of=None):
    """This cycle's counted entries for one credential, with awarded bucket."""
    as_of = as_of or datetime.date.today()
    cred = rules.get(cred_id)
    rep = R.credential_report(rules, led, cred_id, as_of)
    if rep.get("incomplete"):
        raise ValueError(rep["reason"])
    a = R._Rehydrated(rep["assessment"])
    entries = R._entries_in_cycle(led, a, cred)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["activity_date", "title", "provider", "hours", "count",
                "bucket", "credits", "markers", "evidence_files",
                "submitted_to_%s" % cred.body_id, "submitted_on", "uuid"])
    for e in sorted(entries, key=lambda x: x["activity_date"]):
        matches = cred.buckets_for(e["tags"], e["markers"])
        bucket = matches[0] if matches else None
        w.writerow([
            e["activity_date"], e["title"], e["provider"], e["hours"],
            e["count"],
            "" if bucket is None else (bucket.name or bucket.id),
            R._credits_for_entry(cred, e),
            " ".join(e["markers"]),
            e.get("evidence_count", 0),
            "yes" if cred.body_id in (e["submitted_to"] or []) else "no",
            (e.get("submitted_dates") or {}).get(cred.body_id, ""),
            e["uuid"],
        ])
    return out.getvalue()


def submission_rows(rules, led, cred_id, as_of=None):
    """Unsubmitted confirmed entries in the current cycle, portal-ready.

    Returns (cred, fields, rows): `fields` are the body's own portal columns,
    each row is {entry, credits, values} where values line up with fields.
    """
    as_of = as_of or datetime.date.today()
    cred = rules.get(cred_id)
    rep = R.credential_report(rules, led, cred_id, as_of)
    if rep.get("incomplete"):
        raise ValueError(rep["reason"])
    a = R._Rehydrated(rep["assessment"])
    entries = R._entries_in_cycle(led, a, cred)
    attendee = led.get_config("attendee_name") or "{attendee}"
    fields = R.packet_fields(cred)
    rows = []
    for e in sorted(entries, key=lambda x: x["activity_date"]):
        if cred.body_id in (e["submitted_to"] or []):
            continue
        credits = R._credits_for_entry(cred, e)
        values = [R._field_value(f, e, cred, credits)
                  .replace("{attendee}", attendee) for f in fields]
        rows.append({"entry": e, "credits": credits, "values": values})
    return cred, fields, rows


def submission_csv(rules, led, cred_id, as_of=None):
    """The unsubmitted set as a CSV whose columns are the portal's own."""
    cred, fields, rows = submission_rows(rules, led, cred_id, as_of)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(fields + ["uuid"])
    for r in rows:
        w.writerow(r["values"] + [r["entry"]["uuid"]])
    return out.getvalue()

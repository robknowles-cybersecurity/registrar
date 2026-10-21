"""CSV backfill importer.

v0.1 starts with an empty ledger and a history that already happened. The
importer exists so the first renewal report is not a lie of omission.

Two properties matter:

* Backfilled rows are tagged `capture_source=backfill` and are NOT counted as
  real captures. The kill-switch metric asks "is the holder actually feeding this
  thing day to day", and answering it with a one-off spreadsheet import would
  be marking your own homework.

* Import does not confirm. Rows land in `captured` exactly like every other
  capture path, because a bulk import is the single most likely place for a
  duplicate to enter. `confirm=True` is available for a history you have
  already reconciled, and even then every soft duplicate is reported rather
  than waved through.

CSV columns (header row required; order irrelevant; unknown columns error):

    date         YYYY-MM-DD                       required
    title        free text                        required
    provider     free text                        recommended (dupe detection)
    hours        number                           for per-hour activities
    count        integer, default 1               for per-item activities
    tags         semicolon-separated tag ids      required
    markers      semicolon-separated marker ids
    applies_to   semicolon-separated credential ids; blank = all eligible
    overrides    cred_id=value;cred_id=value
    submitted_to semicolon-separated body ids
    notes        free text
    cost_usd     number
"""

import csv
import io

from . import ledger as L

COLUMNS = {
    "date", "title", "provider", "hours", "count", "tags", "markers",
    "applies_to", "overrides", "submitted_to", "notes", "cost_usd",
}
REQUIRED = {"date", "title", "tags"}


class BackfillError(ValueError):
    pass


def _split(value):
    if not value:
        return []
    return [p.strip() for p in str(value).replace(",", ";").split(";") if p.strip()]


def _overrides(value):
    out = {}
    for part in _split(value):
        if "=" not in part:
            raise BackfillError("override %r must look like cred_id=value" % part)
        k, v = part.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


def parse(text, rules):
    """Parse and validate CSV text. Returns (rows, errors) without writing."""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise BackfillError("empty CSV: a header row is required")
    header = set(f.strip() for f in reader.fieldnames if f)
    unknown = header - COLUMNS
    if unknown:
        raise BackfillError("unknown column(s): %s" % ", ".join(sorted(unknown)))
    missing = REQUIRED - header
    if missing:
        raise BackfillError("missing required column(s): %s"
                            % ", ".join(sorted(missing)))

    valid_tags = set(rules.tags)
    valid_markers = set(rules.markers)
    valid_creds = set(rules.ids())
    valid_bodies = set(b["id"] for b in rules.bodies())

    rows, errors = [], []
    for lineno, raw in enumerate(reader, start=2):
        row = dict((k.strip(), (v or "").strip())
                   for k, v in raw.items() if k)
        try:
            if not any(row.values()):
                continue
            date = L._validate_date(row.get("date"))
            title = row.get("title") or ""
            if not title:
                raise BackfillError("title is required")
            tags = _split(row.get("tags"))
            if not tags:
                raise BackfillError("at least one tag is required")
            bad = [t for t in tags if t not in valid_tags]
            if bad:
                raise BackfillError("unknown tag(s): %s" % ", ".join(bad))
            markers = _split(row.get("markers"))
            bad = [m for m in markers if m not in valid_markers]
            if bad:
                raise BackfillError("unknown marker(s): %s" % ", ".join(bad))
            applies_to = _split(row.get("applies_to")) or None
            if applies_to:
                bad = [c for c in applies_to if c not in valid_creds]
                if bad:
                    raise BackfillError("unknown credential(s): %s" % ", ".join(bad))
            submitted_to = _split(row.get("submitted_to"))
            bad = [b for b in submitted_to if b not in valid_bodies]
            if bad:
                raise BackfillError("unknown body id(s): %s" % ", ".join(bad))
            overrides = _overrides(row.get("overrides"))
            bad = [c for c in overrides if c not in valid_creds]
            if bad:
                raise BackfillError("override for unknown credential(s): %s"
                                    % ", ".join(bad))
            hours = float(row["hours"]) if row.get("hours") else 0.0
            count = int(row["count"]) if row.get("count") else 1
            if hours < 0 or count < 1:
                raise BackfillError("hours must be >= 0 and count >= 1")
            cost = float(row["cost_usd"]) if row.get("cost_usd") else None
            rows.append({
                "line": lineno, "activity_date": date, "title": title,
                "provider": row.get("provider", ""), "hours": hours,
                "count": count, "tags": tags, "markers": markers,
                "applies_to": applies_to, "overrides": overrides,
                "submitted_to": submitted_to, "notes": row.get("notes", ""),
                "cost_usd": cost,
            })
        except (BackfillError, ValueError, L.LedgerError) as exc:
            errors.append({"line": lineno, "error": str(exc), "row": row})
    return rows, errors


def import_csv(text, rules, led, dry_run=False, confirm=False):
    """Import CSV text into the ledger. Returns a report dict.

    On `dry_run` nothing is written and the report still lists what WOULD
    happen, including which rows would have tripped the duplicate check.
    """
    rows, errors = parse(text, rules)
    report = {
        "parsed": len(rows),
        "errors": errors,
        "imported": 0,
        "confirmed": 0,
        "duplicates_flagged": [],
        "dry_run": bool(dry_run),
        "uuids": [],
    }
    for row in rows:
        probe = dict(row)
        probe["uuid"] = None
        dupes = led.soft_duplicates(probe)
        if dupes:
            report["duplicates_flagged"].append({
                "line": row["line"],
                "title": row["title"],
                "date": row["activity_date"],
                "matches": [d["uuid"] for d in dupes],
            })
        if dry_run:
            continue
        u = led.add_entry(
            activity_date=row["activity_date"], title=row["title"],
            hours=row["hours"], count=row["count"], provider=row["provider"],
            tags=row["tags"], markers=row["markers"],
            applies_to=row["applies_to"], overrides=row["overrides"],
            submitted_to=row["submitted_to"], notes=row["notes"],
            capture_source="backfill", capture_ref="csv:line %d" % row["line"],
            cost_usd=row["cost_usd"], status=L.CAPTURED, is_real=False,
        )
        report["imported"] += 1
        report["uuids"].append(u)
        if confirm and not dupes:
            led.set_status(u, L.CONFIRMED)
            report["confirmed"] += 1
    return report


TEMPLATE = (
    "date,title,provider,hours,count,tags,markers,applies_to,overrides,"
    "submitted_to,notes,cost_usd\n"
    "2026-03-14,Threat modelling workshop,SANS,8,1,training,domain_security,"
    ",,,,\n"
)

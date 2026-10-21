"""The ledger: SQLite storage for entries, evidence, reminders and counters.

Three things about this file are deliberate and worth defending.

**Nothing auto-commits.** Every capture path — Telegram, the web form, the CSV
importer — writes a row in state `captured`. A captured row is invisible to
the credit engine. It becomes real only when a human confirms it, and the
confirm step is where the soft-duplicate check fires. A tracker that silently
records what it thinks it heard produces a compliance record nobody trusts,
and an untrusted compliance record is worth less than no record, because you
will still have to rebuild it by hand at renewal time and now you will do it
under time pressure.

**Evidence is hashed on the way in.** ACFE requires the CFE to self-certify
their CPE and to retain the supporting evidence for three years, which makes
this an audit trail rather than a convenience. Every file gets a sha256 at
upload, and `verify_evidence()` re-hashes what is on disk. Silent bit-rot in
the year-three certificate is exactly the failure you cannot detect at the
moment you need the file.

**Connections are short-lived.** WAL mode, one connection per request, no
pooling, no ORM. It survives reboots, it survives a second reader, and it
can be backed up by a file-level backup tool without an export step.
"""

import contextlib
import datetime
import hashlib
import json
import os
import re
import sqlite3
import uuid

DEFAULT_DB = os.environ.get(
    "CPEBOT_DB",
    os.path.join(os.path.expanduser("~"), "cpebot", "var", "cpebot.db"),
)
DEFAULT_EVIDENCE_ROOT = os.environ.get(
    "CPEBOT_EVIDENCE",
    os.path.join(os.path.expanduser("~"), "cpebot", "var", "evidence"),
)

# Confirm-state machine. Anything not in here is not a state.
CAPTURED = "captured"
CONFIRMED = "confirmed"
REJECTED = "rejected"
SUPERSEDED = "superseded"
VOID = "void"
STATES = (CAPTURED, CONFIRMED, REJECTED, SUPERSEDED, VOID)

TRANSITIONS = {
    CAPTURED: (CONFIRMED, REJECTED, VOID),
    CONFIRMED: (SUPERSEDED, REJECTED, VOID),
    REJECTED: (CAPTURED, VOID),     # re-open a mistaken rejection, or void it
    SUPERSEDED: (),                 # terminal: the replacement carries on
    # VOID is the "this row should never have existed as a credit claim"
    # terminal state: a test message, a fat-finger, a duplicate confirmed by
    # mistake. It stays on the books (supersede/redact, never delete), it
    # KEEPS its capture event (the kill-switch counter measures whether the
    # capture habit is real, and a voided capture was still a capture), and
    # it counts toward nothing else: not credits, not dupe matching, not
    # totals, not reports.
    VOID: (),
}

SOURCES = ("web", "telegram", "backfill", "import", "fixture")

# What the web boundary accepts as evidence. The ledger itself stores
# whatever a trusted local caller hands it (tests and fixtures attach text
# files); validate_upload() is the check the web layer applies to bytes that
# arrived over HTTP, where the filename is a claim and only the magic bytes
# are evidence.
MAX_EVIDENCE_BYTES = 15 * 1024 * 1024
EVIDENCE_TYPES = {
    ".pdf":  ("application/pdf", (b"%PDF-",)),
    ".png":  ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg":  ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
}


def sniff_evidence(data):
    """MIME type by magic bytes alone, or None. Never trusts a filename."""
    for mime, magics in EVIDENCE_TYPES.values():
        if any(data[:len(m)] == m for m in magics):
            return mime
    return None


def validate_upload(filename, data):
    """Boundary check for HTTP uploads: size cap, extension AND magic bytes.

    Returns the MIME type to record. Raises LedgerError naming exactly which
    check failed — the caller turns that into a 4xx, not a 500.
    """
    if not data:
        raise LedgerError("empty upload")
    if len(data) > MAX_EVIDENCE_BYTES:
        raise LedgerError(
            "evidence file too large: %.1f MB (cap %d MB)"
            % (len(data) / 1048576.0, MAX_EVIDENCE_BYTES // 1048576))
    ext = os.path.splitext(_safe_filename(filename))[1].lower()
    if ext not in EVIDENCE_TYPES:
        raise LedgerError("unsupported evidence type %r — PDF, PNG or JPEG only"
                          % (ext or filename))
    mime, magics = EVIDENCE_TYPES[ext]
    if not any(data[:len(m)] == m for m in magics):
        raise LedgerError(
            "file content does not match its %s extension" % ext)
    return mime

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS entries (
    uuid            TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    activity_date   TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    hours           REAL NOT NULL DEFAULT 0,
    count           INTEGER NOT NULL DEFAULT 1,
    tags            TEXT NOT NULL DEFAULT '[]',
    markers         TEXT NOT NULL DEFAULT '[]',
    applies_to      TEXT,                      -- NULL = every eligible credential
    overrides       TEXT NOT NULL DEFAULT '{}',
    submitted_to    TEXT NOT NULL DEFAULT '[]',
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'captured',
    capture_source  TEXT NOT NULL DEFAULT 'web',
    capture_ref     TEXT NOT NULL DEFAULT '',
    confirmed_at    TEXT,
    supersedes      TEXT,
    cost_usd        REAL,
    voided_at       TEXT,
    submitted_dates TEXT NOT NULL DEFAULT '{}'   -- {body_id: YYYY-MM-DD}
);
CREATE INDEX IF NOT EXISTS idx_entries_date   ON entries(activity_date);
CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status);

CREATE TABLE IF NOT EXISTS evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_uuid  TEXT NOT NULL REFERENCES entries(uuid) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,       -- relative to the evidence root
    sha256      TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    mime        TEXT NOT NULL DEFAULT '',
    added_at    TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_evidence_entry ON evidence(entry_uuid);

CREATE TABLE IF NOT EXISTS reminders_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    credential  TEXT NOT NULL DEFAULT '',
    channel     TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    suppressed  INTEGER NOT NULL DEFAULT 0,
    reason      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders_log(sent_at);

-- Kill-switch instrumentation. One append-only row per capture attempt that
-- reached the ledger, so "did anyone actually use this?" is a query and not
-- an opinion. `is_real` excludes fixtures and backfill.
CREATE TABLE IF NOT EXISTS capture_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    source      TEXT NOT NULL,
    entry_uuid  TEXT,
    is_real     INTEGER NOT NULL DEFAULT 1,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_capture_ts ON capture_events(ts);

CREATE TABLE IF NOT EXISTS config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

-- Per-credential enrolment facts: when the cycle clock started, what rolled
-- in from the last cycle. Kept in the ledger rather than bodies.yaml because
-- these are the holder's dates, not the body's rules.
CREATE TABLE IF NOT EXISTS credential_state (
    credential      TEXT PRIMARY KEY,
    anchor_date     TEXT,
    carryover_in    REAL NOT NULL DEFAULT 0,
    portal_credits  REAL,
    portal_checked  TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    note            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    credential  TEXT NOT NULL,
    name        TEXT NOT NULL,
    amount_usd  REAL,
    due_date    TEXT,
    paid_on     TEXT,
    note        TEXT NOT NULL DEFAULT ''
);
"""


def now():
    return datetime.datetime.now().replace(microsecond=0).isoformat(" ")


def new_uuid():
    return uuid.uuid4().hex


class LedgerError(ValueError):
    pass


class Ledger(object):
    def __init__(self, path=None, evidence_root=None):
        self.path = path or DEFAULT_DB
        self.evidence_root = evidence_root or DEFAULT_EVIDENCE_ROOT
        for d in (os.path.dirname(self.path), self.evidence_root):
            if d and not os.path.isdir(d):
                os.makedirs(d, mode=0o700)
        self.init_schema()

    # -- plumbing ----------------------------------------------------------

    @contextlib.contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def init_schema(self):
        with self.conn() as c:
            c.executescript(SCHEMA)
            # v0.1 -> v0.2 migration: additive columns only. A still-running
            # v0.1 process (the Telegram capture unit) keeps working against
            # the migrated file because every INSERT in this codebase names
            # its columns explicitly and both new columns carry defaults.
            cols = set(r["name"] for r in
                       c.execute("PRAGMA table_info(entries)").fetchall())
            if "voided_at" not in cols:
                c.execute("ALTER TABLE entries ADD COLUMN voided_at TEXT")
            if "submitted_dates" not in cols:
                c.execute("ALTER TABLE entries ADD COLUMN submitted_dates"
                          " TEXT NOT NULL DEFAULT '{}'")

    # -- entries -----------------------------------------------------------

    def add_entry(self, activity_date, title, hours=0.0, count=1, provider="",
                  tags=None, markers=None, applies_to=None, overrides=None,
                  notes="", capture_source="web", capture_ref="",
                  status=CAPTURED, cost_usd=None, submitted_to=None,
                  submitted_dates=None, record_capture=True, is_real=None):
        """Write a new entry. Defaults to `captured`, i.e. NOT yet counted."""
        if status not in STATES:
            raise LedgerError("unknown status %r" % status)
        if capture_source not in SOURCES:
            raise LedgerError("unknown capture_source %r" % capture_source)
        _validate_date(activity_date)
        u = new_uuid()
        ts = now()
        with self.conn() as c:
            c.execute(
                """INSERT INTO entries (uuid, created_at, updated_at,
                       activity_date, title, provider, hours, count, tags,
                       markers, applies_to, overrides, submitted_to,
                       submitted_dates, notes, status, capture_source,
                       capture_ref, confirmed_at, cost_usd)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (u, ts, ts, str(activity_date), title or "", provider or "",
                 float(hours or 0), int(count or 1),
                 json.dumps(list(tags or [])), json.dumps(list(markers or [])),
                 json.dumps(list(applies_to)) if applies_to is not None else None,
                 json.dumps(dict(overrides or {})),
                 json.dumps(list(submitted_to or [])),
                 json.dumps(dict(submitted_dates or {})), notes or "", status,
                 capture_source, capture_ref or "",
                 ts if status == CONFIRMED else None,
                 float(cost_usd) if cost_usd is not None else None),
            )
            if record_capture:
                real = is_real if is_real is not None else (
                    capture_source not in ("backfill", "fixture")
                )
                c.execute(
                    "INSERT INTO capture_events (ts, source, entry_uuid, is_real)"
                    " VALUES (?,?,?,?)",
                    (ts, capture_source, u, 1 if real else 0),
                )
        return u

    def get_entry(self, entry_uuid):
        with self.conn() as c:
            row = c.execute("SELECT * FROM entries WHERE uuid=?",
                            (entry_uuid,)).fetchone()
        return _row_to_entry(row) if row else None

    def list_entries(self, status=None, credential=None, since=None, until=None,
                     tag=None, marker=None, provider=None, q=None, limit=1000,
                     order="activity_date DESC, created_at DESC"):
        sql = ["SELECT * FROM entries WHERE 1=1"]
        args = []
        if status:
            if isinstance(status, str):
                status = [status]
            sql.append("AND status IN (%s)" % ",".join("?" * len(status)))
            args.extend(status)
        if since:
            sql.append("AND activity_date >= ?")
            args.append(str(since))
        if until:
            sql.append("AND activity_date <= ?")
            args.append(str(until))
        if provider:
            sql.append("AND lower(provider) LIKE ?")
            args.append("%%%s%%" % provider.lower())
        if q:
            sql.append("AND (lower(title) LIKE ? OR lower(notes) LIKE ?)")
            args.extend(["%%%s%%" % q.lower()] * 2)
        sql.append("ORDER BY %s LIMIT ?" % order)
        args.append(int(limit))
        with self.conn() as c:
            rows = c.execute(" ".join(sql), args).fetchall()
        out = [_row_to_entry(r) for r in rows]
        # tag / marker / credential filters are applied in Python because they
        # live in JSON columns; the row counts here are hundreds, not millions.
        if tag:
            out = [e for e in out if tag in e["tags"]]
        if marker:
            out = [e for e in out if marker in e["markers"]]
        if credential:
            out = [e for e in out
                   if e["applies_to"] is None or credential in e["applies_to"]]
        return out

    def update_entry(self, entry_uuid, **fields):
        """Update mutable fields. Status changes go through set_status()."""
        allowed = {
            "activity_date", "title", "provider", "hours", "count", "tags",
            "markers", "applies_to", "overrides", "submitted_to",
            "submitted_dates", "notes", "cost_usd",
        }
        bad = set(fields) - allowed
        if bad:
            raise LedgerError("not updatable here: %s" % ", ".join(sorted(bad)))
        e = self.get_entry(entry_uuid)
        if e is None:
            raise LedgerError("no such entry %r" % entry_uuid)
        # The audit-trail rule, enforced where it can't be forgotten: once an
        # entry is confirmed its content is a compliance record. Corrections
        # go through supersede(); the only in-place writes still allowed are
        # the submission bookkeeping fields, which describe what happened TO
        # the record, not what the record says.
        content = set(fields) - {"submitted_to", "submitted_dates"}
        if e["status"] == CONFIRMED and content:
            raise LedgerError(
                "confirmed entries are never edited in place — supersede() "
                "writes the corrected copy and keeps both")
        if e["status"] in (SUPERSEDED, VOID) and fields:
            raise LedgerError("%s entries are read-only" % e["status"])
        if "activity_date" in fields:
            _validate_date(fields["activity_date"])
        sets, args = [], []
        for k, v in fields.items():
            if k in ("tags", "markers", "submitted_to"):
                v = json.dumps(list(v or []))
            elif k == "submitted_dates":
                v = json.dumps(dict(v or {}))
            elif k == "overrides":
                v = json.dumps(dict(v or {}))
            elif k == "applies_to":
                v = json.dumps(list(v)) if v is not None else None
            elif k in ("hours", "cost_usd"):
                v = None if v is None else float(v)
            elif k == "count":
                v = int(v)
            sets.append("%s=?" % k)
            args.append(v)
        sets.append("updated_at=?")
        args.append(now())
        args.append(entry_uuid)
        with self.conn() as c:
            cur = c.execute("UPDATE entries SET %s WHERE uuid=?" % ", ".join(sets),
                            args)
            if cur.rowcount == 0:
                raise LedgerError("no such entry %r" % entry_uuid)
        return self.get_entry(entry_uuid)

    def set_status(self, entry_uuid, new_status, force=False):
        """Move an entry through the confirm-state machine, or refuse."""
        if new_status not in STATES:
            raise LedgerError("unknown status %r" % new_status)
        e = self.get_entry(entry_uuid)
        if e is None:
            raise LedgerError("no such entry %r" % entry_uuid)
        current = e["status"]
        if new_status != current and not force:
            if new_status not in TRANSITIONS.get(current, ()):
                raise LedgerError(
                    "illegal transition %s -> %s for %s" %
                    (current, new_status, entry_uuid)
                )
        ts = now()
        with self.conn() as c:
            c.execute(
                "UPDATE entries SET status=?, updated_at=?,"
                " confirmed_at=COALESCE(confirmed_at, ?),"
                " voided_at=COALESCE(voided_at, ?) WHERE uuid=?",
                (new_status, ts, ts if new_status == CONFIRMED else None,
                 ts if new_status == VOID else None, entry_uuid),
            )
        return self.get_entry(entry_uuid)

    def confirm(self, entry_uuid, force=False):
        """Confirm an entry. Refuses on a soft duplicate unless forced.

        The refusal is the feature: the moment you are most likely to log the
        same conference twice is the moment you are working through a backlog,
        which is also the moment you are least likely to notice.
        """
        e = self.get_entry(entry_uuid)
        if e is None:
            raise LedgerError("no such entry %r" % entry_uuid)
        dupes = self.soft_duplicates(e)
        if dupes and not force:
            raise SoftDuplicate(entry_uuid, dupes)
        return self.set_status(entry_uuid, CONFIRMED)

    def soft_duplicates(self, entry, hours_tolerance=0.25):
        """Confirmed entries with the same date and provider and similar hours.

        Same day + same provider + hours within +/-25% is the shape of a
        double-log. It is deliberately a FLAG and not a block: two sessions
        from the same provider on the same day is a normal conference.
        """
        if isinstance(entry, str):
            entry = self.get_entry(entry)
        provider = _norm_provider(entry.get("provider"))
        if not provider:
            return []
        hours = float(entry.get("hours") or 0)
        lo, hi = hours * (1 - hours_tolerance), hours * (1 + hours_tolerance)
        out = []
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM entries WHERE activity_date=? AND status=?"
                " AND uuid != ?",
                (str(entry["activity_date"]), CONFIRMED, entry.get("uuid") or ""),
            ).fetchall()
        for r in rows:
            other = _row_to_entry(r)
            if _norm_provider(other["provider"]) != provider:
                continue
            oh = float(other["hours"] or 0)
            if hours == 0 and oh == 0:
                out.append(other)
            elif lo <= oh <= hi:
                out.append(other)
        return out

    def supersede(self, entry_uuid, **fields):
        """Replace a confirmed entry with a corrected copy, keeping both.

        Compliance records get corrected, not overwritten — the same rule that
        applies to any audit trail: redact in place, never delete.
        """
        old = self.get_entry(entry_uuid)
        if old is None:
            raise LedgerError("no such entry %r" % entry_uuid)
        if old["status"] != CONFIRMED:
            raise LedgerError("only confirmed entries are superseded; "
                              "%s is %s" % (entry_uuid, old["status"]))
        merged = dict(old)
        merged.update(fields)
        new_id = self.add_entry(
            activity_date=merged["activity_date"], title=merged["title"],
            hours=merged["hours"], count=merged["count"],
            provider=merged["provider"], tags=merged["tags"],
            markers=merged["markers"], applies_to=merged["applies_to"],
            overrides=merged["overrides"], notes=merged["notes"],
            capture_source=merged["capture_source"],
            capture_ref=merged["capture_ref"], status=CONFIRMED,
            cost_usd=merged["cost_usd"], submitted_to=merged["submitted_to"],
            submitted_dates=merged["submitted_dates"], record_capture=False,
        )
        with self.conn() as c:
            c.execute("UPDATE entries SET supersedes=? WHERE uuid=?",
                      (entry_uuid, new_id))
        self.set_status(entry_uuid, SUPERSEDED)
        return new_id

    def void(self, entry_uuid):
        """Retire an entry without erasing it.

        The capture event stays (the kill-switch counter measures the habit,
        and a voided capture was still a capture); everything else — credits,
        dupe matching, totals, reports, exports of counted work — treats the
        row as if it never made a claim.
        """
        return self.set_status(entry_uuid, VOID)

    def superseded_by(self, entry_uuid):
        """The uuid of the correcting copy that replaced this entry, if any."""
        with self.conn() as c:
            row = c.execute("SELECT uuid FROM entries WHERE supersedes=?",
                            (entry_uuid,)).fetchone()
        return row["uuid"] if row else None

    def mark_submitted(self, entry_uuid, body_id, on_date=None):
        """Record that this entry went into a body's portal, and when."""
        e = self.get_entry(entry_uuid)
        if e is None:
            raise LedgerError("no such entry %r" % entry_uuid)
        bodies = list(e["submitted_to"])
        if body_id not in bodies:
            bodies.append(body_id)
        dates = dict(e["submitted_dates"])
        dates[body_id] = _validate_date(str(on_date or datetime.date.today()))
        return self.update_entry(entry_uuid, submitted_to=bodies,
                                 submitted_dates=dates)

    # -- evidence ----------------------------------------------------------

    def add_evidence(self, entry_uuid, filename, data, mime="", note=""):
        """Store a file, hash it, and record the hash. Returns the row."""
        if self.get_entry(entry_uuid) is None:
            raise LedgerError("no such entry %r" % entry_uuid)
        digest = hashlib.sha256(data).hexdigest()
        safe = _safe_filename(filename)
        # v0.2: content-addressed storage. The file on disk is named by its
        # own sha256 (plus the extension, for serving), flat under the
        # evidence root. Identical bytes attached twice share one file — fine
        # under the never-delete rule. v0.1 rows keep their per-entry
        # stored_path and keep resolving.
        rel = digest + os.path.splitext(safe)[1].lower()
        full = os.path.join(self.evidence_root, rel)
        if not os.path.isfile(full):
            with open(full, "wb") as fh:
                fh.write(data)
            os.chmod(full, 0o600)
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO evidence (entry_uuid, filename, stored_path,"
                " sha256, bytes, mime, added_at, note) VALUES (?,?,?,?,?,?,?,?)",
                (entry_uuid, safe, rel, digest, len(data), mime or "", now(),
                 note or ""),
            )
            ev_id = cur.lastrowid
        return {"id": ev_id, "entry_uuid": entry_uuid, "filename": safe,
                "stored_path": rel, "sha256": digest, "bytes": len(data),
                "mime": mime, "note": note}

    def list_evidence(self, entry_uuid=None):
        with self.conn() as c:
            if entry_uuid:
                rows = c.execute(
                    "SELECT * FROM evidence WHERE entry_uuid=? ORDER BY id",
                    (entry_uuid,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM evidence ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def evidence_path(self, row):
        return os.path.join(self.evidence_root, row["stored_path"])

    def verify_evidence(self, entry_uuid=None):
        """Re-hash every stored file. Returns per-file OK / MISSING / ALTERED."""
        out = []
        for row in self.list_evidence(entry_uuid):
            full = self.evidence_path(row)
            if not os.path.isfile(full):
                out.append(dict(row, verify="MISSING"))
                continue
            with open(full, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            out.append(dict(row, verify="OK" if digest == row["sha256"]
                            else "ALTERED", observed_sha256=digest))
        return out

    # -- reminders ---------------------------------------------------------

    def log_reminder(self, kind, credential="", channel="", body="",
                     suppressed=False, reason="", sent_at=None):
        with self.conn() as c:
            c.execute(
                "INSERT INTO reminders_log (sent_at, kind, credential, channel,"
                " body, suppressed, reason) VALUES (?,?,?,?,?,?,?)",
                (sent_at or now(), kind, credential, channel, body,
                 1 if suppressed else 0, reason),
            )

    def reminders_since(self, since, include_suppressed=False):
        sql = "SELECT * FROM reminders_log WHERE sent_at >= ?"
        if not include_suppressed:
            sql += " AND suppressed=0"
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY sent_at DESC",
                                               (str(since),)).fetchall()]

    def unsolicited_today(self, day=None):
        """How many unprompted messages have already gone out today.

        The budget is one. Everything else waits, because a reminder system
        that talks when there is nothing to say gets muted, and a muted
        reminder system is worse than none — it looks like it is working.
        """
        day = str(day or datetime.date.today())
        with self.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) n FROM reminders_log WHERE suppressed=0"
                " AND kind LIKE 'unsolicited%' AND sent_at LIKE ?",
                (day + "%",)).fetchone()
        return row["n"]

    # -- capture counter (kill-switch instrumentation) ---------------------

    def record_capture_event(self, source, entry_uuid=None, is_real=True,
                             note=""):
        with self.conn() as c:
            c.execute(
                "INSERT INTO capture_events (ts, source, entry_uuid, is_real,"
                " note) VALUES (?,?,?,?,?)",
                (now(), source, entry_uuid, 1 if is_real else 0, note),
            )

    def capture_counts(self, since=None, days=30):
        """Real captures since a date. The kill-switch number.

        The project ships with a falsifier attached: if fewer than 8 real
        captures land in the 30 days after first use, the premise that the
        holder will actually feed
        this thing is wrong and the project should be killed rather than
        polished. This query is what answers that, and it deliberately does
        not count backfill or fixtures.
        """
        if since is None:
            since = datetime.date.today() - datetime.timedelta(days=days)
        with self.conn() as c:
            total = c.execute(
                "SELECT COUNT(*) n FROM capture_events WHERE ts >= ?",
                (str(since),)).fetchone()["n"]
            real = c.execute(
                "SELECT COUNT(*) n FROM capture_events WHERE ts >= ?"
                " AND is_real=1", (str(since),)).fetchone()["n"]
            by_source = c.execute(
                "SELECT source, COUNT(*) n FROM capture_events WHERE ts >= ?"
                " AND is_real=1 GROUP BY source ORDER BY n DESC",
                (str(since),)).fetchall()
        return {
            "since": str(since),
            "window_days": days,
            "total": total,
            "real": real,
            "by_source": [dict(r) for r in by_source],
            "threshold": 8,
            "falsified": real < 8,
        }

    def capture_by_month(self, months=12, as_of=None):
        """Real captures per calendar month, oldest first, zero-filled."""
        end = as_of or datetime.date.today()
        with self.conn() as c:
            rows = dict(
                (r["m"], r["n"]) for r in c.execute(
                    "SELECT substr(ts, 1, 7) m, COUNT(*) n FROM capture_events"
                    " WHERE is_real=1 GROUP BY m").fetchall())
        out = []
        y, m = end.year, end.month
        for _ in range(months):
            key = "%04d-%02d" % (y, m)
            out.append((key, rows.get(key, 0)))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return list(reversed(out))

    # -- config and per-credential state ----------------------------------

    def set_config(self, key, value):
        with self.conn() as c:
            c.execute("INSERT INTO config (key, value) VALUES (?,?)"
                      " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))

    def setdefault_config(self, key, value):
        """Set a config key only if absent; return the stored value.

        INSERT OR IGNORE makes the first-writer-wins race between two request
        threads harmless — both end up reading the same stored value.
        """
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?,?)",
                      (key, json.dumps(value)))
        return self.get_config(key)

    def get_config(self, key, default=None):
        with self.conn() as c:
            row = c.execute("SELECT value FROM config WHERE key=?",
                            (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_credential_state(self, credential, **fields):
        allowed = {"anchor_date", "carryover_in", "portal_credits",
                   "portal_checked", "active", "note"}
        bad = set(fields) - allowed
        if bad:
            raise LedgerError("unknown credential_state field(s): %s"
                              % ", ".join(sorted(bad)))
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO credential_state (credential)"
                      " VALUES (?)", (credential,))
            if fields:
                sets = ", ".join("%s=?" % k for k in fields)
                c.execute("UPDATE credential_state SET %s WHERE credential=?" % sets,
                          list(fields.values()) + [credential])
        return self.get_credential_state(credential)

    def get_credential_state(self, credential):
        with self.conn() as c:
            row = c.execute("SELECT * FROM credential_state WHERE credential=?",
                            (credential,)).fetchone()
        return dict(row) if row else None

    def all_credential_state(self):
        with self.conn() as c:
            rows = c.execute("SELECT * FROM credential_state").fetchall()
        return dict((r["credential"], dict(r)) for r in rows)

    def anchors(self):
        return dict((k, v["anchor_date"]) for k, v in
                    self.all_credential_state().items() if v["anchor_date"])

    def carryover_in(self):
        return dict((k, v["carryover_in"] or 0.0)
                    for k, v in self.all_credential_state().items())

    # -- fees --------------------------------------------------------------

    def add_fee(self, credential, name, amount_usd=None, due_date=None,
                paid_on=None, note=""):
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO fees (credential, name, amount_usd, due_date,"
                " paid_on, note) VALUES (?,?,?,?,?,?)",
                (credential, name, amount_usd, due_date, paid_on, note))
            return cur.lastrowid

    def list_fees(self, credential=None):
        with self.conn() as c:
            if credential:
                rows = c.execute("SELECT * FROM fees WHERE credential=?"
                                 " ORDER BY due_date", (credential,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM fees ORDER BY credential,"
                                 " due_date").fetchall()
        return [dict(r) for r in rows]

    def mark_fee_paid(self, fee_id, paid_on=None):
        with self.conn() as c:
            c.execute("UPDATE fees SET paid_on=? WHERE id=?",
                      (str(paid_on or datetime.date.today()), fee_id))

    # -- engine feed -------------------------------------------------------

    def engine_entries(self, include_pending=False):
        """Every entry the engine should see, in engine-normalised shape."""
        states = [CONFIRMED] + ([CAPTURED] if include_pending else [])
        rows = self.list_entries(status=states, limit=100000)
        with self.conn() as c:
            counts = dict(
                (r["entry_uuid"], r["n"]) for r in c.execute(
                    "SELECT entry_uuid, COUNT(*) n FROM evidence"
                    " GROUP BY entry_uuid").fetchall()
            )
        for r in rows:
            r["evidence_count"] = counts.get(r["uuid"], 0)
        return [
            {
                "id": r["uuid"], "date": r["activity_date"], "title": r["title"],
                "provider": r["provider"], "hours": r["hours"],
                "count": r["count"], "tags": r["tags"], "markers": r["markers"],
                "overrides": r["overrides"], "applies_to": r["applies_to"],
                "status": r["status"], "submitted_to": r["submitted_to"],
                "submitted_dates": r["submitted_dates"],
                "evidence_count": r["evidence_count"],
            }
            for r in rows
        ]

    def stats(self):
        with self.conn() as c:
            by_status = c.execute(
                "SELECT status, COUNT(*) n FROM entries GROUP BY status"
            ).fetchall()
            ev = c.execute("SELECT COUNT(*) n, COALESCE(SUM(bytes),0) b"
                           " FROM evidence").fetchone()
        return {
            "entries": dict((r["status"], r["n"]) for r in by_status),
            "evidence_files": ev["n"],
            "evidence_bytes": ev["b"],
        }


class SoftDuplicate(Exception):
    """Raised by confirm() when an entry looks like one already on file."""

    def __init__(self, entry_uuid, matches):
        self.entry_uuid = entry_uuid
        self.matches = matches
        Exception.__init__(
            self,
            "entry %s looks like %d already-confirmed entr%s (same date and "
            "provider, hours within 25%%)" %
            (entry_uuid, len(matches), "y" if len(matches) == 1 else "ies"),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_date(value):
    s = str(value)
    if not _DATE_RE.match(s):
        raise LedgerError("activity_date must be YYYY-MM-DD, got %r" % s)
    try:
        datetime.date.fromisoformat(s)     # rejects 2026-02-30
    except ValueError as exc:
        # The bare stdlib message ("day is out of range for month") does not
        # say WHICH date, which is useless in a CSV import reporting per line
        # and worse in a form that has already cleared the field.
        raise LedgerError("%r is not a real date (%s)" % (s, exc))
    return s


def _norm_provider(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _safe_filename(name):
    name = os.path.basename(str(name or "file"))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:120] or "file"


def _row_to_entry(row):
    e = dict(row)
    e["tags"] = json.loads(e["tags"] or "[]")
    e["markers"] = json.loads(e["markers"] or "[]")
    e["overrides"] = json.loads(e["overrides"] or "{}")
    e["submitted_to"] = json.loads(e["submitted_to"] or "[]")
    e["submitted_dates"] = json.loads(e.get("submitted_dates") or "{}")
    e["applies_to"] = json.loads(e["applies_to"]) if e["applies_to"] else None
    e.setdefault("evidence_count", 0)
    return e

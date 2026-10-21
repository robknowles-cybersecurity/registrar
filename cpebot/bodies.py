"""Load and validate bodies.yaml — the per-body plugin rules.

Each credential entry in bodies.yaml IS the plugin. There is no per-body Python
module, and that is deliberate: the differences between these seven tracks are
differences of NUMBER and SHAPE (cap here, floor there, 24 months not 36), not
differences of algorithm. Expressing them as data means the holder can correct a
number at 7am without touching code, and means the verification provenance
(`source_url`, `verified_date`, `verified`) lives on the same line as the number
it justifies.

The one thing this module refuses to do is load a rules file it does not
understand. A CPE tracker that silently drops a cap is worse than no tracker.
"""

import os

from . import _minyaml

DEFAULT_PATH = os.environ.get(
    "CPEBOT_BODIES",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bodies.yaml"),
)


class RulesError(ValueError):
    """bodies.yaml is malformed or internally inconsistent."""


def is_web_url(url):
    """True only for an http(s) URL, the one kind of source that is safe to
    render as a link. Anything else is shown as plain text, if at all."""
    return isinstance(url, str) and url.lower().startswith(("https://", "http://"))


def _raw_load(path):
    """Parse with PyYAML if it is importable, else with the bundled subset reader."""
    try:
        import yaml  # noqa: WPS433 — optional, by design
    except ImportError:
        return _minyaml.load_path(path), "minyaml"
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh), "pyyaml"


def load(path=None):
    """Return a validated Rules object."""
    path = path or DEFAULT_PATH
    raw, parser = _raw_load(path)
    if not isinstance(raw, dict):
        raise RulesError("%s did not parse to a mapping" % path)
    rules = Rules(raw, path=path, parser=parser)
    rules.validate()
    return rules


class Rules(object):
    def __init__(self, raw, path=None, parser=None):
        self.raw = raw
        self.path = path
        self.parser = parser
        self.schema_version = raw.get("schema_version")
        self.sweep_date = raw.get("sweep_date")
        self.tags = [t["id"] for t in raw.get("tag_vocabulary") or []]
        self.tag_labels = {t["id"]: t["label"] for t in raw.get("tag_vocabulary") or []}
        self.markers = [m["id"] for m in raw.get("topic_markers") or []]
        self.marker_labels = {m["id"]: m["label"] for m in raw.get("topic_markers") or []}
        self.credentials = [Credential(c, self) for c in raw.get("credentials") or []]
        self._by_id = {c.id: c for c in self.credentials}
        # The research catalog: body-level maintenance programs with per-cert
        # totals. Reference data only — the engine scores `credentials`, never
        # these, so a catalog figure cannot turn into a compliance verdict.
        self.programs = [Program(p, self) for p in raw.get("programs") or []]

    def get(self, cred_id):
        try:
            return self._by_id[cred_id]
        except KeyError:
            raise RulesError("no such credential: %r" % (cred_id,))

    def ids(self):
        return [c.id for c in self.credentials]

    def bodies(self):
        """Distinct issuing bodies, in first-seen order."""
        seen = []
        for c in self.credentials:
            if c.body_id not in [b["id"] for b in seen]:
                seen.append({"id": c.body_id, "name": c.body_name})
        return seen

    def unverified(self):
        """Every credential that must be shown UNVERIFIED, with the reason."""
        out = []
        for c in self.credentials:
            reasons = c.unverified_reasons()
            if reasons:
                out.append({"credential": c.id, "reasons": reasons})
        return out

    def validate(self):
        if self.schema_version != 1:
            raise RulesError("unsupported schema_version %r" % (self.schema_version,))
        if not self.credentials:
            raise RulesError("no credentials defined")
        seen = set()
        for cred in self.credentials:
            if cred.id in seen:
                raise RulesError("duplicate credential id %r" % cred.id)
            seen.add(cred.id)
            cred.validate()
        body_ids, cert_ids = set(), set()
        for prog in self.programs:
            if prog.body_id in body_ids:
                raise RulesError("duplicate program for body %r" % prog.body_id)
            body_ids.add(prog.body_id)
            prog.validate()
            for c in prog.certifications:
                if c.id in cert_ids:
                    raise RulesError("duplicate catalog certification %r" % c.id)
                cert_ids.add(c.id)
        return True

    def program(self, body_id):
        for p in self.programs:
            if p.body_id == body_id:
                return p
        raise RulesError("no such program: %r" % (body_id,))


class Credential(object):
    def __init__(self, raw, rules):
        self.raw = raw
        self.rules = rules
        self.id = raw.get("id")
        self.name = raw.get("name")
        self.long_name = raw.get("long_name")
        self.body_id = raw.get("body_id")
        self.body_name = raw.get("body_name")
        self.unit_label = raw.get("unit_label") or "credit"
        self.unit_note = raw.get("unit_note")
        # Optional, informational only; holding state belongs in the ledger.
        self.status = raw.get("status")
        self.verified = bool(raw.get("verified"))
        self.source_url = raw.get("source_url")
        self.verified_date = raw.get("verified_date")
        self.verification_note = raw.get("verification_note")
        self.caps_known = raw.get("caps_known")
        self.caps_unknown_note = raw.get("caps_unknown_note")
        self.cycle = raw.get("cycle") or {}
        self.requirements = raw.get("requirements") or {}
        self.groups = raw.get("groups") or []
        self.buckets = [Bucket(b, self) for b in raw.get("buckets") or []]
        self.group_marker_map = raw.get("group_marker_map") or {}
        self.carryover = raw.get("carryover") or {}
        self.cross_credential = raw.get("cross_credential") or {}
        self.submission = raw.get("submission") or {}
        self.grace_period_days = raw.get("grace_period_days") or 0
        self.grace_note = raw.get("grace_note")
        self.fees = raw.get("fees") or []

    # -- shape --------------------------------------------------------------

    @property
    def cycle_months(self):
        return int(self.cycle.get("length_months") or 12)

    @property
    def cycle_total(self):
        return float(self.requirements.get("cycle_total") or 0)

    @property
    def annual_minimum(self):
        v = self.requirements.get("annual_minimum")
        return None if v is None else float(v)

    @property
    def annual_target(self):
        v = self.requirements.get("annual_target")
        return None if v is None else float(v)

    def group(self, group_id):
        for g in self.groups:
            if g.get("id") == group_id:
                return g
        return None

    def bucket(self, bucket_id):
        for b in self.buckets:
            if b.id == bucket_id:
                return b
        return None

    def buckets_for(self, tags, markers):
        """Every bucket an entry with these tags/markers is eligible for.

        Order matters: bodies.yaml lists the most specific bucket first for any
        credential where one activity could land in two (CISSP's Group A before
        Group B, because a domain-related talk is Group A and only falls to
        Group B when the domain marker is absent). The engine takes the FIRST
        match, never both — double-counting one activity into two buckets of
        the same credential is precisely the error class this tool exists to
        prevent.
        """
        tags = set(tags or [])
        markers = set(markers or [])
        out = []
        for b in self.buckets:
            if not (tags & set(b.tags)):
                continue
            need = set(b.requires_markers or [])
            if need and not need.issubset(markers):
                continue
            out.append(b)
        return out

    # -- verification -------------------------------------------------------

    def unverified_reasons(self):
        reasons = []
        if not self.verified:
            reasons.append(
                "credential-level rules UNVERIFIED (%s)"
                % (self.verification_note or "no note")
            )
        if self.caps_known is False:
            reasons.append(
                "per-category caps NOT KNOWN (%s)"
                % (self.caps_unknown_note or "no note")
            )
        for b in self.buckets:
            if b.cap_verified is False:
                reasons.append(
                    "bucket %r cap UNVERIFIED (%s)" % (b.id, b.cap_note or "no note")
                )
        if self.carryover and self.carryover.get("verified") is False:
            reasons.append(
                "carryover rule UNVERIFIED (%s)"
                % (self.carryover.get("verified_note") or self.carryover.get("window_note") or "no note")
            )
        return reasons

    @property
    def is_fully_verified(self):
        return not self.unverified_reasons()

    def unverified_value(self, kind="rules", bucket=None):
        """Why one displayed rule value is unverified, or None if it is not.

        `kind` names the value: "rules" for the credential's own figures
        (cycle total, rates, group floors and caps), "cap" for `bucket`'s cap,
        "carryover" for the carryover terms. Returns {"note", "source_url"}
        so every place that shows the value can mark it and link the page
        it should have been read from.
        """
        note = None
        if not self.verified:
            note = self.verification_note or "credential-level rules UNVERIFIED"
        elif kind == "cap" and bucket is not None and bucket.cap_verified is False:
            note = bucket.cap_note or "category cap UNVERIFIED"
        elif kind == "cap" and self.caps_known is False:
            note = self.caps_unknown_note or "category caps NOT KNOWN"
        elif (kind == "carryover" and self.carryover
                and self.carryover.get("verified") is False):
            note = (self.carryover.get("verified_note")
                    or "carryover rule UNVERIFIED")
        if note is None:
            return None
        return {"note": note, "source_url": self.source_url}

    # -- validation ---------------------------------------------------------

    def validate(self):
        if not self.id:
            raise RulesError("credential with no id")
        if self.cycle_months <= 0:
            raise RulesError("%s: cycle length_months must be positive" % self.id)
        if self.cycle_total <= 0:
            raise RulesError("%s: requirements.cycle_total must be positive" % self.id)
        if self.verified and not (self.source_url and self.verified_date):
            raise RulesError(
                "%s: verified:true requires both source_url and verified_date" % self.id
            )
        if self.source_url and not is_web_url(self.source_url):
            raise RulesError("%s: source_url must be an http(s) URL" % self.id)
        group_ids = set()
        for g in self.groups:
            gid = g.get("id")
            if not gid:
                raise RulesError("%s: group with no id" % self.id)
            if gid in group_ids:
                raise RulesError("%s: duplicate group %r" % (self.id, gid))
            group_ids.add(gid)
        bucket_ids = set()
        for b in self.buckets:
            b.validate(group_ids)
            if b.id in bucket_ids:
                raise RulesError("%s: duplicate bucket %r" % (self.id, b.id))
            bucket_ids.add(b.id)
        for gid in self.group_marker_map:
            if gid not in group_ids:
                raise RulesError(
                    "%s: group_marker_map references unknown group %r" % (self.id, gid)
                )
        floors = sum(float(g.get("floor") or 0) for g in self.groups)
        if floors > self.cycle_total:
            raise RulesError(
                "%s: group floors (%s) exceed cycle_total (%s)"
                % (self.id, floors, self.cycle_total)
            )
        return True


class Bucket(object):
    def __init__(self, raw, cred):
        self.raw = raw
        self.cred = cred
        self.id = raw.get("id")
        self.name = raw.get("name")
        self.group = raw.get("group")
        self.per_hour = raw.get("per_hour")
        self.per_item = raw.get("per_item")
        self.cap = raw.get("cap")
        self.cap_note = raw.get("cap_note")
        self.cap_verified = raw.get("cap_verified")
        self.tags = raw.get("tags") or []
        self.requires_markers = raw.get("requires_markers") or []
        self.value_is_override = bool(raw.get("value_is_override"))

    def credits_for(self, hours, count=1, override=None):
        """Credits this bucket awards for one entry, BEFORE any cap is applied."""
        if override is not None:
            return float(override)
        if self.value_is_override:
            # The rules file says this bucket's value cannot be computed. It is
            # not zero — it is unknown, and the entry must carry an override.
            return 0.0
        if self.per_hour is not None:
            return float(hours or 0) * float(self.per_hour)
        if self.per_item is not None:
            return float(count or 1) * float(self.per_item)
        return 0.0

    def validate(self, group_ids):
        if not self.id:
            raise RulesError("%s: bucket with no id" % self.cred.id)
        if self.per_hour is not None and self.per_item is not None:
            raise RulesError(
                "%s/%s: a bucket has per_hour OR per_item, never both"
                % (self.cred.id, self.id)
            )
        if self.per_hour is None and self.per_item is None:
            raise RulesError(
                "%s/%s: bucket needs per_hour or per_item" % (self.cred.id, self.id)
            )
        if self.group is not None and self.group not in group_ids:
            raise RulesError(
                "%s/%s: unknown group %r" % (self.cred.id, self.id, self.group)
            )
        if not self.tags:
            raise RulesError("%s/%s: bucket matches no tags" % (self.cred.id, self.id))
        for t in self.tags:
            if t not in self.cred.rules.tags:
                raise RulesError(
                    "%s/%s: tag %r is not in tag_vocabulary"
                    % (self.cred.id, self.id, t)
                )
        for m in self.requires_markers:
            if m not in self.cred.rules.markers:
                raise RulesError(
                    "%s/%s: marker %r is not in topic_markers"
                    % (self.cred.id, self.id, m)
                )
        if self.cap is not None and float(self.cap) < 0:
            raise RulesError("%s/%s: negative cap" % (self.cred.id, self.id))
        return True


# -- research catalog (`programs:`) -----------------------------------------
#
# A program is one certifying body's maintenance scheme, modelled once, with
# per-certification totals beneath it. Every figure carries its own
# provenance, and the provenance vocabulary is closed: a value either was read
# on the body's own page (READ_OK), was looked for there and confirmed not to
# exist (CONFIRMED_ABSENT), or has not been read there (NOT_READ). Only a
# READ_OK figure from an official page may claim `verified: true`.

LOOK_STATUSES = ("READ_OK", "CONFIRMED_ABSENT", "NOT_READ")
SOURCE_TIERS = ("official_page", "official_search_snippet", "secondary")
MFA_VALUES = ("yes", "no", "unknown")
CERT_STATUSES = ("offered", "retired", "unknown")
SUBMISSION_KEYS = ("channel", "public_api", "bulk_upload", "mfa",
                   "provider_reported")


def _check_provenance(where, raw):
    """Raise unless `raw` carries well-formed provenance."""
    if not raw.get("source_url"):
        raise RulesError("%s: source_url is required" % where)
    if not is_web_url(raw.get("source_url")):
        raise RulesError("%s: source_url must be an http(s) URL" % where)
    if raw.get("look_status") not in LOOK_STATUSES:
        raise RulesError("%s: look_status must be one of %s, got %r"
                         % (where, ", ".join(LOOK_STATUSES), raw.get("look_status")))
    if raw.get("source_tier") not in SOURCE_TIERS:
        raise RulesError("%s: source_tier must be one of %s, got %r"
                         % (where, ", ".join(SOURCE_TIERS), raw.get("source_tier")))
    if not raw.get("checked_date"):
        raise RulesError("%s: checked_date is required" % where)
    if raw.get("verified"):
        if raw.get("look_status") != "READ_OK" or raw.get("source_tier") != "official_page":
            raise RulesError("%s: verified:true requires look_status READ_OK on an "
                             "official_page" % where)
        if not raw.get("verified_date"):
            raise RulesError("%s: verified:true requires verified_date" % where)


def _non_negative_or_null(where, value):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RulesError("%s: expected a non-negative number or null, got %r"
                         % (where, value))


class Program(object):
    def __init__(self, raw, rules):
        self.raw = raw
        self.rules = rules
        self.body_id = raw.get("body_id")
        self.body_name = raw.get("body_name")
        self.unit_label = raw.get("unit_label") or "credit"
        self.cycle_months = raw.get("cycle_months")
        self.facts = raw.get("facts") or []
        self.submission = raw.get("submission") or {}
        self.certifications = [ProgramCert(c, self) for c in raw.get("certifications") or []]

    @property
    def verified(self):
        return bool(self.raw.get("verified"))

    def look_statuses(self):
        """Every provenance-bearing item's look_status, for the sweep tally."""
        out = [self.raw.get("look_status")]
        out.extend(f.get("look_status") for f in self.facts)
        out.extend(c.raw.get("look_status") for c in self.certifications)
        out.extend(v.get("look_status") for v in self.submission.values())
        return out

    def validate(self):
        where = "program %s" % (self.body_id,)
        if not self.body_id or not self.body_name:
            raise RulesError("%s: body_id and body_name are required" % where)
        _check_provenance(where, self.raw)
        _non_negative_or_null(where + " cycle_months", self.cycle_months)
        keys = set()
        for f in self.facts:
            k = f.get("key")
            if not k:
                raise RulesError("%s: fact with no key" % where)
            if k in keys:
                raise RulesError("%s: duplicate fact %r" % (where, k))
            keys.add(k)
            _check_provenance("%s fact %s" % (where, k), f)
        if not self.certifications:
            raise RulesError("%s: no certifications listed" % where)
        for c in self.certifications:
            c.validate()
        for k in SUBMISSION_KEYS:
            if k not in self.submission:
                raise RulesError("%s: submission.%s is required (use value "
                                 "\"unknown\" rather than omitting it)" % (where, k))
        for k, v in self.submission.items():
            if k not in SUBMISSION_KEYS:
                raise RulesError("%s: unknown submission key %r" % (where, k))
            _check_provenance("%s submission.%s" % (where, k), v)
        if self.submission["mfa"].get("value") not in MFA_VALUES:
            raise RulesError("%s: submission.mfa value must be one of %s"
                             % (where, ", ".join(MFA_VALUES)))
        return True


class ProgramCert(object):
    def __init__(self, raw, program):
        self.raw = raw
        self.program = program
        self.id = raw.get("id")
        self.name = raw.get("name")
        self.long_name = raw.get("long_name")
        self.status = raw.get("status") or "unknown"
        # A body's own word for the status when the vocabulary has none
        # (GIAC "in abeyance" is carried as retired plus this note).
        self.status_note = raw.get("status_note")
        self.cycle_total = raw.get("cycle_total")
        self.annual_minimum = raw.get("annual_minimum")
        self.engine_track = raw.get("engine_track")

    @property
    def cycle_months(self):
        return self.raw.get("cycle_months") or self.program.cycle_months

    def validate(self):
        where = "program %s cert %s" % (self.program.body_id, self.id)
        if not self.id or not self.name:
            raise RulesError("%s: id and name are required" % where)
        if self.status not in CERT_STATUSES:
            raise RulesError("%s: status must be one of %s"
                             % (where, ", ".join(CERT_STATUSES)))
        if self.status_note is not None and (
                not isinstance(self.status_note, str) or not self.status_note.strip()):
            raise RulesError("%s: status_note must be a non-empty string" % where)
        _check_provenance(where, self.raw)
        _non_negative_or_null(where + " cycle_total", self.cycle_total)
        _non_negative_or_null(where + " annual_minimum", self.annual_minimum)
        _non_negative_or_null(where + " cycle_months", self.raw.get("cycle_months"))
        if (self.cycle_total is not None and self.annual_minimum is not None
                and self.annual_minimum > self.cycle_total):
            raise RulesError("%s: annual_minimum exceeds cycle_total" % where)
        if self.engine_track is not None:
            ids = [c.id for c in self.program.rules.credentials]
            if self.engine_track not in ids:
                raise RulesError("%s: engine_track %r is not a credential id"
                                 % (where, self.engine_track))
        return True

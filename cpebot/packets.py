"""Submission packets: copy-ready fields per entry, plus an evidence bundle.

Submission is ASSISTED, never automated. The holder logs into the body's
portal themselves and submits; Registrar's job is to make that transcription
rather than archaeology. Registrar never asks for, stores or uses a portal
login, and the mapping format below refuses any key shaped like one.

Two outputs per credential:

* **Entry packets** — for each counted entry in the current cycle, the
  portal's own fields in the portal's own order, each with its value, so the
  holder copies field by field. Fields Registrar cannot know (ISC2's
  per-activity domain, say) are marked "choose in portal" rather than
  guessed.
* **Evidence bundle** — a zip of every attached file for those entries,
  re-hashed at bundle time, with `manifest.json` and `MANIFEST.md`. A file
  whose bytes no longer match the hash recorded at upload is NOT bundled:
  it is listed as a gap, the same refusal the web app's evidence route makes.

Which fields a body's form has is DATA, in `packetmaps/<id>.yaml`. Adding a
body means adding a mapping file, not code. A credential uses the first
mapping that names it in `credentials:`, else the first whose `body_id`
matches, else `generic.yaml`.

Integration point: bodies.yaml is not read for packet fields. If bodies.yaml
later grows a `submission.packet_map: <id>` key, `map_for()` is the one
function that should honour it ahead of the lookups above.
"""

import datetime
import hashlib
import io
import json
import os
import zipfile

from . import _minyaml
from . import bodies as B
from . import engine as E

DEFAULT_DIR = os.environ.get(
    "CPEBOT_PACKETMAPS",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "packetmaps"),
)

GENERIC_ID = "generic"

# Where a field's value can come from. This list is the whole contract
# between a mapping file and the code; anything else is a load error.
SOURCES = {
    "entry.activity_date": "the activity date (formatted by date_format)",
    "entry.title": "the entry title",
    "entry.provider": "the provider / sponsor",
    "entry.hours": "hours as logged",
    "entry.count": "item count as logged",
    "entry.notes": "the entry's notes",
    "entry.uuid": "Registrar's entry id",
    "entry.tags": "tag ids, comma-separated",
    "entry.tag_labels": "tag labels from bodies.yaml, comma-separated",
    "entry.markers": "topic-marker ids, comma-separated",
    "entry.marker_labels": "topic-marker labels, comma-separated",
    "credit.amount": "credits this entry claims for the credential, pre-cap",
    "credit.unit": "the credential's unit label (CPE, CEU, PDU...)",
    "bucket.id": "the category id the engine places the entry in",
    "bucket.name": "the category name",
    "group.id": "the group id(s) the entry feeds, comma-separated",
    "group.name": "the group name(s), comma-separated",
    "holder.name": "the attendee name from Settings",
    "credential.name": "the credential's short name",
    "body.name": "the issuing body's name",
    "evidence.filenames": "attached file names, as bundled",
    "evidence.count": "number of attached files",
    "literal": "the field's own `value`",
    "holder_choice": "nothing: the holder picks this in the portal",
}

FIELD_KEYS = {"label", "source", "value", "map", "default", "required",
              "note", "format"}
TOP_KEYS = {"schema_version", "id", "form_name", "body_id", "credentials",
            "verified", "verified_note", "source_url", "holder_note",
            "date_format", "fields"}

# A packet map describes a form. It never carries a way into the portal.
CREDENTIAL_SHAPED = ("password", "passwd", "passphrase", "username", "login",
                     "secret", "token", "cookie", "session", "api_key",
                     "apikey", "otp", "totp")

HOLDER_CHOICE = "[choose in portal]"

# Sources whose value comes from the credential's rules in bodies.yaml (the
# rate, the category, the group). When those rules are unverified, the field
# says so wherever it is shown.
RULE_SOURCES = {"credit.amount", "bucket.id", "bucket.name", "group.id",
                "group.name"}


class PacketMapError(ValueError):
    """A mapping file is malformed, or asks for something it may not have."""


class PacketMap(object):
    def __init__(self, raw, path=None):
        self.raw = raw
        self.path = path
        self.id = raw.get("id")
        self.form_name = raw.get("form_name") or raw.get("id")
        self.body_id = raw.get("body_id")
        self.credentials = list(raw.get("credentials") or [])
        self.verified = bool(raw.get("verified"))
        self.verified_note = raw.get("verified_note")
        self.source_url = raw.get("source_url")
        self.holder_note = raw.get("holder_note")
        self.date_format = raw.get("date_format") or "%Y-%m-%d"
        self.fields = list(raw.get("fields") or [])

    def validate(self, rules=None):
        where = self.path or self.id or "packet map"
        if self.raw.get("schema_version") != 1:
            raise PacketMapError("%s: unsupported schema_version %r"
                                 % (where, self.raw.get("schema_version")))
        _no_credential_keys(self.raw, where)
        unknown = set(self.raw) - TOP_KEYS
        if unknown:
            raise PacketMapError("%s: unknown key(s) %s"
                                 % (where, ", ".join(sorted(unknown))))
        if not self.id:
            raise PacketMapError("%s: no id" % where)
        if self.source_url is not None and not B.is_web_url(self.source_url):
            raise PacketMapError("%s: source_url must be an http(s) URL" % where)
        if self.id != GENERIC_ID and not (self.body_id or self.credentials):
            raise PacketMapError("%s: a non-generic map needs body_id or "
                                 "credentials" % where)
        if not self.fields:
            raise PacketMapError("%s: no fields" % where)
        try:
            datetime.date(2026, 1, 31).strftime(self.date_format)
        except (TypeError, ValueError) as exc:
            raise PacketMapError("%s: bad date_format (%s)" % (where, exc))
        labels = set()
        for i, f in enumerate(self.fields):
            if not isinstance(f, dict):
                raise PacketMapError("%s: field %d is not a mapping"
                                     % (where, i))
            bad = set(f) - FIELD_KEYS
            if bad:
                raise PacketMapError("%s: field %r has unknown key(s) %s"
                                     % (where, f.get("label"),
                                        ", ".join(sorted(bad))))
            if not f.get("label"):
                raise PacketMapError("%s: field %d has no label" % (where, i))
            if f["label"] in labels:
                raise PacketMapError("%s: duplicate field %r"
                                     % (where, f["label"]))
            labels.add(f["label"])
            src = f.get("source")
            if src not in SOURCES:
                raise PacketMapError("%s: field %r has unknown source %r"
                                     % (where, f["label"], src))
            if src == "literal" and f.get("value") is None:
                raise PacketMapError("%s: literal field %r has no value"
                                     % (where, f["label"]))
            if f.get("map") is not None and not isinstance(f["map"], dict):
                raise PacketMapError("%s: field %r map must be a mapping"
                                     % (where, f["label"]))
        if rules is not None:
            known = set(rules.ids())
            for cid in self.credentials:
                if cid not in known:
                    raise PacketMapError("%s: unknown credential %r"
                                         % (where, cid))
            if self.body_id and self.body_id not in [b["id"] for b in
                                                     rules.bodies()]:
                raise PacketMapError("%s: unknown body_id %r"
                                     % (where, self.body_id))
        return True


def _no_credential_keys(node, where):
    if isinstance(node, dict):
        for k, v in node.items():
            low = str(k).lower()
            if any(w in low for w in CREDENTIAL_SHAPED):
                raise PacketMapError(
                    "%s: key %r looks like a portal credential. Registrar "
                    "never stores or uses cert-body logins; a packet map "
                    "describes a form and nothing else." % (where, k))
            _no_credential_keys(v, where)
    elif isinstance(node, list):
        for v in node:
            _no_credential_keys(v, where)


def _load_file(path):
    try:
        import yaml  # noqa: WPS433 — optional, as in bodies.py
    except ImportError:
        return _minyaml.load_path(path)
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_maps(path=None, rules=None):
    """Every mapping in the directory, validated. {id: PacketMap}."""
    path = path or DEFAULT_DIR
    out = {}
    for name in sorted(os.listdir(path)):
        if not name.endswith(".yaml"):
            continue
        full = os.path.join(path, name)
        raw = _load_file(full)
        if not isinstance(raw, dict):
            raise PacketMapError("%s did not parse to a mapping" % full)
        m = PacketMap(raw, path=full)
        m.validate(rules)
        if m.id in out:
            raise PacketMapError("%s: duplicate map id %r" % (full, m.id))
        out[m.id] = m
    if GENERIC_ID not in out:
        raise PacketMapError("%s: no generic.yaml — every credential needs "
                             "a fallback packet map" % path)
    return out


def map_for(cred, maps):
    """The mapping a credential uses: by credential, then body, then generic."""
    for m in maps.values():
        if cred.id in m.credentials:
            return m
    for m in maps.values():
        if m.id != GENERIC_ID and m.body_id == cred.body_id \
                and not m.credentials:
            return m
    return maps[GENERIC_ID]


# ---------------------------------------------------------------------------
# entry packets
# ---------------------------------------------------------------------------

def _cycle_entries(rules, led, cred, as_of):
    state = led.get_credential_state(cred.id) or {}
    anchor = state.get("anchor_date")
    if not anchor:
        raise ValueError("no cycle anchor date recorded — set the "
                         "certification or membership date before a packet "
                         "can be built")
    start, end = E.cycle_window(cred, anchor, as_of)
    evidence = {}
    for row in led.list_evidence():
        evidence.setdefault(row["entry_uuid"], []).append(row)
    out = []
    for e in led.list_entries(status="confirmed", limit=100000):
        d = datetime.date.fromisoformat(e["activity_date"])
        if not (start <= d < end):
            continue
        if e["applies_to"] is not None and cred.id not in e["applies_to"]:
            continue
        matches = cred.buckets_for(e["tags"], e["markers"])
        if not matches:
            continue
        e["evidence"] = evidence.get(e["uuid"], [])
        out.append((e, matches[0]))
    out.sort(key=lambda p: (p[0]["activity_date"], p[0]["created_at"]))
    return start, end, out


def _bundle_name(entry, ev):
    return "%s_%s_%s" % (entry["activity_date"], entry["uuid"][:8],
                         ev["filename"])


def _resolve(field, ctx, pmap):
    src = field["source"]
    e, cred, bucket, groups = ctx["entry"], ctx["cred"], ctx["bucket"], \
        ctx["groups"]
    rules = cred.rules
    if src == "literal":
        raw = field["value"]
    elif src == "holder_choice":
        return ""
    elif src == "entry.activity_date":
        raw = datetime.date.fromisoformat(e["activity_date"]).strftime(
            field.get("format") or pmap.date_format)
    elif src == "entry.title":
        raw = e["title"]
    elif src == "entry.provider":
        raw = e["provider"]
    elif src == "entry.hours":
        raw = _n(e["hours"]) if e["hours"] else ""
    elif src == "entry.count":
        raw = str(e["count"])
    elif src == "entry.notes":
        raw = e.get("notes") or ""
    elif src == "entry.uuid":
        raw = e["uuid"]
    elif src == "entry.tags":
        raw = [t for t in e["tags"]]
    elif src == "entry.tag_labels":
        raw = [rules.tag_labels.get(t, t) for t in e["tags"]]
    elif src == "entry.markers":
        raw = [m for m in e["markers"]]
    elif src == "entry.marker_labels":
        raw = [rules.marker_labels.get(m, m) for m in e["markers"]]
    elif src == "credit.amount":
        raw = _n(ctx["credits"])
    elif src == "credit.unit":
        raw = cred.unit_label
    elif src == "bucket.id":
        raw = bucket.id
    elif src == "bucket.name":
        raw = bucket.name or bucket.id
    elif src == "group.id":
        raw = list(groups)
    elif src == "group.name":
        raw = [(cred.group(g) or {}).get("name") or g for g in groups]
    elif src == "holder.name":
        raw = ctx["holder"] or ""
    elif src == "credential.name":
        raw = cred.name
    elif src == "body.name":
        raw = cred.body_name
    elif src == "evidence.filenames":
        raw = [_bundle_name(e, ev) for ev in e["evidence"]]
    elif src == "evidence.count":
        raw = str(len(e["evidence"]))
    else:                                    # validate() makes this unreachable
        raise PacketMapError("unknown source %r" % src)

    table = field.get("map")
    values = raw if isinstance(raw, list) else [raw]
    if table is not None:
        values = [table.get(v, field.get("default", v)) for v in values]
    values = [str(v) for v in values if v not in (None, "")]
    value = ", ".join(values)
    if not value and field.get("default") is not None and table is None:
        value = str(field["default"])
    return value


def entry_packets(rules, led, cred_id, as_of=None, maps=None,
                  unsubmitted_only=False):
    """Copy-ready fields for every counted entry in one credential's cycle."""
    as_of = as_of or datetime.date.today()
    cred = rules.get(cred_id)
    maps = maps if maps is not None else load_maps(rules=rules)
    pmap = map_for(cred, maps)
    start, end, rows = _cycle_entries(rules, led, cred, as_of)
    holder = led.get_config("attendee_name") or ""
    mode = E.group_mode(cred)
    rules_u = cred.unverified_value()
    out = []
    for e, bucket in rows:
        submitted = cred.body_id in (e["submitted_to"] or [])
        if unsubmitted_only and submitted:
            continue
        credits = bucket.credits_for(e["hours"], e["count"],
                                     (e["overrides"] or {}).get(cred.id))
        ctx = {"entry": e, "cred": cred, "bucket": bucket, "credits": credits,
               "groups": E._groups_for(cred, mode, bucket, e["markers"]),
               "holder": holder}
        fields = []
        for f in pmap.fields:
            value = _resolve(f, ctx, pmap)
            choice = f["source"] == "holder_choice"
            fields.append({
                "label": f["label"],
                "value": value,
                "holder_choice": choice,
                "required": bool(f.get("required")),
                "missing": bool(f.get("required")) and not value
                and not choice,
                "note": f.get("note"),
                "unverified": rules_u if (f["source"] in RULE_SOURCES
                                          and value) else None,
            })
        out.append({
            "uuid": e["uuid"],
            "activity_date": e["activity_date"],
            "title": e["title"],
            "credits": credits,
            "submitted": submitted,
            "submitted_on": (e.get("submitted_dates") or {}).get(cred.body_id),
            "fields": fields,
            "evidence": e["evidence"],
        })
    return {
        "credential": cred.id,
        "name": cred.name,
        "body": cred.body_name,
        "body_id": cred.body_id,
        "unit_label": cred.unit_label,
        "cycle_start": start.isoformat(),
        "cycle_end": (end - datetime.timedelta(days=1)).isoformat(),
        "as_of": str(as_of),
        "map": {"id": pmap.id, "form_name": pmap.form_name,
                "verified": pmap.verified,
                "verified_note": pmap.verified_note,
                "source_url": pmap.source_url,
                "holder_note": pmap.holder_note},
        "rules_unverified": rules_u,
        "unsubmitted_only": bool(unsubmitted_only),
        "entries": out,
    }


def render_entry_text(packet, entry):
    """One entry's fields, one `Label: value` per line, ready to copy."""
    lines = ["%s — %s" % (packet["name"], packet["map"]["form_name"]),
             "Entry %s (%s)%s" % (entry["uuid"][:8], entry["activity_date"],
                                 "  [already submitted %s]"
                                 % (entry["submitted_on"] or "")
                                 if entry["submitted"] else ""),
             ""]
    marked = False
    for f in entry["fields"]:
        value = HOLDER_CHOICE if f["holder_choice"] else (
            f["value"] or ("[MISSING]" if f["missing"] else ""))
        if f.get("unverified"):
            value += " [unverified*]"
            marked = True
        lines.append("%s: %s" % (f["label"], value))
        if f["note"]:
            lines.append("    (%s)" % f["note"])
    if marked:
        lines += ["", "* unverified: this value " + _unverified_footnote(
            packet["rules_unverified"])]
    return "\n".join(lines) + "\n"


def _unverified_footnote(u):
    return ("comes from rules not confirmed on the body's own page. Check it "
            "at %s before you submit. Why: %s"
            % (u.get("source_url") or "the body's site", u["note"]))


def render_text(packet):
    """The whole packet as plain text: header, caveats, then every entry."""
    m = packet["map"]
    out = ["%s (%s) — submission packet" % (packet["name"], packet["body"]),
           "Cycle %s to %s, prepared %s" % (packet["cycle_start"],
                                            packet["cycle_end"],
                                            packet["as_of"]),
           "Form: %s (packet map %r)" % (m["form_name"], m["id"]),
           ""]
    if not m["verified"]:
        out += ["UNVERIFIED FIELD MAP: %s" % (m["verified_note"] or
                                             "not checked against the live "
                                             "portal"), ""]
    if packet["rules_unverified"]:
        out += ["UNVERIFIED RULES: credits, category and group "
                + _unverified_footnote(packet["rules_unverified"]), ""]
    out += ["You submit. Log into the body's portal yourself; Registrar never "
            "asks for or stores portal logins. Copy each field below.", ""]
    if m["holder_note"]:
        out += [m["holder_note"], ""]
    if not packet["entries"]:
        out.append("No %sentries in this cycle."
                   % ("unsubmitted " if packet["unsubmitted_only"] else
                      "counted "))
    for entry in packet["entries"]:
        out.append("-" * 72)
        out.append(render_entry_text(packet, entry))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# evidence bundle
# ---------------------------------------------------------------------------

def _zinfo(name, when):
    zi = zipfile.ZipInfo(name, date_time=(when.year, when.month, when.day,
                                          0, 0, 0))
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o600 << 16
    return zi


def bundle(rules, led, cred_id, as_of=None, maps=None,
           unsubmitted_only=False):
    """Build the evidence bundle in memory. Returns (zip_bytes, manifest)."""
    as_of = as_of or datetime.date.today()
    packet = entry_packets(rules, led, cred_id, as_of=as_of, maps=maps,
                           unsubmitted_only=unsubmitted_only)
    root = "%s-evidence-%s" % (cred_id, as_of)
    buf = io.BytesIO()
    gaps, entries_out = [], []
    with zipfile.ZipFile(buf, "w") as zf:
        for entry in packet["entries"]:
            files = []
            if not entry["evidence"]:
                gaps.append({"entry": entry["uuid"], "file": None,
                             "why": "no evidence attached to a counted entry"})
            for ev in entry["evidence"]:
                name = _bundle_name(entry, ev)
                rec = {"path": "evidence/%s" % name,
                       "original_filename": ev["filename"],
                       "sha256": ev["sha256"], "bytes": ev["bytes"],
                       "added_at": ev["added_at"]}
                full = led.evidence_path(ev)
                if not os.path.isfile(full):
                    rec["status"] = "MISSING"
                    gaps.append({"entry": entry["uuid"],
                                 "file": ev["filename"],
                                 "why": "file missing from disk"})
                    files.append(rec)
                    continue
                # Read once, hash what was read, bundle what was hashed: no
                # window in which the zipped bytes differ from the checked.
                with open(full, "rb") as fh:
                    data = fh.read()
                if hashlib.sha256(data).hexdigest() != ev["sha256"]:
                    rec["status"] = "ALTERED"
                    gaps.append({"entry": entry["uuid"],
                                 "file": ev["filename"],
                                 "why": "hash no longer matches the one "
                                        "recorded at upload — NOT bundled"})
                    files.append(rec)
                    continue
                rec["status"] = "OK"
                zf.writestr(_zinfo("%s/%s" % (root, rec["path"]), as_of),
                            data)
                files.append(rec)
            zf.writestr(_zinfo("%s/packets/%s_%s.txt"
                               % (root, entry["activity_date"],
                                  entry["uuid"][:8]), as_of),
                        render_entry_text(packet, entry))
            entries_out.append({
                "uuid": entry["uuid"],
                "activity_date": entry["activity_date"],
                "title": entry["title"],
                "credits": entry["credits"],
                "submitted": entry["submitted"],
                "fields": [{"label": f["label"], "value": f["value"],
                            "holder_choice": f["holder_choice"],
                            "unverified": bool(f["unverified"])}
                           for f in entry["fields"]],
                "files": files,
            })
        manifest = {
            "format": "registrar-evidence-bundle-v1",
            "credential": packet["credential"],
            "name": packet["name"],
            "body": packet["body"],
            "unit_label": packet["unit_label"],
            "cycle_start": packet["cycle_start"],
            "cycle_end": packet["cycle_end"],
            "as_of": packet["as_of"],
            "packet_map": packet["map"],
            "rules_unverified": packet["rules_unverified"],
            "unsubmitted_only": packet["unsubmitted_only"],
            "files_bundled": sum(1 for e in entries_out for f in e["files"]
                                 if f["status"] == "OK"),
            "gaps": gaps,
            "entries": entries_out,
        }
        zf.writestr(_zinfo("%s/manifest.json" % root, as_of),
                    json.dumps(manifest, indent=1, sort_keys=True))
        zf.writestr(_zinfo("%s/MANIFEST.md" % root, as_of),
                    _render_manifest(manifest))
        zf.writestr(_zinfo("%s/PACKET.txt" % root, as_of),
                    render_text(packet))
    return buf.getvalue(), manifest


def _render_manifest(m):
    out = ["# %s — evidence bundle" % m["name"], "",
           "%s | cycle %s to %s | prepared %s" % (m["body"], m["cycle_start"],
                                                 m["cycle_end"], m["as_of"]),
           "",
           "Packet map: `%s` (%s)%s" % (
               m["packet_map"]["id"], m["packet_map"]["form_name"],
               "" if m["packet_map"]["verified"]
               else " — **UNVERIFIED field map**"),
           ""]
    if m.get("rules_unverified"):
        u = m["rules_unverified"]
        out += ["Rules: **unverified** — credits, category and group in the "
                "packets come from rules not confirmed on the body's own "
                "page. Source: %s. Why: %s"
                % (u.get("source_url") or "—", u["note"]), ""]
    # Gaps first, as in the renewal packet: nobody should find one by
    # reading to the bottom.
    out += ["## Gaps", ""]
    if m["gaps"]:
        for g in m["gaps"]:
            out.append("- entry `%s`%s: %s"
                       % (g["entry"][:8],
                          " (`%s`)" % g["file"] if g["file"] else "",
                          g["why"]))
    else:
        out.append("None: every entry has evidence and every file "
                   "re-hashed clean.")
    out += ["", "## Files", "",
            "| Entry | Date | File | sha256 | Status |",
            "|---|---|---|---|---|"]
    for e in m["entries"]:
        if not e["files"]:
            out.append("| `%s` | %s | **none** | | |"
                       % (e["uuid"][:8], e["activity_date"]))
        for f in e["files"]:
            out.append("| `%s` | %s | `%s` | `%s` | %s |"
                       % (e["uuid"][:8], e["activity_date"], f["path"],
                          f["sha256"], f["status"]))
    out += ["", "%d file(s) bundled. Hashes are sha256 as recorded at upload "
            "and re-checked when this bundle was built." % m["files_bundled"],
            ""]
    return "\n".join(out)


def _n(value):
    if value is None:
        return ""
    return ("%.2f" % float(value)).rstrip("0").rstrip(".")

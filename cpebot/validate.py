"""Entry-time validation: rules-based advisories, plus a seam for more.

Two layers, deliberately unequal:

**RulesValidator** is the compliance truth. Everything it says is derived
from `bodies.yaml` and the engine's own arithmetic — bucket match, caps,
override requirements, unverified bodies, evidence expectations. It is
always on and it never guesses.

**LLMValidator** is a SEAM, not a feature. v0.2 ships the interface and the
UI slot so that a future change can wire a model behind it without touching
the web layer, and nothing else: it is disabled by default via config
(`llm_validator_enabled`), it handles no API keys, and it makes no network
calls in any configuration. Even switched on, it announces itself as a stub
rather than pretending to have judged anything — a validator that fabricates
confidence is worse than no validator, for the same reason an untrusted
compliance record is worse than none.
"""

from . import __version__

INFO = "info"
CAUTION = "caution"
WARNING = "warning"


def advisories(rules, entry, assessments=None):
    """Rules-based advisory lines for one entry (ledger or form shape).

    Returns [{level, credential, text}] — advisory, never blocking: the
    human confirms, the panel just makes sure they confirm with the caps,
    floors and paperwork rules in view.
    """
    out = []
    tags = list(entry.get("tags") or [])
    markers = list(entry.get("markers") or [])
    hours = float(entry.get("hours") or 0)
    count = int(entry.get("count") or 1)
    overrides = dict(entry.get("overrides") or {})
    applies_to = entry.get("applies_to")

    for cred in rules.credentials:
        if applies_to is not None and cred.id not in applies_to:
            continue
        a = (assessments or {}).get(cred.id)

        matches = cred.buckets_for(tags, markers)
        if not matches:
            out.append({
                "level": INFO, "credential": cred.id,
                "text": "%s: no category claims this entry — it will count "
                        "for nothing here." % cred.name})
            continue
        bucket = matches[0]
        credits = bucket.credits_for(hours, count, overrides.get(cred.id))

        if bucket.value_is_override and cred.id not in overrides:
            out.append({
                "level": WARNING, "credential": cred.id,
                "text": "%s: bucket %r has no computable rate and this entry "
                        "carries no override — it will be counted as 0. Set "
                        "an override with the body-published figure."
                        % (cred.name, bucket.name or bucket.id)})
        else:
            out.append({
                "level": INFO, "credential": cred.id,
                "text": "%s: counts under %r as %s %s."
                        % (cred.name, bucket.name or bucket.id,
                           _n(credits), cred.unit_label)})

        if bucket.cap is not None:
            held = 0.0
            if a is not None:
                for b in a.buckets:
                    if b.get("id") == bucket.id:
                        held = b.get("awarded", 0.0)
            if a is not None and held + credits > bucket.cap:
                room = max(0.0, bucket.cap - held)
                out.append({
                    "level": CAUTION, "credential": cred.id,
                    "text": "%s: %r caps at %s per cycle and already holds "
                            "%s — only %s of this entry's %s can survive."
                            % (cred.name, bucket.name or bucket.id,
                               _n(bucket.cap), _n(held), _n(room),
                               _n(credits))})
            else:
                out.append({
                    "level": INFO, "credential": cred.id,
                    "text": "%s: %r caps at %s per cycle."
                            % (cred.name, bucket.name or bucket.id,
                               _n(bucket.cap))})

        if a is not None:
            for g in a.groups:
                if g.get("floor") and g.get("shortfall") and markers:
                    feeds = set(markers) & set(
                        cred.group_marker_map.get(g["id"], []))
                    if feeds:
                        out.append({
                            "level": INFO, "credential": cred.id,
                            "text": "%s: this entry feeds %r, currently %s "
                                    "of a required %s."
                                    % (cred.name, g["name"], _n(g["awarded"]),
                                       _n(g["floor"]))})

        if cred.verified is False:
            out.append({
                "level": CAUTION, "credential": cred.id,
                "text": "%s: this body's rules are UNVERIFIED in bodies.yaml "
                        "— treat the numbers as advisory until they are "
                        "checked against the portal." % cred.name})
        if cred.caps_known is False:
            out.append({
                "level": WARNING, "credential": cred.id,
                "text": "%s: per-category caps are NOT encoded (caps_known: "
                        "false). The engine refuses a compliance verdict "
                        "here; nothing this panel says about %s is a pass."
                        % (cred.name, cred.name)})

    if not int(entry.get("evidence_count") or 0):
        out.append({
            "level": CAUTION, "credential": "",
            "text": "No evidence attached. Confirming without it is fine — "
                    "log now, evidence later — but audit rules mean the "
                    "certificate has to land here eventually."})
    return out


class Validator(object):
    """The seam. A validator looks at one entry and returns advisory lines."""

    name = "base"
    label = "validator"

    def enabled(self, led):
        raise NotImplementedError

    def validate(self, rules, entry, assessments=None):
        """Return [{level, credential, text}]. Advisory only, never blocking."""
        raise NotImplementedError


class RulesValidator(Validator):
    name = "rules"
    label = "Rules-based checks (bodies.yaml)"

    def enabled(self, led):
        return True

    def validate(self, rules, entry, assessments=None):
        return advisories(rules, entry, assessments)


class LLMValidator(Validator):
    """Interface + UI slot only. No model, no keys, no network — v0.2."""

    name = "llm"
    label = "LLM sanity check"
    CONFIG_KEY = "llm_validator_enabled"

    def enabled(self, led):
        return bool(led.get_config(self.CONFIG_KEY, False))

    def validate(self, rules, entry, assessments=None):
        # Deliberately honest: switched on, the stub says it is a stub. It
        # never fabricates a judgement and it never opens a socket.
        return [{
            "level": INFO, "credential": "",
            "text": "LLM validator is enabled but not wired in v%s — this "
                    "is the seam, not the feature. Rules-based checks above "
                    "are the compliance truth." % __version__}]


VALIDATORS = (RulesValidator(), LLMValidator())


def run_validators(rules, led, entry, assessments=None):
    """Every enabled validator's lines, plus the disabled ones' names."""
    lines, disabled = [], []
    for v in VALIDATORS:
        if v.enabled(led):
            lines.extend(v.validate(rules, entry, assessments))
        else:
            disabled.append(v)
    return {"lines": lines, "disabled": disabled}


def _n(value):
    if value is None:
        return "—"
    return ("%.2f" % float(value)).rstrip("0").rstrip(".")

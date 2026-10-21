"""Private-network-only web app.

Stdlib only, one file, sqlite behind it, bound to loopback by default or to
one private interface you already trust (a VPN or overlay network) — never
to a public one. There is no login form and that is deliberate — the
private network IS the boundary. Adding a password here would add a
credential to store, rotate and leak without adding a single attacker who
could reach the port.

    CPEBOT_BIND    default 127.0.0.1   (never 0.0.0.0)
    CPEBOT_PORT    default 8762
    CPEBOT_DB      default <repo>/cpebot.db
    CPEBOT_EVIDENCE default <repo>/evidence
    CPEBOT_BODIES  default <repo>/bodies.yaml
    CPEBOT_ALLOWED_HOSTS  optional, comma-separated extra host names

Every request is refused with a 400 unless its Host header (port ignored)
names localhost, 127.0.0.1, [::1], the bind address, or an entry in
CPEBOT_ALLOWED_HOSTS. A page loaded from any other name cannot talk to the
app, which is what stops a DNS-rebinding page from reaching it through the
browser.

Pages: dashboard, per-credential detail, entries table with filters, entry
editor with evidence upload (edits to a confirmed entry route through
supersede — corrected copy written, original kept), submissions tracker,
charts (server-rendered SVG, no scripts), what-if calculator, exports,
rules render, renewal report, packet export, backfill import, settings.
v1 adds the deadline calendar (/deadlines.ics) and per-entry submission
packets with an evidence bundle (/packets/<credential>, .zip).

v0.2 hardening: a per-install CSRF token is injected into every POST form
at the single point all pages render through, and checked on every POST;
responses carry a CSP that permits no scripts at all, no framing and no
referrer; evidence uploads are validated by size, extension AND magic bytes
at this boundary; evidence is served with a sniff-derived content type
(never the stored filename's claim), attachment by default, behind a
resolved-path prefix check on the evidence root.
"""

import datetime
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import tempfile
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__ as VERSION
from . import backfill as BF
from . import bodies as B
from . import charts as C
from . import engine as E
from . import exports as X
from . import ics as I
from . import ledger as L
from . import packets as P
from . import reminders as RM
from . import report as R
from . import validate as V
from . import whatif as W

BIND = os.environ.get("CPEBOT_BIND", "127.0.0.1")
PORT = int(os.environ.get("CPEBOT_PORT", "8762"))
ALLOWED_HOSTS = [h for h in os.environ.get("CPEBOT_ALLOWED_HOSTS", "").split(",")
                 if h.strip()]
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
# One evidence file caps at 15MB (L.MAX_EVIDENCE_BYTES); the request body
# cap is that plus headroom for the form fields around it.
MAX_BODY = 16 * 1024 * 1024

STATUS_CLASS = {
    L.CAPTURED: "pill warn", L.CONFIRMED: "pill ok",
    L.REJECTED: "pill bad", L.SUPERSEDED: "pill mute",
    L.VOID: "pill void",
}
VERDICT_CLASS = {
    E.COMPLIANT: "ok", E.ON_PACE: "ok", E.BEHIND: "warn", E.SHORT: "bad",
    E.UNVERIFIED_CANNOT_ASSERT: "mute",
}

CSS = """
:root{--fg:#2a2622;--mute:#6a645a;--line:#e4e0d8;--bg:#f6f4ee;--card:#fdfcfa;
--ok:#2f6f4f;--warn:#8a6116;--bad:#96302c;--accent:#3b5ea6;--soft:#f1efe8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line);padding:.7rem 1rem;
display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap;position:sticky;top:0;z-index:5}
header b{font-size:1.05rem;letter-spacing:.01em}
header a{color:var(--accent);text-decoration:none;font-size:.92rem}
header a:hover{text-decoration:underline}
main{max-width:1080px;margin:0 auto;padding:1.2rem 1rem 4rem}
h1{font-size:1.45rem;margin:.2rem 0 1rem;letter-spacing:-.01em}
h2{font-size:1.15rem;margin:1.6rem 0 .5rem}
h3{font-size:.85rem;margin:1.2rem 0 .4rem;color:var(--mute);
text-transform:uppercase;letter-spacing:.05em}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:1rem 1.1rem;margin:0 0 1rem}
.card>h2:first-child,.card>h3:first-child{margin-top:.1rem}
.grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fill,minmax(330px,1fr))}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:.42rem .5rem;border-bottom:1px solid var(--line);
vertical-align:top}
th{color:var(--mute);font-weight:600;font-size:.78rem;text-transform:uppercase;
letter-spacing:.03em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto}
.bar{background:#ebe8e0;border-radius:3px;height:9px;position:relative;overflow:hidden;
margin:.25rem 0}
.bar i{display:block;height:100%;background:var(--accent)}
.bar i.ok{background:var(--ok)}.bar i.warn{background:var(--warn)}
.bar i.bad{background:var(--bad)}
.bar u{position:absolute;top:-2px;bottom:-2px;width:2px;background:#333;opacity:.55}
.pill{display:inline-block;padding:.06rem .5rem;border-radius:99px;font-size:.74rem;
font-weight:600;letter-spacing:.02em;border:1px solid}
.pill.ok{color:var(--ok);border-color:#bcd8c8;background:#f0f7f3}
.pill.warn{color:var(--warn);border-color:#e6d5ac;background:#fbf6ea}
.pill.bad{color:var(--bad);border-color:#e3bfbd;background:#fbf0ef}
.pill.mute{color:var(--mute);border-color:var(--line);background:var(--soft)}
.pill.void{color:var(--mute);border-color:var(--line);background:var(--soft);
text-decoration:line-through}
.mute{color:var(--mute)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.banner{border-left:3px solid var(--warn);background:#fbf6ea;padding:.6rem .8rem;
border-radius:0 6px 6px 0;margin:0 0 1rem;font-size:.9rem}
.banner.bad{border-color:var(--bad);background:#fbf0ef}
.banner.ok{border-color:var(--ok);background:#f0f7f3}
label,legend{display:block;font-size:.8rem;color:var(--mute);margin:.6rem 0 .15rem}
fieldset{border:0;margin:0;padding:0;min-width:0}legend{padding:0}
input,select,textarea{width:100%;padding:.42rem .5rem;border:1px solid var(--line);
border-radius:5px;background:#fff;font:inherit;font-size:.9rem}
textarea{min-height:4.5rem}
.row{display:flex;gap:.8rem;flex-wrap:wrap}
.row>div{flex:1 1 150px}
button,.btn{background:var(--accent);color:#fff;border:1px solid var(--accent);
padding:.42rem .9rem;border-radius:5px;font:inherit;font-size:.88rem;cursor:pointer;
text-decoration:none;display:inline-block;margin:.9rem .35rem 0 0}
button.sec,.btn.sec{background:#fff;color:var(--fg);border-color:var(--line)}
button.danger{background:var(--bad);border-color:var(--bad)}
a{color:var(--accent)}
code{background:var(--soft);padding:.05rem .28rem;border-radius:3px;font-size:.85em}
pre{background:#f7f5f0;border:1px solid var(--line);border-radius:6px;padding:.8rem;
overflow-x:auto;font-size:.82rem;line-height:1.45}
.small{font-size:.82rem}
figure{margin:0 0 .3rem;padding:0}
figure svg{display:block;max-width:100%;height:auto}
details{margin:.4rem 0 0}
summary{cursor:pointer;color:var(--mute);font-size:.82rem}
.advisory{list-style:none;margin:.2rem 0 0;padding:0}
.advisory li{padding:.35rem 0;border-bottom:1px solid var(--line);font-size:.88rem}
.advisory li:last-child{border-bottom:0}
.lvl{display:inline-block;min-width:4.2rem;text-align:center;margin-right:.5rem;
padding:.02rem .4rem;border-radius:3px;font-size:.68rem;font-weight:700;
text-transform:uppercase;letter-spacing:.05em}
.lvl.info{color:var(--mute);background:var(--soft)}
.lvl.caution{color:var(--warn);background:#fbf6ea}
.lvl.warning{color:var(--bad);background:#fbf0ef}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:.25rem 1.2rem;
margin:.4rem 0}
dl.kv dt{color:var(--mute);font-size:.82rem}
dl.kv dd{margin:0}
footer{max-width:1080px;margin:0 auto;padding:0 1rem 3rem;color:var(--mute);
font-size:.8rem}
"""

NAV = [("/", "Dashboard"), ("/entries", "Entries"), ("/entry/new", "Log"),
       ("/submissions", "Submissions"), ("/charts", "Charts"),
       ("/whatif", "What-if"), ("/report", "Report"), ("/export", "Export"),
       ("/bodies", "Rules"), ("/backfill", "Import"),
       ("/settings", "Settings")]


# ---------------------------------------------------------------------------
# rendering helpers
# ---------------------------------------------------------------------------

def esc(value):
    return html.escape("" if value is None else str(value))


def fmt(value, dash="—"):
    if value is None:
        return dash
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


_POST_FORM_RE = re.compile(r"<form\b[^>]*\bmethod=post\b[^>]*>")


def _page(title, body, banner=None, csrf=None):
    nav = " ".join('<a href="%s">%s</a>' % (h, esc(t)) for h, t in NAV)
    ban = ""
    if banner:
        kind, text = banner
        ban = '<div class="banner %s">%s</div>' % (esc(kind), text)
    if csrf:
        # Every POST form carries the anti-forgery token, structurally: the
        # injection happens here, at the one point every page renders
        # through, so a new form cannot forget it.
        field = '<input type=hidden name=csrf value="%s">' % esc(csrf)
        body = _POST_FORM_RE.sub(lambda m: m.group(0) + field, body)
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s · cpebot</title>"
            "<link rel=stylesheet href=/static/cpebot.css></head><body>"
            "<header><b>cpebot</b>%s</header><main>%s%s</main>"
            "<footer>Private-network only. No authentication by design — the "
            "network boundary is the access control. Rules render from <code>bodies.yaml</code>; "
            "anything marked UNVERIFIED was not confirmed against a primary "
            "source and must be checked before you rely on it.</footer>"
            "</body></html>"
            % (esc(title), nav, ban, body)).encode("utf-8")


def bar(value, target, cap=None, marker_fraction=None, klass=""):
    """A progress bar with the pace line drawn on it.

    The tick mark is where you should be today. A bar without it just says
    "some progress"; with it, the bar answers the only question that matters.
    """
    target = float(target or 0)
    pct = 0 if not target else min(100.0, 100.0 * float(value or 0) / target)
    tick = ""
    if marker_fraction is not None:
        tick = '<u style="left:%.1f%%"></u>' % min(100.0, 100.0 * marker_fraction)
    capnote = ""
    if cap:
        capnote = ' <span class="small mute">cap %s</span>' % fmt(cap)
    return ('<div class="bar"><i class="%s" style="width:%.1f%%"></i>%s</div>'
            '<div class="small mute">%s of %s%s</div>'
            % (klass, pct, tick, fmt(float(value or 0)), fmt(target), capnote))


def link(href, text):
    return '<a href="%s">%s</a>' % (href, esc(text))


def source_link(url):
    """A rules source as a link, but only for http(s); any other scheme
    (javascript:, data:, ...) is shown as plain text and never as an href."""
    if B.is_web_url(url):
        return '<a href="%s">%s</a>' % (esc(url), esc(url))
    return esc(url)


def unverified_mark(u):
    """The visible marker for one rule value that was not confirmed on the
    body's own page: the tooltip says why, the link goes to that page."""
    if not u:
        return ""
    title = "Not confirmed on the body's own page: %s" % u["note"]
    url = u.get("source_url") or ""
    if B.is_web_url(url):
        return (' <a class="pill warn unverified" href="%s" title="%s" '
                'rel="noreferrer">unverified</a>' % (esc(url), esc(title)))
    return ' <span class="pill warn unverified" title="%s">unverified</span>' \
        % esc(title)


def opts(values, selected=None, blank=None):
    out = []
    if blank is not None:
        out.append('<option value="">%s</option>' % esc(blank))
    for v in values:
        val, lab = (v if isinstance(v, tuple) else (v, v))
        sel = " selected" if str(val) == str(selected or "") else ""
        out.append('<option value="%s"%s>%s</option>' % (esc(val), sel, esc(lab)))
    return "".join(out)


# ---------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------

class App(object):

    def __init__(self, rules=None, led=None):
        self.rules = rules or B.load(os.environ.get("CPEBOT_BODIES"))
        self.led = led or L.Ledger()
        self._csrf = None

    def csrf(self):
        """The per-install anti-forgery token, minted once into config."""
        if self._csrf is None:
            self._csrf = self.led.setdefault_config(
                "csrf_token", secrets.token_hex(32))
        return self._csrf

    def page(self, title, body, banner=None):
        return _page(title, body, banner, csrf=self.csrf())

    def _assessments(self, as_of=None):
        """Current-cycle assessment per anchored credential."""
        anchors = self.led.anchors()
        carry = self.led.carryover_in()
        feed = self.led.engine_entries()
        out = {}
        for cred in self.rules.credentials:
            anchor = anchors.get(cred.id)
            if not anchor:
                continue
            try:
                out[cred.id] = E.assess(
                    cred, feed, anchor=anchor, as_of=as_of,
                    carryover_in=float(carry.get(cred.id) or 0))
            except E.EngineError:
                continue
        return out

    # -- dashboard ---------------------------------------------------------

    def dashboard(self, q):
        as_of = _as_of(q)
        anchors = self.led.anchors()
        cards = []
        for cred in self.rules.credentials:
            if cred.id not in anchors:
                cards.append(
                    '<div class="card"><h2>%s</h2><p class="mute small">%s</p>'
                    '<p class="small">No cycle anchor recorded, so nothing can '
                    'be asserted about this credential. %s</p></div>'
                    % (esc(cred.name), esc(cred.body_name),
                       link("/settings", "Set the anchor date")))
                continue
            rep = R.credential_report(self.rules, self.led, cred.id, as_of)
            cards.append(self._cred_card(cred, rep))
        stats = self.led.stats()
        cc = self.led.capture_counts()
        pending = stats["entries"].get(L.CAPTURED, 0)
        banner = None
        if pending:
            banner = ("warn", '%d captured %s awaiting confirmation — nothing '
                              'counts until you confirm it. %s'
                      % (pending, "entry" if pending == 1 else "entries",
                         link("/entries?status=captured", "Review")))
        body = ['<h1>Portfolio</h1>', '<div class="grid">%s</div>' % "".join(cards)]
        body.append(
            '<div class="card"><h3>Instrumentation</h3>'
            '<p class="small">%d real captures in the last %d days against a '
            'kill threshold of %d — <b class="%s">%s</b>. Backfill and fixture '
            'rows are excluded on purpose: the question is whether this tool '
            'is actually used, and a one-off import cannot answer it.</p>'
            '<p class="small mute">%d entries confirmed, %d captured, '
            '%d evidence files.</p></div>'
            % (cc["real"], cc["window_days"], cc["threshold"],
               "bad" if cc["falsified"] else "ok",
               "BELOW THRESHOLD" if cc["falsified"] else "above threshold",
               stats["entries"].get(L.CONFIRMED, 0), pending,
               stats["evidence_files"]))
        return self.page("Dashboard", "".join(body), banner)

    def _cred_card(self, cred, rep):
        a, p = rep["assessment"], rep["pace"]
        klass = VERDICT_CLASS.get(a["verdict"], "mute")
        out = ['<div class="card">',
               '<h2>%s <span class="pill %s">%s</span></h2>'
               % (esc(cred.name), klass, esc(a["verdict"])),
               '<p class="small mute">%s · cycle %s to %s · %s %s required%s</p>'
               % (esc(cred.body_name), a["cycle_start"], a["cycle_end"],
                  fmt(a["required"]), esc(cred.unit_label),
                  unverified_mark(cred.unverified_value()))]
        if rep["advisory_only"]:
            out.append('<div class="banner">This body\'s category caps could '
                       'not be verified, so the figure below is an UPPER BOUND '
                       'and cpebot will not assert compliance.</div>')
        out.append(bar(a["awarded"], a["required"],
                       marker_fraction=p["elapsed_fraction"],
                       klass=klass if klass in ("ok", "warn", "bad") else ""))
        if a["forfeited"]:
            out.append('<p class="small warn">%s %s earned but not counted '
                       '(category caps). A naive total would have said %s.</p>'
                       % (fmt(a["forfeited"]), esc(cred.unit_label),
                          fmt(a["naive_total"])))
        for g in a["groups"]:
            if not (g["floor"] or g["cap"]):
                continue
            gk = "bad" if g["shortfall"] else "ok"
            out.append('<div class="small" style="margin-top:.5rem">%s</div>'
                       % esc(g["name"]))
            out.append(bar(g["awarded"], g["floor"] or g["cap"], cap=g["cap"],
                           marker_fraction=p["elapsed_fraction"] if g["floor"]
                           else None, klass=gk if g["floor"] else ""))
        out.append('<p class="small">%d days left · %s %s/month needed · '
                   'expected %s by now</p>'
                   % (p["days_left"], fmt(p["per_month_needed"]),
                      esc(cred.unit_label), fmt(p["expected_by_now"])))
        overdue = [d for d in rep["submission"]["deadlines"] if d["overdue"]]
        if overdue:
            out.append('<div class="banner bad small">%d registration window(s) '
                       'closed with credits unregistered. Those credits do not '
                       'come back.</div>' % len(overdue))
        if rep["unverified_reasons"]:
            out.append('<p class="small warn">%d rule(s) UNVERIFIED</p>'
                       % len(rep["unverified_reasons"]))
        out.append('<p>%s %s</p>' % (
            link("/credential/%s" % cred.id, "Detail"),
            link("/report?credential=%s" % cred.id, "· Renewal report")))
        out.append("</div>")
        return "".join(out)

    # -- credential detail -------------------------------------------------

    def credential(self, cred_id, q):
        cred = self.rules.get(cred_id)
        as_of = _as_of(q)
        rep = R.credential_report(self.rules, self.led, cred_id, as_of)
        if rep.get("incomplete"):
            return self.page(cred.name,
                        '<h1>%s</h1><div class="card"><p>%s</p><p>%s</p></div>'
                        % (esc(cred.name), esc(rep["reason"]),
                           link("/settings", "Settings")))
        a = rep["assessment"]
        out = ['<h1>%s <span class="pill %s">%s</span></h1>'
               % (esc(cred.name), VERDICT_CLASS.get(a["verdict"], "mute"),
                  esc(a["verdict"])),
               '<p class="mute">%s · %s · source %s</p>'
               % (esc(cred.body_name), esc(cred.unit_label),
                  ('%s (verified %s)'
                   % (source_link(cred.source_url),
                      esc(cred.verified_date))) if cred.source_url else "—")]
        out.append(self._cred_card(cred, rep))
        out.append('<div class="card"><h3>Categories</h3><div class="wrap">'
                   '<table><tr><th>Category</th><th class=num>Raw</th>'
                   '<th class=num>Counted</th><th class=num>Lost to cap</th>'
                   '<th class=num>Cap</th></tr>')
        for b in a["buckets"]:
            out.append('<tr><td>%s</td><td class=num>%s</td><td class=num>%s</td>'
                       '<td class="num %s">%s</td><td class=num>%s%s</td></tr>'
                       % (esc(b["name"]), fmt(b["raw"]), fmt(b["awarded"]),
                          "warn" if b["forfeited"] else "mute",
                          fmt(b["forfeited"], "—"), fmt(b["cap"]),
                          unverified_mark(cred.unverified_value(
                              "cap", cred.bucket(b["id"])))))
        out.append("</table></div></div>")
        out.append('<div class="card"><h3>Renewal report</h3><pre>%s</pre>'
                   '<p>%s</p></div>'
                   % (esc(R.render_markdown(rep)),
                      link("/packet/%s" % cred.id, "Download submission packet")))
        out.append('<div class="card"><h3>Submit</h3><p>%s &middot; %s'
                   ' &middot; %s</p></div>'
                   % (link("/packets/%s" % cred.id,
                           "Copy-ready fields per entry"),
                      link("/packets/%s.zip" % cred.id, "Evidence bundle (zip)"),
                      link("/deadlines.ics?credential=%s" % cred.id,
                           "Deadlines (.ics)")))
        return self.page(cred.name, "".join(out))

    # -- entries -----------------------------------------------------------

    def entries(self, q):
        f = dict(status=q.get("status", [""])[0], credential=q.get("credential", [""])[0],
                 tag=q.get("tag", [""])[0], since=q.get("since", [""])[0],
                 until=q.get("until", [""])[0], q=q.get("q", [""])[0],
                 provider=q.get("provider", [""])[0],
                 marker=q.get("marker", [""])[0],
                 evidence=q.get("evidence", [""])[0])
        rows = self.led.list_entries(
            status=f["status"] or None, credential=f["credential"] or None,
            tag=f["tag"] or None, since=f["since"] or None,
            until=f["until"] or None, q=f["q"] or None,
            provider=f["provider"] or None, marker=f["marker"] or None)
        ev = {}
        for r in self.led.list_evidence():
            ev[r["entry_uuid"]] = ev.get(r["entry_uuid"], 0) + 1
        if f["evidence"] == "with":
            rows = [e for e in rows if ev.get(e["uuid"])]
        elif f["evidence"] == "without":
            rows = [e for e in rows if not ev.get(e["uuid"])]
        form = (
            '<form class="card" method=get><div class="row">'
            '<div><label for=f-status>Status</label>'
            '<select id=f-status name=status>%s</select></div>'
            '<div><label for=f-credential>Credential</label>'
            '<select id=f-credential name=credential>%s</select></div>'
            '<div><label for=f-tag>Tag</label>'
            '<select id=f-tag name=tag>%s</select></div>'
            '<div><label for=f-marker>Marker</label>'
            '<select id=f-marker name=marker>%s</select></div>'
            '<div><label for=f-evidence>Evidence</label>'
            '<select id=f-evidence name=evidence>%s</select>'
            '</div>'
            '<div><label for=f-since>From</label>'
            '<input id=f-since type=date name=since value="%s"></div>'
            '<div><label for=f-until>To</label>'
            '<input id=f-until type=date name=until value="%s"></div>'
            '<div><label for=f-provider>Provider</label>'
            '<input id=f-provider name=provider value="%s"></div>'
            '<div><label for=f-q>Text</label><input id=f-q name=q value="%s"></div>'
            '</div><button>Filter</button> <a class="btn sec" href="/entries">Clear</a>'
            '</form>'
            % (opts(L.STATES, f["status"], blank="any"),
               opts([(c.id, c.name) for c in self.rules.credentials],
                    f["credential"], blank="any"),
               opts(sorted(self.rules.tags), f["tag"], blank="any"),
               opts(sorted(self.rules.markers), f["marker"], blank="any"),
               opts([("with", "has evidence"), ("without", "no evidence")],
                    f["evidence"], blank="any"),
               esc(f["since"]), esc(f["until"]), esc(f["provider"]), esc(f["q"])))
        body = ['<h1>Entries</h1>', form,
                '<div class="card"><div class="wrap"><table>'
                '<tr><th>Date</th><th>Activity</th><th>Provider</th>'
                '<th class=num>Hours</th><th class=num>Count</th><th>Tags</th>'
                '<th>Status</th><th class=num>Ev</th><th>Source</th></tr>']
        for e in rows:
            body.append(
                '<tr><td>%s</td><td>%s</td><td>%s</td><td class=num>%s</td>'
                '<td class=num>%s</td><td class="small mute">%s</td>'
                '<td><span class="%s">%s</span></td><td class=num>%s</td>'
                '<td class="small mute">%s</td></tr>'
                % (esc(e["activity_date"]),
                   link("/entry/%s" % e["uuid"], e["title"] or "(untitled)"),
                   esc(e["provider"]), fmt(e["hours"]), e["count"],
                   esc(", ".join(e["tags"])),
                   STATUS_CLASS.get(e["status"], "pill"), esc(e["status"]),
                   ev.get(e["uuid"], 0) or '<span class="warn">0</span>',
                   esc(e["capture_source"])))
        if not rows:
            body.append('<tr><td colspan=9 class="mute">No entries match. %s</td>'
                        '</tr>' % link("/entry/new", "Log one"))
        body.append('</table></div><p class="small mute">%d row(s).</p></div>'
                    % len(rows))
        return self.page("Entries", "".join(body))

    def entry_form(self, entry=None, error=None, dupes=None):
        if entry is not None and entry["status"] in (L.SUPERSEDED, L.VOID):
            return self._entry_readonly(entry, error)
        e = entry or {"activity_date": str(datetime.date.today()), "title": "",
                      "provider": "", "hours": "", "count": 1, "tags": [],
                      "markers": [], "applies_to": None, "notes": "",
                      "cost_usd": "", "submitted_to": [], "overrides": {}}
        new = entry is None
        tagbox = "".join(
            '<label class="small" style="display:inline-block;margin-right:1rem">'
            '<input type=checkbox name=tags value="%s"%s style="width:auto"> %s'
            '</label>'
            % (esc(t), " checked" if t in e["tags"] else "",
               esc(self.rules.tag_labels.get(t, t)))
            for t in sorted(self.rules.tags))
        markbox = "".join(
            '<label class="small" style="display:inline-block;margin-right:1rem">'
            '<input type=checkbox name=markers value="%s"%s style="width:auto"> %s'
            '</label>'
            % (esc(m), " checked" if m in e["markers"] else "",
               esc(self.rules.marker_labels.get(m, m)))
            for m in sorted(self.rules.markers))
        credbox = "".join(
            '<label class="small" style="display:inline-block;margin-right:1rem">'
            '<input type=checkbox name=applies_to value="%s"%s style="width:auto">'
            ' %s</label>'
            % (esc(c.id),
               " checked" if e["applies_to"] and c.id in e["applies_to"] else "",
               esc(c.name))
            for c in self.rules.credentials)
        banner = ("bad", esc(error)) if error else None
        out = ['<h1>%s</h1>' % ("Log an activity" if new else "Edit entry")]
        if not new and e.get("status") == L.CONFIRMED:
            out.append('<div class="banner">This entry is confirmed. Saving '
                       'edits does not rewrite it — a corrected copy is '
                       'written and this one is marked superseded. Both stay '
                       'on the books.</div>')
        if dupes:
            out.append(
                '<div class="banner"><b>Possible duplicate.</b> These entries '
                'share the date and provider with hours within 25%%:<ul>%s</ul>'
                'Confirm anyway only if they really are different activities.'
                '</div>'
                % "".join('<li>%s — %s, %s hours</li>'
                          % (link("/entry/%s" % d["uuid"], d["title"]),
                             esc(d["activity_date"]), fmt(d["hours"]))
                          for d in dupes))
        action = "/entry/new" if new else "/entry/%s" % e["uuid"]
        out.append(
            '<form class="card" method=post action="%s" '
            'enctype="multipart/form-data">'
            '<div class="row">'
            '<div><label for=f-activity_date>Date</label>'
            '<input id=f-activity_date type=date name=activity_date '
            'value="%s" required></div>'
            '<div><label for=f-hours>Hours</label>'
            '<input id=f-hours name=hours value="%s" '
            'inputmode=decimal></div>'
            '<div><label for=f-count>Count (per-item activities)</label>'
            '<input id=f-count name=count value="%s" inputmode=numeric></div>'
            '<div><label for=f-cost_usd>Cost (USD)</label>'
            '<input id=f-cost_usd name=cost_usd value="%s" '
            'inputmode=decimal></div></div>'
            '<label for=f-title>Title</label>'
            '<input id=f-title name=title value="%s" required>'
            '<label for=f-provider>Provider</label>'
            '<input id=f-provider name=provider value="%s">'
            '<fieldset><legend>Tags — what kind of activity this was</legend>'
            '<div>%s</div></fieldset>'
            '<fieldset><legend>Markers — what it was ABOUT (drives category '
            'floors)</legend><div>%s</div></fieldset>'
            '<fieldset><legend>Applies to (leave all unticked to let every '
            'eligible credential claim it)</legend><div>%s</div></fieldset>'
            '<label for=f-notes>Notes</label>'
            '<textarea id=f-notes name=notes>%s</textarea>'
            '<label for=f-evidence>Evidence file (optional)</label>'
            '<input id=f-evidence type=file name=evidence>'
            '<button name=action value="save">%s</button>'
            % (action, esc(e["activity_date"]), esc(e["hours"]),
               esc(e["count"]), esc(e["cost_usd"]), esc(e["title"]),
               esc(e["provider"]), tagbox, markbox, credbox, esc(e["notes"]),
               "Capture" if new
               else ("Save as correction (supersedes)"
                     if e.get("status") == L.CONFIRMED else "Save")))
        if dupes:
            out.append('<button name=action value="save_force" class="danger">'
                       'Save and confirm anyway</button>')
        out.append('</form>')
        if not new:
            out.append(self._entry_side(e))
            out.append(self._advisory_panel(e))
        return self.page("Log" if new else "Entry", "".join(out), banner)

    def entry_detail(self, uuid, error=None):
        e = self.led.get_entry(uuid)
        if not e:
            return self.not_found()
        return self.entry_form(e, error=error)

    def _entry_side(self, e):
        ev = self.led.verify_evidence(e["uuid"])
        rows = "".join(
            '<tr><td>%s <a class="small" href="/evidence/%d?view=1">view</a>'
            '</td><td class="small mute">%s bytes</td>'
            '<td><span class="pill %s">%s</span></td><td>%s</td></tr>'
            % (link("/evidence/%d" % r["id"], r["filename"]), r["id"],
               r["bytes"],
               "ok" if r["verify"] == "OK" else "bad", esc(r["verify"]),
               esc(r["sha256"][:12]))
            for r in ev)
        applies = ("every eligible credential" if e["applies_to"] is None
                   else ", ".join(e["applies_to"]))
        creds = []
        for cred in self.rules.credentials:
            if e["applies_to"] is not None and cred.id not in e["applies_to"]:
                continue
            buckets = cred.buckets_for(e["tags"], e["markers"])
            if not buckets:
                continue
            credits = buckets[0].credits_for(e["hours"], e["count"],
                                             (e["overrides"] or {}).get(cred.id))
            creds.append('<tr><td>%s</td><td>%s</td><td class=num>%s %s</td>'
                         '<td>%s</td></tr>'
                         % (esc(cred.name), esc(buckets[0].name), fmt(credits),
                            esc(cred.unit_label),
                            '<span class="pill ok">submitted</span>'
                            if cred.body_id in (e["submitted_to"] or [])
                            else ('<form method=post action="/entry/%s" '
                                  'style="display:inline">'
                                  '<input type=hidden name=action '
                                  'value=submitted>'
                                  '<input type=hidden name=body value="%s">'
                                  '<button class="sec" style="margin:0;'
                                  'padding:.1rem .5rem;font-size:.78rem">'
                                  'mark submitted</button></form>'
                                  % (esc(e["uuid"]), esc(cred.body_id))
                                  if e["status"] == L.CONFIRMED
                                  else '<span class="small mute">—</span>')))
        buttons = []
        for target, label, cls in ((L.CONFIRMED, "Confirm", ""),
                                   (L.REJECTED, "Reject", "sec"),
                                   (L.CAPTURED, "Re-open", "sec"),
                                   (L.VOID, "Void", "danger")):
            if target in L.TRANSITIONS.get(e["status"], ()):
                buttons.append(
                    '<form method=post action="/entry/%s" style="display:inline">'
                    '<input type=hidden name=action value=status>'
                    '<input type=hidden name=to value="%s">'
                    '<button class="%s">%s</button></form>'
                    % (esc(e["uuid"]), target, cls, label))
        lineage = []
        if e.get("supersedes"):
            lineage.append("corrects %s" % link(
                "/entry/%s" % e["supersedes"], e["supersedes"][:8]))
        successor = self.led.superseded_by(e["uuid"])
        if successor:
            lineage.append("superseded by %s" % link(
                "/entry/%s" % successor, successor[:8]))
        if e.get("voided_at"):
            lineage.append("voided %s" % esc(e["voided_at"]))
        return (
            '<div class="card"><h3>State</h3>'
            '<p><span class="%s">%s</span> · captured via <code>%s</code>%s · '
            'applies to %s</p>%s'
            '<p class="small mute">Nothing counts until it is confirmed. '
            'Voiding is terminal: a voided entry keeps its capture event and '
            'its place on the books but never credits anything.</p>'
            '%s</div>'
            '<div class="card"><h3>What this is worth</h3><div class="wrap">'
            '<table><tr><th>Credential</th><th>Category</th><th class=num>Credits'
            '</th><th>Submitted</th></tr>%s</table></div>'
            '<p class="small mute">One activity matches at most one category per '
            'credential — the most specific one. Double-counting an activity '
            'into two categories of the same credential is the error class this '
            'tool exists to prevent.</p></div>'
            '<div class="card"><h3>Evidence</h3><div class="wrap"><table>'
            '<tr><th>File</th><th>Size</th><th>Verify</th><th>sha256</th></tr>'
            '%s</table></div>%s</div>'
            % (STATUS_CLASS.get(e["status"], "pill"), esc(e["status"]),
               esc(e["capture_source"]),
               (" <span class=small>%s</span>" % esc(e["capture_ref"]))
               if e["capture_ref"] else "",
               esc(applies),
               ('<p class="small mute">%s</p>' % " · ".join(lineage))
               if lineage else "",
               " ".join(buttons),
               "".join(creds) or
               '<tr><td colspan=4 class="mute">Nothing claims this '
                        'entry — check its tags.</td></tr>',
               rows or '<tr><td colspan=4 class="warn">No evidence attached.'
                       '</td></tr>',
               '<p class="small mute">Hashes are recorded at upload and '
               're-checked on every read. ACFE self-certification plus a '
               'three-year retention rule makes this an audit trail, not a '
               'convenience.</p>'))

    # -- rules -------------------------------------------------------------

    def bodies_page(self):
        out = ['<h1>Rules</h1>',
               '<p class="mute">Read-only render of <code>%s</code>, swept %s. '
               'Editing rules is a file edit plus a restart, on purpose: these '
               'numbers decide whether a credential survives, and they should '
               'not be changeable from a browser form.</p>'
               % (esc(self.rules.path), esc(self.rules.sweep_date))]
        unver = self.rules.unverified()
        if unver:
            out.append('<div class="banner"><b>%d credential(s) carry '
                       'unverified rules.</b> Their figures are shown but must '
                       'be checked against the body before you rely on them: '
                       '%s.</div>'
                       % (len(unver),
                          esc(", ".join(sorted(u["credential"] for u in unver)))))
        for cred in self.rules.credentials:
            reasons = cred.unverified_reasons()
            out.append(
                '<div class="card"><h2>%s %s</h2>'
                '<p class="small mute">%s · %s%s per cycle of %d months · '
                'unit “%s” · anchor %s</p>'
                % (esc(cred.name),
                   "" if cred.is_fully_verified
                   else '<span class="pill warn">UNVERIFIED</span>',
                   esc(cred.body_name), fmt(cred.cycle_total),
                   unverified_mark(cred.unverified_value()),
                   cred.cycle_months, esc(cred.unit_label),
                   esc(cred.raw.get("cycle_anchor"))))
            if reasons:
                out.append('<ul class="small warn">%s</ul>'
                           % "".join("<li>%s</li>" % esc(r) for r in reasons))
            out.append('<div class="wrap"><table><tr><th>Category</th>'
                       '<th>Rate</th><th class=num>Cap</th><th>Requires</th>'
                       '</tr>')
            for b in cred.buckets:
                rate = ("%s per hour" % fmt(b.per_hour) if b.per_hour is not None
                        else "%s per item" % fmt(b.per_item))
                req = ", ".join(list(b.tags) + list(b.requires_markers))
                if b.value_is_override:
                    rate = "value must be entered per entry"
                out.append('<tr><td>%s</td><td>%s%s</td><td class=num>%s%s</td>'
                           '<td class="small mute">%s</td></tr>'
                           % (esc(b.name), esc(rate),
                              unverified_mark(cred.unverified_value()),
                              fmt(b.cap),
                              unverified_mark(cred.unverified_value("cap", b)),
                              esc(req or "—")))
            out.append("</table></div>")
            if cred.groups:
                out.append('<div class="wrap"><table><tr><th>Group</th>'
                           '<th class=num>Floor</th><th class=num>Cap</th></tr>')
                gmark = unverified_mark(cred.unverified_value())
                for g in cred.groups:
                    out.append('<tr><td>%s</td><td class=num>%s%s</td>'
                               '<td class=num>%s%s</td></tr>'
                               % (esc(g["name"]), fmt(g.get("floor")),
                                  gmark if g.get("floor") is not None else "",
                                  fmt(g.get("cap")),
                                  gmark if g.get("cap") is not None else ""))
                out.append("</table></div>")
            if cred.source_url:
                out.append('<p class="small mute">Source: %s · verified %s</p>'
                           % (source_link(cred.source_url),
                              esc(cred.verified_date)))
            out.append("</div>")
        return self.page("Rules", "".join(out))

    # -- report and packet -------------------------------------------------

    def report_page(self, q):
        as_of = _as_of(q)
        cred_id = q.get("credential", [""])[0]
        if cred_id:
            rep = R.credential_report(self.rules, self.led, cred_id, as_of)
            md = R.render_markdown(rep)
            title = self.rules.get(cred_id).name
            extra = '<p>%s</p>' % link("/packet/%s" % cred_id,
                                       "Download submission packet")
        else:
            rep = R.full_report(self.rules, self.led, as_of)
            md = R.render_markdown(rep)
            title = "All credentials"
            extra = ""
        picker = ('<form class="card" method=get><div class="row">'
                  '<div><label for=f-credential>Credential</label>'
                  '<select id=f-credential name=credential>%s'
                  '</select></div><div><label for=f-as_of>As of</label>'
                  '<input id=f-as_of type=date name=as_of value="%s"></div></div>'
                  '<button>Render</button></form>'
                  % (opts([(c.id, c.name) for c in self.rules.credentials],
                          cred_id, blank="all credentials"), esc(str(as_of))))
        return self.page("Report",
                    '<h1>Renewal report — %s</h1>%s%s<div class="card">'
                    '<pre>%s</pre></div>'
                    % (esc(title), picker, extra, esc(md)))

    def packet(self, cred_id, q):
        as_of = _as_of(q)
        workdir = tempfile.mkdtemp(prefix="cpebot-packet-")
        try:
            out = os.path.join(workdir, "packet.zip")
            summary = R.zip_packet(self.rules, self.led, cred_id, out,
                                   os.path.join(workdir, "build"), as_of)
            with open(out, "rb") as fh:
                data = fh.read()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        name = "%s-packet-%s.zip" % (cred_id, as_of)
        return ("application/zip", data,
                {"Content-Disposition": 'attachment; filename="%s"' % name},
                summary)

    # -- submission packets (v1) -------------------------------------------

    def submission_packets_page(self, cred_id, q):
        as_of = _as_of(q)
        only = bool((q.get("unsubmitted") or [""])[0])
        packet = P.entry_packets(self.rules, self.led, cred_id, as_of=as_of,
                                 unsubmitted_only=only)
        m = packet["map"]
        qs = "?as_of=%s%s" % (as_of, "&unsubmitted=1" if only else "")
        out = ['<h1>%s &mdash; submission packet</h1>' % esc(packet["name"]),
               '<p class="mute">%s &middot; cycle %s to %s &middot; form: %s'
               ' (map <code>%s</code>)</p>'
               % (esc(packet["body"]), esc(packet["cycle_start"]),
                  esc(packet["cycle_end"]), esc(m["form_name"]), esc(m["id"]))]
        if not m["verified"]:
            out.append('<div class="banner warn"><b>UNVERIFIED field map.'
                       '</b> %s</div>' % esc(m["verified_note"] or ""))
        out.append(
            '<div class="card"><p><b>You submit.</b> Sign in to the body&#x27;s '
            'portal yourself and copy each field across. Registrar never asks '
            'for, stores or uses your portal login.</p>%s<p>%s &middot; %s'
            '</p></div>'
            % ('<p class="small">%s</p>' % esc(m["holder_note"])
               if m["holder_note"] else "",
               link("/packets/%s.zip%s" % (cred_id, qs),
                    "Download evidence bundle (zip)"),
               link("/packets/%s?as_of=%s%s" % (cred_id, as_of,
                                               "" if only else "&unsubmitted=1"),
                    "show all counted entries" if only
                    else "show unsubmitted only")))
        if not packet["entries"]:
            out.append('<div class="card"><p class="mute">No %sentries in '
                       'this cycle.</p></div>'
                       % ("unsubmitted " if only else "counted "))
        for entry in packet["entries"]:
            rows = []
            for f in entry["fields"]:
                if f["holder_choice"]:
                    val = '<span class="warn">choose in portal</span>'
                elif f["missing"]:
                    val = '<span class="bad">MISSING</span>'
                else:
                    val = '<code>%s</code>' % esc(f["value"]) \
                        if f["value"] else '<span class="mute">&mdash;</span>'
                rows.append('<tr><th>%s</th><td>%s%s%s</td></tr>'
                            % (esc(f["label"]), val,
                               unverified_mark(f.get("unverified")),
                               '<div class="small mute">%s</div>' % esc(f["note"])
                               if f["note"] else ""))
            out.append(
                '<div class="card"><h3>%s &middot; %s %s</h3><div class="wrap">'
                '<table>%s</table></div><details><summary>plain text</summary>'
                '<pre>%s</pre></details></div>'
                % (esc(entry["activity_date"]),
                   link("/entry/%s" % entry["uuid"], entry["uuid"][:8]),
                   '<span class="pill ok">submitted %s</span>'
                   % esc(entry["submitted_on"] or "") if entry["submitted"]
                   else "",
                   "".join(rows), esc(P.render_entry_text(packet, entry))))
        return self.page("Packet", "".join(out))

    def submission_bundle(self, cred_id, q):
        as_of = _as_of(q)
        only = bool((q.get("unsubmitted") or [""])[0])
        data, manifest = P.bundle(self.rules, self.led, cred_id, as_of=as_of,
                                  unsubmitted_only=only)
        name = "%s-evidence-%s.zip" % (cred_id, as_of)
        return data, {"Content-Disposition":
                      'attachment; filename="%s"' % name}, manifest

    # -- backfill ----------------------------------------------------------

    def backfill_page(self, result=None, text="", error=None):
        out = ['<h1>Backfill import</h1>',
               '<p class="mute">CSV of activities that already happened. '
               'Imported rows land as <b>captured</b>, not confirmed — a bulk '
               'import is the single most likely place for a duplicate to '
               'enter — and they are excluded from the real-capture counter, '
               'because a one-off spreadsheet cannot answer whether this tool '
               'gets used day to day.</p>',
               '<div class="card"><h3>Template</h3><pre>%s</pre></div>'
               % esc(BF.TEMPLATE)]
        if error:
            out.append('<div class="banner bad">%s</div>' % esc(error))
        if result:
            out.append(self._backfill_result(result))
        out.append(
            '<form class="card" method=post enctype="multipart/form-data">'
            '<label for=f-csv>Paste CSV</label>'
            '<textarea id=f-csv name=csv style="min-height:12rem">'
            '%s</textarea><label for=f-file>…or upload a file</label>'
            '<input id=f-file type=file name=file>'
            '<button name=action value=dry>Dry run</button>'
            '<button name=action value=import class="sec">Import</button>'
            '</form>' % esc(text))
        return self.page("Import", "".join(out))

    def _backfill_result(self, r):
        klass = "bad" if r["errors"] else ("warn" if r["duplicates_flagged"]
                                           else "ok")
        out = ['<div class="banner %s"><b>%s</b> — %d row(s) parsed, %d '
               'imported, %d error(s), %d possible duplicate(s).</div>'
               % (klass, "Dry run" if r["dry_run"] else "Imported",
                  r["parsed"], r["imported"], len(r["errors"]),
                  len(r["duplicates_flagged"]))]
        if r["errors"]:
            out.append('<div class="card"><h3>Rejected rows</h3><div class="wrap">'
                       '<table><tr><th>Line</th><th>Problem</th></tr>%s</table>'
                       '</div></div>'
                       % "".join('<tr><td>%d</td><td>%s</td></tr>'
                                 % (e["line"], esc(e["error"]))
                                 for e in r["errors"]))
        if r["duplicates_flagged"]:
            out.append('<div class="card"><h3>Possible duplicates</h3>'
                       '<div class="wrap"><table><tr><th>Line</th><th>Date</th>'
                       '<th>Title</th><th>Matches</th></tr>%s</table></div>'
                       '</div>'
                       % "".join(
                           '<tr><td>%d</td><td>%s</td><td>%s</td>'
                           '<td class="small">%s</td></tr>'
                           % (d["line"], esc(d["date"]), esc(d["title"]),
                              ", ".join(link("/entry/%s" % m, m[:8])
                                        for m in d["matches"]))
                           for d in r["duplicates_flagged"]))
        return "".join(out)

    # -- settings ----------------------------------------------------------

    def settings_page(self, saved=False):
        state = self.led.all_credential_state()
        rows = []
        for cred in self.rules.credentials:
            s = state.get(cred.id, {})
            rows.append(_scope_ids(
                '<div class="card"><h2>%s</h2>'
                '<p class="small mute">%s · cycle anchored on %s</p>'
                '<form method=post><input type=hidden name=credential value="%s">'
                '<div class="row">'
                '<div><label for=f-anchor_date>Anchor date</label>'
                '<input id=f-anchor_date type=date name=anchor_date '
                'value="%s"></div>'
                '<div><label for=f-carryover_in>Carried in</label>'
                '<input id=f-carryover_in name=carryover_in '
                'value="%s"></div>'
                '<div><label for=f-portal_credits>Portal figure</label>'
                '<input id=f-portal_credits name=portal_credits '
                'value="%s"></div>'
                '<div><label for=f-portal_checked>Portal checked</label>'
                '<input id=f-portal_checked type=date '
                ' name=portal_checked value="%s"></div>'
                '<div><label for=f-active>Tracking</label>'
                '<select id=f-active name=active>%s</select></div>'
                '</div><button>Save</button></form>%s</div>'
                % (esc(cred.name), esc(cred.body_name),
                   esc(cred.raw.get("cycle_anchor")), esc(cred.id),
                   esc(s.get("anchor_date") or ""),
                   esc(s.get("carryover_in") if s.get("carryover_in") else ""),
                   esc(s.get("portal_credits")
                       if s.get("portal_credits") is not None else ""),
                   esc(s.get("portal_checked") or ""),
                   opts([("1", "tracked"), ("0", "paused")],
                        str(s.get("active", 1))),
                   self._fee_block(cred)), esc(cred.id)))
        attendee = self.led.get_config("attendee_name", "") or ""
        head = ('<div class="card"><h3>Packet identity</h3>'
                '<form method=post><input type=hidden name=config value=1>'
                '<label for=f-attendee_name>Name as it should appear on submission '
                'packets</label>'
                '<input id=f-attendee_name name=attendee_name value="%s">'
                '<button>Save</button></form></div>' % esc(attendee))
        banner = ("ok", "Saved.") if saved else None
        return self.page("Settings", "<h1>Settings</h1>" + head + "".join(rows),
                    banner)

    def _fee_block(self, cred):
        fees = self.led.list_fees(cred.id)
        rows = "".join(
            '<tr><td>%s</td><td class=num>%s</td><td>%s</td><td>%s</td></tr>'
            % (esc(f["name"]), fmt(f["amount_usd"]), esc(f["due_date"]),
               ('paid %s' % esc(f["paid_on"])) if f["paid_on"] else
               '<form method=post style="display:inline">'
               '<input type=hidden name=fee_paid value="%d">'
               '<button class="sec" style="margin:0;padding:.1rem .5rem;'
               'font-size:.78rem">mark paid</button></form>' % f["id"])
            for f in fees)
        defined = "".join(
            '<li>%s: $%s %s</li>' % (esc(d.get("name")), fmt(d.get("amount_usd")),
                                     esc(d.get("cadence") or ""))
            for d in cred.fees)
        return ('<h3>Fees</h3>%s<div class="wrap"><table>'
                '<tr><th>Fee</th><th class=num>USD</th><th>Due</th><th></th></tr>'
                '%s</table></div>'
                '<form method=post><input type=hidden name=fee_credential '
                'value="%s"><div class="row">'
                '<div><label for=f-fee_name>Name</label>'
                '<input id=f-fee_name name=fee_name></div>'
                '<div><label for=f-fee_amount>USD</label>'
                '<input id=f-fee_amount name=fee_amount></div>'
                '<div><label for=f-fee_due>Due</label>'
                '<input id=f-fee_due type=date name=fee_due></div>'
                '</div><button class="sec">Add fee</button></form>'
                % (('<ul class="small mute">%s</ul>' % defined) if defined else "",
                   rows or '<tr><td colspan=4 class="mute">none recorded</td></tr>',
                   esc(cred.id)))

    # -- read-only terminal states -----------------------------------------

    def _entry_readonly(self, e, error=None):
        banner = ("bad", esc(error)) if error else None
        if e["status"] == L.VOID:
            why = ("This entry is void: kept on the books as a real capture "
                   "event, but it never credits anything, is excluded from "
                   "totals and duplicate checks, and exports carry its "
                   "status so the reader can filter it.")
        else:
            why = ("This entry was superseded by a correction. It stays on "
                   "the books read-only; the successor carries the credit.")
        kv = []
        for label, val in (
                ("Date", e["activity_date"]), ("Title", e["title"]),
                ("Provider", e["provider"]), ("Hours", fmt(e["hours"])),
                ("Count", e["count"]), ("Tags", " ".join(e["tags"])),
                ("Markers", " ".join(e["markers"])),
                ("Applies to", "all credentials" if e["applies_to"] is None
                 else " ".join(e["applies_to"])),
                ("Notes", e["notes"])):
            kv.append("<dt>%s</dt><dd>%s</dd>"
                      % (esc(label), esc(val) or "&mdash;"))
        body = ('<h1>Entry (read-only)</h1>'
                '<div class="banner">%s</div>'
                '<div class="card"><dl class="kv">%s</dl></div>%s'
                % (why, "".join(kv), self._entry_side(e)))
        return self.page("Entry", body, banner)

    # -- advisories --------------------------------------------------------

    def _advisory_panel(self, e):
        entry = dict(e)
        entry["evidence_count"] = len(self.led.list_evidence(e["uuid"]))
        res = V.run_validators(self.rules, self.led, entry,
                               self._assessments())
        items = "".join(
            '<li><span class="lvl %s">%s</span>%s</li>'
            % (esc(line["level"]), esc(line["level"]), esc(line["text"]))
            for line in res["lines"])
        off = ""
        if res["disabled"]:
            off = ('<p class="small mute">Off: %s &mdash; a disabled seam '
                   'in v%s; no model, no keys, no network calls in any '
                   'configuration. Config key <code>%s</code>.</p>'
                   % (esc(", ".join(v.label for v in res["disabled"])),
                      esc(VERSION),
                      esc(V.LLMValidator.CONFIG_KEY)))
        return ('<div class="card"><h3>Advisories</h3>'
                '<ul class="advisory">%s</ul>%s'
                '<p class="small mute">Advisory only &mdash; nothing here '
                'blocks a confirm.</p></div>'
                % (items or '<li class="mute">Nothing to flag.</li>', off))

    # -- submissions -------------------------------------------------------

    def submissions_page(self, q):
        as_of = _as_of(q)
        out = ['<h1>Submissions</h1>',
               '<p class="mute">Confirmed and counted this cycle, but not '
               'yet marked submitted to the body &mdash; the transcription '
               'queue for each portal.</p>']
        for cred in self.rules.credentials:
            try:
                _, fields, rows = X.submission_rows(
                    self.rules, self.led, cred.id, as_of)
            except ValueError as exc:
                out.append('<div class="card"><h2>%s</h2>'
                           '<p class="mute">%s</p></div>'
                           % (esc(cred.name), esc(exc)))
                continue
            if not rows:
                out.append('<div class="card"><h2>%s</h2><p class="ok">'
                           'Everything counted this cycle is marked '
                           'submitted.</p></div>' % esc(cred.name))
                continue
            head = "".join('<th>%s</th>' % esc(f) for f in fields)
            trs = []
            for r in rows:
                e = r["entry"]
                cells = "".join('<td>%s</td>' % esc(v) for v in r["values"])
                trs.append(
                    '<tr>%s<td class=num>%s</td><td>%s</td><td>'
                    '<form method=post action="/submissions">'
                    '<input type=hidden name=entry value="%s">'
                    '<input type=hidden name=body value="%s">'
                    '<input type=date name=date value="%s" '
                    'aria-label="Date submitted" '
                    'style="width:9.5rem;display:inline-block">'
                    '<button class="sec" style="margin:0 0 0 .3rem;'
                    'padding:.15rem .6rem">mark</button></form></td></tr>'
                    % (cells, fmt(r["credits"]),
                       link("/entry/%s" % e["uuid"], e["uuid"][:8]),
                       esc(e["uuid"]), esc(cred.body_id), esc(str(as_of))))
            out.append(
                '<div class="card"><h2>%s</h2><div class="wrap"><table>'
                '<tr>%s<th class=num>%s</th><th>Entry</th>'
                '<th>Mark submitted</th></tr>%s</table></div>'
                '<p class="small">%s &mdash; the columns are the '
                'portal&#x27;s own fields, so transcription is a copy, not '
                'a mapping exercise.</p></div>'
                % (esc(cred.name), head, esc(cred.unit_label), "".join(trs),
                   link("/submissions.csv?credential=%s" % cred.id,
                        "Download portal CSV")))
        return self.page("Submissions", "".join(out))

    # -- export ------------------------------------------------------------

    def export_page(self):
        rows = "".join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s &middot; %s</td></tr>'
            % (esc(cred.name),
               link("/export/credential/%s.csv" % cred.id, "cycle CSV"),
               link("/packet/%s" % cred.id, "submission packet (zip)"),
               link("/packets/%s" % cred.id, "per-entry fields"),
               link("/packets/%s.zip" % cred.id, "evidence bundle"))
            for cred in self.rules.credentials)
        body = (
            '<h1>Export</h1>'
            '<div class="card"><h2>Full ledger</h2>'
            '<p>Every entry in every state &mdash; captured, confirmed, '
            'rejected, superseded and void alike. An audit trail that '
            'exports only its flattering rows is not an audit trail; '
            'status is a column and the reader filters.</p>'
            '<p>%s &middot; %s</p></div>'
            '<div class="card"><h2>Per credential</h2><div class="wrap">'
            '<table><tr><th>Credential</th><th>Cycle detail</th>'
            '<th>Packet</th><th>Submit</th></tr>%s</table></div>'
            '<p class="small mute">Cycle CSVs carry the bucket and credit '
            'figures the engine actually awarded, not naive hours.</p>'
            '</div>'
            '<div class="card"><h2>Deadlines</h2>'
            '<p>%s &mdash; cycle ends, grace ends, registration windows, '
            'mid-cycle minimums and fees for every anchored credential, as '
            'an iCalendar file any calendar app imports. Re-importing an '
            'updated file updates the events rather than duplicating '
            'them.</p></div>'
            % (link("/export/entries.csv", "entries.csv"),
               link("/export/entries.json", "entries.json"), rows,
               link("/deadlines.ics", "deadlines.ics")))
        return self.page("Export", body)

    # -- charts ------------------------------------------------------------

    def charts_page(self, q):
        as_of = _as_of(q)
        anchors = self.led.anchors()
        carry = self.led.carryover_in()
        feed = self.led.engine_entries()
        out = ['<h1>Charts</h1>',
               '<p class="mute">Server-rendered SVG, straight from the '
               'engine&#x27;s numbers. No scripts run on these pages '
               '&mdash; the CSP forbids them.</p>']
        for cred in self.rules.credentials:
            anchor = anchors.get(cred.id)
            if not anchor:
                out.append('<div class="card"><h2>%s</h2><p class="mute">'
                           'No cycle anchor set &mdash; anchor it in '
                           'Settings to chart it.</p></div>' % esc(cred.name))
                continue
            try:
                a = E.assess(cred, feed, anchor=anchor, as_of=as_of,
                             carryover_in=float(carry.get(cred.id) or 0))
            except E.EngineError as exc:
                out.append('<div class="card"><h2>%s</h2><p class="mute">'
                           'Engine refused: %s</p></div>'
                           % (esc(cred.name), esc(exc)))
                continue
            pc = E.pace(a)
            points = C.burndown_points(cred, self.led, a, as_of)
            table = "".join(
                '<tr><td>%s</td><td class=num>%s</td></tr>'
                % (esc(str(d)), fmt(v)) for d, v in points)
            out.append(
                '<div class="card"><h2>%s</h2>'
                '<figure>%s</figure>'
                '<details><summary>Data table</summary><div class="wrap">'
                '<table><tr><th>Date</th><th class=num>Cumulative %s</th>'
                '</tr>%s</table></div></details>'
                % (esc(cred.name), C.burndown_svg(cred, a, pc, points),
                   esc(cred.unit_label), table))
            fill = C.floor_fill_svg(a, pc)
            if fill:
                gt = "".join(
                    '<tr><td>%s</td><td class=num>%s</td>'
                    '<td class=num>%s</td><td class=num>%s</td></tr>'
                    % (esc(g["name"]), fmt(g["awarded"]),
                       fmt(g.get("floor")), fmt(g.get("cap")))
                    for g in a.groups)
                out.append(
                    '<h3>Category floors</h3><figure>%s</figure>'
                    '<details><summary>Data table</summary>'
                    '<div class="wrap"><table><tr><th>Group</th>'
                    '<th class=num>Awarded</th><th class=num>Floor</th>'
                    '<th class=num>Cap</th></tr>%s</table></div></details>'
                    % (fill, gt))
            out.append('</div>')
        series = self.led.capture_by_month(12, as_of)
        vol = C.volume_svg(series)
        if vol:
            vt = "".join('<tr><td>%s</td><td class=num>%d</td></tr>'
                         % (esc(m), n) for m, n in series)
            out.append(
                '<div class="card"><h2>Capture volume</h2>'
                '<figure>%s</figure>'
                '<details><summary>Data table</summary><div class="wrap">'
                '<table><tr><th>Month</th><th class=num>Entries</th></tr>'
                '%s</table></div></details>'
                '<p class="small mute">Real capture events per month; '
                'backfill imports and fixtures are excluded.</p>'
                '</div>' % (vol, vt))
        return self.page("Charts", "".join(out))

    # -- what-if -----------------------------------------------------------

    def whatif_page(self, q):
        one = lambda k: (q.get(k) or [""])[0]     # noqa: E731
        as_of = _as_of(q)
        form = (
            '<form class="card" method=get>'
            '<div class="row">'
            '<div><label for=f-date>Date</label>'
            '<input id=f-date type=date name=date value="%s"></div>'
            '<div><label for=f-hours>Hours</label>'
            '<input id=f-hours name=hours value="%s"></div>'
            '<div><label for=f-count>Count</label>'
            '<input id=f-count name=count value="%s"></div>'
            '<div><label for=f-override>Override value</label>'
            '<input id=f-override name=override value="%s"></div>'
            '</div><div class="row">'
            '<div><label for=f-tags>Tags</label>'
            '<select id=f-tags name=tags multiple size=6>%s</select></div>'
            '<div><label for=f-markers>Markers</label>'
            '<select id=f-markers name=markers multiple size=6>%s</select></div>'
            '<div><label for=f-applies_to>Applies to (blank = all)</label>'
            '<select id=f-applies_to name=applies_to multiple size=6>%s</select></div>'
            '</div>'
            '<input type=hidden name=run value=1>'
            '<button>Run the engine</button></form>'
            % (esc(one("date") or str(as_of)), esc(one("hours")),
               esc(one("count") or "1"), esc(one("override")),
               opts(sorted(self.rules.tags), None),
               opts(sorted(self.rules.markers), None),
               opts([(c.id, c.name) for c in self.rules.credentials], None)))
        out = ['<h1>What-if</h1>',
               '<p class="mute">The calculator does no arithmetic of its '
               'own: it runs the real engine twice per credential, without '
               'and with the hypothetical activity, and reports the '
               'difference. The gap between the raw figure and what '
               'actually lands after caps and cycle rules &mdash; the '
               'forfeited column &mdash; is the product.</p>', form]
        if one("run"):
            activity = {
                "date": one("date") or None,
                "hours": one("hours") or 0,
                "count": one("count") or 1,
                "tags": q.get("tags") or [],
                "markers": q.get("markers") or [],
                "applies_to": q.get("applies_to") or None,
            }
            if one("override"):
                try:
                    ov = float(one("override"))
                except ValueError:
                    return self.page("What-if", "".join(out),
                                     ("bad", "Override must be a number."))
                activity["overrides"] = dict(
                    (c.id, ov) for c in self.rules.credentials)
            try:
                rows = W.dry_run(self.rules, self.led, activity, as_of)
            except (L.LedgerError, ValueError) as exc:
                return self.page("What-if", "".join(out),
                                 ("bad", esc(exc)))
            trs = []
            for r in rows:
                if "raw" not in r:
                    trs.append('<tr><td>%s</td>'
                               '<td colspan=6 class="mute">%s</td></tr>'
                               % (esc(r["name"]), esc(r["skipped"])))
                    continue
                b = r["bucket"]
                notes = []
                if r["skipped"]:
                    notes.append(r["skipped"])
                if b and b["needs_override"]:
                    notes.append("needs a body-published override value")
                for g in r["groups_moved"]:
                    notes.append("moves %s %s to %s"
                                 % (g["name"], fmt(g["before"]),
                                    fmt(g["after"])))
                for fid in r["floors_closed"]:
                    notes.append("closes floor %s" % fid)
                if r["advisory_only"]:
                    notes.append("ADVISORY ONLY: caps not fully encoded, "
                                 "no verdict here is a pass")
                before, after = r["before"], r["after"]
                trs.append(
                    '<tr><td>%s</td><td>%s</td><td class=num>%s</td>'
                    '<td class=num><b>%s</b></td><td class=num>%s</td>'
                    '<td>%s &rarr; %s of %s &middot; %s &rarr; %s</td>'
                    '<td class="small">%s</td></tr>'
                    % (esc(r["name"]),
                       "&mdash;" if b is None else
                       "%s%s" % (esc(b["name"]),
                                 "" if b["cap"] is None
                                 else " (cap %s)" % fmt(b["cap"])),
                       fmt(r["raw"]), fmt(r["delta"]), fmt(r["forfeited"]),
                       fmt(before["total"]), fmt(after["total"]),
                       fmt(after["required"]),
                       esc(before["verdict"]), esc(after["verdict"]),
                       esc("; ".join(notes)) if notes else ""))
            out.append(
                '<div class="card"><h2>Where it lands</h2>'
                '<div class="wrap"><table><tr><th>Credential</th>'
                '<th>Category</th><th class=num>Raw</th>'
                '<th class=num>Counts</th><th class=num>Forfeited</th>'
                '<th>Position</th><th>Notes</th></tr>%s</table></div>'
                '<p class="small mute">Raw is the naive figure; Counts is '
                'what the engine actually awards after caps and cycle '
                'rules.</p></div>' % "".join(trs))
        return self.page("What-if", "".join(out))

    def not_found(self):
        return self.page("Not found", '<h1>404</h1><p>No such page. %s</p>'
                    % link("/", "Dashboard"))


def _as_of(q):
    raw = (q.get("as_of") or [""])[0]
    if raw:
        try:
            return datetime.date.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.date.today()


# ---------------------------------------------------------------------------
# multipart, hand-rolled
# ---------------------------------------------------------------------------

def _multipart(body, content_type):
    """Parse multipart/form-data into {name: [values]} and {name: (fn, bytes)}.

    Hand-rolled because `cgi` was removed in Python 3.13 and the alternative
    is a dependency for one form. Scope is exactly what these forms send:
    flat fields plus a single optional file per field.
    """
    fields, files = {}, {}
    marker = "boundary="
    if marker not in content_type:
        return fields, files
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    sep = ("--" + boundary).encode()
    for part in body.split(sep):
        if not part.strip() or part.strip() == b"--":
            continue
        head, _, data = part.partition(b"\r\n\r\n")
        if not _:
            continue
        data = data[:-2] if data.endswith(b"\r\n") else data
        disp = ""
        ctype = ""
        for line in head.decode("utf-8", "replace").split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition:"):
                disp = line
            elif low.startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        name = _disp_param(disp, "name")
        if not name:
            continue
        filename = _disp_param(disp, "filename")
        if filename:
            if data:
                files[name] = (filename, data, ctype)
        else:
            fields.setdefault(name, []).append(data.decode("utf-8", "replace"))
    return fields, files


def _disp_param(disp, key):
    for chunk in disp.split(";"):
        chunk = chunk.strip()
        if chunk.lower().startswith(key.lower() + "="):
            return chunk.split("=", 1)[1].strip().strip('"')
    return None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _scope_ids(html, scope):
    """Prefix the for=/id= pairs in a repeated form so ids stay unique."""
    return (html.replace(" for=f-", " for=f-%s-" % scope)
                .replace(" id=f-", " id=f-%s-" % scope))


def _host_name(value):
    """The name in a Host header: port dropped, IPv6 brackets removed,
    lower-cased. "" when the header does not parse."""
    v = (value or "").strip().lower()
    if v.startswith("["):
        end = v.find("]")
        rest = v[end + 1:] if end > 0 else None
        if rest is None or not re.fullmatch(r"(:\d*)?", rest):
            return ""
        return v[1:end]
    name, _sep, port = v.partition(":")
    if port and not port.isdigit():
        return ""
    return name.rstrip(".")


def allowed_host_names(bind, extra=()):
    """The set of Host names the server answers to."""
    names = set()
    for h in (*LOOPBACK_HOSTS, bind, *extra):
        h = (h or "").strip().lower()
        # A bare IPv6 address stays whole; anything else is normalised the
        # way a request's Host is, so "name:port" and "[v6]:port" match.
        h = h.rstrip(".") if h.count(":") > 1 and not h.startswith("[") \
            else _host_name(h)
        if h:
            names.add(h)
    return frozenset(names)


class Handler(BaseHTTPRequestHandler):
    server_version = "cpebot/" + VERSION
    app = None
    # Set by build_server(); empty means every request is refused.
    allowed_hosts = frozenset()

    def log_message(self, fmt_, *args):
        # Activity titles and providers are personal, and query strings carry
        # both. The access log keeps the method, the path and the status, and
        # drops everything after the "?".
        print("%s %s %s %s" % (self.log_date_time_string(), self.command,
                               self.path.split("?", 1)[0],
                               args[1] if len(args) > 1 else ""), flush=True)

    # -- plumbing ----------------------------------------------------------

    def _send(self, payload, code=200, ctype="text/html; charset=utf-8",
              headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # No scripts, no framing, nothing loaded from anywhere but here.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Clacks-Overhead", "GNU Terry Pratchett")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("upload too large (%d bytes, limit %d)"
                             % (length, MAX_BODY))
        return self.rfile.read(length) if length else b""

    def _form(self):
        ctype = self.headers.get("Content-Type") or ""
        body = self._read_body()
        if ctype.startswith("multipart/form-data"):
            return _multipart(body, ctype)
        return urllib.parse.parse_qs(body.decode("utf-8", "replace"),
                                     keep_blank_values=True), {}

    def _host_ok(self):
        hosts = self.headers.get_all("Host") or []
        return len(hosts) == 1 and _host_name(hosts[0]) in self.allowed_hosts

    def _bad_host(self):
        self.close_connection = True
        self._send(b"Bad Host header\n", 400, "text/plain; charset=utf-8",
                   {"Connection": "close"})

    # -- routing -----------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._bad_host()
        try:
            self._get()
        except Exception:
            self._oops()

    do_HEAD = do_GET

    def _get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        q = urllib.parse.parse_qs(parsed.query)
        app = self.app

        if path == "/health":
            payload = json.dumps({
                "status": "ok",
                "version": VERSION,
                "rules": app.rules.path,
                "credentials": len(app.rules.credentials),
                "unverified": sorted(u["credential"]
                                     for u in app.rules.unverified()),
                "entries": app.led.stats()["entries"],
                "capture_counts": app.led.capture_counts(),
            }, indent=2).encode()
            return self._send(payload, ctype="application/json")
        if path == "/static/cpebot.css":
            return self._send(CSS.encode("utf-8"),
                              ctype="text/css; charset=utf-8",
                              headers={"Cache-Control": "max-age=300"})
        if path == "/":
            return self._send(app.dashboard(q))
        if path == "/entries":
            return self._send(app.entries(q))
        if path == "/entry/new":
            return self._send(app.entry_form())
        if path == "/submissions":
            return self._send(app.submissions_page(q))
        if path == "/submissions.csv":
            cid = (q.get("credential") or [""])[0]
            if cid not in app.rules.ids():
                return self._send(app.not_found(), 404)
            try:
                data = X.submission_csv(app.rules, app.led, cid, _as_of(q))
            except ValueError as exc:
                return self._send(app.page(
                    "Submissions", '<h1>Cannot build the CSV</h1>'
                    '<div class="card"><p>%s</p></div>' % esc(exc)), 409)
            return self._send(
                data.encode("utf-8"), ctype="text/csv; charset=utf-8",
                headers={"Content-Disposition":
                         'attachment; filename="%s-submit-%s.csv"'
                         % (cid, _as_of(q))})
        if path == "/export":
            return self._send(app.export_page())
        if path == "/export/entries.csv":
            return self._send(
                X.entries_csv(app.led).encode("utf-8"),
                ctype="text/csv; charset=utf-8",
                headers={"Content-Disposition":
                         'attachment; filename="cpebot-ledger-%s.csv"'
                         % datetime.date.today()})
        if path == "/export/entries.json":
            return self._send(
                X.entries_json(app.led).encode("utf-8"),
                ctype="application/json",
                headers={"Content-Disposition":
                         'attachment; filename="cpebot-ledger-%s.json"'
                         % datetime.date.today()})
        if path.startswith("/export/credential/"):
            cid = path.split("/", 3)[3]
            if cid.endswith(".csv"):
                cid = cid[:-4]
            if cid not in app.rules.ids():
                return self._send(app.not_found(), 404)
            try:
                data = X.credential_csv(app.rules, app.led, cid, _as_of(q))
            except ValueError as exc:
                return self._send(app.page(
                    "Export", '<h1>Cannot export this cycle</h1>'
                    '<div class="card"><p>%s</p></div>' % esc(exc)), 409)
            return self._send(
                data.encode("utf-8"), ctype="text/csv; charset=utf-8",
                headers={"Content-Disposition":
                         'attachment; filename="%s-cycle-%s.csv"'
                         % (cid, _as_of(q))})
        if path == "/charts":
            return self._send(app.charts_page(q))
        if path == "/whatif":
            return self._send(app.whatif_page(q))
        if path.startswith("/credential/"):
            cid = path.split("/", 2)[2]
            if cid not in app.rules.ids():
                return self._send(app.not_found(), 404)
            return self._send(app.credential(cid, q))
        if path.startswith("/entry/"):
            return self._send(app.entry_detail(path.split("/", 2)[2]))
        if path == "/bodies":
            return self._send(app.bodies_page())
        if path == "/report":
            return self._send(app.report_page(q))
        if path.startswith("/packet/"):
            cid = path.split("/", 2)[2]
            if cid not in app.rules.ids():
                return self._send(app.not_found(), 404)
            try:
                ctype, data, headers, _summary = app.packet(cid, q)
            except ValueError as exc:
                return self._send(app.page("Packet",
                                       '<h1>Cannot build a packet</h1>'
                                       '<div class="card"><p>%s</p></div>'
                                       % esc(exc)), 409)
            return self._send(data, ctype=ctype, headers=headers)
        if path == "/deadlines.ics":
            cid = (q.get("credential") or [""])[0]
            if cid and cid not in app.rules.ids():
                return self._send(app.not_found(), 404)
            data = I.export(app.rules, app.led, as_of=_as_of(q),
                            only=[cid] if cid else None)
            return self._send(
                data.encode("utf-8"), ctype="text/calendar; charset=utf-8",
                headers={"Content-Disposition":
                         'attachment; filename="registrar-deadlines%s.ics"'
                         % ("-" + cid if cid else "")})
        if path.startswith("/packets/"):
            cid = path.split("/", 2)[2]
            want_zip = cid.endswith(".zip")
            if want_zip:
                cid = cid[:-4]
            if cid not in app.rules.ids():
                return self._send(app.not_found(), 404)
            try:
                if want_zip:
                    data, headers, _m = app.submission_bundle(cid, q)
                    return self._send(data, ctype="application/zip",
                                      headers=headers)
                return self._send(app.submission_packets_page(cid, q))
            except ValueError as exc:
                return self._send(app.page("Packet",
                                       '<h1>Cannot build a packet</h1>'
                                       '<div class="card"><p>%s</p></div>'
                                       % esc(exc)), 409)
        if path == "/backfill":
            return self._send(app.backfill_page())
        if path == "/settings":
            return self._send(app.settings_page(
                saved=bool(q.get("saved"))))
        if path == "/reminders":
            plan = RM.plan(app.rules, app.led)
            return self._send(json.dumps(plan, indent=2, default=str).encode(),
                              ctype="application/json")
        if path.startswith("/evidence/"):
            return self._evidence(path.split("/", 2)[2], q)
        return self._send(app.not_found(), 404)

    def _evidence(self, ev_id, q=None):
        app = self.app
        try:
            ev_id = int(ev_id)
        except ValueError:
            return self._send(app.not_found(), 404)
        row = None
        for r in app.led.verify_evidence():
            if r["id"] == ev_id:
                row = r
                break
        if row is None:
            return self._send(app.not_found(), 404)
        if row["verify"] != "OK":
            return self._send(app.page(
                "Evidence",
                '<h1>%s</h1><div class="banner bad">This file does not match '
                'the hash recorded when it was uploaded (<b>%s</b>). It is not '
                'served, because a file that fails verification is not evidence '
                '— produce the original or re-upload it as a new entry.</div>'
                % (esc(row["filename"]), esc(row["verify"]))), 409)
        # The store is append-only and cpebot-owned, but serving files is
        # exactly where defense-in-depth is cheap: the resolved path must
        # stay under the evidence root, the content type comes from magic
        # bytes (never the stored filename's claim), and disposition is
        # attachment unless the sniffed type is safe and ?view=1 asked.
        root = os.path.realpath(app.led.evidence_root)
        full = os.path.realpath(app.led.evidence_path(row))
        if not full.startswith(root + os.sep):
            return self._send(app.not_found(), 404)
        with open(full, "rb") as fh:
            data = fh.read()
        sniffed = L.sniff_evidence(data)
        ctype = sniffed or "application/octet-stream"
        disp = ("inline" if sniffed and (q or {}).get("view")
                else "attachment")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_",
                           os.path.basename(row["filename"] or "evidence"))
        return self._send(
            data, ctype=ctype,
            headers={"Content-Disposition":
                     '%s; filename="%s"' % (disp, safe_name)})

    def do_POST(self):
        if not self._host_ok():
            return self._bad_host()
        try:
            self._post()
        except Exception:
            self._oops()

    def _post(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        app = self.app
        fields, files = self._form()
        one = lambda k, d="": (fields.get(k) or [d])[0]     # noqa: E731

        # Every POST form carries the per-install token (_page injects it);
        # a POST without it is a forgery or a stale page. Compare in
        # constant time, reject with an explanation either way.
        # Compare bytes, not str: compare_digest raises TypeError on a
        # non-ASCII str, and a hostile token must be a 403, not a 500.
        token = one("csrf").encode("utf-8", "replace")
        if not hmac.compare_digest(token, app.csrf().encode("utf-8")):
            return self._send(app.page(
                "Forbidden",
                '<h1>403 — cross-site request rejected</h1>'
                '<div class="card"><p>The POST arrived without the '
                'anti-forgery token every cpebot form carries. Go back, '
                'reload the page, and resubmit.</p></div>'), 403)

        if path == "/entry/new":
            return self._entry_write(None, fields, files, one)
        if path.startswith("/entry/"):
            uuid = path.split("/", 2)[2]
            action = one("action")
            if action == "status":
                return self._entry_status(uuid, one("to"))
            if action == "submitted":
                app.led.mark_submitted(uuid, one("body"))
                return self._redirect("/entry/%s" % uuid)
            return self._entry_write(uuid, fields, files, one)
        if path == "/submissions":
            try:
                app.led.mark_submitted(one("entry"), one("body"),
                                       one("date") or None)
            except (L.LedgerError, ValueError) as exc:
                return self._send(app.page(
                    "Submissions", '<h1>Cannot mark submitted</h1>'
                    '<div class="card"><p>%s</p></div>' % esc(exc)), 400)
            return self._redirect("/submissions")
        if path == "/backfill":
            return self._backfill(fields, files, one)
        if path == "/settings":
            return self._settings(fields, one)
        return self._send(app.not_found(), 404)

    def _entry_write(self, uuid, fields, files, one):
        app = self.app
        applies = fields.get("applies_to") or []
        payload = {
            "activity_date": one("activity_date"),
            "title": one("title"),
            "provider": one("provider"),
            "hours": float(one("hours", "0") or 0),
            "count": int(one("count", "1") or 1),
            "tags": fields.get("tags") or [],
            "markers": fields.get("markers") or [],
            "applies_to": applies or None,
            "notes": one("notes"),
            "cost_usd": float(one("cost_usd")) if one("cost_usd") else None,
        }
        current = app.led.get_entry(uuid) if uuid else None
        if uuid and current is None:
            return self._send(app.not_found(), 404)
        upload = None
        if "evidence" in files:
            name, data, _mime = files["evidence"]
            try:
                # Size, extension AND magic bytes — validated here at the
                # boundary, before anything is written to the ledger.
                mime = L.validate_upload(name, data)
            except L.LedgerError as exc:
                return self._send(
                    app.entry_form(current, error=str(exc)), 400)
            upload = (name, data, mime)
        try:
            if current is not None and current["status"] == L.CONFIRMED:
                # Editing a confirmed entry never rewrites it: the ledger
                # writes a corrected copy and marks this one superseded.
                old_uuid = uuid
                uuid = app.led.supersede(uuid, **payload)
                self._carry_evidence(old_uuid, uuid)
            elif current is not None:
                app.led.update_entry(uuid, **payload)
            else:
                uuid = app.led.add_entry(capture_source="web", **payload)
        except (L.LedgerError, ValueError) as exc:
            return self._send(app.entry_form(current, error=str(exc)), 400)
        if upload:
            name, data, mime = upload
            app.led.add_evidence(uuid, name, data, mime=mime)
        if one("action") == "save_force":
            try:
                app.led.confirm(uuid, force=True)
            except L.LedgerError:
                pass
        return self._redirect("/entry/%s" % uuid)

    def _carry_evidence(self, old_uuid, new_uuid):
        """Copy evidence links from a superseded entry to its correction.

        Only files whose bytes still match their recorded hash are carried
        — an altered file must never be silently re-hashed under a fresh
        digest. The store is content-addressed, so this links, not copies.
        """
        app = self.app
        for row in app.led.list_evidence(old_uuid):
            p = app.led.evidence_path(row)
            if not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                data = fh.read()
            if hashlib.sha256(data).hexdigest() != row["sha256"]:
                continue
            app.led.add_evidence(new_uuid, row["filename"], data,
                                 mime=row["mime"], note=row["note"])

    def _entry_status(self, uuid, to):
        app = self.app
        try:
            if to == L.CONFIRMED:
                app.led.confirm(uuid)
            else:
                app.led.set_status(uuid, to)
        except L.SoftDuplicate as dup:
            entry = app.led.get_entry(uuid)
            return self._send(app.entry_form(entry, dupes=dup.matches), 409)
        except L.LedgerError as exc:
            return self._send(app.entry_detail(uuid, error=str(exc)), 400)
        return self._redirect("/entry/%s" % uuid)

    def _backfill(self, fields, files, one):
        app = self.app
        text = one("csv")
        if "file" in files:
            text = files["file"][1].decode("utf-8", "replace")
        if not text.strip():
            return self._send(app.backfill_page(error="Nothing to import."), 400)
        dry = one("action") != "import"
        try:
            result = BF.import_csv(text, app.rules, app.led, dry_run=dry)
        except BF.BackfillError as exc:
            return self._send(app.backfill_page(text=text, error=str(exc)), 400)
        return self._send(app.backfill_page(result=result,
                                            text=text if dry else ""))

    def _settings(self, fields, one):
        app = self.app
        if one("config"):
            app.led.set_config("attendee_name", one("attendee_name"))
            return self._redirect("/settings?saved=1")
        if one("fee_paid"):
            app.led.mark_fee_paid(int(one("fee_paid")))
            return self._redirect("/settings?saved=1")
        if one("fee_credential"):
            if one("fee_name"):
                app.led.add_fee(one("fee_credential"), one("fee_name"),
                                float(one("fee_amount")) if one("fee_amount")
                                else None, one("fee_due") or None)
            return self._redirect("/settings?saved=1")
        cred = one("credential")
        if cred:
            app.led.set_credential_state(
                cred,
                anchor_date=one("anchor_date") or None,
                carryover_in=float(one("carryover_in")) if one("carryover_in")
                else 0.0,
                portal_credits=float(one("portal_credits"))
                if one("portal_credits") else None,
                portal_checked=one("portal_checked") or None,
                active=int(one("active", "1")),
            )
        return self._redirect("/settings?saved=1")

    def _oops(self):
        # The traceback goes to the log only. The page names no code, path
        # or data, so a failure tells a visitor nothing about the install.
        print(traceback.format_exc(), flush=True)
        body = _page("Error",
                    '<h1>Something broke</h1><div class="card"><p>The request '
                    'could not be completed. The details are in the server '
                    'log.</p></div><p>%s</p>' % link("/", "Dashboard"))
        try:
            self._send(body, 500)
        except Exception:
            pass


def build_server(bind=BIND, port=PORT, app=None, allowed_hosts=None):
    extra = ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
    handler = type("BoundHandler", (Handler,), {
        "app": app or App(),
        "allowed_hosts": allowed_host_names(bind, extra)})
    return ThreadingHTTPServer((bind, port), handler)


def main():
    app = App()
    app.led.init_schema()
    srv = build_server(BIND, PORT, app)
    print("cpebot listening on http://%s:%d (private network only)" % (BIND, PORT),
          flush=True)
    print("answers to Host: %s" % ", ".join(sorted(srv.RequestHandlerClass.allowed_hosts)),
          flush=True)
    print("rules: %s (swept %s); %d credential(s), %d unverified"
          % (app.rules.path, app.rules.sweep_date, len(app.rules.credentials),
             len(app.rules.unverified())), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()

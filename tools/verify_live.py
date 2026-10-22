#!/usr/bin/env python3
"""Drive the whole product over a real URL and print what came back.

`tests/test_web.py` proves the same journey against a server it starts
itself on loopback (plus the v0.2 legs: supersede, void, evidence upload,
submissions, export, what-if, and the refusals — forged POSTs, bad uploads,
traversal shapes). This script proves it against the server that is
actually running, on the interface it is actually bound to, which is a
different claim — a bind address, a systemd unit and a firewall are all
things a unit test cannot be wrong about, because it never meets them.

    python3 tools/verify_live.py http://<BIND_ADDRESS>:8762

Exit status is 0 only if every step passed. Nothing here is destructive
beyond the entries it creates in whatever ledger the server is pointed at,
so point it at a demo database.
"""

from http.client import HTTPConnection
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid as uuidlib
import zipfile

PASS, FAIL = [], []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


OPENER = urllib.request.build_opener(NoRedirect)

# The per-install anti-forgery token, read from a rendered form the way
# a browser would carry it. Filled in by main() before the first POST.
CSRF = {"token": ""}


def http(url, data=None, follow=True, csrf=True):
    if data is not None and csrf and CSRF["token"]:
        data = dict(data)
        data.setdefault("csrf", CSRF["token"])
    body = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    opener = urllib.request.urlopen if follow else OPENER.open
    try:
        with opener(urllib.request.Request(url, data=body), timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def http_multipart(url, fields, files, follow=False):
    """POST a form the way the evidence upload actually arrives."""
    fields = dict(fields)
    if CSRF["token"]:
        fields.setdefault("csrf", CSRF["token"])
    boundary = "----cpebotverify%s" % uuidlib.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            out.write(("--%s\r\nContent-Disposition: form-data; "
                       'name="%s"\r\n\r\n%s\r\n' % (boundary, k, item))
                      .encode())
    for k, (fname, blob, ctype) in (files or {}).items():
        out.write(("--%s\r\nContent-Disposition: form-data; "
                   'name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                   % (boundary, k, fname, ctype)).encode())
        out.write(blob)
        out.write(b"\r\n")
    out.write(("--%s--\r\n" % boundary).encode())
    req = urllib.request.Request(url, data=out.getvalue(), headers={
        "Content-Type": "multipart/form-data; boundary=%s" % boundary})
    opener = urllib.request.urlopen if follow else OPENER.open
    try:
        with opener(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def foreign_host_status(base, path):
    """GET `path` with a Host header the server must not answer to."""
    u = urllib.parse.urlsplit(base)
    conn = HTTPConnection(u.hostname, u.port or 80, timeout=20)
    try:
        conn.putrequest("GET", path, skip_host=True)
        conn.putheader("Host", "rebind.invalid")
        conn.endheaders()
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           ("  — %s" % detail) if detail else ""))
    return ok


def main(base):
    base = base.rstrip("/")
    marker = "LIVE VERIFY %s" % uuidlib.uuid4().hex[:8]
    print("Registrar live verification against %s\n" % base)

    print("health")
    status, body, _ = http(base + "/health")
    doc = json.loads(body.decode())
    check("/health returns 200", status == 200)
    check("rules loaded", doc.get("credentials", 0) > 0,
          "%d credentials" % doc.get("credentials", 0))
    check("kill-switch counter is exposed", "capture_counts" in doc,
          "real=%s threshold=%s" % (doc["capture_counts"]["real"],
                                    doc["capture_counts"]["threshold"]))
    check("the running server is v1.0.0", doc.get("version") == "1.0.0",
          "version=%s" % doc.get("version"))

    print("\nHost allowlist")
    for path in ("/", "/entry/new", "/export/entries.json"):
        status, body = foreign_host_status(base, path)
        check("a foreign Host is refused on %s" % path,
              status == 400 and body == b"Bad Host header\n", "HTTP %d" % status)

    print("\nanti-forgery token")
    status, body, _ = http(base + "/entry/new")
    m = re.search(r'name=csrf value="([0-9a-f]{64})"', body.decode())
    check("the capture form carries the per-install token", bool(m))
    if m:
        CSRF["token"] = m.group(1)

    print("\ncapture")
    status, body, headers = http(base + "/entry/new", follow=False, data={
        "activity_date": "2026-08-30", "title": marker,
        "provider": "Example Security Institute %s" % marker[-8:],
        "hours": "4", "count": "1",
        "tags": "training", "markers": "domain_security",
        "notes": "created by tools/verify_live.py"})
    check("POST /entry/new redirects", status == 303, "HTTP %d" % status)
    loc = headers.get("Location", "")
    entry_uuid = loc.rsplit("/", 1)[-1]
    check("redirect names the new entry", len(entry_uuid) == 32, loc)

    status, body, _ = http(base + "/entry/" + entry_uuid)
    page = body.decode()
    check("entry page renders", status == 200)
    check("it landed CAPTURED, not confirmed", "captured" in page.lower())

    print("\nthe capture is not yet counted")
    status, body, _ = http(base + "/")
    before = body.decode()
    check("dashboard renders", status == 200)
    check("dashboard does not show the pending entry",
          marker not in before)

    print("\nconfirm")
    status, _, _ = http(base + "/entry/" + entry_uuid, follow=False,
                        data={"action": "status", "to": "confirmed"})
    check("confirm redirects", status == 303, "HTTP %d" % status)
    status, body, _ = http(base + "/entries?status=confirmed")
    check("it is now in the confirmed list", marker in body.decode())

    print("\nsoft duplicate")
    # Same day, same provider, same hours. This is the shape of a double-log,
    # and confirming it must be an explicit act rather than a default.
    dupe_title = marker + " (duplicate)"
    status, body, headers = http(base + "/entry/new", follow=False, data={
        "activity_date": "2026-08-30", "title": dupe_title,
        "provider": "Example Security Institute %s" % marker[-8:],
        "hours": "4", "count": "1", "tags": "training",
        "markers": "domain_security"})
    dupe_uuid = headers.get("Location", "").rsplit("/", 1)[-1]
    check("the duplicate is still accepted as a capture", status == 303)
    status, body, _ = http(base + "/entry/" + dupe_uuid, follow=False,
                           data={"action": "status", "to": "confirmed"})
    check("confirming it is intercepted, not silently allowed", status == 409,
          "HTTP %d" % status)
    check("the interception names it a duplicate",
          "duplicate" in body.decode().lower())
    status, _, _ = http(base + "/entry/" + dupe_uuid, follow=False, data={
        "action": "save_force", "activity_date": "2026-08-30",
        "title": dupe_title,
        "provider": "Example Security Institute %s" % marker[-8:],
        "hours": "4", "count": "1", "tags": "training",
        "markers": "domain_security"})
    check("an explicit override goes through", status == 303,
          "HTTP %d" % status)

    print("\ndashboard reflects cap-aware math")
    status, body, _ = http(base + "/")
    dash = body.decode()
    check("dashboard renders after confirm", status == 200)
    naive = re.search(r"naive total would have said\s*([\d.]+)", dash)
    check("dashboard states the naive total it rejected", bool(naive),
          naive.group(1) if naive else "phrase absent")
    check("dashboard shows what the caps cost",
          "earned but not counted" in dash.lower())

    print("\nrenewal report")
    status, body, _ = http(base + "/report")
    rep = body.decode()
    check("/report renders", status == 200)
    for phrase in ("Evidence", "Fees", "Portal", "Submitted"):
        check("report covers %s" % phrase.lower(), phrase.lower() in rep.lower())
    check("report gives the position and the gap",
          "Earned" in rep and "Gap" in rep)
    check("report gives runway and pace", "runway" in rep.lower()
          and "pace" in rep.lower())
    check("UNVERIFIED rules are surfaced in the UI",
          "UNVERIFIED" in rep or "unverified" in rep)

    print("\npacket prep")
    status, body, headers = http(base + "/packet/cissp")
    check("/packet/cissp returns a file", status == 200, "HTTP %d" % status)
    check("it is a zip", headers.get("Content-Type", "").endswith("zip"),
          headers.get("Content-Type", ""))
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
        names = zf.namelist()
        check("zip contains manifest.md",
              any(n.endswith("manifest.md") for n in names),
              "%d members" % len(names))
        man = zf.read([n for n in names if n.endswith("manifest.md")][0]).decode()
        check("manifest states the counted total",
              "counted** against a requirement of" in man)
        check("manifest lists evidence gaps", "gap" in man.lower())
        check("manifest carries the new entry", marker in man)
    except zipfile.BadZipFile:
        check("zip is readable", False, "not a zip file")

    print("\nbodies.yaml is readable in the UI")
    status, body, _ = http(base + "/bodies")
    check("/bodies renders", status == 200)
    check("/bodies marks unverified plugins", "UNVERIFIED" in body.decode())

    print("\nevidence integrity route")
    status, body, _ = http(base + "/entries?q=" + urllib.parse.quote(marker))
    check("filters work over HTTP", marker in body.decode())

    # ---- v0.2: hardening ------------------------------------------------
    print("\nsecurity headers")
    status, body, h = http(base + "/")
    check("CSP forbids inline and remote script",
          "default-src 'none'" in h.get("Content-Security-Policy", ""),
          h.get("Content-Security-Policy", "absent")[:60])
    check("X-Frame-Options DENY", h.get("X-Frame-Options") == "DENY")
    check("nosniff", h.get("X-Content-Type-Options") == "nosniff")
    check("Referrer-Policy no-referrer",
          h.get("Referrer-Policy") == "no-referrer")
    check("no <script> anywhere on the dashboard", b"<script" not in body)

    print("\nforgery is rejected")
    forged = marker + " forged"
    status, body, _ = http(base + "/entry/new", follow=False, csrf=False,
                           data={"activity_date": "2026-08-30",
                                 "title": forged, "provider": "x",
                                 "hours": "1", "count": "1",
                                 "tags": "training"})
    check("POST without the token is a 403", status == 403,
          "HTTP %d" % status)
    # the /entries page reflects the query back in the search box, so ask
    # the full dump instead — a row that was never written is in no dump
    status, body, _ = http(base + "/export/entries.csv")
    check("and nothing was written", forged not in body.decode())

    # ---- v0.2: evidence upload and serving ------------------------------
    print("\nevidence upload")
    pdf = b"%PDF-1.4 live-verify sample evidence, not a real document"
    ev_title = marker + " evidenced"
    status, _, headers = http_multipart(base + "/entry/new", {
        "activity_date": "2026-08-30", "title": ev_title,
        "provider": "Example Evidence Works %s" % marker[-8:],
        "hours": "2", "count": "1",
        "tags": "training", "markers": "domain_security",
        "notes": "created by tools/verify_live.py", "cost_usd": ""},
        {"evidence": ("live-cert.pdf", pdf, "application/pdf")})
    check("capture with a PDF attaches", status == 303, "HTTP %d" % status)
    ev_uuid = headers.get("Location", "").rsplit("/", 1)[-1]
    status, body, _ = http(base + "/entry/" + ev_uuid)
    page = body.decode()
    check("evidence listed with a verified hash",
          "live-cert.pdf" in page and "OK" in page)
    ev_ids = re.findall(r'href="/evidence/(\d+)', page)
    check("evidence download link present", bool(ev_ids))
    if ev_ids:
        status, blob, h = http(base + "/evidence/" + ev_ids[0])
        check("served with the sniffed content type",
              status == 200 and h.get("Content-Type") == "application/pdf",
              h.get("Content-Type", ""))
        check("attachment by default",
              h.get("Content-Disposition", "").startswith("attachment"))
        check("bytes round-trip exactly", blob == pdf)
        _s, _b, h2 = http(base + "/evidence/" + ev_ids[0] + "?view=1")
        check("inline only on explicit ?view=1",
              h2.get("Content-Disposition", "").startswith("inline"))

    print("\nbad uploads are refused at the boundary")
    bad_fields = {"activity_date": "2026-08-30", "title": marker + " bad",
                  "provider": "x", "hours": "1", "count": "1",
                  "tags": "training", "markers": "", "notes": "",
                  "cost_usd": ""}
    status, body, _ = http_multipart(base + "/entry/new", bad_fields,
                                     {"evidence": ("cert.pdf",
                                                   b"<html>not a pdf</html>",
                                                   "application/pdf")})
    check("wrong magic bytes are a 400", status == 400, "HTTP %d" % status)
    big = b"%PDF-1.4 " + b"\0" * (15 * 1024 * 1024)
    status, body, _ = http_multipart(base + "/entry/new", bad_fields,
                                     {"evidence": ("big.pdf", big,
                                                   "application/pdf")})
    check("an oversized upload is a 400", status == 400, "HTTP %d" % status)
    status, body, _ = http(base + "/export/entries.csv")
    check("neither refused upload wrote an entry",
          (marker + " bad") not in body.decode())

    print("\ntraversal shapes 404")
    for shape in ("/evidence/../../etc/passwd",
                  "/evidence/..%2f..%2fetc%2fpasswd",
                  "/evidence/1e1"):
        status, _, _ = http(base + shape)
        check("%s -> 404" % shape, status == 404, "HTTP %d" % status)

    # ---- v0.2: edit is a supersede --------------------------------------
    print("\nedit routes through supersede")
    status, _, _ = http(base + "/entry/" + ev_uuid, follow=False,
                        data={"action": "status", "to": "confirmed"})
    check("the evidenced entry confirms", status == 303, "HTTP %d" % status)
    status, _, headers = http(base + "/entry/" + ev_uuid, follow=False, data={
        "activity_date": "2026-08-30", "title": ev_title + " (corrected)",
        "provider": "Example Evidence Works %s" % marker[-8:],
        "hours": "2", "count": "1",
        "tags": "training", "markers": "domain_security",
        "notes": "corrected by tools/verify_live.py", "cost_usd": ""})
    check("saving a confirmed entry redirects to a NEW entry",
          status == 303, "HTTP %d" % status)
    new_uuid = headers.get("Location", "").rsplit("/", 1)[-1]
    check("the correction is a new row",
          len(new_uuid) == 32 and new_uuid != ev_uuid)
    status, body, _ = http(base + "/entry/" + ev_uuid)
    check("the old row is marked superseded",
          "superseded" in body.decode().lower())
    status, body, _ = http(base + "/entry/" + new_uuid)
    check("evidence carried to the correction",
          "live-cert.pdf" in body.decode())

    # ---- v0.2: void is terminal and uncounted ---------------------------
    print("\nvoid is terminal and uncounted")

    def cissp_total():
        _s, b, _ = http(base + "/")
        m2 = re.search(r"([\d.]+) of 120", b.decode())
        return m2.group(1) if m2 else None

    t0 = cissp_total()
    status, _, headers = http(base + "/entry/new", follow=False, data={
        "activity_date": "2026-08-30", "title": marker + " voidable",
        "provider": "Example Voidworks %s" % marker[-8:],
        "hours": "3", "count": "1",
        "tags": "training", "markers": "domain_security"})
    v_uuid = headers.get("Location", "").rsplit("/", 1)[-1]
    http(base + "/entry/" + v_uuid, follow=False,
         data={"action": "status", "to": "confirmed"})
    t1 = cissp_total()
    check("the confirmed entry counts", t0 is not None and t1 is not None
          and float(t1) > float(t0), "%s -> %s of 120" % (t0, t1))
    status, _, _ = http(base + "/entry/" + v_uuid, follow=False,
                        data={"action": "status", "to": "void"})
    check("void transition accepted", status == 303, "HTTP %d" % status)
    t2 = cissp_total()
    check("voided credits leave the total the moment it is void",
          t2 == t0, "%s -> %s -> %s of 120" % (t0, t1, t2))
    status, body, _ = http(base + "/entry/" + v_uuid)
    check("the void page is read-only", "read-only" in body.decode())
    status, _, _ = http(base + "/entry/" + v_uuid, follow=False,
                        data={"action": "status", "to": "confirmed"})
    check("void never transitions out", status == 400, "HTTP %d" % status)

    # ---- v0.2: submissions ----------------------------------------------
    print("\nsubmission tracking")
    status, body, _ = http(base + "/submissions")
    check("/submissions renders", status == 200)
    check("the corrected entry is queued", new_uuid[:8] in body.decode())
    status, body, _ = http(base + "/submissions.csv?credential=cissp")
    check("the portal CSV includes it", new_uuid in body.decode())
    status, _, _ = http(base + "/submissions", follow=False,
                        data={"entry": new_uuid, "body": "isc2",
                              "date": "2026-08-30"})
    check("marking submitted redirects", status == 303, "HTTP %d" % status)
    status, body, _ = http(base + "/submissions.csv?credential=cissp")
    check("a marked entry leaves the cissp queue",
          new_uuid not in body.decode())

    # ---- v0.2: export ---------------------------------------------------
    print("\nexport")
    status, body, _ = http(base + "/export")
    check("/export hub renders", status == 200)
    status, body, _ = http(base + "/export/entries.csv")
    dump = body.decode()
    check("full CSV dump renders",
          status == 200 and dump.startswith("uuid,status,"))
    check("the dump carries the voided row too", v_uuid in dump)
    status, body, _ = http(base + "/export/entries.json")
    doc = json.loads(body.decode())
    check("the JSON dump self-identifies",
          doc.get("format") == "cpebot-ledger-v0.2")
    status, body, _ = http(base + "/export/credential/cissp.csv")
    cyc = body.decode()
    check("the cycle CSV renders", status == 200 and "bucket" in cyc)
    check("the cycle CSV excludes the voided row", v_uuid not in cyc)

    # ---- v0.2: charts and what-if ---------------------------------------
    print("\ncharts")
    status, body, _ = http(base + "/charts")
    page = body.decode()
    check("/charts renders inline SVG", status == 200 and "<svg" in page)
    check("charts ship no script", "<script" not in page)
    check("every chart has a data-table fallback", "Data table" in page)

    print("\nwhat-if is a dry run")
    _s, b, _ = http(base + "/export/entries.csv")
    rows_before = b.decode().count("\n")
    status, body, _ = http(base + "/whatif?run=1&date=2026-08-30&hours=8"
                           "&count=1&tags=training&markers=domain_security")
    page = body.decode()
    check("/whatif answers", status == 200 and "Where it lands" in page)
    check("forfeits are named, not hidden", "Forfeited" in page)
    _s, b, _ = http(base + "/export/entries.csv")
    check("and nothing was written",
          b.decode().count("\n") == rows_before)

    # ---- v0.2: the bind is still the bind -------------------------------
    print("\nloopback stays refused")
    parts = urllib.parse.urlsplit(base)
    port = parts.port or 80
    if (parts.hostname or "") in ("127.0.0.1", "localhost", "::1"):
        # Against a loopback target this check cannot mean anything: the
        # server is SUPPOSED to answer there. It guards a private-interface
        # bind against a regression to 0.0.0.0.
        print("  skipped: the target is loopback")
    else:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % port,
                                   timeout=5)
            check("127.0.0.1 refuses the connection", False, "it answered")
        except urllib.error.URLError as exc:
            check("127.0.0.1 refuses the connection", True,
                  str(getattr(exc, "reason", exc)))

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: %s" % f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1
                  else "http://127.0.0.1:8762"))

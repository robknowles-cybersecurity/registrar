"""Command line: the same exports the web app serves, without a browser.

    python3 -m cpebot ics [--out deadlines.ics] [--credential cissp]
    python3 -m cpebot packet cissp [--unsubmitted] [--zip bundle.zip]
    python3 -m cpebot packetmaps

Every command reads the same environment as the web app (CPEBOT_DB,
CPEBOT_EVIDENCE, CPEBOT_BODIES, CPEBOT_PACKETMAPS) and takes --db,
--evidence and --bodies to override it. Nothing here writes to the ledger.
Dates are YYYY-MM-DD; --as-of defaults to today.
"""

import argparse
import datetime
import os
import sys

from . import bodies as B
from . import ics as I
from . import ledger as L
from . import packets as P


def _date(value):
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got %r" % value)


def _open(args):
    rules = B.load(args.bodies)
    led = L.Ledger(args.db, args.evidence)
    return rules, led


def _write(path, data, binary=False):
    if path in (None, "-"):
        if binary:
            sys.stdout.buffer.write(data)
        else:
            # Calendar text is CRLF by specification; write it untranslated.
            sys.stdout.buffer.write(data.encode("utf-8"))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data if binary else data.encode("utf-8"))


def cmd_ics(args):
    rules, led = _open(args)
    only = [args.credential] if args.credential else None
    if only and args.credential not in rules.ids():
        sys.stderr.write("unknown credential %r\n" % args.credential)
        return 2
    events, skipped = I.deadlines(rules, led, as_of=args.as_of, only=only)
    _write(args.out, I.render(events))
    for s in skipped:
        sys.stderr.write("skipped %s: %s\n" % (s["credential"], s["reason"]))
    if args.out not in (None, "-"):
        sys.stderr.write("%d event(s) written to %s\n" % (len(events),
                                                          args.out))
    return 0


def cmd_packet(args):
    rules, led = _open(args)
    if args.credential not in rules.ids():
        sys.stderr.write("unknown credential %r\n" % args.credential)
        return 2
    maps = P.load_maps(args.maps, rules=rules)
    try:
        if args.zip:
            data, manifest = P.bundle(rules, led, args.credential,
                                      as_of=args.as_of, maps=maps,
                                      unsubmitted_only=args.unsubmitted)
            _write(args.zip, data, binary=True)
            sys.stderr.write("%d file(s) bundled, %d gap(s), written to %s\n"
                             % (manifest["files_bundled"],
                                len(manifest["gaps"]), args.zip))
            return 0
        packet = P.entry_packets(rules, led, args.credential,
                                 as_of=args.as_of, maps=maps,
                                 unsubmitted_only=args.unsubmitted)
    except ValueError as exc:
        sys.stderr.write("%s: %s\n" % (args.credential, exc))
        return 1
    _write(args.out, P.render_text(packet))
    return 0


def cmd_packetmaps(args):
    rules = B.load(args.bodies)
    maps = P.load_maps(args.maps, rules=rules)
    for cred in rules.credentials:
        m = P.map_for(cred, maps)
        print("%-16s %-10s %s%s" % (cred.id, m.id, m.form_name,
                                    "" if m.verified else "  [UNVERIFIED]"))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="python3 -m cpebot",
                                 description="Registrar command line.")
    ap.add_argument("--db", default=None, help="ledger path (CPEBOT_DB)")
    ap.add_argument("--evidence", default=None,
                    help="evidence root (CPEBOT_EVIDENCE)")
    ap.add_argument("--bodies", default=None,
                    help="rules file (CPEBOT_BODIES)")
    sub = ap.add_subparsers(dest="command")
    sub.required = True

    p = sub.add_parser("ics", help="deadline calendar (.ics)")
    p.add_argument("--out", help="file to write; default stdout")
    p.add_argument("--credential", help="one credential id only")
    p.add_argument("--as-of", type=_date, default=None)
    p.set_defaults(func=cmd_ics)

    p = sub.add_parser("packet", help="copy-ready submission packet")
    p.add_argument("credential")
    p.add_argument("--unsubmitted", action="store_true",
                   help="only entries not yet marked submitted")
    p.add_argument("--zip", help="write the evidence bundle here instead")
    p.add_argument("--out", help="file for the text packet; default stdout")
    p.add_argument("--maps", default=None,
                   help="packet map directory (CPEBOT_PACKETMAPS)")
    p.add_argument("--as-of", type=_date, default=None)
    p.set_defaults(func=cmd_packet)

    p = sub.add_parser("packetmaps", help="which packet map each credential "
                                          "uses")
    p.add_argument("--maps", default=None)
    p.set_defaults(func=cmd_packetmaps)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

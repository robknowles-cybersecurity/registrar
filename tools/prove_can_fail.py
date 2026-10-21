#!/usr/bin/env python3
"""Break the code on purpose and watch the tests notice.

A green test suite is evidence of nothing until you have seen it go red for
the right reason. Each mutation here removes one load-bearing protection in
a COPY (the real tree and the real bodies.yaml are never touched) and runs
the suite that claims to cover it. The good outcome is failure.

Mutations:

1. **cap-removed** — CISSP's 30-CPE Group B ceiling goes to `cap: null` in
   a copied bodies.yaml; the golden engine suite must fail. (The v0.1
   mutation, kept: the whole claim of this project is "the caps are encoded
   and the arithmetic respects them".)
2. **void-counts** — VOID exclusion is doubly guarded: the ledger filters
   voided rows out of `engine_entries()` (ledger.py) AND the engine drops
   any non-confirmed status on award (engine.py). Breaking either layer
   alone leaks nothing — the other still holds, which this harness proved
   empirically. So this mutation breaks BOTH in a copied tree; the web
   suite must fail, because a voided entry crediting anything is exactly
   the defect VOID exists to prevent.
3. **csrf-off** — the CSRF gate in a copied web.py is short-circuited; the
   web suite must fail, because a POST without the token being accepted is
   the forgery the gate exists to reject.
4. **bundle-hash-off** — the evidence bundle's re-hash check in a copied
   packets.py is short-circuited; the packets suite must fail, because a
   file altered since upload being handed to an auditor as evidence is the
   exact failure the bundle's hash check exists to prevent.
5. **host-check-off** — the Host allowlist in a copied web.py accepts any
   Host; the web suite must fail, because a page served under a foreign
   name reaching the app is the DNS-rebinding path the check exists to
   close.

    python3 tools/prove_can_fail.py

Exit status 0 means every mutation was caught. Exit status 1 means at least
one suite passed with broken code, which would be a serious finding about
the tests, and the run says which.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAP_TARGET = re.compile(r'(      - id: "group_b"\n(?:.*\n)*?        cap: )30\n')

VOID_OLD = 'states = [CONFIRMED] + ([CAPTURED] if include_pending else [])'
VOID_NEW = ('states = [CONFIRMED, VOID] + '
            '([CAPTURED] if include_pending else [])')

ENGINE_OLD = 'if e["status"] != "confirmed":'
ENGINE_NEW = 'if e["status"] not in ("confirmed", "void"):'

CSRF_OLD = 'if not hmac.compare_digest(token, app.csrf().encode("utf-8")):'
CSRF_NEW = 'if False:  # MUTANT: forgery gate removed'

BUNDLE_OLD = 'if hashlib.sha256(data).hexdigest() != ev["sha256"]:'
BUNDLE_NEW = 'if False:  # MUTANT: bundle hash check removed'

HOST_OLD = ('return len(hosts) == 1 and _host_name(hosts[0]) in '
            'self.allowed_hosts')
HOST_NEW = 'return True  # MUTANT: Host check removed'


def run_suite(cwd, test_file, env=None):
    proc = subprocess.run(
        [sys.executable, os.path.join("tests", test_file)],
        cwd=cwd, env=env or dict(os.environ), capture_output=True, text=True)
    tail = (proc.stderr or proc.stdout).strip().splitlines()
    failures = [l for l in tail if l.startswith(("FAIL:", "ERROR:"))]
    return proc.returncode, failures, tail


def copy_tree(tmp):
    """A runnable copy of the package, tests and rules — nothing else."""
    dst = os.path.join(tmp, "mutant")
    os.makedirs(dst)
    shutil.copytree(os.path.join(REPO, "cpebot"), os.path.join(dst, "cpebot"))
    shutil.copytree(os.path.join(REPO, "tests"), os.path.join(dst, "tests"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(REPO, "bodies.yaml"),
                os.path.join(dst, "bodies.yaml"))
    shutil.copytree(os.path.join(REPO, "packetmaps"),
                    os.path.join(dst, "packetmaps"))
    return dst


def mutate_file(path, old, new, what):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.count(old) != 1:
        raise SystemExit(
            "could not find %s to break — the source has changed shape; "
            "update this script rather than trusting its silence." % what)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text.replace(old, new))


def mutation_cap_removed(tmp):
    with open(os.path.join(REPO, "bodies.yaml"), encoding="utf-8") as fh:
        text = fh.read()
    new, n = CAP_TARGET.subn(r"\1null\n", text, count=1)
    if n != 1:
        raise SystemExit("could not find CISSP's group_b cap to break — "
                         "bodies.yaml has changed shape; update this script "
                         "rather than trusting its silence.")
    broken = os.path.join(tmp, "bodies.yaml")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write(new)
    env = dict(os.environ, CPEBOT_BODIES=broken)
    return run_suite(REPO, "test_engine_golden.py", env)


def mutation_void_counts(tmp):
    dst = copy_tree(tmp)
    # Both guards must fall for VOID to credit — that is the point.
    mutate_file(os.path.join(dst, "cpebot", "ledger.py"),
                VOID_OLD, VOID_NEW, "the ledger's engine-feed filter")
    mutate_file(os.path.join(dst, "cpebot", "engine.py"),
                ENGINE_OLD, ENGINE_NEW, "the engine's award gate")
    return run_suite(dst, "test_web.py")


def mutation_csrf_off(tmp):
    dst = copy_tree(tmp)
    mutate_file(os.path.join(dst, "cpebot", "web.py"),
                CSRF_OLD, CSRF_NEW, "the CSRF gate")
    return run_suite(dst, "test_web.py")


def mutation_bundle_hash_off(tmp):
    dst = copy_tree(tmp)
    mutate_file(os.path.join(dst, "cpebot", "packets.py"),
                BUNDLE_OLD, BUNDLE_NEW, "the evidence bundle's hash check")
    return run_suite(dst, "test_packets.py")


def mutation_host_check_off(tmp):
    dst = copy_tree(tmp)
    mutate_file(os.path.join(dst, "cpebot", "web.py"),
                HOST_OLD, HOST_NEW, "the Host allowlist")
    return run_suite(dst, "test_web.py")


MUTATIONS = [
    ("cap-removed: CISSP group_b cap 30 -> null (golden suite)",
     mutation_cap_removed),
    ("void-counts: both VOID guards (ledger feed + engine award) broken "
     "(web suite)", mutation_void_counts),
    ("csrf-off: the POST forgery gate short-circuited (web suite)",
     mutation_csrf_off),
    ("bundle-hash-off: evidence bundle stops re-checking hashes "
     "(packets suite)", mutation_bundle_hash_off),
    ("host-check-off: any Host header accepted (web suite)",
     mutation_host_check_off),
]


def main():
    bad = 0
    for label, fn in MUTATIONS:
        print("=== mutation: %s" % label)
        tmp = tempfile.mkdtemp(prefix="cpebot-mutant-")
        try:
            code, failures, tail = fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if code == 0:
            print("PROBLEM: the suite PASSED against this broken code. "
                  "The tests are not asserting this protection.")
            print("\n".join(tail[-6:]))
            bad += 1
        else:
            print("Good: the suite failed, as it must. %d test(s) caught it:"
                  % len(failures))
            for f in failures:
                print("  %s" % f)
        print("")
    if bad:
        print("%d mutation(s) NOT caught — the suite is weaker than it looks."
              % bad)
        return 1
    print("All %d mutations caught. The real tree was never touched."
          % len(MUTATIONS))
    return 0


if __name__ == "__main__":
    sys.exit(main())

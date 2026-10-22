# How Registrar was verified

Four layers, in increasing order of how much they prove, as recorded for
v0.2 (the browser management layer). The v1 release slice (deadline
calendar, submission packets, landing page) is recorded at the end, in
"v1 additions".

## 1. The unit and feature suite — 272 tests, 0 failing

```
$ bash tools/run_tests.sh
test_engine_golden.py                  Ran 36 tests   OK
test_ledger.py                         Ran 63 tests   OK
test_mutation_sensitivity.py           Ran 17 tests   OK
test_reminders.py                      Ran 24 tests   OK
test_report.py                         Ran 26 tests   OK
test_tgcapture.py                      Ran 35 tests   OK
test_v02_units.py                      Ran 12 tests   OK
test_web.py                            Ran 48 tests   OK
test_yaml_equivalence.py               Ran 11 tests   OK
----
272 tests across 9 files; 0 file(s) not OK
```

Every capped credential has both a **naive trap** (a portfolio that a
spreadsheet would score wrong and Registrar scores right) and a **negative case**
(a portfolio where the cap bites and the verdict must be BEHIND). The v0.2
additions (`test_v02_units.py` and 28 new tests in `test_web.py`) cover VOID,
supersede-over-HTTP, upload validation, evidence serving, CSRF, security
headers, submissions, exports, charts, what-if and the advisory panel.

## 2. Proof the tests can fail

A green suite proves nothing until you have watched it go red for the right
reason.

```
$ python3 tools/prove_can_fail.py
=== mutation: cap-removed: CISSP group_b cap 30 -> null (golden suite)
Good: the suite failed, as it must. 2 test(s) caught it:
  FAIL: test_naive_trap_group_b_cap_and_group_a_floor
  FAIL: test_negative_group_b_cap_bites

=== mutation: void-counts: both VOID guards (ledger feed + engine award) broken (web suite)
Good: the suite failed, as it must. 1 test(s) caught it:
  FAIL: test_void_is_terminal_excluded_and_dupe_transparent

=== mutation: csrf-off: the POST forgery gate short-circuited (web suite)
Good: the suite failed, as it must. 1 test(s) caught it:
  FAIL: test_post_without_token_is_rejected

All 3 mutations caught. The real tree was never touched.
```

Each mutation removes one load-bearing protection in a copy and the suite
that claims to cover it must go red. The **void-counts** mutation earned its
shape empirically: the first version broke only the ledger-side filter
(`engine_entries()`), and the suite stayed green — because the engine
independently drops any non-confirmed status at award time. VOID exclusion is
doubly guarded, so a single-layer regression cannot leak a voided entry into
totals; the harness now breaks both layers and documents why. That first
"failed" run was the tool doing its job.

`tests/test_mutation_sensitivity.py` does the rules-file version of this
programmatically across 17 mutations, so that property is regression-tested
rather than demonstrated once.

## 3. The journey, over HTTP, in the test suite

`tests/test_web.py` starts a real server on `127.0.0.1:0` in a thread and
drives it with a real HTTP client, including multipart evidence upload,
observable 303s and the CSRF token a browser would carry. It exists because a
test that calls `App.dashboard()` directly proves the renderer works and does
not prove the form field names match what the handler reads — and that
mismatch is the defect that would survive every other test in this repo.

The adversarial cases are the point of the v0.2 additions: a POST with a
wrong token writes nothing; a `.pdf` whose bytes are HTML is refused with the
reason; an oversized file is refused; a traversal-shaped evidence id 404s
even when the database row itself has been tampered to point outside the
evidence root with hash-identical bytes — the realpath prefix guard is the
only thing left standing in that test, which is exactly why it exists.

## 4. The journey, over a private network, against the running server

This is the claim the unit tests cannot make: a bind address, a systemd
unit and a firewall are all things a unit test never meets.

```
$ python3 tools/verify_live.py http://<BIND_ADDRESS>:8762
```

Result on 2026-08-31: **85 passed, 0 failed**, against the demo ledger, with
the server bound to a private, non-loopback interface and *not* to loopback. The demo
database was a populated v0.1 file, so this run also proved the additive
v0.2 migration on real data: the server opened it, added the new columns in
place, and the whole journey ran on top.

What the live run walks through, in order:

| Step | Asserted |
|---|---|
| `/health` | rules loaded (7 credentials), kill-switch counter exposed, version 0.2.0 |
| CSRF bootstrap | the capture form carries the per-install token |
| capture | POST redirects 303; entry lands **captured**, not confirmed |
| pre-confirm | the pending entry does **not** appear in dashboard totals |
| confirm | 303; entry appears in the confirmed list |
| soft duplicate | an identical entry is still *captured*, but confirming it is intercepted with **409**; an explicit override then goes through |
| dashboard | states the naive total it rejected, and what the caps cost |
| report | position, gap, runway, pace, evidence, fees, portal, submitted, UNVERIFIED flags |
| packet | `/packet/cissp` is a real zip; manifest states the counted total and the gaps |
| `/bodies` | renders the rules read-only and marks unverified plugins |
| security headers | CSP (no script at all), `X-Frame-Options DENY`, nosniff, no-referrer; no `<script>` on the page |
| forgery | POST without the token → **403**, and the full dump proves nothing was written |
| evidence upload | PDF attaches; hash verifies; served with the *sniffed* content type, attachment by default, inline only on explicit `?view=1`; bytes round-trip exactly |
| bad uploads | wrong magic → **400**; 15 MB+ → **400**; neither wrote an entry |
| traversal | `../../etc/passwd`, `..%2f..%2f`, `1e1` → all **404** |
| supersede | saving a confirmed entry creates a NEW row, marks the old one superseded, carries the evidence |
| void | credits leave the total the moment it is void (72.5 → 75.5 → 72.5 of 120); the page goes read-only; no transition out |
| submissions | queued → portal CSV → marked with a date → leaves the queue |
| export | full CSV/JSON dump carries the voided row; the cycle CSV excludes it |
| charts | inline SVG, no script, a data-table fallback per chart |
| what-if | answers with landings and forfeits; writes nothing |
| loopback | `127.0.0.1:8762` → connection refused; the private network is the boundary (skipped when the target itself is loopback) |

Two verifier-side lessons from this run, kept because they are the sort of
thing that silently rots a live check: the `/entries?q=` page *reflects the
query back in the search box*, so "the forged title does not appear" must be
asserted against the full export, not the search page; and the evidence and
void legs need per-run provider names or the second execution trips the
duplicate interceptor — correctly — the same way the v0.1 confirm leg did.

## What is NOT verified

- **The numbers in `bodies.yaml` for ATAP membership and CTM.** They could
  not be fully confirmed on ATAP's own pages. Those two are
  marked `verified: false`, and CTM is additionally `caps_known: false` — the
  engine refuses to give it a compliance verdict at all. Four smaller gaps
  (CISSP rollover, CySA+ two bucket caps, GCITP rollover) are listed in
  the README's units table.
- **Telegram v0.2 interplay under a restart.** The v0.2 migration is
  additive and the Telegram path is unchanged, but a v0.1 capture process
  restarting onto v0.2 code was not observed as part of this verification.
- **`cpebot-web.service` under systemd.** The unit template carries a
  standard hardening block, but the live run above used a foreground
  process. The app itself has been proven on a non-loopback bind address,
  so what is unproven is the unit's hardening block, not the program.
- **The LLM validator.** It is a disabled seam (`llm_validator_enabled`,
  default off) whose enabled stub says it is not wired. There is no model,
  no key and no network call to verify — by design.

## v1 additions (2026-10-20)

Recorded on Python 3.13 (PyYAML 6.0.3 present, so the YAML-equivalence
tests ran both parsers). Release notes: `docs/RELEASE-NOTES-v1.md`.

```
$ bash tools/run_tests.sh
test_engine_golden.py                  Ran 36 tests   OK
test_ics.py                            Ran 30 tests   OK
test_ledger.py                         Ran 63 tests   OK
test_mutation_sensitivity.py           Ran 17 tests   OK
test_packets.py                        Ran 27 tests   OK
test_release.py                        Ran 13 tests   OK
test_reminders.py                      Ran 24 tests   OK
test_report.py                         Ran 26 tests   OK
test_tgcapture.py                      Ran 35 tests   OK
test_v02_units.py                      Ran 12 tests   OK
test_web.py                            Ran 48 tests   OK
test_yaml_equivalence.py               Ran 11 tests   OK
----
342 tests across 12 files; 0 file(s) not OK
```

`tools/prove_can_fail.py` gained a fourth mutation, **bundle-hash-off**:
the evidence bundle's re-hash check is short-circuited in a copied
`packets.py`, and `test_packets.py` must fail. It does (3 tests catch it),
alongside the original three. `tools/verify_live.py` was re-run against a
demo ledger on loopback: 84 passed, 0 failed, 1 skipped (the loopback-refusal
check, which only applies to a non-loopback bind). It does not yet exercise
the v1 routes; those are covered over real HTTP in `test_ics.py` and
`test_packets.py`.

The `.ics` output was additionally parsed by the third-party `icalendar`
library (7.3.0, installed in a throwaway virtualenv, not a dependency of
this project): 16 events and 16 alarms from the demo ledger
read back with the expected dates.

Not verified in v1: the ISC2 packet map's field labels against the live
ISC2 form (member login required), and the landing page as deployed on
Cloudflare Pages.

## v1.0.0 (2026-10-21)

The Host-header allowlist, the fail-closed Telegram chat check, the
form-label wiring, http(s)-only source links, the generic error page and
the byte-wise anti-forgery token check added tests, and
`tools/prove_can_fail.py` gained a fifth mutation, **host-check-off**, which
the web suite catches (6 tests).

```
$ bash tools/run_tests.sh
408 tests across 13 files; 0 file(s) not OK
$ python3 tools/prove_can_fail.py
All 5 mutations caught. The real tree was never touched.
```


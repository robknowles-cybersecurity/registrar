"""cpebot — CPE/CEU/ECE/PDU registrar for a multi-body certification portfolio.

Public name: Registrar. v1.2.0. v1.0.0 was the v1 release (deadlines calendar,
submission packets and the bodies catalog, over the v0.2 browser layer and
v0.1 ledger).
Self-hosted, private-network-only, stdlib-first.

The whole point of this package is in one sentence: professional bodies do not
let you add up your hours. Every body applies caps (this category tops out at
12), floors (at least 2 of these must be ethics), group splits (90 of the 120
must be Group A) and windows (credits earned in this calendar year must be
registered by 1 February). A naive sum tells you that you are compliant when
you are not, and it tells you that in the direction that costs you the
credential. `engine.py` is the answer to that; everything else feeds it.
"""

__version__ = "1.2.0"

# Content sources for the landing-page guide

The CPE/CEU guide on the landing page (`site/index.html`, the sections from
"The problem" to "Glossary") is written from the sources below. Every page
listed here was fetched on 22 October 2026 while the guide was written; the
note on each says what was taken from it. Nothing was copied word for word:
the text is paraphrased and expanded.

If a body changes its rules, update `bodies.yaml` first (it is the source of
truth for every number the app uses), then the table and any sentence here
that names the old value.

## The guide's base text

| Source | Licence | What was taken |
|---|---|---|
| https://insiderthreatanalyst.com/cpe (CPE Resources) | CC BY 4.0 | The two-minute version (cycles, one credit per hour, Group A and Group B, the three ways credits reach your record, credits that apply across certifications); the habits ("log it the week it happens", one evidence folder, read the handbook, grace periods are not a plan, touch the system weekly); the provider list and its links; the "give back" note; the July 2026 currency caveat on provider details. |
| https://creativecommons.org/licenses/by/4.0/ | — | The licence terms, to word the attribution line. |

The attribution line under the glossary links both pages. "More on CPE
rules" in "The problem" links the CPE Resources page.

## The per-body table ("How each body counts")

Every row comes from `bodies.yaml`, and each body's name links to the
`source_url` that file records for it. An **UNVERIFIED** badge is shown
wherever `bodies.yaml` marks a value as unverified. No value was added that
could not be confirmed by fetching the body's own page; where a body's page
was not fetched, the row says only what `bodies.yaml` already says.

| Body | Page fetched | What was taken or checked |
|---|---|---|
| ISC2 | https://www.isc2.org/policies-procedures/member-policies | Group A and Group B definitions; credits added to your account apply to all your ISC2 certifications in their active cycle, not one in its grace period; the 90-day grace period. That ISC2 events and listed CPE Partners can submit for you is as recorded in `bodies.yaml` from ISC2's CPE Partners page. |
| ISC2 | https://www.isc2.org/Insights/2024/05/Managing-Your-ISC2-CPE-Credits-and-Activities | The same points, cross-checked, and that Group A claims need supporting documentation and a summary of what you learned. |
| ISC2 | https://www.isc2.org/Insights/2026/06/whats-new-in-isc2-cpe-credits | The same points as above, cross-checked against the current wording. |
| EC-Council | https://cert.eccouncil.org/cpe-policy.html | 120 credits per certification over three years; each calendar year's credits registered by 1 February of the next; credits apply across all your EC-Council certifications; an activity counts in one window only. |
| PMI | https://www.pmi.org/-/media/pmi/documents/public/pdf/certifications/resources/ccr-certification-requirements-handbook.pdf?rev=4fb44c29242e428ab7776469c59efb4a | Checked that the handbook link answers. The figures (cycle, PMP and CAPM totals, Education and Giving Back limits, carryover, retention) are as recorded in `bodies.yaml`, from the 2022 handbook. |
| ACFE | https://www.acfe.com/cfe-credential/continuing-professional-education-cpe-requirements | 20 CPE a year with at least 10 on fraud and 2 on ethics; one course can count toward both; compliance is self-certified, so you keep the proof. The carryover (up to 10), the end-of-anniversary-month deadline, the 30-day extension, three-year retention and the 60 days to answer an audit are as recorded in `bodies.yaml` from the ACFE's CPE FAQ and reporting pages. |
| ATAP | https://www.atapworldwide.org/page/certificationexam | Checked that the certification page answers; the CTM figures and their UNVERIFIED badges are as recorded in `bodies.yaml`. The CTM page recorded for the credential (https://www.atapworldwide.org/page/CTM) returned a "page not found" page, so the table links the certification page instead. |
| ISACA | not fetched | From `bodies.yaml` only. |
| CompTIA | not fetched | From `bodies.yaml` only. |
| GIAC | not fetched | From `bodies.yaml` only. |
| IAPP | not fetched | From `bodies.yaml` only. |
| University of Maryland (GCITP) | not fetched | From `bodies.yaml` only. |

The audit-retention figures in "Audit-ready records" (ACFE 3 years, PMI 18
months and ISACA 12 months after the cycle ends) and the caps quoted in
"Common surprises" come from the same rows.

## Providers ("Where to earn credits")

Taken from the CPE Resources page. Each link was fetched to check that it
still answers and, where noted, that the page still says what the guide says.

| Provider | Page fetched | Result |
|---|---|---|
| NITSIG | https://www.nationalinsiderthreatsig.org/ | Answers; free membership, meetings, webinars and the Symposium & Expo confirmed. |
| CISA Insider Threat Mitigation | https://www.cisa.gov/topics/physical-security/insider-threat-mitigation | Refuses scripted requests (403); a browser-style fetch confirmed the page and its free resources. |
| CMU SEI / CERT | https://www.sei.cmu.edu/ | Answers. |
| CInRM CoE | https://www.cinrmcoe-cdecgrin.ca/solutions | Answers; the practical certificates, the professional certificate, the Carleton University partnership and the 12 ASIS-recognized CPE credits confirmed. |
| SecureWorld | https://www.secureworld.io/ | Answers; Remote Sessions webcasts confirmed. The 6 to 18 CPE range by pass is from the CPE Resources page. |
| GuidePoint GPSEC | https://www.guidepointsecurity.com/gpsec/ | Answers; free regional events confirmed. Automatic ISC2 submission and the Brick House webinar are from the CPE Resources page. |
| Gartner | https://www.gartner.com/en/webinars | Refuses automated requests (403); not confirmed. Kept as the CPE Resources page gives it. |
| FS-ISAC | https://www.fsisac.com/ | Answers. |

## Written for this guide

"Audit-ready records" (the checklist), "Common surprises: what usually
doesn't count", "Your first 90 days with a new cert", the glossary and the
"In Registrar" lines at the end of each section were written for this page
from general practice and from the sources above. Every specific figure in
them is one of the values listed above.

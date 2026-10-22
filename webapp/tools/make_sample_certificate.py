"""Write webapp/src/sample/try-capture-certificate.pdf.

The certificate the empty ledger offers as "try capture with this": an
invented holder, provider and course, laid out like a typical attendance
letter so the parser has a date, hours, provider and title to find. The
expected values are in webapp/tests/sample_certificate.test.mjs.

    python3 webapp/tools/make_sample_certificate.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests", "fixtures"))

from make_fixtures import write_pdf   # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "src", "sample",
                   "try-capture-certificate.pdf")

LINES = [
    ("c", 735, "B", 9, "SAMPLE CERTIFICATE - INVENTED FOR TRYING REGISTRAR - NOT A REAL RECORD"),
    ("c", 660, "TB", 28, "Certificate of Attendance"),
    ("c", 618, "I", 14, "This is to certify that"),
    ("c", 584, "TB", 24, "Jordan Sample"),
    ("c", 548, "T", 14, "attended the live online course"),
    ("c", 520, "TB", 18, "“Incident Response Tabletop Exercises”"),
    ("c", 490, "T", 13, "held on September 24, 2026"),
    ("c", 466, "T", 13, "and earned 3.5 CPE credits (3.5 contact hours)."),
    (72, 360, "B", 11, "Provider:"),
    (190, 360, "R", 11, "Example Cyber Academy"),
    (72, 342, "B", 11, "Delivery:"),
    (190, 342, "R", 11, "Virtual, instructor-led"),
    (72, 324, "B", 11, "Certificate no.:"),
    (190, 324, "R", 11, "SAMPLE-0001"),
    ("c", 200, "R", 9, "Example Cyber Academy is an invented provider. This file exists to try Registrar's evidence capture."),
]

if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    write_pdf(OUT, LINES)
    print("wrote %s" % os.path.relpath(OUT))

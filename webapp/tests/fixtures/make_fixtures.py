"""Generate the synthetic certificate fixtures for the parser tests.

Every certificate here is INVENTED: invented holders ("Sample", "Example"),
invented providers, invented events. They exist only to exercise the
evidence parser across the layouts real certificates use: centred award
style, left-aligned letters, label/value tables, two-column blocks, landscape
and portrait, US and international dates, hours vs CPE vs CEU vs PDU wording.

Writes certificates/NN-name.pdf (text PDFs, standard fonts, no dependencies)
and expected.json (what the parser should find in each).

    python3 webapp/tests/fixtures/make_fixtures.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "certificates")

# WinAnsi for the few non-ASCII characters the fixtures use.
WINANSI = {"’": 0x92, "“": 0x93, "”": 0x94, "–": 0x96,
           "—": 0x97, "é": 0xE9, "·": 0xB7}

FONTS = {"R": "Helvetica", "B": "Helvetica-Bold", "T": "Times-Roman",
         "TB": "Times-Bold", "I": "Times-Italic"}


def _pdf_str(s):
    out = bytearray(b"(")
    for ch in s:
        if ch in "()\\":
            out += b"\\" + ch.encode()
        elif ch in WINANSI:
            out += b"\\%03o" % WINANSI[ch]
        else:
            out += ch.encode("latin-1")
    out += b")"
    return bytes(out)


def _width(text, size):
    return len(text) * size * 0.5          # rough: good enough to centre


def write_pdf(path, lines, landscape=False):
    """lines: [(x or "c", y, font, size, text)] in PDF points from bottom-left."""
    w, h = (792, 612) if landscape else (612, 792)
    content = bytearray()
    for x, y, font, size, text in lines:
        if x == "c":
            x = (w - _width(text, size)) / 2
        content += b"BT /F%s %d Tf %.1f %.1f Td %s Tj ET\n" % (
            font.encode(), size, x, y, _pdf_str(text))
    objs = []
    font_ids = {}
    for i, key in enumerate(FONTS):
        font_ids[key] = 4 + i
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    res = b" ".join(b"/F%s %d 0 R" % (k.encode(), v) for k, v in font_ids.items())
    content_id = 4 + len(FONTS)
    objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                b"/Resources << /Font << %s >> >> /Contents %d 0 R >>"
                % (w, h, res, content_id))
    for key, name in FONTS.items():
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s "
                    b"/Encoding /WinAnsiEncoding >>" % name.encode())
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + bytes(content)
                + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R /Info << /Producer "
            b"(Registrar synthetic fixture) >> >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def centred(rows, top=520, landscape=True):
    """Award style: every line centred, descending."""
    y = top
    out = []
    for font, size, text, gap in rows:
        out.append(("c", y, font, size, text))
        y -= gap
    return out


def left(rows, x=72, top=720):
    y = top
    out = []
    for font, size, text, gap in rows:
        out.append((x, y, font, size, text))
        y -= gap
    return out


def table(pairs, x=72, x2=250, top=560, size=12, gap=22, font="R"):
    out = []
    y = top
    for label, value in pairs:
        out.append((x, y, "B", size, label))
        out.append((x2, y, font, size, value))
        y -= gap
    return out


CERTS = []


def cert(name, expected, lines, landscape=False):
    CERTS.append((name, expected, lines, landscape))


# 1. ISC2-style CPE certificate, centred, labelled credits.
cert("isc2-webinar-centred", {
    "activity_date": "2026-03-12", "hours": 1.5, "provider": "Example Security Institute",
    "title": "Zero Trust Architecture in Practice", "holder_name": "Jordan Sample",
    "credential": "cissp"},
    centred([
        ("TB", 30, "Certificate of Completion", 50),
        ("I", 14, "This certifies that", 34),
        ("TB", 26, "Jordan Sample", 40),
        ("T", 14, "has successfully completed the webinar", 26),
        ("TB", 18, "“Zero Trust Architecture in Practice”", 36),
        ("T", 13, "on March 12, 2026", 26),
        ("T", 13, "and is awarded 1.5 CPE credits (ISC2 Group A)", 60),
        ("B", 12, "Example Security Institute", 18),
        ("R", 10, "Provider of continuing professional education", 18),
    ]), landscape=True)

# 2. CompTIA-style CEU letter, left aligned, numeric US date.
cert("comptia-ceu-letter", {
    "activity_date": "2026-01-27", "hours": 8, "provider": "Placeholder Training Academy",
    "title": "Threat Hunting with Packet Analysis", "holder_name": "Alex Example",
    "credential": "cysa_plus"},
    left([
        ("B", 16, "Placeholder Training Academy", 20),
        ("R", 10, "100 Sample Street, Exampleville", 50),
        ("R", 11, "Date: 01/27/2026", 30),
        ("R", 11, "To whom it may concern,", 24),
        ("R", 11, "This letter confirms that Alex Example attended and completed", 16),
        ("R", 11, "Threat Hunting with Packet Analysis, a live instructor-led course.", 24),
        ("R", 11, "Course length: 8 hours. Eligible for 8 CompTIA CEUs.", 40),
        ("R", 11, "Sincerely,", 30),
        ("R", 11, "Registrar of Courses, Placeholder Training Academy", 16),
    ]))

# 3. EC-Council style, table of labels.
cert("eccouncil-table", {
    "activity_date": "2026-02-18", "hours": 4, "provider": "Sample Cyber Range LLC",
    "title": "Web Application Penetration Testing Lab", "holder_name": "Riley Sample",
    "credential": "ceh"},
    left([("B", 20, "CERTIFICATE OF PARTICIPATION", 60)], top=700)
    + table([
        ("Participant:", "Riley Sample"),
        ("Course Title:", "Web Application Penetration Testing Lab"),
        ("Training Provider:", "Sample Cyber Range LLC"),
        ("Completion Date:", "2026-02-18"),
        ("ECE Credits:", "4"),
        ("Delivery:", "Virtual"),
    ], top=600))

# 4. UMD GCITP, insider threat, day-first date written out.
cert("gcitp-pdu-insider", {
    "activity_date": "2026-04-09", "hours": 3, "provider": "Example Insider Risk Council",
    "title": "Behavioral Indicators of Insider Threat", "holder_name": "Morgan Example",
    "credential": "gcitp"},
    centred([
        ("B", 24, "Certificate of Attendance", 48),
        ("R", 13, "Awarded to", 30),
        ("B", 22, "Morgan Example", 40),
        ("R", 13, "for attending Behavioral Indicators of Insider Threat", 24),
        ("R", 13, "held on 9 April 2026", 24),
        ("R", 13, "3 PDUs (counter-insider threat, C-InT)", 60),
        ("B", 12, "Example Insider Risk Council", 18),
    ]), landscape=True)

# 5. ATAP chapter meeting.
cert("atap-chapter-meeting", {
    "activity_date": "2026-05-14", "hours": 2, "provider": "Sample Chapter of ATAP",
    "title": "Threat Assessment Case Review", "holder_name": "Casey Sample",
    "credential": "ctm"},
    left([
        ("B", 18, "Sample Chapter of ATAP", 28),
        ("R", 12, "Chapter Meeting Attendance", 40),
        ("R", 12, "Name: Casey Sample", 22),
        ("R", 12, "Meeting: Threat Assessment Case Review", 22),
        ("R", 12, "Date: May 14, 2026", 22),
        ("R", 12, "Contact hours: 2.0", 22),
        ("R", 12, "Topic: threat assessment and management", 40),
        ("I", 11, "Chapter Secretary", 16),
    ]))

# 6. ACFE fraud CPE, labelled, ethics.
cert("acfe-fraud-ethics", {
    "activity_date": "2026-06-03", "hours": 2, "provider": "Example Fraud Examiners Forum",
    "title": "Ethics for Fraud Examiners", "holder_name": "Taylor Example",
    "credential": "cfe"},
    centred([
        ("TB", 28, "Certificate of Completion", 46),
        ("T", 14, "presented to", 30),
        ("TB", 24, "Taylor Example", 40),
        ("T", 13, "for completion of", 24),
        ("TB", 17, "Ethics for Fraud Examiners", 34),
        ("T", 12, "Sponsored by Example Fraud Examiners Forum", 22),
        ("T", 12, "Date completed: June 3, 2026", 22),
        ("T", 12, "CPE awarded: 2.0  ·  Field of study: Ethics", 22),
    ]), landscape=True)

# 7. Conference, multi-day range: the activity date is the last day.
cert("conference-range", {
    "activity_date": "2026-09-17", "hours": 14, "provider": "Example SecCon",
    "title": "Example SecCon 2026", "holder_name": "Jamie Sample",
    "credential": "cissp"},
    centred([
        ("B", 26, "Example SecCon 2026", 40),
        ("R", 14, "Certificate of Attendance", 40),
        ("R", 13, "This is to certify that", 28),
        ("B", 22, "Jamie Sample", 36),
        ("R", 13, "attended Example SecCon 2026, September 15–17, 2026", 24),
        ("R", 13, "and earned 14 CPE hours.", 60),
        ("R", 10, "Example SecCon · Conference Committee", 16),
    ]), landscape=True)

# 8. Self-paced online course with ISO date and duration in h/m.
cert("online-selfpaced-iso", {
    "activity_date": "2026-07-21", "hours": 3.5, "provider": "Sample Learning Platform Inc.",
    "title": "Cloud Incident Response Fundamentals", "holder_name": "Drew Example",
    "credential": "cysa_plus"},
    left([
        ("B", 14, "Sample Learning Platform Inc.", 60),
        ("B", 22, "Course Completion", 40),
        ("R", 12, "Drew Example", 20),
        ("R", 12, "completed Cloud Incident Response Fundamentals", 20),
        ("R", 12, "Completed: 2026-07-21", 20),
        ("R", 12, "Duration: 3h 30m", 20),
        ("R", 9, "Certificate ID SAMPLE-0000-0008", 20),
    ], top=700))

# 9. Two-column block layout (labels on the left, values on the right).
cert("two-column-block", {
    "activity_date": "2026-08-05", "hours": 6, "provider": "Placeholder Cyber Academy",
    "title": "Malware Analysis Essentials", "holder_name": "Avery Sample",
    "credential": "cissp"},
    left([("B", 20, "Placeholder Cyber Academy", 30),
          ("R", 12, "Record of Training", 50)], top=720)
    + table([
        ("Student", "Avery Sample"),
        ("Course", "Malware Analysis Essentials"),
        ("Date of Completion", "August 5, 2026"),
        ("CPE Hours", "6"),
        ("Instructor", "Sample Instructor"),
    ], x2=260, top=620, gap=26))

# 10. Day-first numeric date (DD/MM/YYYY, unambiguous day > 12).
cert("intl-date-dmy", {
    "activity_date": "2026-03-24", "hours": 7, "provider": "Example European Security Forum",
    "title": "GDPR Breach Response Workshop", "holder_name": "Robin Example",
    "credential": "cissp"},
    centred([
        ("B", 24, "Certificate", 44),
        ("R", 13, "We hereby certify that", 28),
        ("B", 20, "Robin Example", 36),
        ("R", 13, "participated in the GDPR Breach Response Workshop", 24),
        ("R", 13, "on 24/03/2026, for a total of 7 hours of training.", 60),
        ("B", 12, "Example European Security Forum", 18),
    ]), landscape=True)

# 11. Teaching / presenting letter.
cert("presenter-letter", {
    "activity_date": "2026-02-26", "hours": 1, "provider": "Example ISSA Chapter",
    "title": "Securing CI/CD Pipelines", "holder_name": "Sam Sample",
    "credential": "cissp"},
    left([
        ("B", 16, "Example ISSA Chapter", 50),
        ("R", 11, "February 26, 2026", 30),
        ("R", 11, "Dear Sam Sample,", 22),
        ("R", 11, "Thank you for presenting “Securing CI/CD Pipelines” at our", 16),
        ("R", 11, "monthly chapter meeting. Your session ran for 1 hour.", 30),
        ("R", 11, "Kind regards,", 26),
        ("R", 11, "Program Chair, Example ISSA Chapter", 16),
    ]))

# 12. CEU with a contact-hours conversion: the hours figure is the one to use.
cert("ceu-conversion", {
    "activity_date": "2026-10-02", "hours": 15, "provider": "Example University Extension",
    "title": "Applied Cryptography for Practitioners", "holder_name": "Quinn Example",
    "credential": "cysa_plus"},
    centred([
        ("TB", 24, "Example University Extension", 44),
        ("T", 14, "certifies that", 30),
        ("TB", 22, "Quinn Example", 36),
        ("T", 13, "has completed Applied Cryptography for Practitioners", 24),
        ("T", 13, "earning 1.5 CEUs (15 contact hours)", 24),
        ("T", 13, "Issued October 2, 2026", 24),
    ]), landscape=True)

# 13. Expiry date present: it must not be taken as the activity date.
cert("with-expiry-date", {
    "activity_date": "2026-05-30", "hours": 12, "provider": "Sample Cloud Institute",
    "title": "Cloud Security Practitioner Bootcamp", "holder_name": "Kai Sample",
    "credential": "cysa_plus"},
    left([
        ("B", 18, "Sample Cloud Institute", 40),
        ("B", 14, "Certificate of Achievement", 40),
        ("R", 12, "Recipient: Kai Sample", 22),
        ("R", 12, "Program: Cloud Security Practitioner Bootcamp", 22),
        ("R", 12, "Valid until: 2029-05-30", 22),
        ("R", 12, "Date awarded: 2026-05-30", 22),
        ("R", 12, "Learning hours: 12", 22),
    ], top=700))

# 14. Abbreviated month and "hrs".
cert("abbrev-month-hrs", {
    "activity_date": "2026-11-04", "hours": 2.5, "provider": "Placeholder SOC Guild",
    "title": "Detection Engineering Roundtable", "holder_name": "Lee Example",
    "credential": "cissp"},
    left([
        ("B", 18, "Placeholder SOC Guild", 40),
        ("R", 12, "Attendance record", 40),
        ("R", 12, "Lee Example attended Detection Engineering Roundtable", 22),
        ("R", 12, "Nov 4, 2026  |  2.5 hrs", 22),
    ], top=680))

# 15. Fraud CPE with field of study in a table; time on the date line.
cert("acfe-table-fraud", {
    "activity_date": "2026-08-19", "hours": 1.5, "provider": "Example Audit Network",
    "title": "Detecting Procurement Fraud", "holder_name": "Parker Sample",
    "credential": "cfe"},
    left([("B", 18, "Example Audit Network", 30),
          ("R", 12, "CPE Certificate", 40)], top=700)
    + table([
        ("Attendee", "Parker Sample"),
        ("Program title", "Detecting Procurement Fraud"),
        ("Event date", "08/19/2026 1:00 PM ET"),
        ("Field of study", "Fraud (detection and deterrence)"),
        ("CPE credits", "1.5"),
        ("Delivery method", "Group-Internet based"),
    ], top=600, x2=230))

# 16. Portrait award with the name on its own line under a long sentence.
cert("portrait-award", {
    "activity_date": "2026-01-15", "hours": 5, "provider": "Example Threat Intel Alliance",
    "title": "Adversary Emulation Workshop", "holder_name": "Charlie Example",
    "credential": "ceh"},
    centred([
        ("TB", 26, "Example Threat Intel Alliance", 60),
        ("T", 14, "This is to certify that", 34),
        ("TB", 24, "Charlie Example", 44),
        ("T", 13, "successfully completed the", 22),
        ("TB", 16, "Adversary Emulation Workshop", 34),
        ("T", 12, "January 15, 2026", 22),
        ("T", 12, "Credits: 5 ECE", 22),
    ], top=680, landscape=False))


def main():
    os.makedirs(OUT, exist_ok=True)
    expected = {}
    for i, (name, exp, lines, landscape) in enumerate(CERTS, 1):
        fname = "%02d-%s.pdf" % (i, name)
        write_pdf(os.path.join(OUT, fname), lines, landscape=landscape)
        expected[fname] = exp
    with open(os.path.join(HERE, "expected.json"), "w", encoding="utf-8") as fh:
        json.dump(expected, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote %d certificates to %s" % (len(CERTS), OUT))


if __name__ == "__main__":
    main()

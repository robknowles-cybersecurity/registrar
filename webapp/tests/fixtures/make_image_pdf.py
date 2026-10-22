"""Wrap images/scan-cert.jpg in a one-page PDF with NO text layer.

That is what a scanner or a phone "save as PDF" produces, and it is the case
the app must notice (no text from pdf.js) and send to OCR instead.
"""

import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))


def jpeg_size(data):
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg
    raise ValueError("no SOF marker")


def main():
    src = os.path.join(HERE, "images", "scan-cert.jpg")
    data = open(src, "rb").read()
    w, h = jpeg_size(data)
    pw, ph = 792, 792 * h / w
    content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (pw, ph)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>" % (pw, ph),
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, len(data)) + data + b"\nendstream",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offs = []
    for n, body in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for o in offs:
        out += b"%010d 00000 n \n" % o
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    dst = os.path.join(HERE, "images", "scan-cert-image-only.pdf")
    open(dst, "wb").write(bytes(out))
    print("wrote", dst, len(out), "bytes")


if __name__ == "__main__":
    main()

"""Fail if the built browser app carries internal build or research wording.

The app ships to a public site, so it must carry no local paths, no
private-network addresses or hostnames, and no old project name outside the
export format ID.

    python3 webapp/tools/check_public_text.py site      # the whole site, app included
"""

import os
import re
import sys

PATTERNS = [
    # absolute /home/ and /srv/ paths (not a URL path segment)
    r"(?<![\w.-])/(?:home|srv)/",
    # RFC 1918 address literals
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
    # .lan and .local hostnames
    r"(?i)\b[a-z0-9-]+\.(?:lan|local)\b",
    # the old project name, except in the export format ID
    r"\bcpebot\b(?!-ledger-v0\.2\b)",
]
SKIP_DIRS = ("vendor",)


def main(root):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith((".json", ".js", ".mjs", ".html", ".css",
                                  ".webmanifest", ".txt", ".svg")):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for pat in PATTERNS:
                for m in re.finditer(pat, text):
                    a = max(0, m.start() - 40)
                    hits.append("%s: %s: ...%s..." % (
                        os.path.relpath(path, root), pat,
                        text[a:m.end() + 30].replace("\n", " ")))
    for h in hits:
        print(h)
    print("%d public-text hit(s) in %s" % (len(hits), root))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

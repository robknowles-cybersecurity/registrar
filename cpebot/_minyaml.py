"""A deliberately tiny YAML reader for the exact subset bodies.yaml uses.

Why this exists: the design rule for this project is stdlib-only — no venv,
no pip, nothing that can rot between a reboot and a renewal deadline. bodies.yaml is the one file
that wants to be YAML, because a human edits it whenever a body changes its
rules and JSON has no comments to hold the provenance.

So: cpebot uses PyYAML when the interpreter has it, and falls back to this
when it does not. The obvious hazard of shipping two parsers is that they
disagree. tests/test_yaml_equivalence.py exists solely to close that hazard —
when PyYAML IS present it parses bodies.yaml both ways and asserts the two
dicts are identical. If they ever diverge, the test fails and this file is
wrong, not bodies.yaml.

Supported subset, and nothing else:
  * `# comment` lines and trailing comments outside quoted strings
  * `key: value` mappings, nested by indentation (spaces only)
  * `- ` sequence items, holding either a scalar or a mapping
  * inline flow sequences: ["a", "b"] and []
  * scalars: double-quoted strings, ints, floats, true/false, null
  * bare (unquoted) scalar strings

Anything else raises MinYamlError rather than guessing. A rules file that
silently half-parses is worse than one that refuses to load.
"""


class MinYamlError(ValueError):
    pass


def _strip_comment(line):
    """Remove a trailing # comment, respecting double-quoted strings."""
    out = []
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" and i + 1 < len(line):
                out.append(ch)
                out.append(line[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            out.append(ch)
        else:
            if ch == '"':
                in_str = True
                out.append(ch)
            elif ch == "#":
                # A '#' only starts a comment at line start or after whitespace.
                if i == 0 or line[i - 1].isspace():
                    break
                out.append(ch)
            else:
                out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _scalar(tok):
    tok = tok.strip()
    if tok == "":
        return None
    if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
        body = tok[1:-1]
        return (
            body.replace("\\\\", "\x00")
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\x00", "\\")
        )
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        return [_scalar(p) for p in _split_flow(inner)]
    low = tok.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


def _split_flow(inner):
    """Split an inline-list body on commas that are outside quotes."""
    parts = []
    buf = []
    in_str = False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if in_str:
            if ch == "\\" and i + 1 < len(inner):
                buf.append(ch)
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            buf.append(ch)
        elif ch == '"':
            in_str = True
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p.strip() != ""]


def _split_key(line):
    """Split `key: value` on the first colon outside a quoted string."""
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == ":":
            after = line[i + 1: i + 2]
            if after in ("", " "):
                return line[:i], line[i + 1:]
        i += 1
    return None, None


def loads(text):
    lines = []
    for raw_no, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw.split("#")[0]:
            raise MinYamlError("tab indentation at line %d; use spaces" % raw_no)
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip(), raw_no))
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, lines[0][0])
    if idx != len(lines):
        raise MinYamlError("unconsumed input at line %d" % lines[idx][2])
    return value


def _parse_block(lines, idx, indent):
    if lines[idx][1].startswith("- "):
        return _parse_seq(lines, idx, indent)
    if lines[idx][1] == "-":
        return _parse_seq(lines, idx, indent)
    return _parse_map(lines, idx, indent)


def _parse_seq(lines, idx, indent):
    out = []
    while idx < len(lines):
        cur_indent, content, lineno = lines[idx]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise MinYamlError("unexpected indent at line %d" % lineno)
        if not (content == "-" or content.startswith("- ")):
            break
        rest = content[1:].lstrip()
        if rest == "":
            idx += 1
            if idx < len(lines) and lines[idx][0] > indent:
                val, idx = _parse_block(lines, idx, lines[idx][0])
                out.append(val)
            else:
                out.append(None)
            continue
        key, after = _split_key(rest)
        if key is None:
            out.append(_scalar(rest))
            idx += 1
            continue
        # A mapping that starts on the dash line. Its members sit at the
        # column where `rest` began.
        item_indent = cur_indent + (len(content) - len(content[1:].lstrip()) - 1) + 1
        item_indent = cur_indent + 2 if item_indent <= cur_indent else item_indent
        synthetic = [(item_indent, rest, lineno)]
        j = idx + 1
        while j < len(lines) and lines[j][0] >= item_indent:
            synthetic.append(lines[j])
            j += 1
        mapping, consumed = _parse_map(synthetic, 0, item_indent)
        if consumed != len(synthetic):
            raise MinYamlError("unconsumed mapping body at line %d" % lineno)
        out.append(mapping)
        idx = j
    return out, idx


def _parse_map(lines, idx, indent):
    out = {}
    while idx < len(lines):
        cur_indent, content, lineno = lines[idx]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise MinYamlError("unexpected indent at line %d" % lineno)
        if content.startswith("- ") or content == "-":
            break
        key, after = _split_key(content)
        if key is None:
            raise MinYamlError("expected 'key: value' at line %d" % lineno)
        key = _scalar(key)
        after = after.strip()
        if after != "":
            out[key] = _scalar(after)
            idx += 1
            continue
        idx += 1
        if idx < len(lines) and lines[idx][0] > cur_indent:
            child, idx = _parse_block(lines, idx, lines[idx][0])
            out[key] = child
        elif idx < len(lines) and lines[idx][0] == cur_indent and (
            lines[idx][1].startswith("- ") or lines[idx][1] == "-"
        ):
            # A sequence written at the same indent as its key — legal YAML.
            child, idx = _parse_seq(lines, idx, cur_indent)
            out[key] = child
        else:
            out[key] = None
    return out, idx


def load_path(path):
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())

"""Server-rendered SVG charts. No JavaScript, no chart library, no CDN.

The constraint is the design: every chart on the site is a string of SVG
built here from the engine's own numbers, so the page that shows a burn-down
makes zero external requests and the chart can never disagree with the
report — they are the same assessment object.

Chart grammar (one accent, everything else recedes):
  * data wears the accent (#3b5ea6); reference lines are dashed gray; grid
    is hairline; ALL text wears ink/mute, never the data color.
  * bars are thin (<=20px) with the data-end rounded and the baseline square;
    lines are 2px; the current-position marker is an 8px dot with a surface
    ring.
  * every chart is direct-labeled where it matters (end values, floor marks)
    and the page pairs it with a plain table of the same numbers — the
    no-JS answer to tooltips, and the accessibility answer as well.
"""

import datetime
import html

from . import engine as E

ACCENT = "#3b5ea6"          # validated: lightness band, chroma, 3:1 contrast
INK = "#2a2622"
MUTE = "#6a645a"
GRID = "#e4e0d8"
REF = "#8a8478"             # dashed reference lines (pace, caps)
SURFACE = "#fdfcfa"

FONT = 'font-family="inherit" font-size="11"'


def _esc(s):
    return html.escape(str(s), quote=True)


def _n(v):
    if v is None:
        return "—"
    return ("%.1f" % float(v)).rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# burn-down: awarded credits vs the pace line, over the cycle
# ---------------------------------------------------------------------------

def burndown_points(cred, led, assessment, as_of=None):
    """(date, cumulative counted total) at each entry date in the cycle.

    Each point is a full engine assessment of the feed truncated at that
    date — caps and floors bite at every step, so the line can flatten when
    a bucket fills even while raw hours keep landing. That flattening IS the
    information.
    """
    as_of = as_of or assessment.as_of
    feed = led.engine_entries()
    carry = assessment.carryover_in
    kw = dict(anchor=None, cycle_start=assessment.cycle_start,
              as_of=as_of, carryover_in=carry)
    dates = sorted(set(
        e["date"] for e in feed
        if str(assessment.cycle_start) <= e["date"] < str(assessment.cycle_end)
    ))
    points = [(assessment.cycle_start, carry)]
    for d in dates:
        sub = [e for e in feed if e["date"] <= d]
        a = E.assess(cred, sub, **kw)
        points.append((datetime.date.fromisoformat(d),
                       a.total_with_carryover))
    return points


def burndown_svg(cred, assessment, pace, points, width=680, height=240):
    a = assessment
    ml, mr, mt, mb = 44, 96, 18, 30
    pw, ph = width - ml - mr, height - mt - mb
    span = max(1, (a.cycle_end - a.cycle_start).days)
    ymax = max(a.required, (max(p[1] for p in points) if points else 0)) * 1.06
    ymax = ymax or 1.0

    def x(d):
        return ml + pw * min(1.0, max(0.0, (d - a.cycle_start).days / span))

    def y(v):
        return mt + ph - ph * (v / ymax)

    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" '
           'aria-label="%s burn-down: counted %s vs required pace">'
           % (width, height, _esc(cred.name), _esc(cred.unit_label))]

    # hairline grid + y labels
    for frac in (0.25, 0.5, 0.75, 1.0):
        v = ymax * frac
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="1"/>' % (ml, y(v), ml + pw, y(v), GRID))
        out.append('<text x="%d" y="%.1f" text-anchor="end" fill="%s" %s>'
                   '%s</text>' % (ml - 6, y(v) + 4, MUTE, FONT, _n(v)))
    # x labels: cycle ends
    for d, anchor in ((a.cycle_start, "start"), (a.cycle_end, "end")):
        out.append('<text x="%.1f" y="%d" text-anchor="%s" fill="%s" %s>%s'
                   '</text>' % (x(d), height - 8, anchor, MUTE, FONT, d))

    # pace reference: (start, carryover) -> (end, required), dashed gray
    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
               'stroke-width="2" stroke-dasharray="6 5"/>'
               % (x(a.cycle_start), y(a.carryover_in),
                  x(a.cycle_end), y(a.required), REF))
    out.append('<text x="%.1f" y="%.1f" fill="%s" %s>required %s</text>'
               % (x(a.cycle_end) + 6, y(a.required) + 4, MUTE, FONT,
                  _n(a.required)))

    # today marker
    if a.cycle_start <= a.as_of <= a.cycle_end:
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" '
                   'stroke-width="1" stroke-dasharray="2 3"/>'
                   % (x(a.as_of), mt, x(a.as_of), mt + ph, GRID))
        out.append('<text x="%.1f" y="%d" text-anchor="middle" fill="%s" %s>'
                   'today</text>' % (x(a.as_of), mt - 4, MUTE, FONT))

    # the actual line: step-after, accent, 2px
    if points:
        d_attr = []
        px = py = None
        for (d, v) in points:
            cx, cy = x(d), y(v)
            if px is None:
                d_attr.append("M %.1f %.1f" % (cx, cy))
            else:
                d_attr.append("L %.1f %.1f L %.1f %.1f" % (cx, py, cx, cy))
            px, py = cx, cy
        # carry the line to today at the last value
        if a.cycle_start <= a.as_of <= a.cycle_end and px is not None:
            tx = x(a.as_of)
            if tx > px:
                d_attr.append("L %.1f %.1f" % (tx, py))
                px = tx
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2" '
                   'stroke-linejoin="round"/>' % (" ".join(d_attr), ACCENT))
        # end marker: 8px dot with a surface ring, direct-labeled
        out.append('<circle cx="%.1f" cy="%.1f" r="6" fill="%s" stroke="%s" '
                   'stroke-width="2"/>' % (px, py, ACCENT, SURFACE))
        out.append('<text x="%.1f" y="%.1f" fill="%s" font-weight="600" %s>'
                   '%s counted</text>'
                   % (px + 10, py + 4, INK, FONT, _n(points[-1][1])))
    if a.forfeited:
        out.append('<text x="%d" y="%.1f" text-anchor="end" fill="%s" %s>'
                   'a naive sum would claim %s</text>'
                   % (ml + pw, mt + 12, MUTE, FONT, _n(a.naive_total)))
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# floor fill: per-group awarded vs floor (and cap), horizontal bars
# ---------------------------------------------------------------------------

def floor_fill_svg(assessment, pace, width=680):
    groups = [g for g in assessment.groups if g.get("floor") or g.get("cap")]
    if not groups:
        return ""
    bar_h, gap, ml, mr, top = 18, 26, 190, 84, 12
    height = top + len(groups) * (bar_h + gap)
    scale_max = max(
        [g["awarded"] for g in groups] +
        [g["floor"] or 0 for g in groups] +
        [g["cap"] or 0 for g in groups]) * 1.08 or 1.0
    pw = width - ml - mr
    frac = pace["elapsed_fraction"]

    def x(v):
        return ml + pw * (v / scale_max)

    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" '
           'aria-label="category floors: awarded against required minimums">'
           % (width, height)]
    yy = top
    for g in groups:
        cy = yy + bar_h / 2.0
        # label, in ink — never in the data color
        out.append('<text x="%d" y="%.1f" text-anchor="end" fill="%s" %s>'
                   '%s</text>' % (ml - 10, cy + 4, INK, FONT, _esc(g["name"])))
        # track
        out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" '
                   'rx="4"/>' % (ml, yy, pw, bar_h, GRID))
        # awarded bar: square at baseline, rounded at the data end
        w = max(0.0, x(g["awarded"]) - ml)
        if w > 0:
            r = min(4, w / 2.0)
            out.append(
                '<path d="M %d %d H %.1f Q %.1f %d %.1f %.1f V %.1f '
                'Q %.1f %d %.1f %d H %d Z" fill="%s"/>'
                % (ml, yy, ml + w - r, ml + w, yy, ml + w, yy + r,
                   yy + bar_h - r, ml + w, yy + bar_h, ml + w - r,
                   yy + bar_h, ml, ACCENT))
        # floor tick (dark) and its pace position (gray)
        if g.get("floor"):
            fx = x(g["floor"])
            out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" '
                       'stroke="%s" stroke-width="2"/>'
                       % (fx, yy - 3, fx, yy + bar_h + 3, INK))
            px_ = x(g["floor"] * frac)
            out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" '
                       'stroke="%s" stroke-width="2" stroke-dasharray="2 3"/>'
                       % (px_, yy, px_, yy + bar_h, REF))
        # cap marker
        if g.get("cap"):
            cx = x(g["cap"])
            out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" '
                       'stroke="%s" stroke-width="1" stroke-dasharray="4 3"/>'
                       % (cx, yy - 3, cx, yy + bar_h + 3, REF))
        # direct label: awarded of floor/cap
        target = g.get("floor") or g.get("cap")
        word = "floor" if g.get("floor") else "cap"
        out.append('<text x="%d" y="%.1f" fill="%s" %s>%s of %s %s</text>'
                   % (ml + pw + 8, cy + 4, MUTE, FONT,
                      _n(g["awarded"]), _n(target), word))
        yy += bar_h + gap
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------------------
# capture volume: real captures per month, columns
# ---------------------------------------------------------------------------

def volume_svg(series, width=680, height=180):
    """`series`: [(\"YYYY-MM\", count)] oldest first, from the ledger."""
    if not series:
        return ""
    ml, mr, mt, mb = 34, 12, 16, 26
    pw, ph = width - ml - mr, height - mt - mb
    n = len(series)
    ymax = max(c for _, c in series) * 1.15 or 1.0
    slot = pw / float(n)
    bw = min(20.0, slot * 0.55)

    out = ['<svg viewBox="0 0 %d %d" width="100%%" role="img" '
           'aria-label="capture volume per month">' % (width, height)]
    for frac in (0.5, 1.0):
        v = ymax * frac
        yv = mt + ph - ph * (v / ymax)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" '
                   'stroke-width="1"/>' % (ml, yv, ml + pw, yv, GRID))
        out.append('<text x="%d" y="%.1f" text-anchor="end" fill="%s" %s>'
                   '%d</text>' % (ml - 5, yv + 4, MUTE, FONT, round(v)))
    for i, (month, count) in enumerate(series):
        cx = ml + slot * (i + 0.5)
        h = ph * (count / ymax)
        yy = mt + ph - h
        if count:
            r = min(4.0, bw / 2.0, h)
            out.append(
                '<path d="M %.1f %.1f V %.1f Q %.1f %.1f %.1f %.1f H %.1f '
                'Q %.1f %.1f %.1f %.1f V %.1f Z" fill="%s"/>'
                % (cx - bw / 2, mt + ph, yy + r, cx - bw / 2, yy,
                   cx - bw / 2 + r, yy, cx + bw / 2 - r, cx + bw / 2, yy,
                   cx + bw / 2, yy + r, mt + ph, ACCENT))
            out.append('<text x="%.1f" y="%.1f" text-anchor="middle" '
                       'fill="%s" %s>%d</text>'
                       % (cx, yy - 5, MUTE, FONT, count))
        if n <= 12 or i % 2 == (n - 1) % 2:
            out.append('<text x="%.1f" y="%d" text-anchor="middle" '
                       'fill="%s" %s>%s</text>'
                       % (cx, height - 8, MUTE, FONT, month))
    out.append("</svg>")
    return "".join(out)

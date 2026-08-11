#!/usr/bin/env python3
"""
ccreport - dependency-free SVG chart rendering for ccaudit's HTML report.

Everything here emits plain SVG strings. No JavaScript, no CDN, no build step:
the resulting HTML file is one self-contained artifact you can email to a
maintainer or attach to a bug report.
"""

import html
import math

PALETTE = {
    "input_tokens": "#7c9cbf",
    "cache_creation_input_tokens": "#e0a458",
    "cache_read_input_tokens": "#8d7ba5",
    "output_tokens": "#5b9279",
}
SERIES_LABEL = {
    "input_tokens": "input",
    "cache_creation_input_tokens": "cache write",
    "cache_read_input_tokens": "cache read",
    "output_tokens": "output",
}
ACCENT = "#c4643a"
GRID = "#dfd9d0"
INK = "#2f2b28"
MUTED = "#7d756c"


def esc(s):
    return html.escape(str(s), quote=True)


def human(n):
    n = float(n)
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return f"{n:.0f}"


def _nice_ceiling(v):
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def _axis(w, h, pad, top):
    """Horizontal gridlines + y labels, returns (svg, plot_h, y_of)."""
    return None


def stacked_bars(rows, series, width=880, height=300, title="", ylabel=""):
    """rows: [(label, {series_key: value})]. Stacked vertical bars."""
    if not rows:
        return ""
    padl, padr, padt, padb = 62, 14, 26, 54
    pw, ph = width - padl - padr, height - padt - padb
    totals = [sum(d.get(k, 0) for k in series) for _, d in rows]
    ymax = _nice_ceiling(max(totals) or 1)
    n = len(rows)
    slot = pw / n
    bw = min(slot * 0.68, 58)

    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
           f'role="img" aria-label="{esc(title)}">']
    for i in range(5):
        y = padt + ph - ph * i / 4
        val = ymax * i / 4
        out.append(f'<line x1="{padl}" y1="{y:.1f}" x2="{width - padr}" y2="{y:.1f}" '
                   f'stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{padl - 8}" y="{y + 4:.1f}" text-anchor="end" '
                   f'class="tick">{human(val)}</text>')

    for i, (label, d) in enumerate(rows):
        x = padl + slot * i + (slot - bw) / 2
        acc = 0.0
        for k in series:
            v = d.get(k, 0)
            if v <= 0:
                continue
            bh = ph * v / ymax
            y = padt + ph - (acc + v) / ymax * ph
            acc += v
            out.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                f'fill="{PALETTE.get(k, ACCENT)}"><title>{esc(label)} — '
                f'{esc(SERIES_LABEL.get(k, k))}: {human(v)}</title></rect>')
        step = max(1, n // 12)
        if i % step == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{padt + ph + 18}" '
                       f'text-anchor="middle" class="tick">{esc(label)}</text>')

    out.append(f'<line x1="{padl}" y1="{padt + ph}" x2="{width - padr}" '
               f'y2="{padt + ph}" stroke="{INK}" stroke-width="1.2"/>')
    lx = padl
    for k in series:
        out.append(f'<rect x="{lx}" y="{height - 22}" width="10" height="10" '
                   f'fill="{PALETTE.get(k, ACCENT)}"/>')
        out.append(f'<text x="{lx + 15}" y="{height - 13}" class="tick">'
                   f'{esc(SERIES_LABEL.get(k, k))}</text>')
        lx += 24 + 7 * len(SERIES_LABEL.get(k, k))
    if ylabel:
        out.append(f'<text x="10" y="{padt + ph / 2}" class="tick" '
                   f'transform="rotate(-90 10 {padt + ph / 2})" '
                   f'text-anchor="middle">{esc(ylabel)}</text>')
    out.append("</svg>")
    return "".join(out)


def hbars(rows, width=880, bar_h=26, title="", unit=""):
    """rows: [(label, value, sublabel)] sorted desc."""
    if not rows:
        return "<p class='empty'>nothing to show</p>"
    padl, padr, padt = 250, 90, 8
    height = padt + bar_h * len(rows) + 8
    vmax = max(v for _, v, _ in rows) or 1
    pw = width - padl - padr
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
           f'role="img" aria-label="{esc(title)}">']
    for i, (label, v, sub) in enumerate(rows):
        y = padt + i * bar_h
        bw = max(1.0, pw * v / vmax)
        short = label if len(label) <= 36 else "…" + label[-35:]
        out.append(f'<text x="{padl - 10}" y="{y + bar_h * 0.65:.1f}" '
                   f'text-anchor="end" class="lbl">{esc(short)}</text>')
        out.append(f'<rect x="{padl}" y="{y + 4}" width="{bw:.1f}" '
                   f'height="{bar_h - 9}" fill="{ACCENT}" opacity="0.82" rx="2">'
                   f'<title>{esc(label)}: {human(v)}{esc(unit)}'
                   + (f" ({esc(sub)})" if sub else "") + '</title></rect>')
        out.append(f'<text x="{padl + bw + 8:.1f}" y="{y + bar_h * 0.65:.1f}" '
                   f'class="tick">{human(v)}{esc(unit)}</text>')
    out.append("</svg>")
    return "".join(out)


def heatmap(grid, days, width=880, cell=None, title=""):
    """grid: {(day_label, hour): value}. days: ordered day labels."""
    if not days:
        return ""
    padl, padt, padb = 92, 20, 26
    cell = cell or min(30, (width - padl - 14) / 24)
    height = padt + cell * len(days) + padb
    vmax = max(grid.values()) if grid else 1
    vmax = vmax or 1
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
           f'role="img" aria-label="{esc(title)}">']
    for h in range(0, 24, 2):
        out.append(f'<text x="{padl + cell * h + cell / 2:.1f}" y="{padt - 6}" '
                   f'text-anchor="middle" class="tick">{h:02d}</text>')
    for r, day in enumerate(days):
        y = padt + r * cell
        out.append(f'<text x="{padl - 10}" y="{y + cell * 0.68:.1f}" '
                   f'text-anchor="end" class="tick">{esc(day)}</text>')
        for h in range(24):
            v = grid.get((day, h), 0)
            t = (v / vmax) ** 0.55 if v > 0 else 0
            fill = "#f2eee8" if v <= 0 else _ramp(t)
            out.append(
                f'<rect x="{padl + cell * h:.1f}" y="{y:.1f}" '
                f'width="{cell - 1.5:.1f}" height="{cell - 1.5:.1f}" fill="{fill}" '
                f'rx="2"><title>{esc(day)} {h:02d}:00 — {human(v)}</title></rect>')
    out.append(f'<text x="{padl}" y="{height - 8}" class="tick">'
               f'darker = more consumption · local time</text>')
    out.append("</svg>")
    return "".join(out)


def _ramp(t):
    """Pale sand to deep rust."""
    c0 = (247, 240, 230)
    c1 = (150, 52, 24)
    return "#" + "".join(f"{int(c0[i] + (c1[i] - c0[i]) * t):02x}" for i in range(3))


def line_chart(points, width=880, height=260, title="", ylabel="", threshold=None):
    """points: [(x_label, y_value)] in order."""
    if len(points) < 2:
        return ""
    padl, padr, padt, padb = 62, 14, 20, 44
    pw, ph = width - padl - padr, height - padt - padb
    ymax = _nice_ceiling(max(v for _, v in points) or 1)
    n = len(points)
    xs = [padl + pw * i / (n - 1) for i in range(n)]
    ys = [padt + ph - ph * v / ymax for _, v in points]

    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" '
           f'role="img" aria-label="{esc(title)}">']
    for i in range(5):
        y = padt + ph - ph * i / 4
        out.append(f'<line x1="{padl}" y1="{y:.1f}" x2="{width - padr}" '
                   f'y2="{y:.1f}" stroke="{GRID}"/>')
        out.append(f'<text x="{padl - 8}" y="{y + 4:.1f}" text-anchor="end" '
                   f'class="tick">{human(ymax * i / 4)}</text>')
    area = (f'M {xs[0]:.1f} {padt + ph:.1f} '
            + " ".join(f"L {x:.1f} {y:.1f}" for x, y in zip(xs, ys))
            + f' L {xs[-1]:.1f} {padt + ph:.1f} Z')
    out.append(f'<path d="{area}" fill="{ACCENT}" opacity="0.16"/>')
    out.append('<path d="' + " ".join(
        ("M" if i == 0 else "L") + f" {x:.1f} {y:.1f}"
        for i, (x, y) in enumerate(zip(xs, ys))) +
        f'" fill="none" stroke="{ACCENT}" stroke-width="1.8"/>')
    if threshold:
        ty = padt + ph - ph * min(threshold, ymax) / ymax
        out.append(f'<line x1="{padl}" y1="{ty:.1f}" x2="{width - padr}" '
                   f'y2="{ty:.1f}" stroke="{INK}" stroke-dasharray="5 4"/>')
    step = max(1, n // 10)
    for i in range(0, n, step):
        out.append(f'<text x="{xs[i]:.1f}" y="{padt + ph + 18}" '
                   f'text-anchor="middle" class="tick">{esc(points[i][0])}</text>')
    out.append("</svg>")
    return "".join(out)


def donut(rows, size=230, title=""):
    """rows: [(label, value, color)]."""
    total = sum(v for _, v, _ in rows) or 1
    r, cx, cy, sw = size * 0.36, size / 2, size / 2, size * 0.17
    out = [f'<svg viewBox="0 0 {size} {size}" class="donut" '
           f'role="img" aria-label="{esc(title)}">']
    ang = -math.pi / 2
    for label, v, color in rows:
        sweep = 2 * math.pi * v / total
        if sweep <= 0:
            continue
        x0, y0 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        ang += sweep
        x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        large = 1 if sweep > math.pi else 0
        out.append(
            f'<path d="M {x0:.2f} {y0:.2f} A {r:.2f} {r:.2f} 0 {large} 1 '
            f'{x1:.2f} {y1:.2f}" fill="none" stroke="{color}" '
            f'stroke-width="{sw:.1f}"><title>{esc(label)}: '
            f'{100 * v / total:.1f}%</title></path>')
    out.append("</svg>")
    return "".join(out)


CSS = """
:root{--ink:#2f2b28;--muted:#7d756c;--line:#dfd9d0;--bg:#fbf9f6;--card:#fff;
--accent:#c4643a;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif;}
.wrap{max-width:1000px;margin:0 auto;padding:44px 24px 90px}
h1{font-size:27px;margin:0 0 4px;letter-spacing:-.02em;font-weight:650}
h2{font-size:17px;margin:44px 0 6px;letter-spacing:-.01em;font-weight:620}
h2::before{content:"";display:block;height:1px;background:var(--line);
margin-bottom:14px}
p.sub{color:var(--muted);margin:0 0 6px;font-size:13.5px}
.meta{color:var(--muted);font-size:13px;margin-bottom:26px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
gap:12px;margin:20px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
padding:13px 15px}
.card .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;
color:var(--muted)}
.card .v{font-size:22px;font-weight:620;margin-top:3px;
font-variant-numeric:tabular-nums}
.card .n{font-size:12px;color:var(--muted);margin-top:2px}
.chart{width:100%;height:auto;background:var(--card);border:1px solid var(--line);
border-radius:9px;padding:10px;margin-top:10px}
.donut{width:190px;height:190px}
text.tick{font:11px ui-sans-serif,system-ui;fill:#7d756c}
text.lbl{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;fill:#2f2b28}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13.5px;
background:var(--card);border:1px solid var(--line);border-radius:9px;
overflow:hidden}
th{text-align:left;font-weight:600;font-size:11.5px;text-transform:uppercase;
letter-spacing:.06em;color:var(--muted);padding:9px 12px;
border-bottom:1px solid var(--line)}
td{padding:8px 12px;border-bottom:1px solid #f0ece6;
font-variant-numeric:tabular-nums}
td.mono{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
tr:last-child td{border-bottom:none}
.flag{background:#fff6ef;border:1px solid #f0d3bf;border-left:3px solid var(--accent);
border-radius:7px;padding:11px 14px;margin:9px 0;font-size:14px}
.flag b{font-weight:620}
.ok{background:#f4f7f4;border-color:#dbe5db;border-left-color:#5b9279}
.split{display:flex;gap:26px;align-items:center;flex-wrap:wrap}
.legend{font-size:13px}
.legend div{margin:3px 0}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;
margin-right:7px;vertical-align:middle}
footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--line);
color:var(--muted);font-size:12.5px}
code{background:#f2eee8;padding:1px 5px;border-radius:4px;font-size:12.5px}
"""


def page(title, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>{CSS}</style></head>'
            f'<body><div class="wrap">{body}</div></body></html>')

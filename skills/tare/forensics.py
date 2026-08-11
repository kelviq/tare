#!/usr/bin/env python3
"""
forensics - answer "why did I hit the limit" from a ccaudit CSV.

ccaudit tells you how many tokens went where. This tells you what *shape* the
usage had, which is usually where the answer is: a burst, a swarm of parallel
sessions, a cache rebuild, or just a heavy day.

    python3 forensics.py usage.csv                  # full pass
    python3 forensics.py usage.csv --at 2026-08-10T19:46
    python3 forensics.py usage.csv --day 2026-08-10

Reads only the CSV written by `ccaudit.py --csv`. Stdlib only, Python 3.9+.
"""

import argparse
import csv
import datetime as dt
import statistics
import sys
from collections import Counter, defaultdict

TOKEN_COLS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")


def human(n):
    n = float(n)
    for u, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= d:
            return f"{n / d:.2f}{u}"
    return f"{n:.0f}"


def load(path, tz):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not r.get("ts"):
                continue
            try:
                t = dt.datetime.fromisoformat(r["ts"])
            except ValueError:
                continue
            r["t"] = t + dt.timedelta(hours=tz)
            for c in TOKEN_COLS:
                r[c] = int(r.get(c) or 0)
            try:
                r["w"] = float(r.get("weight") or 0)
            except ValueError:
                r["w"] = 0.0
            r["total"] = sum(r[c] for c in TOKEN_COLS)
            rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def rolling(rows, hours=5):
    """Returns [(t, window_weight)] and (peak, peak_time)."""
    out, acc, j, best, bt = [], 0.0, 0, 0.0, None
    win = dt.timedelta(hours=hours)
    for i, r in enumerate(rows):
        acc += r["w"]
        while rows[j]["t"] < r["t"] - win:
            acc -= rows[j]["w"]
            j += 1
        out.append((r["t"], acc))
        if acc > best:
            best, bt = acc, r["t"]
    return out, best, bt


def session_shape(rows):
    s = defaultdict(lambda: {"n": 0, "w": 0.0, "t0": None, "t1": None})
    for r in rows:
        b = s[r.get("session", "?")]
        b["n"] += 1
        b["w"] += r["w"]
        b["t0"] = r["t"] if b["t0"] is None else min(b["t0"], r["t"])
        b["t1"] = r["t"] if b["t1"] is None else max(b["t1"], r["t"])
    return s


def peak_concurrency(sessions):
    ev = []
    for b in sessions.values():
        ev.append((b["t0"], 1))
        ev.append((b["t1"], -1))
    ev.sort()
    cur = mx = 0
    mxt = None
    for t, d in ev:
        cur += d
        if cur > mx:
            mx, mxt = cur, t
    return mx, mxt


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


def report_days(rows):
    section("Daily totals")
    days = defaultdict(lambda: {"n": 0, "w": 0.0, "tot": 0, "cw": 0, "cr": 0})
    for r in rows:
        d = days[r["t"].strftime("%Y-%m-%d %a")]
        d["n"] += 1
        d["w"] += r["w"]
        d["tot"] += r["total"]
        d["cw"] += r["cache_creation_input_tokens"]
        d["cr"] += r["cache_read_input_tokens"]
    keys = sorted(days)
    print(f"{'day':<16} {'reqs':>7} {'tokens':>9} {'cache_w':>9} {'weight':>8}  vs median")
    med = statistics.median([days[k]["w"] for k in keys]) or 1
    for k in keys:
        d = days[k]
        ratio = d["w"] / med
        bar = "#" * min(40, int(ratio * 6))
        print(f"{k:<16} {d['n']:>7,} {human(d['tot']):>9} {human(d['cw']):>9} "
              f"{d['w']:>8.1f}  {ratio:>4.1f}x {bar}")
    return days, med


def report_discontinuity(days, med):
    keys = sorted(days)
    if len(keys) < 5:
        # With this few days the median is set by the very days under test, so
        # both "spike" and "evenly distributed" would be unfounded. On a 2-day
        # CSV with a 30x spread the old code reported even usage.
        hi, lo = max(d["w"] for d in days.values()), min(d["w"] for d in days.values())
        print(f"\nOnly {len(keys)} day(s) in this CSV — too few to establish a "
              "baseline, so no")
        print("spike detection was done. Day weights range "
              f"{lo:.1f}-{hi:.1f}. Re-export with a")
        print("wider --days window to see whether any day stands out.")
        return
    spikes = [k for k in keys if days[k]["w"] > med * 2.5]
    if not spikes:
        print("\nNo day exceeds 2.5x the median. Usage looks evenly distributed;")
        print("the cause is more likely per-request cost than a one-off event.")
        return
    print(f"\n{len(spikes)} day(s) above 2.5x median: {', '.join(spikes)}")
    first = spikes[0]
    i = keys.index(first)
    if i > 0:
        before = statistics.mean([days[k]["w"] for k in keys[:i]])
        after = statistics.mean([days[k]["w"] for k in keys[i:]])
        print(f"Step change at {first}: {before:.1f} -> {after:.1f} avg weight/day "
              f"({after / max(before, 0.01):.1f}x).")
        print("Something changed on that date. That is the question to answer.")


def report_sessions(rows, label=""):
    section(f"Session shape{label}")
    s = session_shape(rows)
    ns = [b["n"] for b in s.values()]
    durs = [(b["t1"] - b["t0"]).total_seconds() / 60 for b in s.values()]
    conc, ct = peak_concurrency(s)
    print(f"sessions            : {len(s):,}")
    print(f"median requests/sess: {statistics.median(ns):.0f}")
    print(f"median duration     : {statistics.median(durs):.1f} min")
    print(f"peak concurrent     : {conc}" + (f" at {ct:%Y-%m-%d %H:%M}" if ct else ""))

    span_h = (rows[-1]["t"] - rows[0]["t"]).total_seconds() / 3600 or 1
    per_day = len(s) / max(span_h / 24, 0.04)
    automated = (per_day > 50 and statistics.median(durs) < 3 and conc >= 5)
    if automated:
        print("\n  >> AUTOMATION SIGNATURE <<")
        print("  Many short sessions running in parallel is not interactive use.")
        print("  Something is invoking Claude Code programmatically: a script")
        print("  calling `claude -p`, the Agent SDK, a CI job, or a batch harness.")
        print("  Each fresh session pays full cache-creation cost on its first")
        print("  turn, so a swarm is far more expensive than one long session.")
    elif conc >= 3:
        print(f"\n  Note: up to {conc} sessions overlapped. Check whether that was")
        print("  you in multiple terminals or something spawning them.")

    top = sorted(s.items(), key=lambda kv: -kv[1]["w"])[:8]
    print(f"\n  {'session':<12} {'reqs':>6} {'weight':>7}  window")
    for k, b in top:
        d = (b["t1"] - b["t0"]).total_seconds() / 60
        print(f"  {k[:10]:<12} {b['n']:>6} {b['w']:>7.1f}  "
              f"{b['t0']:%m-%d %H:%M}-{b['t1']:%H:%M} ({d:.0f}m)")
    return automated


def report_concentration(rows):
    section("Concentration")
    for field in ("model", "project", "sidechain"):
        c = Counter(r.get(field, "?") for r in rows)
        if len(c) <= 1 and field == "sidechain":
            continue
        top = c.most_common(5)
        line = ", ".join(f"{k[-34:]} ({100 * v / len(rows):.0f}%)" for k, v in top)
        print(f"{field:<10}: {line}")
    cw = sum(r["cache_creation_input_tokens"] for r in rows)
    cr = sum(r["cache_read_input_tokens"] for r in rows)
    if cr and cw / cr > 0.25:
        print(f"\n  Cache writes are {100 * cw / cr:.0f}% of cache reads "
              "(normal is well under 25%).")
        print("  Contexts are being built rather than reused. Causes: many short")
        print("  sessions, or resuming an old session after the cache expired.")
        print("  Writes cost ~12x what reads cost per token.")


def report_window(rows, at, hours=5):
    series, peak, pt = rolling(rows, hours)
    section(f"Rolling {hours}-hour load")
    print(f"peak {peak:.1f} at {pt:%Y-%m-%d %H:%M}")
    if at:
        # sum weights actually inside the window ending at `at` — not the value
        # at the last prior request, which can be hours stale across a gap
        lo = at - dt.timedelta(hours=hours)
        inwin = [r for r in rows if lo < r["t"] <= at]
        if not inwin:
            print(f"Nothing in the {hours}h window ending {at:%Y-%m-%d %H:%M}.")
            return
        v = sum(r["w"] for r in inwin)
        pct = 100 * v / peak if peak else 0
        print(f"at {at:%Y-%m-%d %H:%M}: {v:.1f} from {len(inwin):,} requests "
              f"({pct:.0f}% of your observed peak)")
        oldest = min(r["t"] for r in inwin)
        print(f"  oldest request still counting: {oldest:%m-%d %H:%M} "
              f"(ages out {oldest + dt.timedelta(hours=hours):%H:%M})")
        if pct > 60:
            print("\n  The window was already substantially loaded when you")
            print("  resumed. Earlier work had not aged out yet, so a short")
            print("  session was enough to reach the cap. This is expected")
            print("  rolling-window behaviour, not a metering fault.")
        else:
            print("\n  The window was fairly empty at that point. If you hit a")
            print("  limit shortly after, that is harder to explain from local")
            print("  data alone and is worth recording carefully.")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("csv", help="output of `ccaudit.py --csv`")
    p.add_argument("--tz", type=float, default=None, help="local UTC offset in hours")
    p.add_argument("--day", help="restrict deep analysis to YYYY-MM-DD")
    p.add_argument("--at", help="report window load at YYYY-MM-DDTHH:MM local")
    p.add_argument("--window", type=float, default=5.0, help="window hours (default 5)")
    a = p.parse_args()

    if a.tz is None:
        off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta()
        a.tz = off.total_seconds() / 3600

    rows = load(a.csv, a.tz)
    if not rows:
        print(f"No usable rows in {a.csv}.", file=sys.stderr)
        return 1

    print("=" * 66)
    print(f"forensics — {len(rows):,} requests, "
          f"{rows[0]['t']:%Y-%m-%d %H:%M} to {rows[-1]['t']:%Y-%m-%d %H:%M} "
          f"(UTC{a.tz:+g})")
    print("=" * 66)

    days, med = report_days(rows)
    report_discontinuity(days, med)

    at = None
    if a.at:
        try:
            at = dt.datetime.fromisoformat(a.at).replace(tzinfo=rows[0]["t"].tzinfo)
        except ValueError:
            print(f"Could not parse --at {a.at}", file=sys.stderr)
    report_window(rows, at, a.window)

    focus = rows
    label = ""
    if a.day:
        focus = [r for r in rows if r["t"].strftime("%Y-%m-%d") == a.day]
        label = f" — {a.day}"
        if not focus:
            print(f"\nNo requests on {a.day}.")
            return 0

    report_sessions(focus, label)
    report_concentration(focus)

    section("Hour by hour" + label)
    h = defaultdict(lambda: {"n": 0, "w": 0.0, "cw": 0})
    for r in focus:
        k = r["t"].strftime("%m-%d %Hh")
        h[k]["n"] += 1
        h[k]["w"] += r["w"]
        h[k]["cw"] += r["cache_creation_input_tokens"]
    for k in sorted(h):
        b = h[k]
        print(f"  {k}  n={b['n']:>5}  w={b['w']:>6.1f}  cache_w={human(b['cw']):>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

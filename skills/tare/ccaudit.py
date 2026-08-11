#!/usr/bin/env python3
"""
ccaudit - find out where your Claude Code quota actually went.

Reads the session transcripts Claude Code already writes to
~/.claude/projects/<slug>/<session-id>.jsonl and answers three questions:

  1. How many tokens, on which model, at what time?
  2. Which *tools* put those tokens into your context - file reads, bash
     output, web fetches, MCP servers, skills, subagents?
  3. Does anything look anomalous, and what explains it?

No dependencies, no network, nothing leaves your machine. Python 3.9+.

  python3 ccaudit.py                        # last 7 days, text summary
  python3 ccaudit.py --days 30 --html report.html
  python3 ccaudit.py --doctor
  python3 ccaudit.py --by tool --top 25
  python3 ccaudit.py --share share.md       # redacted, safe to post

MIT licensed.
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import ccreport
except ImportError:
    ccreport = None

__version__ = "0.2.0"

# --- Weighting -------------------------------------------------------------
# USD per million tokens: a *relative weight proxy* so you can see which model
# is eating the quota. Subscription limits meter compute, not these numbers.
# Substring match, longest key first. Edit freely.
MODEL_RATES = {
    "fable":  {"in": 15.00, "out": 75.00},
    "mythos": {"in": 15.00, "out": 75.00},
    "opus":   {"in": 15.00, "out": 75.00},
    "sonnet": {"in": 3.00,  "out": 15.00},
    "haiku":  {"in": 0.80,  "out": 4.00},
}
# Families with no published pricing. Their entries above are placeholders
# copied from Opus, so any weight or share attributed to them is an artifact
# of that guess, not a measurement. Everything derived from weight marks them
# with an asterisk and says so. Remove a family from here once you replace its
# rate with a real one.
ESTIMATED_FAMILIES = {"fable", "mythos"}
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10
CHARS_PER_TOKEN = 4.0     # rough, for sizing tool results

USAGE_KEYS = ("input_tokens", "output_tokens",
              "cache_creation_input_tokens", "cache_read_input_tokens")
SHORT = {"input_tokens": "in", "output_tokens": "out",
         "cache_creation_input_tokens": "cache_w",
         "cache_read_input_tokens": "cache_r"}


# --- Small helpers ---------------------------------------------------------

def parse_ts(raw):
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return dt.datetime.fromtimestamp(
            raw / 1000.0 if raw > 1e11 else raw, dt.timezone.utc)
    try:
        p = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return p if p.tzinfo else p.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def human(n):
    n = float(n)
    for u, d in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= d:
            return f"{n / d:.2f}{u}"
    return f"{n:.0f}"


def content_blocks(entry):
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return []
    c = msg.get("content")
    return [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []


def normalize_tool(name, tool_input):
    """Collapse a raw tool name into a readable, groupable label + detail."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    if not name:
        return "unknown", None
    m = re.match(r"^mcp__(.+?)__(.+)$", name)
    if m:
        return f"MCP {m.group(1)}/{m.group(2)}", None
    if name in ("Task", "Agent"):
        return f"Agent:{ti.get('subagent_type') or 'unnamed'}", None
    if name == "Skill":
        return f"Skill:{ti.get('skill_name') or ti.get('command') or 'unnamed'}", None
    if name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        return name, ti.get("file_path") or ti.get("notebook_path")
    if name == "Bash":
        cmd = (ti.get("command") or "").strip().split()
        return "Bash", (cmd[0] if cmd else None)
    if name == "WebFetch":
        url = ti.get("url") or ""
        return "WebFetch", (re.sub(r"^https?://([^/]+).*$", r"\1", url) or None)
    if name == "WebSearch":
        return "WebSearch", None
    return name, None


def est_tokens(obj):
    if obj is None:
        return 0
    if isinstance(obj, str):
        return int(len(obj) / CHARS_PER_TOKEN)
    try:
        return int(len(json.dumps(obj, default=str)) / CHARS_PER_TOKEN)
    except (TypeError, ValueError):
        return int(len(str(obj)) / CHARS_PER_TOKEN)


# --- Parsing ---------------------------------------------------------------

def parse_session(fp, project, since, stats):
    """Walk one transcript in order. Returns (requests, tool_events)."""
    requests, tool_events = [], []
    tool_names = {}       # tool_use_id -> (label, detail)
    seen = {}             # request key -> index into requests

    try:
        fh = open(fp, "r", encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    with fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                stats["lines_bad_json"] += 1
                continue
            if not isinstance(e, dict):
                stats["lines_not_object"] += 1
                continue

            ts = parse_ts(e.get("timestamp"))
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            blocks = content_blocks(e)

            # register tool_use blocks so results can be named later
            for b in blocks:
                if b.get("type") == "tool_use":
                    tool_names[b.get("id")] = normalize_tool(
                        b.get("name"), b.get("input"))

            # tool_result blocks: this is context being injected
            for b in blocks:
                if b.get("type") != "tool_result":
                    continue
                label, detail = tool_names.get(
                    b.get("tool_use_id"), ("unknown tool", None))
                tokens = est_tokens(b.get("content"))
                if not tokens and e.get("toolUseResult") is not None:
                    tokens = est_tokens(e.get("toolUseResult"))
                tool_events.append({
                    "session": e.get("sessionId") or fp.stem,
                    "project": project, "ts": ts, "tool": label,
                    "detail": detail, "tokens": tokens,
                    "is_error": bool(b.get("is_error")),
                    "req_index": len(requests),
                })

            # API responses carry the usage block
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            if not usage:
                continue
            counts = {k: int(usage.get(k) or 0) for k in USAGE_KEYS}
            if sum(counts.values()) == 0:
                stats["lines_zero_usage"] += 1
                continue
            if since and ts and ts < since:
                stats["records_old"] += 1
                continue

            key = e.get("requestId") or msg.get("id") or f"{fp.stem}:{lineno}"
            if key in seen:
                # same API response, another content block: don't double count
                prev = requests[seen[key]]
                prev["_dupes"] += 1
                if counts["output_tokens"] > prev["output_tokens"]:
                    prev.update(counts)
                stats["dupes"] += 1
                continue

            seen[key] = len(requests)
            requests.append({
                "file": str(fp), "project": project,
                "session": e.get("sessionId") or fp.stem, "ts": ts,
                "model": str(msg.get("model") or "unknown"),
                "request_id": key, "sidechain": bool(e.get("isSidechain")),
                "version": e.get("version") or "",
                "stop_reason": msg.get("stop_reason"),
                "cost_usd": e.get("costUSD"), "_dupes": 1, **counts,
            })
            stats["records"] += 1

    # content injected at request i is re-sent on every later request
    total = len(requests)
    for t in tool_events:
        t["amplified"] = t["tokens"] * max(0, total - t["req_index"])
    return requests, tool_events


def load_all(root, since, verbose=False):
    stats = Counter()
    requests, tools = [], []
    for fp in sorted(root.rglob("*.jsonl")):
        try:
            mtime = dt.datetime.fromtimestamp(fp.stat().st_mtime, dt.timezone.utc)
        except OSError:
            continue
        if since and mtime < since:
            stats["files_skipped_old"] += 1
            continue
        stats["files_read"] += 1
        try:
            rel = fp.relative_to(root)
            project = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        except ValueError:
            project = fp.parent.name
        r, t = parse_session(fp, project, since, stats)
        requests.extend(r)
        tools.extend(t)
    requests.sort(key=lambda x: x["ts"] or dt.datetime.min.replace(
        tzinfo=dt.timezone.utc))
    if verbose:
        print(f"[scan] {dict(stats)}", file=sys.stderr)
    return requests, tools, stats


# --- Metrics ---------------------------------------------------------------

def rate_for(model):
    m = model.lower()
    for k in sorted(MODEL_RATES, key=len, reverse=True):
        if k in m:
            return MODEL_RATES[k]
    return None


def family(model):
    m = model.lower()
    return next((k for k in MODEL_RATES if k in m), "other")


def rate_is_estimated(model):
    """True when this model's rate is a placeholder rather than a real price."""
    return family(model) in ESTIMATED_FAMILIES


def model_label(model):
    """Model name, asterisked when its weight rests on a guessed rate."""
    return f"{model} *" if rate_is_estimated(model) else model


def estimated_note(requests):
    """Footnote naming the guessed-rate models present, or None if there are none."""
    est = sorted({r["model"] for r in requests if rate_is_estimated(r["model"])})
    if not est:
        return None
    return (f"* {', '.join(est)} — no published rate; weight uses an "
            "Opus-equivalent placeholder. Compare their requests and tokens, "
            "not their share of weight.")


def weight(r):
    if isinstance(r.get("cost_usd"), (int, float)):
        return float(r["cost_usd"])
    rt = rate_for(r["model"])
    if not rt:
        return 0.0
    return (r["input_tokens"] * rt["in"]
            + r["cache_creation_input_tokens"] * rt["in"] * CACHE_WRITE_MULT
            + r["cache_read_input_tokens"] * rt["in"] * CACHE_READ_MULT
            + r["output_tokens"] * rt["out"]) / 1_000_000


def blank():
    return {"requests": 0, "weight": 0.0, **{k: 0 for k in USAGE_KEYS}}


def accumulate(records, keyfn):
    b = defaultdict(blank)
    for r in records:
        d = b[keyfn(r)]
        d["requests"] += 1
        d["weight"] += weight(r)
        for k in USAGE_KEYS:
            d[k] += r[k]
    return b


def rolling_max(records, hours=5):
    timed = sorted((r for r in records if r["ts"]), key=lambda r: r["ts"])
    if not timed:
        return 0.0, None, []
    win = dt.timedelta(hours=hours)
    best, best_start, series, acc, j = 0.0, None, [], 0.0, 0
    for r in timed:
        acc += weight(r)
        while timed[j]["ts"] < r["ts"] - win:
            acc -= weight(timed[j])
            j += 1
        series.append((r["ts"], acc))
        if acc > best:
            best, best_start = acc, timed[j]["ts"]
    return best, best_start, series


def tool_summary(tools):
    agg = defaultdict(lambda: {"calls": 0, "tokens": 0, "amplified": 0, "errors": 0})
    for t in tools:
        a = agg[t["tool"]]
        a["calls"] += 1
        a["tokens"] += t["tokens"]
        a["amplified"] += t["amplified"]
        a["errors"] += 1 if t["is_error"] else 0
    return agg


def detail_summary(tools):
    agg = defaultdict(lambda: {"calls": 0, "tokens": 0, "amplified": 0})
    for t in tools:
        if not t["detail"]:
            continue
        a = agg[f"{t['tool']} · {t['detail']}"]
        a["calls"] += 1
        a["tokens"] += t["tokens"]
        a["amplified"] += t["amplified"]
    return agg


# --- Text output -----------------------------------------------------------

def print_table(title, buckets, top=None, label="key"):
    rows = sorted(buckets.items(), key=lambda kv: kv[1]["weight"], reverse=True)
    if top:
        rows = rows[:top]
    if not rows:
        return
    w = min(max(len(label), *(len(str(k)) for k, _ in rows)), 46)
    print(f"\n{title}")
    hdr = (f"{label:<{w}}  {'reqs':>6}  {'in':>8}  {'cache_w':>8}  {'cache_r':>9}"
           f"  {'out':>8}  {'weight':>8}  {'share':>6}")
    print(hdr + "\n" + "-" * len(hdr))
    total = sum(b["weight"] for b in buckets.values()) or 1.0
    for k, b in rows:
        n = str(k)
        n = n if len(n) <= w else "…" + n[-(w - 1):]
        print(f"{n:<{w}}  {b['requests']:>6}  {human(b['input_tokens']):>8}  "
              f"{human(b['cache_creation_input_tokens']):>8}  "
              f"{human(b['cache_read_input_tokens']):>9}  "
              f"{human(b['output_tokens']):>8}  {b['weight']:>8.2f}  "
              f"{100 * b['weight'] / total:>5.1f}%")


def print_tools(tools, top=20):
    agg = tool_summary(tools)
    if not agg:
        print("\nNo tool activity found in these transcripts.")
        return
    rows = sorted(agg.items(), key=lambda kv: -kv[1]["amplified"])[:top]
    tot = sum(a["amplified"] for a in agg.values()) or 1
    print("\nBy tool — context injected, and what it cost after amplification")
    hdr = (f"{'tool':<34}  {'calls':>6}  {'injected':>9}  {'amplified':>10}"
           f"  {'share':>6}  {'err':>4}")
    print(hdr + "\n" + "-" * len(hdr))
    for name, a in rows:
        n = name if len(name) <= 34 else name[:33] + "…"
        print(f"{n:<34}  {a['calls']:>6}  {human(a['tokens']):>9}  "
              f"{human(a['amplified']):>10}  "
              f"{100 * a['amplified'] / tot:>5.1f}%  {a['errors']:>4}")
    print("\n  injected  = tokens the tool's output added to the context")
    print("  amplified = injected × how many later API calls re-sent it")
    print("              (this is what shows up as cache_read on your bill)")


def print_panel(requests, tools, stats, tz, days):
    """Compact at-a-glance summary, in the spirit of /usage but built from
    the local transcripts. One screen, no scrolling."""
    W = 74
    now = dt.datetime.now(dt.timezone.utc)
    tot = {k: sum(r[k] for r in requests) for k in USAGE_KEYS}
    total_w = sum(weight(r) for r in requests) or 1.0

    def bar(frac, width=12):
        n = max(0, min(width, round(frac * width)))
        return "█" * n + "·" * (width - n)

    print("─" * W)
    print(f"tare · last {days} day(s) · from local transcripts, this machine "
          "only")
    print(f"{len(requests):,} requests · "
          f"{len({r['session'] for r in requests})} sessions · "
          f"{human(sum(tot.values()))} tokens · "
          f"{100 * tot['cache_read_input_tokens'] / (sum(tot.values()) or 1):.0f}% cache reads")
    print("─" * W)

    mrows = sorted(accumulate(requests, lambda r: r["model"]).items(),
                   key=lambda kv: -kv[1]["weight"])[:3]
    names = [re.sub(r"^claude-", "", model_label(m)) for m, _ in mrows]
    cw = max([12] + [len(n) for n in names]) + 2
    print(f"{'Breakdown':<12}" + "".join(f"{n:>{cw}}" for n in names)
          + f"{'total':>{cw}}")
    for label, key in (("Input", "input_tokens"), ("Output", "output_tokens"),
                       ("Cache read", "cache_read_input_tokens"),
                       ("Cache write", "cache_creation_input_tokens")):
        print(f"{label:<12}"
              + "".join(f"{human(b[key]):>{cw}}" for _, b in mrows)
              + f"{human(tot[key]):>{cw}}")
    print(f"{'Weight':<12}"
          + "".join(f"{b['weight']:>{cw}.1f}" for _, b in mrows)
          + f"{total_w:>{cw}.1f}")
    if any(rate_is_estimated(m) for m, _ in mrows):
        print("  * no published rate; placeholder weight — compare tokens, "
              "not weight")

    print("\nWhat's using your limits?" + " " * 21 + "share of weight")
    by_proj = sorted(accumulate(requests, lambda r: r["project"]).items(),
                     key=lambda kv: -kv[1]["weight"])[:4]
    for name, b in by_proj:
        frac = b["weight"] / total_w
        print(f"  {name[-33:]:<35} {bar(frac)} {100 * frac:>4.0f}%")

    agg = tool_summary(tools)
    if agg:
        tot_amp = sum(a["amplified"] for a in agg.values()) or 1
        print("Context, by tool" + " " * 30 + "share of amplified")
        for name, a in sorted(agg.items(),
                              key=lambda kv: -kv[1]["amplified"])[:4]:
            frac = a["amplified"] / tot_amp
            print(f"  {name[:33]:<35} {bar(frac)} {100 * frac:>4.0f}%")

    lo = now - dt.timedelta(hours=5)
    inwin = [r for r in requests if r["ts"] and r["ts"] > lo]
    peak, _, _ = rolling_max(requests, 5)
    cur = sum(weight(r) for r in inwin)
    line = f"\n5h window now: {cur:.1f}"
    if peak:
        line += f" ({100 * cur / peak:.0f}% of the peak seen in this range)"
    oldest = min((r["ts"] for r in inwin), default=None)
    if oldest:
        line += (f" · oldest work ages out "
                 f"{oldest + dt.timedelta(hours=5 + tz):%H:%M}")
    print(line)

    tip = next((h for s, h, _ in run_checks(requests, tools, stats, tz)
                if s in ("flag", "warn")), None)
    print("─" * W)
    if tip:
        print(f"Tip: {tip}. Run --doctor for the full findings.")


# --- Checks ----------------------------------------------------------------

def run_checks(requests, tools, stats, tz, redact=False):
    """Returns [(severity, headline, detail)]; severity in ok|warn|flag.

    redact=True omits local identifiers so the findings can be published.
    The checks themselves are identical either way: this stays one function so
    the text, HTML and share summary can never disagree about what was found.
    """
    out = []
    n = len(requests)
    if not n:
        return [("flag", "No usage records found", "Widen --days or check --dir.")]
    total_w = sum(weight(r) for r in requests) or 1.0
    tot = {k: sum(r[k] for r in requests) for k in USAGE_KEYS}
    grand = sum(tot.values()) or 1

    unparsed = stats["lines_bad_json"] + stats["lines_not_object"]
    if unparsed > stats["lines"] * 0.02:
        out.append(("flag", "Transcript schema may have changed",
                    f"{unparsed} of {stats['lines']} lines failed to parse. Run "
                    "--dump-sample and check field names before trusting the rest."))
    else:
        out.append(("ok", "Transcripts parsed cleanly",
                    f"{stats['records']} API responses from {stats['lines']} lines "
                    f"across {stats['files_read']} session files."))

    dupes = stats["dupes"]
    out.append(("ok" if dupes <= n * 2 else "warn",
                f"{dupes} duplicate entries collapsed",
                "One API response is stored as one entry per content block, each "
                "repeating the same usage object. Summing raw lines would have "
                f"overstated totals by {100 * dupes / max(n, 1):.0f}%."))

    # Size the request by what it actually generated. Counting cached tokens
    # here made every small auxiliary call sharing a big cached prefix look
    # large, so near-identical 3-in/172-out calls tripped the check in bulk.
    sig = defaultdict(set)
    for r in requests:
        if r["input_tokens"] + r["output_tokens"] > 5000:
            sig[(r["model"], r["input_tokens"], r["output_tokens"],
                 r["cache_creation_input_tokens"],
                 r["cache_read_input_tokens"])].add(r["request_id"])
    rep = {k: v for k, v in sig.items() if len(v) > 2}
    if rep:
        w = max(rep.items(), key=lambda kv: len(kv[1]))
        out.append(("flag", f"{len(rep)} usage blocks repeated under >2 request ids",
                    "Byte-identical token counts re-sent many times is the "
                    f"signature of a retry loop metered each time. Worst: {w[0][0]} "
                    f"in={human(w[0][1])} out={human(w[0][2])} × {len(w[1])} "
                    "requests."))
    else:
        out.append(("ok", "No repeated-usage signature",
                    "No sign of one request being metered more than once."))

    cr = tot["cache_read_input_tokens"] / grand
    cw = tot["cache_creation_input_tokens"]
    if cr > 0.85:
        out.append(("warn", f"Cache reads are {100 * cr:.0f}% of all tokens",
                    "Long-lived sessions: every turn re-sends the whole context. "
                    "/clear between unrelated tasks is the biggest single lever."))
    if cw > tot["cache_read_input_tokens"] * 0.5:
        out.append(("flag", "Cache is being rebuilt constantly",
                    f"Cache writes ({human(cw)}) are large relative to reads "
                    f"({human(tot['cache_read_input_tokens'])}); normally writes "
                    "are a small fraction. Edits early in context, or idle gaps "
                    "past the cache TTL, force a full rewrite each turn at 1.25× "
                    "the input rate. A plausible mechanism for sudden quota burn."))

    est = [r for r in requests if rate_is_estimated(r["model"])]
    if est:
        est_w = sum(weight(r) for r in est)
        seen_est = sorted({r["model"] for r in est})
        names = ", ".join(seen_est)
        one = len(seen_est) == 1
        est_tok = sum(sum(r[k] for k in USAGE_KEYS) for r in est)
        out.append(("warn", "Some weight rests on a placeholder rate",
                    f"{names} {'has' if one else 'have'} no published price, so "
                    f"MODEL_RATES prices {'it' if one else 'them'} as Opus. "
                    f"{'It is' if one else 'They are'} {len(est):,} requests "
                    f"({human(est_tok)} tokens) and shows up as "
                    f"{100 * est_w / total_w:.0f}% of weight, but that share is an "
                    "artifact of the guess. Judge by requests and tokens until "
                    "the rate is known."))

    fam = defaultdict(float)
    for r in requests:
        fam[family(r["model"])] += weight(r)
    if fam:
        f, w = max(fam.items(), key=lambda kv: kv[1])
        # Only claim a share for families we can actually price; for the others
        # the finding above already says the number means nothing.
        if w / total_w > 0.5 and f == "opus":
            out.append(("warn",
                        f"{f.title()} is {100 * w / total_w:.0f}% of consumption",
                        "Premium-tier models draw down the shared cap far faster "
                        "per token. If you didn't deliberately pick this model for "
                        "all of it, check your model setting and any per-project "
                        "settings.json overriding it."))

    models = Counter(r["model"] for r in requests)
    if len(models) > 1:
        out.append(("ok", f"{len(models)} distinct models used",
                    ", ".join(f"{model_label(m)} ({c})"
                              for m, c in models.most_common(6))))

    side = sum(weight(r) for r in requests if r["sidechain"])
    if side / total_w > 0.4:
        out.append(("warn", f"Subagents are {100 * side / total_w:.0f}% of weight",
                    "Each subagent starts with its own context copy, so a task "
                    "that spawns several costs much more than it appears to."))

    hours = Counter()
    for r in requests:
        if r["ts"]:
            hours[(r["ts"] + dt.timedelta(hours=tz)).hour] += 1
    night = sum(v for h, v in hours.items() if h < 6 or h >= 23)
    if night > n * 0.1:
        out.append(("flag", f"{100 * night / n:.0f}% of requests fall in 23:00-06:00",
                    "Traffic while you were probably not at the keyboard. Look for "
                    "a background agent, a file watcher, a CI job, or an editor "
                    "extension holding a session open. Check the heatmap."))

    per_sess = Counter(r["session"] for r in requests)
    if per_sess:
        s, c = per_sess.most_common(1)[0]
        if c > 400:
            # The session id is the one local identifier in any finding, so it
            # is named locally and withheld from anything meant for publishing.
            out.append(("flag", f"One session made {c} API calls",
                        ("" if redact else f"Session {s[:20]}… ")
                        + "That volume usually means an agent loop that didn't "
                        + ("terminate. Run --by session to find it, then read "
                           "that transcript." if redact
                           else "terminate. Read that transcript.")))

    agg = tool_summary(tools)
    if agg:
        tot_amp = sum(a["amplified"] for a in agg.values()) or 1
        name, a = max(agg.items(), key=lambda kv: kv[1]["amplified"])
        if a["amplified"] / tot_amp > 0.4:
            out.append(("warn", f"{name} is "
                        f"{100 * a['amplified'] / tot_amp:.0f}% of injected context",
                        f"{a['calls']} calls, {human(a['tokens'])} tokens injected, "
                        f"{human(a['amplified'])} after re-sending. One noisy tool "
                        "can dominate an entire week."))
        errs = sum(x["errors"] for x in agg.values())
        if errs > len(tools) * 0.15:
            out.append(("warn", f"{errs} tool calls returned errors",
                        f"{100 * errs / max(len(tools), 1):.0f}% of tool calls "
                        "failed. Failed calls still cost a full round trip and are "
                        "often retried with even more context."))
    return out


# --- HTML ------------------------------------------------------------------

def build_html(requests, tools, stats, tz, days, root, path):
    if ccreport is None:
        print("ccreport.py must sit next to ccaudit.py for --html.",
              file=sys.stderr)
        return False
    R, e = ccreport, ccreport.esc
    total_w = sum(weight(r) for r in requests) or 1.0
    tot = {k: sum(r[k] for r in requests) for k in USAGE_KEYS}

    def loc(t):
        return t + dt.timedelta(hours=tz)

    parts = ["<h1>Claude Code usage audit</h1>",
             f'<p class="meta">{e(root)} · last {days} days · generated '
             f'{dt.datetime.now():%Y-%m-%d %H:%M} local · ccaudit {__version__}</p>']

    best_w, best_start, series = rolling_max(requests, 5)
    cards = [
        ("API requests", f"{len(requests):,}",
         f"{stats['dupes']:,} duplicate entries collapsed"),
        ("Total tokens", R.human(sum(tot.values())),
         f"{100 * tot['cache_read_input_tokens'] / max(sum(tot.values()), 1):.0f}%"
         " cache reads"),
        ("Sessions", f"{len({r['session'] for r in requests}):,}",
         f"across {len({r['project'] for r in requests})} projects"),
        ("Weight", f"{total_w:.1f}", "USD-equivalent proxy"),
        ("Peak 5h window", f"{100 * best_w / total_w:.0f}%",
         "of the period" + (loc(best_start).strftime(", from %a %H:%M")
                            if best_start else "")),
    ]
    parts.append('<div class="cards">' + "".join(
        f'<div class="card"><div class="k">{e(k)}</div><div class="v">{e(v)}</div>'
        f'<div class="n">{e(s)}</div></div>' for k, v, s in cards) + "</div>")

    parts.append("<h2>Findings</h2>")
    order = {"flag": 0, "warn": 1, "ok": 2}
    for sev, head, detail in sorted(run_checks(requests, tools, stats, tz),
                                    key=lambda c: order[c[0]]):
        cls = "flag ok" if sev == "ok" else "flag"
        mark = "✓" if sev == "ok" else "▲"
        parts.append(f'<div class="{cls}"><b>{mark} {e(head)}</b><br>'
                     f'{e(detail)}</div>')

    by_day = defaultdict(lambda: {k: 0 for k in USAGE_KEYS})
    for r in requests:
        if r["ts"]:
            d = loc(r["ts"]).strftime("%m-%d")
            for k in USAGE_KEYS:
                by_day[d][k] += r[k]
    parts.append("<h2>Tokens per day</h2>"
                 '<p class="sub">Cache reads usually dwarf everything else. That '
                 "is the running cost of keeping a long session alive.</p>"
                 + R.stacked_bars(sorted(by_day.items()), list(USAGE_KEYS),
                                  title="tokens per day"))

    grid, dayL = {}, []
    for r in requests:
        if not r["ts"]:
            continue
        lt = loc(r["ts"])
        d = lt.strftime("%a %m-%d")
        if d not in dayL:
            dayL.append(d)
        grid[(d, lt.hour)] = grid.get((d, lt.hour), 0) + weight(r)
    dayL.sort(key=lambda s: s[4:])
    parts.append("<h2>When it burned</h2>"
                 '<p class="sub">Consumption by hour in your local time. Blocks '
                 "outside your working hours are the ones that need explaining.</p>"
                 + R.heatmap(grid, dayL, title="usage heatmap"))

    if series:
        step = max(1, len(series) // 220)
        parts.append("<h2>Rolling 5-hour load</h2>"
                     '<p class="sub">The 5-hour window is what triggers the '
                     "session lockout. This is the shape your meter sees.</p>"
                     + R.line_chart([(loc(t).strftime("%m-%d %H:%M"), v)
                                     for t, v in series[::step]],
                                    title="rolling 5h weight"))

    by_model = accumulate(requests, lambda r: r["model"])
    colors = ["#c4643a", "#5b9279", "#7c9cbf", "#e0a458", "#8d7ba5", "#9c9086"]
    mrows = sorted(by_model.items(), key=lambda kv: -kv[1]["weight"])
    legend = "".join(
        f'<div><span class="sw" style="background:{colors[i % len(colors)]}">'
        f'</span>{e(model_label(m))} — {100 * b["weight"] / total_w:.1f}% · '
        f'{b["requests"]} reqs · {R.human(b["output_tokens"])} out</div>'
        for i, (m, b) in enumerate(mrows))
    note = estimated_note(requests)
    if note:
        legend += f'<div class="n" style="margin-top:8px">{e(note)}</div>'
    parts.append("<h2>By model</h2>"
                 '<p class="sub">Share of weight, not share of requests. A premium '
                 "model can be a handful of calls and most of the drawdown.</p>"
                 f'<div class="split">' + R.donut(
                     [(model_label(m), b["weight"], colors[i % len(colors)])
                      for i, (m, b) in enumerate(mrows)], title="model share")
                 + f'<div class="legend">{legend}</div></div>')

    agg = tool_summary(tools)
    if agg:
        rows = sorted(agg.items(), key=lambda kv: -kv[1]["amplified"])[:16]
        parts.append("<h2>What put tokens in your context</h2>"
                     '<p class="sub"><b>Injected</b> is what a tool\'s output added. '
                     "<b>Amplified</b> multiplies that by how many later API calls "
                     "re-sent it: a 20K-token file read early in a 200-call session "
                     "becomes 4M tokens of cache reads.</p>"
                     + R.hbars([(k, a["amplified"],
                                 f"{a['calls']} calls, {R.human(a['tokens'])} "
                                 "injected") for k, a in rows],
                               title="tools by amplified tokens"))
        parts.append("<table><tr><th>tool</th><th>calls</th><th>injected</th>"
                     "<th>amplified</th><th>errors</th></tr>" + "".join(
                         f'<tr><td class="mono">{e(k)}</td><td>{a["calls"]:,}</td>'
                         f'<td>{R.human(a["tokens"])}</td>'
                         f'<td>{R.human(a["amplified"])}</td>'
                         f'<td>{a["errors"] or ""}</td></tr>' for k, a in rows)
                     + "</table>")

        tot_amp = sum(a["amplified"] for a in agg.values()) or 1
        seg = [
            ("Web browsing", {k: v for k, v in agg.items()
                              if k.startswith(("WebFetch", "WebSearch"))}),
            ("MCP servers", {k: v for k, v in agg.items() if k.startswith("MCP ")}),
            ("Skills", {k: v for k, v in agg.items() if k.startswith("Skill:")}),
            ("Subagents", {k: v for k, v in agg.items() if k.startswith("Agent:")}),
        ]
        parts.append('<div class="cards">' + "".join(
            f'<div class="card"><div class="k">{e(nm)}</div><div class="v">'
            f'{100 * sum(a["amplified"] for a in d.values()) / tot_amp:.0f}%</div>'
            f'<div class="n">{sum(a["calls"] for a in d.values())} calls</div>'
            "</div>" for nm, d in seg) + "</div>")

        det = sorted(detail_summary(tools).items(),
                     key=lambda kv: -kv[1]["amplified"])[:14]
        if det:
            parts.append("<h2>Specific files, commands and hosts</h2>"
                         '<p class="sub">The individual offenders behind the tools '
                         "above.</p><table><tr><th>what</th><th>calls</th>"
                         "<th>injected</th><th>amplified</th></tr>" + "".join(
                             f'<tr><td class="mono">{e(k)}</td>'
                             f'<td>{a["calls"]:,}</td>'
                             f'<td>{R.human(a["tokens"])}</td>'
                             f'<td>{R.human(a["amplified"])}</td></tr>'
                             for k, a in det) + "</table>")

    srows = sorted(accumulate(requests, lambda r: r["session"]).items(),
                   key=lambda kv: -kv[1]["weight"])[:12]
    parts.append("<h2>Heaviest sessions</h2><table><tr><th>session</th><th>reqs</th>"
                 "<th>cache read</th><th>output</th><th>share</th></tr>" + "".join(
                     f'<tr><td class="mono">{e(s[:28])}</td>'
                     f'<td>{b["requests"]:,}</td>'
                     f'<td>{R.human(b["cache_read_input_tokens"])}</td>'
                     f'<td>{R.human(b["output_tokens"])}</td>'
                     f'<td>{100 * b["weight"] / total_w:.1f}%</td></tr>'
                     for s, b in srows) + "</table>")

    parts.append("<footer>Generated locally by ccaudit from "
                 "<code>~/.claude/projects</code>; no data left this machine. "
                 "Weights come from the editable MODEL_RATES table and are a "
                 "relative proxy, not a bill.</footer>")

    Path(path).write_text(R.page("Claude Code usage audit", "".join(parts)),
                          encoding="utf-8")
    return True


# --- Shareable summary ------------------------------------------------------

def share_summary(requests, tools, stats, tz, days, path):
    """Redacted, paste-ready. No paths, prompts, arguments or identifiers."""
    total_w = sum(weight(r) for r in requests) or 1.0
    tot = {k: sum(r[k] for r in requests) for k in USAGE_KEYS}
    best_w, _, _ = rolling_max(requests, 5)
    versions = sorted({r["version"] for r in requests if r["version"]})
    L = [
        "## Claude Code usage audit",
        f"_ccaudit {__version__} · {days}-day window · local offset UTC{tz:+g}_", "",
        "| metric | value |", "|---|---|",
        f"| API requests | {len(requests):,} |",
        f"| Duplicate transcript entries collapsed | {stats['dupes']:,} |",
        f"| Sessions | {len({r['session'] for r in requests}):,} |",
        f"| Tool calls | {len(tools):,} |",
        f"| Input tokens | {tot['input_tokens']:,} |",
        f"| Output tokens | {tot['output_tokens']:,} |",
        f"| Cache creation tokens | {tot['cache_creation_input_tokens']:,} |",
        f"| Cache read tokens | {tot['cache_read_input_tokens']:,} |",
        f"| Heaviest 5h window | {100 * best_w / total_w:.1f}% of period |",
        f"| Claude Code versions | {', '.join(versions) or 'n/a'} |",
        "", "### Models", "", "| model | requests | share of weight |", "|---|---|---|",
    ]
    for m, b in sorted(accumulate(requests, lambda r: r["model"]).items(),
                       key=lambda kv: -kv[1]["weight"]):
        L.append(f"| `{m}`{' *' if rate_is_estimated(m) else ''} | "
                 f"{b['requests']:,} | {100 * b['weight'] / total_w:.1f}% |")
    note = estimated_note(requests)
    if note:
        L += ["", f"_{note}_"]

    L += ["", "### Daily totals", "",
          "| day | requests | input | output | cache write | cache read |",
          "|---|---|---|---|---|---|"]
    by_day = defaultdict(lambda: {**{k: 0 for k in USAGE_KEYS}, "n": 0})
    for r in requests:
        if r["ts"]:
            d = (r["ts"] + dt.timedelta(hours=tz)).strftime("%Y-%m-%d")
            by_day[d]["n"] += 1
            for k in USAGE_KEYS:
                by_day[d][k] += r[k]
    for d, b in sorted(by_day.items()):
        L.append(f"| {d} | {b['n']:,} | {b['input_tokens']:,} | "
                 f"{b['output_tokens']:,} | {b['cache_creation_input_tokens']:,} | "
                 f"{b['cache_read_input_tokens']:,} |")

    agg = tool_summary(tools)
    if agg:
        tot_amp = sum(a["amplified"] for a in agg.values()) or 1
        L += ["", "### Tool attribution (names only, no arguments)", "",
              "| tool | calls | injected | amplified | share |", "|---|---|---|---|---|"]
        for k, a in sorted(agg.items(), key=lambda kv: -kv[1]["amplified"])[:15]:
            L.append(f"| `{k}` | {a['calls']:,} | {a['tokens']:,} | "
                     f"{a['amplified']:,} | "
                     f"{100 * a['amplified'] / tot_amp:.1f}% |")

    L += ["", "### Automated findings", ""]
    for sev, head, detail in run_checks(requests, tools, stats, tz, redact=True):
        if sev != "ok":
            L.append(f"- **{head}** — {detail}")
    L += ["", "_Generated by ccaudit. Contains no prompts, file paths, file "
          "contents, command arguments, session ids or account "
          "identifiers._"]
    Path(path).write_text("\n".join(L), encoding="utf-8")


# --- CLI -------------------------------------------------------------------

def _wrap(text, width):
    out, line = [], ""
    for w in text.split():
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def main():
    p = argparse.ArgumentParser(
        description="Analyze Claude Code token usage from local session logs.")
    p.add_argument("--dir", default=str(Path.home() / ".claude" / "projects"))
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--by", action="append", choices=[
        "day", "hour", "model", "session", "project", "version", "tool", "detail"])
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--html", metavar="PATH", help="self-contained HTML report")
    p.add_argument("--csv", metavar="PATH")
    p.add_argument("--share", metavar="PATH",
                   help="redacted markdown summary safe to post publicly")
    p.add_argument("--doctor", action="store_true")
    p.add_argument("--panel", action="store_true",
                   help="compact one-screen summary, like /usage but local")
    p.add_argument("--tz", type=float, default=None,
                   help="local UTC offset in hours (default: from system)")
    p.add_argument("--dump-sample", action="store_true")
    p.add_argument("--version", action="version", version=f"ccaudit {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    root = Path(os.path.expanduser(a.dir))
    if not root.exists():
        print(f"Not found: {root}\nClaude Code stores sessions under "
              "~/.claude/projects; pass --dir if you use CLAUDE_CONFIG_DIR.",
              file=sys.stderr)
        return 2

    if a.tz is None:
        off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta()
        a.tz = off.total_seconds() / 3600

    if a.dump_sample:
        for fp in sorted(root.rglob("*.jsonl"),
                         key=lambda f: -f.stat().st_mtime)[:20]:
            for line in open(fp, encoding="utf-8", errors="replace"):
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(o, dict) and isinstance(o.get("message"), dict) \
                        and o["message"].get("usage"):
                    print(f"# {fp}\n" + json.dumps(o, indent=2)[:4000])
                    return 0
        print("No entry with a usage block found.", file=sys.stderr)
        return 1

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=a.days)
    requests, tools, stats = load_all(root, since, a.verbose)
    if not requests:
        print(f"No usage found in {root} for the last {a.days} days.")
        return 0

    tot = {k: sum(r[k] for r in requests) for k in USAGE_KEYS}
    total_w = sum(weight(r) for r in requests)
    span = [r["ts"] for r in requests if r["ts"]]

    if a.panel:
        print_panel(requests, tools, stats, a.tz, a.days)
    else:
        print("=" * 74)
        print(f"Claude Code usage — last {a.days} days   ({root})")
        print("=" * 74)
        if span:
            print(f"Range     : {min(span) + dt.timedelta(hours=a.tz):%Y-%m-%d %H:%M}"
                  f" → {max(span) + dt.timedelta(hours=a.tz):%Y-%m-%d %H:%M} local")
        print(f"Requests  : {len(requests):,}  ({stats['dupes']:,} duplicate "
              "entries collapsed)")
        print(f"Sessions  : {len({r['session'] for r in requests})} across "
              f"{len({r['project'] for r in requests})} projects, "
              f"{len(tools):,} tool calls")
        for k in USAGE_KEYS:
            print(f"{SHORT[k]:<10}: {tot[k]:>15,}")
        print(f"{'TOTAL':<10}: {sum(tot.values()):>15,} tokens")
        print(f"{'weight':<10}: {total_w:>15.2f}  (proxy; see MODEL_RATES)")

    keyers = {
        "day": lambda r: (r["ts"] + dt.timedelta(hours=a.tz)).strftime("%Y-%m-%d %a") if r["ts"] else "?",
        "hour": lambda r: (r["ts"] + dt.timedelta(hours=a.tz)).strftime("%m-%d %H:00") if r["ts"] else "?",
        "model": lambda r: model_label(r["model"]),
        "session": lambda r: r["session"],
        "project": lambda r: r["project"],
        "version": lambda r: r["version"] or "unknown",
    }
    # --panel replaces the default tables; explicit --by still adds them
    for v in (a.by or ([] if a.panel else ["day", "model", "tool", "session"])):
        if v == "tool":
            print_tools(tools, a.top)
        elif v == "detail":
            rows = sorted(detail_summary(tools).items(),
                          key=lambda kv: -kv[1]["amplified"])[:a.top]
            print("\nBy file / command / host   (amplified tokens)")
            for k, d in rows:
                print(f"  {human(d['amplified']):>9}  {d['calls']:>4} calls  {k[:66]}")
        else:
            print_table(f"By {v}", accumulate(requests, keyers[v]),
                        None if v in ("day", "model", "version") else a.top, v)
            if v == "model":
                note = estimated_note(requests)
                if note:
                    for chunk in _wrap(note, 70):
                        print(f"  {chunk}")

    if a.csv:
        cols = ["ts", "model", "project", "session", "request_id", "sidechain",
                "version", "stop_reason", *USAGE_KEYS, "weight"]
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in requests:
                row = dict(r)
                row["ts"] = r["ts"].isoformat() if r["ts"] else ""
                row["weight"] = round(weight(r), 6)
                w.writerow(row)
        print(f"\nWrote {len(requests):,} rows to {a.csv}")

    if a.doctor:
        print("\n" + "=" * 74 + "\nFINDINGS\n" + "=" * 74)
        order = {"flag": 0, "warn": 1, "ok": 2}
        for sev, head, detail in sorted(run_checks(requests, tools, stats, a.tz),
                                        key=lambda c: order[c[0]]):
            print(f"\n{ {'flag': '[!]', 'warn': '[~]', 'ok': '[ok]'}[sev] } {head}")
            for chunk in _wrap(detail, 70):
                print(f"     {chunk}")

    if a.html and build_html(requests, tools, stats, a.tz, a.days, root, a.html):
        print(f"\nHTML report → {a.html}\n  open {a.html}")

    if a.share:
        share_summary(requests, tools, stats, a.tz, a.days, a.share)
        print(f"Redacted shareable summary → {a.share}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

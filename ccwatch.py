#!/usr/bin/env python3
"""
ccwatch - correlate the server's usage counters with your local token spend.

This is the part that can actually settle whether something is broken.

Claude subscriptions expose a usage endpoint that reports two counters:
a rolling five-hour utilization and a seven-day utilization, each with a
reset timestamp. ccwatch polls that endpoint, records each reading to a
local file, and then lines those readings up against the tokens your own
transcripts say you spent in the same interval.

If the server counter climbs while your transcripts show no requests, that
is phantom usage and you can prove it with a timestamped series. If the
counter climbs exactly in step with your token spend, the metering is fine
and the answer is somewhere else (model choice, context size, tool output).

    python3 ccwatch.py --poll                    # leave running
    python3 ccwatch.py --analyze                 # after a few hours

CAVEATS, read these:
  * The endpoint is not documented by Anthropic. It can change or disappear
    without notice. Treat a failure here as "the tool broke", not evidence.
  * Independent monitoring has reported the seven-day counter resetting on a
    roughly 72-hour cadence rather than weekly. If you see a reset that looks
    early, that may be why. Record it rather than assuming a bug.
  * ccwatch reads your existing OAuth token from local storage so it can call
    your own account's endpoint. The token is sent only to claude.ai over
    HTTPS, is never written to the log, and is never printed. Read the code.
    If you would rather not, export the token yourself and pass --token-env.

MIT licensed.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

USAGE_URL = "https://claude.ai/api/oauth/usage"
DEFAULT_LOG = Path.home() / ".claude-usage-watch.jsonl"


# --- token discovery -------------------------------------------------------

def find_token(env_name=None):
    """Locate the existing OAuth token. Never returns it to stdout."""
    if env_name:
        tok = os.environ.get(env_name)
        if tok:
            return tok.strip(), f"env:{env_name}"
        raise SystemExit(f"Environment variable {env_name} is empty.")

    for var in ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_OAUTH_TOKEN"):
        if os.environ.get(var):
            return os.environ[var].strip(), f"env:{var}"

    cred = Path.home() / ".claude" / ".credentials.json"
    if cred.exists():
        try:
            data = json.loads(cred.read_text())
            tok = _dig_token(data)
            if tok:
                return tok, str(cred)
        except (OSError, json.JSONDecodeError):
            pass

    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s",
                 "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                try:
                    tok = _dig_token(json.loads(out.stdout))
                except json.JSONDecodeError:
                    tok = out.stdout.strip()
                if tok:
                    return tok, "macOS Keychain"
        except (OSError, subprocess.SubprocessError):
            pass

    raise SystemExit(
        "Could not find an OAuth token.\n"
        "Log in with `claude` first, or export the token yourself and run\n"
        "  python3 ccwatch.py --poll --token-env MY_TOKEN_VAR")


def _dig_token(node, depth=0):
    if depth > 5:
        return None
    if isinstance(node, dict):
        for k in ("accessToken", "access_token", "token"):
            v = node.get(k)
            if isinstance(v, str) and len(v) > 20:
                return v
        for v in node.values():
            got = _dig_token(v, depth + 1)
            if got:
                return got
    elif isinstance(node, list):
        for v in node:
            got = _dig_token(v, depth + 1)
            if got:
                return got
    return None


# --- polling ---------------------------------------------------------------

def fetch_usage(token):
    req = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/json",
                 "User-Agent": "ccwatch/0.2 (local usage audit)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(payload):
    """Pull the two counters out without assuming the exact shape."""
    def grab(key):
        d = payload.get(key)
        if not isinstance(d, dict):
            return None, None
        util = d.get("utilization", d.get("used", d.get("percentage")))
        return util, d.get("resets_at", d.get("reset_at"))

    five, five_reset = grab("five_hour")
    seven, seven_reset = grab("seven_day")
    return five, five_reset, seven, seven_reset


def poll(log_path, interval, token_env, once=False):
    token, source = find_token(token_env)
    print(f"token loaded from {source} (not logged, not printed)")
    print(f"polling {USAGE_URL} every {interval}s → {log_path}")
    print("leave this running; Ctrl-C to stop\n")
    last = {}
    while True:
        now = dt.datetime.now(dt.timezone.utc)
        rec = {"ts": now.isoformat(), "ok": False}
        try:
            payload = fetch_usage(token)
            five, fr, seven, sr = summarize(payload)
            rec.update(ok=True, five_hour=five, five_hour_resets_at=fr,
                       seven_day=seven, seven_day_resets_at=sr, raw=payload)
            d5 = "" if last.get("five") is None or five is None else \
                f"  Δ5h {five - last['five']:+.2f}"
            d7 = "" if last.get("seven") is None or seven is None else \
                f"  Δ7d {seven - last['seven']:+.2f}"
            print(f"{now:%H:%M:%S}  5h={_pct(five)}  7d={_pct(seven)}{d5}{d7}")
            if last.get("five") is not None and five is not None and five < last["five"] - 1:
                print("           ↳ 5-hour counter reset")
            if last.get("seven") is not None and seven is not None and seven < last["seven"] - 1:
                print(f"           ↳ SEVEN-DAY COUNTER RESET at {now:%Y-%m-%d %H:%M}Z")
            last = {"five": five, "seven": seven}
        except urllib.error.HTTPError as ex:
            rec["error"] = f"HTTP {ex.code}"
            print(f"{now:%H:%M:%S}  HTTP {ex.code}"
                  + ("  (token expired? run `claude` to refresh)"
                     if ex.code in (401, 403) else ""))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as ex:
            rec["error"] = str(ex)
            print(f"{now:%H:%M:%S}  {type(ex).__name__}: {ex}")

        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        if once:
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped")
            return


def _pct(v):
    return "  n/a" if v is None else f"{float(v):5.1f}%"


# --- correlation -----------------------------------------------------------

def analyze(log_path, projects_dir, tz):
    try:
        import ccaudit
    except ImportError:
        raise SystemExit("ccaudit.py must sit next to ccwatch.py for --analyze.")

    readings = []
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("ok"):
                r["_ts"] = ccaudit.parse_ts(r["ts"])
                readings.append(r)
    if len(readings) < 2:
        raise SystemExit(f"Need at least 2 successful readings in {log_path}. "
                         "Run --poll for a while first.")
    readings.sort(key=lambda r: r["_ts"])

    span_days = max(1, (readings[-1]["_ts"] - readings[0]["_ts"]).days + 1)
    since = readings[0]["_ts"] - dt.timedelta(minutes=5)
    requests, tools, stats = ccaudit.load_all(
        Path(os.path.expanduser(projects_dir)), since)

    print("=" * 74)
    print("Server counters vs local token spend")
    print("=" * 74)
    print(f"{len(readings)} readings over {span_days}d · "
          f"{len(requests):,} local API requests in the same period\n")

    hdr = (f"{'interval (local)':<22} {'Δ5h':>7} {'Δ7d':>7} {'reqs':>6} "
           f"{'tokens':>10} {'weight':>8}  note")
    print(hdr + "\n" + "-" * len(hdr))

    phantom, silent = [], []
    for a, b in zip(readings, readings[1:]):
        t0, t1 = a["_ts"], b["_ts"]
        if (t1 - t0).total_seconds() > 3600 * 3:
            continue  # gap in monitoring, skip
        d5 = _delta(a.get("five_hour"), b.get("five_hour"))
        d7 = _delta(a.get("seven_day"), b.get("seven_day"))
        inwin = [r for r in requests if r["ts"] and t0 <= r["ts"] < t1]
        toks = sum(r[k] for r in inwin for k in ccaudit.USAGE_KEYS)
        wt = sum(ccaudit.weight(r) for r in inwin)
        note = ""
        if d7 is not None and d7 > 0.3 and not inwin:
            note = "PHANTOM: counter rose with zero local requests"
            phantom.append((t0, t1, d7))
        elif d7 is not None and d7 < -1:
            note = "counter reset"
        elif inwin and d7 is not None and abs(d7) < 0.01 and toks > 200_000:
            note = "counter flat despite real spend"
            silent.append((t0, t1, toks))
        local = (t0 + dt.timedelta(hours=tz)).strftime("%m-%d %H:%M")
        print(f"{local} →{(t1 + dt.timedelta(hours=tz)):%H:%M}   "
              f"{_fmt(d5):>7} {_fmt(d7):>7} {len(inwin):>6} "
              f"{ccaudit.human(toks):>10} {wt:>8.2f}  {note}")

    print()
    if phantom:
        total = sum(d for _, _, d in phantom)
        print(f"[!] {len(phantom)} intervals where the weekly counter rose with "
              f"no local activity, {total:.1f} percentage points total.")
        print("    This is the evidence to attach to a bug report. Include this")
        print("    table and the raw log; both are timestamped.")
    if silent:
        print(f"[~] {len(silent)} intervals with real token spend but a flat "
              "counter (batching or lag is normal; note it, don't conclude).")
    if not phantom and not silent:
        print("[ok] Counter movement tracks local spend. The metering looks")
        print("     consistent with what your transcripts show, so the cause is")
        print("     more likely model choice, context size, or tool output.")
        print("     Run: python3 ccaudit.py --days 7 --html report.html")

    # burn rate
    used = [r for r in readings if r.get("seven_day") is not None]
    if len(used) >= 2:
        hours = (used[-1]["_ts"] - used[0]["_ts"]).total_seconds() / 3600
        gained = sum(max(0, _delta(a.get("seven_day"), b.get("seven_day")) or 0)
                     for a, b in zip(used, used[1:]))
        if hours > 0.5 and gained > 0:
            rate = gained / hours
            print(f"\nWeekly-counter burn rate: {rate:.2f} %/hour "
                  f"→ 100% in {100 / rate:.1f} hours of this workload.")


def _delta(a, b):
    if a is None or b is None:
        return None
    try:
        return float(b) - float(a)
    except (TypeError, ValueError):
        return None


def _fmt(v):
    return "-" if v is None else f"{v:+.2f}"


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--poll", action="store_true", help="poll and record")
    p.add_argument("--once", action="store_true", help="one reading, then exit")
    p.add_argument("--analyze", action="store_true",
                   help="correlate recorded readings with local transcripts")
    p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--interval", type=int, default=120, help="seconds (default 120)")
    p.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"))
    p.add_argument("--token-env", metavar="VAR",
                   help="read the token from this env var instead of local storage")
    p.add_argument("--tz", type=float, default=None)
    a = p.parse_args()

    if a.tz is None:
        off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta()
        a.tz = off.total_seconds() / 3600

    if a.analyze:
        analyze(a.log, a.projects, a.tz)
    elif a.poll or a.once:
        poll(a.log, a.interval, a.token_env, once=a.once)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

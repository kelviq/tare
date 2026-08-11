#!/usr/bin/env python3
"""
otel_sink - a 200-line local OpenTelemetry collector for Claude Code.

Claude Code can export a telemetry event for *every* API request it makes,
including model, input/output/cache tokens, cost, duration, retry count and
which subsystem issued the request (main thread, subagent, compaction, ...).
Normally that needs a collector stack. This script is the collector: it
listens on localhost, accepts OTLP over HTTP/JSON, and appends every event and
metric datapoint to a JSONL file. Nothing leaves your machine.

Run it:
    python3 otel_sink.py --out ~/claude-telemetry.jsonl

Then, in the shell where you start Claude Code:
    export CLAUDE_CODE_ENABLE_TELEMETRY=1
    export OTEL_LOGS_EXPORTER=otlp
    export OTEL_METRICS_EXPORTER=otlp
    export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
    export OTEL_LOGS_EXPORT_INTERVAL=2000
    export OTEL_METRIC_EXPORT_INTERVAL=10000
    claude

Optional, to capture the full request/response bodies as well (these contain
your entire conversation history — only do this on a machine you control):
    export OTEL_LOG_RAW_API_BODIES=file:$HOME/claude-bodies
    mkdir -p ~/claude-bodies

Analyze afterwards:
    python3 otel_sink.py --report ~/claude-telemetry.jsonl
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

INTERESTING = {
    "claude_code.api_request",
    "claude_code.api_error",
    "claude_code.api_retries_exhausted",
    "claude_code.compaction",
    "claude_code.tool_result",
}


def attrs_to_dict(attrs):
    """OTLP KeyValue list -> flat dict."""
    out = {}
    for a in attrs or []:
        k = a.get("key")
        v = a.get("value", {})
        if "stringValue" in v:
            out[k] = v["stringValue"]
        elif "intValue" in v:
            try:
                out[k] = int(v["intValue"])
            except (TypeError, ValueError):
                out[k] = v["intValue"]
        elif "doubleValue" in v:
            out[k] = v["doubleValue"]
        elif "boolValue" in v:
            out[k] = v["boolValue"]
        elif "arrayValue" in v:
            out[k] = [list(x.values())[0] if x else None
                      for x in v["arrayValue"].get("values", [])]
        else:
            out[k] = v
    return out


def norm_name(name):
    """Claude Code sets the log body to `claude_code.api_request` but the
    `event.name` attribute to bare `api_request`. Normalize to the long form."""
    if not name:
        return "unknown"
    return name if name.startswith("claude_code.") else f"claude_code.{name}"


def nanos_to_iso(n):
    try:
        return dt.datetime.fromtimestamp(int(n) / 1e9, dt.timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def flatten_logs(payload):
    for rl in payload.get("resourceLogs", []):
        res = attrs_to_dict(rl.get("resource", {}).get("attributes"))
        for sl in rl.get("scopeLogs", []):
            for rec in sl.get("logRecords", []):
                a = attrs_to_dict(rec.get("attributes"))
                body = rec.get("body", {}).get("stringValue")
                yield {
                    "kind": "event",
                    "name": norm_name(body or a.get("event.name")),
                    "time": nanos_to_iso(rec.get("timeUnixNano")),
                    "resource": res,
                    "attrs": a,
                }


def flatten_metrics(payload):
    for rm in payload.get("resourceMetrics", []):
        res = attrs_to_dict(rm.get("resource", {}).get("attributes"))
        for sm in rm.get("scopeMetrics", []):
            for m in sm.get("metrics", []):
                name = m.get("name")
                for shape in ("sum", "gauge", "histogram"):
                    data = m.get(shape)
                    if not data:
                        continue
                    for dp in data.get("dataPoints", []):
                        val = dp.get("asInt", dp.get("asDouble", dp.get("sum")))
                        try:
                            val = float(val)
                        except (TypeError, ValueError):
                            val = None
                        yield {
                            "kind": "metric",
                            "name": name,
                            "time": nanos_to_iso(dp.get("timeUnixNano")),
                            "value": val,
                            "resource": res,
                            "attrs": attrs_to_dict(dp.get("attributes")),
                        }


class Handler(BaseHTTPRequestHandler):
    out_fh = None
    quiet = False
    counts = Counter()

    def log_message(self, *a):  # silence default access logging
        pass

    def _reply(self, code=200, body=b"{}"):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200, json.dumps({"ok": True, "counts": dict(self.counts)}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Almost certainly protobuf: the exporter protocol is misconfigured.
            self.counts["non_json_payloads"] += 1
            if not self.quiet:
                print("  ! received a non-JSON payload — set "
                      "OTEL_EXPORTER_OTLP_PROTOCOL=http/json", file=sys.stderr)
            self._reply(200)
            return

        path = self.path.rstrip("/")
        if path.endswith("/v1/logs"):
            records = list(flatten_logs(payload))
        elif path.endswith("/v1/metrics"):
            records = list(flatten_metrics(payload))
        elif path.endswith("/v1/traces"):
            records = [{"kind": "trace", "raw": payload,
                        "time": dt.datetime.now(dt.timezone.utc).isoformat()}]
        else:
            self._reply(404, b'{"error":"unknown path"}')
            return

        for r in records:
            self.counts[r.get("name", r["kind"])] += 1
            self.out_fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            if not self.quiet:
                self._echo(r)
        self.out_fh.flush()
        self._reply(200)

    def _echo(self, r):
        if r["kind"] != "event" or r["name"] not in INTERESTING:
            return
        a = r["attrs"]
        stamp = (r["time"] or "")[11:19]
        if r["name"] == "claude_code.api_request":
            print(f"{stamp}  {a.get('model','?'):<28} "
                  f"in={a.get('input_tokens',0):>7} "
                  f"out={a.get('output_tokens',0):>6} "
                  f"cw={a.get('cache_creation_tokens',0):>7} "
                  f"cr={a.get('cache_read_tokens',0):>8} "
                  f"${float(a.get('cost_usd') or 0):.4f}  "
                  f"{a.get('query_source','-')}"
                  + (f"  agent={a['agent.name']}" if a.get("agent.name") else ""))
        elif r["name"] == "claude_code.api_error":
            print(f"{stamp}  ERROR {a.get('status_code','?')} "
                  f"attempt={a.get('attempt','?')} {str(a.get('error',''))[:70]}")
        elif r["name"] == "claude_code.api_retries_exhausted":
            print(f"{stamp}  RETRIES EXHAUSTED after {a.get('total_attempts')} "
                  f"attempts over {a.get('total_retry_duration_ms')}ms")
        elif r["name"] == "claude_code.compaction":
            print(f"{stamp}  COMPACT ({a.get('trigger')}) "
                  f"{a.get('pre_tokens')} -> {a.get('post_tokens')} tokens")


def report(path):
    """Summarize a captured JSONL file."""
    by_model = defaultdict(lambda: Counter())
    by_source = defaultdict(float)
    by_hour = defaultdict(float)
    errors = Counter()
    compactions = 0
    total_cost = 0.0

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("kind") != "event":
                continue
            a = r.get("attrs", {})
            r["name"] = norm_name(r.get("name"))
            if r["name"] == "claude_code.api_request":
                m = a.get("model", "unknown")
                for k in ("input_tokens", "output_tokens",
                          "cache_creation_tokens", "cache_read_tokens"):
                    by_model[m][k] += int(a.get(k) or 0)
                by_model[m]["requests"] += 1
                cost = float(a.get("cost_usd") or 0)
                total_cost += cost
                by_source[a.get("query_source", "-")] += cost
                if r.get("time"):
                    by_hour[r["time"][:13]] += cost
            elif r["name"] == "claude_code.api_error":
                errors[f"{a.get('status_code','?')}"] += 1
            elif r["name"] == "claude_code.compaction":
                compactions += 1

    print(f"Total estimated cost: ${total_cost:.2f}   compactions: {compactions}")
    if errors:
        print(f"API errors by status: {dict(errors)}")
    print("\nBy model:")
    for m, c in sorted(by_model.items(), key=lambda kv: -kv[1]["output_tokens"]):
        print(f"  {m:<30} {c['requests']:>5} reqs  in={c['input_tokens']:>10,} "
              f"out={c['output_tokens']:>9,} cw={c['cache_creation_tokens']:>11,} "
              f"cr={c['cache_read_tokens']:>12,}")
    print("\nBy query source (cost share):")
    for s, c in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {s:<30} ${c:>8.2f}  {100 * c / (total_cost or 1):5.1f}%")
    print("\nTop 10 hours by cost:")
    for h, c in sorted(by_hour.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {h}:00Z  ${c:.2f}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=4318)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--out", default=os.path.expanduser("~/claude-telemetry.jsonl"))
    p.add_argument("--quiet", action="store_true", help="don't echo events to stdout")
    p.add_argument("--report", metavar="PATH", help="summarize a captured file and exit")
    args = p.parse_args()

    if args.report:
        report(args.report)
        return 0

    fh = open(args.out, "a", encoding="utf-8")
    Handler.out_fh = fh
    Handler.quiet = args.quiet
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"OTLP sink listening on http://{args.host}:{args.port} -> {args.out}")
    print("Set OTEL_EXPORTER_OTLP_ENDPOINT to that URL and "
          "OTEL_EXPORTER_OTLP_PROTOCOL=http/json, then start claude.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

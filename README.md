# ccaudit

**Find out where your Claude Code quota actually went.**

Four small Python scripts, no dependencies, no network, nothing leaves your
machine. Works on macOS and Linux with the Python that's already installed.

```bash
git clone <your-repo> && cd ccaudit
./ccaudit            # analyses the last 7 days, opens an HTML report
```

<!-- screenshot of report.html goes here -->

---

## Why

Claude Code meters a rolling 5-hour window and a separate weekly cap, and
neither is exposed as a token counter. When usage drains faster than it used
to, there's no built-in way to see *what* drained it. These tools reconstruct
it from data already on your disk.

They answer four questions:

| Question | Tool |
|---|---|
| How many tokens, which model, when? | `ccaudit.py` |
| Which *tool* put those tokens in my context? | `ccaudit.py --by tool` |
| Does the server's counter match my actual spend? | `ccwatch.py` |
| What is happening right now, request by request? | `otel_sink.py` |

---

## `ccaudit.py` — retroactive analysis

Claude Code writes a JSONL transcript per session under
`~/.claude/projects/<slug>/<session-id>.jsonl`. Every assistant entry carries
the API `usage` block: input, output, cache-read and cache-creation tokens,
plus the model and request id. This reads them.

```bash
python3 ccaudit.py                        # last 7 days, text summary
python3 ccaudit.py --days 30 --html report.html
python3 ccaudit.py --doctor               # automated anomaly checks
python3 ccaudit.py --by tool --top 25     # tool attribution
python3 ccaudit.py --by detail            # per-file, per-command, per-host
python3 ccaudit.py --bug-report bug.md    # redacted, safe to post publicly
python3 ccaudit.py --csv usage.csv        # one row per API request
```

### The two numbers that matter

**Injected** — tokens a tool's output added to your context.
**Amplified** — injected × how many later API calls re-sent it.

That second number is the one people miss. A 20K-token file read on call 3 of
a 200-call session isn't 20K tokens; it's 20K re-sent 197 times, ≈4M tokens of
cache reads. One `Read` of a minified bundle can dominate an entire week.
Sample output:

```
tool                                 calls   injected   amplified   share   err
-------------------------------------------------------------------------------
Read                                    73      1.59M      55.76M   41.5%     5
MCP postgres/query                      40    880.00K      23.28M   17.3%     4
Agent:explore                           36    540.00K      16.66M   12.4%     2
Bash                                    38    456.00K      15.22M   11.3%     2
WebFetch                                38    228.00K       7.49M    5.6%     4
Skill:pptx                              31     77.50K       2.95M    2.2%     2
```

Web browsing, MCP servers, skills and subagents are all broken out separately,
and `--by detail` names the specific file, command or host responsible.

### A correctness note

One API response is persisted as **one transcript entry per content block**,
and every one of those entries repeats the same `usage` object. Summing lines
naively inflates totals — typically by 30–50%. `ccaudit` deduplicates by
request id and tells you how many it collapsed. If another tool has given you
a number that looks impossible, this is usually why.

### The HTML report

`--html report.html` produces one self-contained file: no CDN, no JavaScript,
no build step. Daily stacked bars, an hour-by-hour heatmap (the fastest way to
spot usage while you were asleep), the rolling 5-hour load curve, model share,
tool attribution, and the automated findings at the top.

---

## `ccwatch.py` — server counters vs. your actual spend

**This is the one that can settle whether something is broken.**

Claude subscriptions expose a usage endpoint reporting a rolling five-hour
utilization and a seven-day utilization. `ccwatch` polls it, records each
reading, and lines those readings up against what your own transcripts say you
spent in the same interval.

```bash
python3 ccwatch.py --poll        # leave running
python3 ccwatch.py --analyze     # after a few hours
```

```
interval (local)           Δ5h     Δ7d   reqs     tokens   weight  note
-----------------------------------------------------------------------
08-10 21:09 →21:39       +2.00   +1.20      0          0     0.00  PHANTOM: counter rose with zero local requests
08-10 21:39 →22:09       +1.85   +0.90    142      3.10M     4.21
```

If the counter climbs while your transcripts show no requests, that's phantom
usage and you have a timestamped series to prove it. If it climbs in step with
your spend, metering is fine and the cause is elsewhere — model choice, context
size, or tool output. `--analyze` also reports your burn rate as "100% in N
hours of this workload", which is the number worth comparing against others.

**Caveats, please read.** The endpoint is undocumented; it can change or vanish,
and a failure here means the tool broke, not that anything is wrong with your
account. Independent monitoring has reported the seven-day counter resetting on
a ~72-hour cadence rather than weekly — record what you see rather than assuming.
`ccwatch` reads your existing OAuth token from local storage (env var,
`~/.claude/.credentials.json`, or the macOS Keychain) to call *your own*
account's endpoint. The token is sent only to claude.ai over HTTPS, is never
logged and never printed. Read the code. If you'd rather not, export it
yourself and use `--token-env`.

---

## `otel_sink.py` — live, per-request capture

Claude Code has a built-in OpenTelemetry exporter that emits an event for every
API request. It normally wants a collector stack; this script *is* the
collector, in ~200 lines of stdlib.

Terminal 1:
```bash
python3 otel_sink.py --out ~/claude-telemetry.jsonl
```

Terminal 2, then start Claude Code from that shell:
```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_LOGS_EXPORT_INTERVAL=2000
claude
```

```
14:22:07  claude-opus-5      in=     12 out=  1240 cw=   8100 cr=  184320 $0.4821  repl_main_thread
14:22:31  claude-haiku-4-5   in=      8 out=   210 cw=      0 cr=    3100 $0.0009  subagent  agent=explore
14:23:02  COMPACT (auto) 168000 -> 41000 tokens
14:24:11  ERROR 429 attempt=3 rate_limit_error
```

Three things this has that the transcripts don't:

- `query_source` — `repl_main_thread`, `subagent`, `compact`, auxiliary.
  Auto-compaction is itself a full-context request; ten per session is a real
  line item.
- Retry counts. `CLAUDE_CODE_MAX_RETRIES` defaults to 10, so one transient
  failure can mean 11 attempts.
- Per-request `cost_usd` and `duration_ms`.

Add `OTEL_LOG_RAW_API_BODIES=file:$HOME/claude-bodies` to capture complete
request/response JSON per request — the full "intercept everything" view. Those
files contain your entire conversation history and any file contents Claude
read, so only on a machine you control.

---

## Before concluding it's a bug

- Run `/usage` in Claude Code. There is no *daily* limit — it's a rolling
  5-hour window and a separate weekly cap, and they're different problems.
- The quota is shared across Claude Code, the Claude apps and Cowork. Chat
  usage drains the same bucket.
- "Server is temporarily limiting requests" is capacity on Anthropic's side,
  not your usage limit, and costs you nothing.
- Premium-tier models draw down the shared cap far faster per token. Check the
  model breakdown before assuming the meter is wrong.
- `/context` shows what's in your window right now.

If `--doctor` flags repeated usage blocks, off-hours traffic, or a session with
400+ calls — or if `ccwatch --analyze` finds phantom intervals — that's worth
reporting. `--bug-report` writes a markdown summary with no prompts, file
paths, file contents, command arguments or account identifiers in it. File at
[anthropics/claude-code](https://github.com/anthropics/claude-code/issues).

## Caveats

The transcript format is internal to Claude Code and changes between releases,
so the parser is defensive and reports what it couldn't read. If `--doctor`
says a large share of lines were unparsed, run `--dump-sample` and check the
field names. The `MODEL_RATES` table at the top of `ccaudit.py` is a relative
weight proxy, not a bill; subscription limits meter compute, not those dollar
figures. Edit it freely.

## Contributing

Issues and PRs welcome. Useful directions: a `--watch` live TUI, Windows paths,
aggregating anonymised `--bug-report` output across users to spot patterns no
single person can see, and better token estimation than `len/4` for tool
results.

MIT licensed.

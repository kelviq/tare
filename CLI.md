# Using tare from the terminal

Everything the skill does is three plain Python scripts you can run yourself.
This page is for developers, tinkerers, and anyone scripting the tools
directly — if you just want answers, install the skill and ask Claude Code
instead (see the [README](README.md)).

No dependencies, no network, Python 3.9+. `forensics.py` reads the CSV that
`ccaudit.py --csv` writes, so most multi-step recipes start with:

```bash
python3 skills/tare/ccaudit.py --days 30 --csv usage.csv
```

## Recipes

### Routine

**Weekly check-in.** One command, opens a self-contained HTML report with
findings at the top:

```bash
./ccaudit
```

**At-a-glance panel** — one screen, like `/usage` but from the transcripts,
with attribution by project and tool, the current 5-hour window load, and
the top finding as a tip:

```bash
python3 skills/tare/ccaudit.py --days 1 --panel
```

**Monthly deep report:**

```bash
python3 skills/tare/ccaudit.py --days 30 --doctor --html report.html
```

**"Is it safe to start a heavy session right now?"** The 5-hour window is
rolling — work from four hours ago still counts. Check the load before
starting something big:

```bash
python3 skills/tare/forensics.py usage.csv --at 2026-08-11T17:30
```

```
at 2026-08-11 17:30: 19.2 from 56 requests (7% of your observed peak)
```

At 7%, go ahead. At 80%, your morning is still in the window — starting a
heavy refactor now is how you hit a limit ten minutes in.

**A daily text summary on a schedule** (no HTML, cron-friendly):

```bash
python3 skills/tare/ccaudit.py --days 1 > ~/claude-usage-today.txt
```

### When you hit a limit

**5-hour limit autopsy.** Give `--at` the time you got locked out:

```bash
python3 skills/tare/forensics.py usage.csv --at 2026-08-10T19:46
```

It reports how full the window already was at that moment and when the oldest
still-counting request ages out. A window at 60%+ before you typed anything
is the single most common "impossible" limit hit — expected rolling-window
behaviour, not a fault.

**Weekly cap autopsy.** Same tool, wider window:

```bash
python3 skills/tare/forensics.py usage.csv --window 168
```

The peak of the 168-hour curve and when it happened tells you which stretch
of days actually filled the weekly cap.

**Autopsy one specific day:**

```bash
python3 skills/tare/forensics.py usage.csv --day 2026-08-10
```

**Find a runaway session.** An agent loop that didn't terminate shows up as
one session with hundreds of calls:

```bash
python3 skills/tare/ccaudit.py --days 7 --by session --top 10
```

`--doctor` flags any session over 400 calls automatically. Then deep-dive
it — a prefix of the id is enough:

```bash
python3 skills/tare/forensics.py usage.csv --session 2d4f21e3
```

You get the session's story: request timeline, how its context grew, idle
gaps, and — often the punchline — what resuming it after the prompt cache
expired cost compared to starting fresh.

**Detect automation you forgot about.** Scheduled jobs, SDK scripts, CI, a
file watcher — anything invoking Claude Code programmatically shows a
distinctive shape: many short sessions, running in parallel. `forensics.py`
prints an explicit automation signature when it sees it:

```
sessions            : 1,555
median requests/sess: 4
median duration     : 0.5 min
peak concurrent     : 51 at 2026-08-10 19:54

  >> AUTOMATION SIGNATURE <<
```

The hour-by-hour heatmap in the HTML report shows the same thing visually —
blocks of usage while you were asleep need explaining.

### Cutting consumption

**Find the tool that fills your context:**

```bash
python3 skills/tare/ccaudit.py --days 7 --by tool --top 20
```

**Then find the specific file, command or host:**

```bash
python3 skills/tare/ccaudit.py --days 7 --by detail --top 20
```

A minified bundle you `Read` once, early, in a long session can dominate a
week. The fix is usually one of: don't read generated files, `/clear` between
unrelated tasks, or do the heavy reading in a subagent so it doesn't stay in
the main context.

**Count what failed calls cost you.** The `err` column in `--by tool`: failed
tool calls still cost a full round trip and are usually retried with *more*
context. A tool erroring 20%+ of the time is a configuration problem with a
token bill attached.

**Measure an MCP server's footprint.** Every MCP server appears as its own
`MCP server/tool` row in `--by tool`, with injected and amplified tokens. If
you maintain an MCP server, this is your tool's context cost as your users
experience it — a chatty schema dump on every call shows up immediately.

**Measure a skill's or subagent's cost.** Skills appear as `Skill:` rows and
subagents as `Agent:` rows. `--doctor` also warns when subagent sidechains
exceed 40% of total weight — each subagent starts with its own copy of the
context, so a task that spawns five costs far more than it appears to.

**Check your model mix:**

```bash
python3 skills/tare/ccaudit.py --days 30 --by model
```

Premium-tier models draw down the shared cap several times faster per token.
Models marked `*` have no published rate and are priced with a placeholder —
compare those by requests and tokens, not by weight share.

**A/B test a habit.** Adopt one change — say, `/clear` between unrelated
tasks — and compare the daily weight table for the week before and after:

```bash
python3 skills/tare/ccaudit.py --days 14 --by day
```

Cache reads as a share of your total is the number that should move.

**Did a Claude Code release change your burn?**

```bash
python3 skills/tare/ccaudit.py --days 30 --by version
```

Weight per request by version, on your own workload — evidence, either way,
when a release feels hungrier.

**Estimate what your usage would cost on the API.** The `weight` column is a
USD-equivalent proxy computed from the editable `MODEL_RATES` table at the
top of `skills/tare/ccaudit.py`. It is not a bill — subscription limits meter compute,
not dollars — but it's the right order of magnitude for "should I be on the
API instead?"

**Measure a batch job before scaling it.** Running an Agent SDK script, a
test harness, or anything that spawns sessions in bulk? Run it once, then:

```bash
python3 skills/tare/ccaudit.py --days 1 --by project
python3 skills/tare/forensics.py usage.csv --day 2026-08-11
```

Every fresh session pays full cache-creation on its first turn, at 1.25× the
input rate — a swarm of short sessions costs several times what one long
session doing the same work costs. Know the per-run price before you put it
on a schedule.

### Sharing and comparing

**Ask for help without leaking anything.** Your transcripts contain your
prompts and your files; this contains neither:

```bash
python3 skills/tare/ccaudit.py --days 30 --share share.md
```

A 5KB markdown summary — totals, daily table, model split, tool names,
findings. No prompts, file paths, file contents, command arguments, session
ids or account identifiers. Post it, send it to a colleague, ask "what am I
missing?"

**Compare with a teammate.** Two people run the same command and diff the
tables: requests per day, cache-read share, tool mix. "Is 200M tokens a day
a lot?" is unanswerable alone and obvious side by side.

**Help someone remotely.** Have them run the command above and send you the
file. Everything above that doesn't need `--at` precision can be read
straight off it.

### Power users

**Export everything to a spreadsheet or pandas.** One row per API request:

```bash
python3 skills/tare/ccaudit.py --days 30 --csv usage.csv
```

Columns: timestamp, model, project, session, request id, sidechain flag,
Claude Code version, stop reason, all four token counts, weight.

**Another machine or a custom config dir:**

```bash
python3 skills/tare/ccaudit.py --dir /path/to/.claude/projects
```

Copy a `projects` directory from a VM or second machine and analyse it
anywhere — the tools never modify it.

**Fix the timezone** when analysing data from a machine set to UTC:

```bash
python3 skills/tare/ccaudit.py --days 7 --tz 5.5
```

**Study the transcript format** if you're building your own tooling:

```bash
python3 skills/tare/ccaudit.py --dump-sample
```

Prints one complete usage-bearing entry. And read the correctness note below
before summing anything.

## The two numbers that matter

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

## A correctness note

One API response is persisted as **one transcript entry per content block**,
and every one of those entries repeats the same `usage` object. Summing lines
naively inflates totals — by **86%** on the data this tool was developed
against. `ccaudit` deduplicates by request id and tells you how many entries
it collapsed. If another tool has given you a number that looks impossible,
this is usually why.

## `ccwatch.py` — server counters vs. your actual spend (experimental)

Polls the subscription usage endpoint and lines its readings up against what
your transcripts say you spent in the same interval.

```bash
python3 ccwatch.py --poll        # leave running
python3 ccwatch.py --analyze     # after a few hours
```

**Caveats, please read.** The endpoint is undocumented and this script is the
least-tested thing here: treat a failure as the tool breaking, never as
evidence about your account. Independent monitoring has reported the
seven-day counter resetting on a ~72-hour cadence rather than weekly — record
what you see rather than assuming. `ccwatch` reads your existing OAuth token
from local storage to call *your own* account's endpoint; the token is sent
only to claude.ai over HTTPS and is never logged or printed. Read the code.
If you'd rather not, export the token yourself and use `--token-env`.

## `otel_sink.py` — live, per-request capture

Claude Code has a built-in OpenTelemetry exporter that emits an event for
every API request. It normally wants a collector stack; this script *is* the
collector, in ~200 lines of stdlib, bound to localhost only.

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

Three things this has that the transcripts don't: `query_source` (main
thread, subagent, compaction — auto-compaction is itself a full-context
request), retry counts (`CLAUDE_CODE_MAX_RETRIES` defaults to 10, so one
transient failure can mean 11 attempts), and per-request `cost_usd` and
`duration_ms`.

## Caveats

The transcript format is internal to Claude Code and changes between
releases, so the parser is defensive and reports what it couldn't read. If
`--doctor` says a large share of lines were unparsed, run `--dump-sample` and
check the field names. The `MODEL_RATES` table at the top of `skills/tare/ccaudit.py` is
a relative weight proxy, not a bill; subscription limits meter compute, not
those dollar figures. Edit it freely. Token sizing of tool results is
`len/4` — comparative, not exact.

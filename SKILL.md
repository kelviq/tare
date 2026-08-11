---
name: tare
description: Diagnose Claude Code usage limits — find out where tokens actually went and why a 5-hour or weekly limit was hit. Use this whenever the user mentions hitting a usage limit, being rate limited, burning through their quota, running out of Claude Code usage, unexpected or suspicious token consumption, "why am I hitting limits", phantom usage, or asks to audit, analyze, or track their Claude Code token usage. Also use when the user suspects a billing or metering bug, wonders which tool or model is eating their quota, or wants a usage report. Trigger even if they don't use the word "tare" or "audit" — a complaint about limits is enough.
---

# tare

Diagnose Claude Code usage from the session transcripts already on disk.

The scripts produce numbers. Your job is the diagnosis. A user asking "why did
I hit the limit" wants a cause and a fix, not a table — so lead with the
finding, then show the evidence for it.

## Scripts

All in `scripts/`, stdlib only, no network:

| Script | Purpose |
|---|---|
| `ccaudit.py` | Parses `~/.claude/projects/**/*.jsonl`. Text summary, HTML report, CSV, redacted bug report. |
| `forensics.py` | Reads the CSV. Finds spikes, session shape, concurrency, rolling-window load. |
| `ccreport.py` | SVG rendering, imported by `ccaudit.py`. Not run directly. |

## Step 1 — verify the parser before trusting any number

The transcript format is internal to Claude Code and changes between releases.
Run this first, every time:

```bash
python3 scripts/ccaudit.py --dump-sample
```

Check that `requestId`, `message.usage`, `message.model` and `timestamp` are
present and shaped as the parser expects. If they aren't, stop and tell the
user the parser needs updating — do not present numbers you don't trust.

Then run the audit and check the dedupe count in the header:

```bash
python3 scripts/ccaudit.py --days 30 --doctor --csv /tmp/usage.csv
```

One API response is written to the transcript as one entry *per content block*,
each repeating the same usage object. If "duplicate entries collapsed" is zero,
the dedupe key isn't matching and totals may be inflated by 30-80%. Say so
rather than reporting the numbers as fact.

## Step 2 — establish what kind of problem this is

Ask the user two things if they haven't said, because the answer changes the
whole analysis:

- **Which limit** — the rolling 5-hour window, or the weekly cap? There is no
  daily limit, so if they say "daily" they almost certainly mean the 5-hour
  window. These have completely different causes.
- **Roughly when** — the date, and the hour if they know it.

Then:

```bash
python3 scripts/forensics.py /tmp/usage.csv
```

Look at the daily table for a **discontinuity**. Usage that steps up 3-10x on a
specific date is the single most informative signal available: something
changed that day, and identifying what it was usually is the answer.

## Step 3 — for a 5-hour limit, check the window

```bash
python3 scripts/forensics.py /tmp/usage.csv --day YYYY-MM-DD --at YYYY-MM-DDTHH:MM
```

The window is rolling, so it does not clear because the user walked away.
Work from earlier in the day is still counting. If the window was already at
60%+ when they resumed, a short session reaching the cap is expected behaviour
and not a fault — explain the mechanism rather than just reporting it.

If the window was nearly empty and they still hit the cap in minutes, that is
genuinely hard to explain from local data. Record it carefully and don't
explain it away.

## Step 4 — session shape is where the answer usually is

`forensics.py` reports sessions per day, median requests per session, median
duration, and peak concurrency. Read these together:

- **Many short sessions running in parallel** = something is invoking Claude
  Code programmatically. A script calling `claude -p`, the Agent SDK, a CI job,
  a batch harness. Every fresh session pays full cache-creation cost on its
  first turn, so a swarm is dramatically more expensive than one long session
  doing the same work. This is the most common cause of a sudden inexplicable
  spike, and users often don't think of it as "their" usage.
- **One session with hundreds of calls** = an agent loop that didn't terminate.
- **High cache writes relative to reads** = contexts being built rather than
  reused. Either the swarm above, or resuming a large session after the prompt
  cache expired. Writes cost roughly 12x what reads cost per token.

Check concentration too. If one project, one model, or one tool accounts for
90%+ of requests, name it — that is the lead.

## Step 5 — tool attribution

```bash
python3 scripts/ccaudit.py --days 30 --by tool --top 20
python3 scripts/ccaudit.py --days 30 --by detail --top 20
```

Two numbers per tool. **Injected** is what the tool's output added to context.
**Amplified** is that multiplied by how many later API calls re-sent it. Sort
by amplified: a 20K-token file read early in a 200-call session is ~4M tokens
of cache reads, while the same read at the end is 200K. `--by detail` names the
specific file, command or host.

Watch the error column. Failed tool calls still cost a full round trip and are
often retried with more context.

## Step 6 — report

Structure the answer like this:

1. **The finding**, in one or two sentences, first. "Something is spawning
   ~1,500 short Claude Code sessions a day in project X" beats any table.
2. **The evidence** — the specific numbers that establish it.
3. **The mechanism** — why that shape costs what it costs.
4. **What to check or change**, concretely.
5. **What you're unsure about**, honestly.

Offer the HTML report if they want to look themselves:

```bash
python3 scripts/ccaudit.py --days 30 --doctor --html report.html
```

And the redacted markdown summary if they want to file an issue. It contains no
prompts, file paths, file contents, command arguments or account identifiers:

```bash
python3 scripts/ccaudit.py --days 30 --bug-report bug.md
```

## Known false positives — don't over-report these

- **"Usage blocks repeated under >2 request ids"** fires on small auxiliary
  calls that legitimately repeat with an identical cached prefix. Only treat it
  as a retry loop if the repeated requests are individually large in `input` or
  `output`, not merely large in cached tokens.
- **"Requests between 23:00-06:00"** is meaningless for anyone who works late
  or lives across a timezone boundary from where the `--tz` default resolved.
  Confirm before calling it background activity.
- **Model weights are a proxy.** `MODEL_RATES` in `ccaudit.py` is an editable
  table, and newer models may carry placeholder rates. Never present a
  model's cost share as authoritative without checking that table first.

## Do not conclude "it's a bug" from local data alone

Local transcripts show what was sent. They cannot show what was metered. Most
apparent bugs turn out to be premium model choice, a rolling window that hadn't
cleared, or automation the user forgot was running.

To distinguish genuine phantom usage, the server counter has to be compared
against local spend over the same interval — that requires polling and is out
of scope here. If everything local looks proportionate and the user still hits
limits early, say that plainly rather than manufacturing a cause.

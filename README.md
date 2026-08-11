# tare

**Ask Claude Code where your usage went.**

You hit a usage limit and don't know why. Your quota drains faster than it
used to. You suspect something is eating tokens in the background. The
records that answer all of this are already on your computer — Claude Code
keeps a log of every request it makes. `tare` teaches Claude Code to read
its own logs, so you can just ask.

No dashboards, no commands to learn. Install once, then ask in plain
English.

*Tare: the weight of the container, subtracted to find what's inside. Most
of what a session costs is the container — context re-sent again and again —
and that's exactly what these tools subtract.*

## Install

One command, in any terminal:

```bash
git clone https://github.com/OWNER/tare ~/.claude/skills/tare
```

That's it. Nothing else to set up — no accounts, no packages, no
configuration. Claude Code picks it up automatically. (Other ways to
install, including for a whole team, are in [INSTALL.md](INSTALL.md).)

## Then just ask

Open Claude Code and ask your question the way you'd ask a person. These all
work — the words don't have to match, complaining about your limits is
enough:

**When you hit a limit**

> *Why did I hit my usage limit yesterday?*

> *I got locked out ten minutes into my evening session — how is that
> possible?*

> *Did I hit the 5-hour limit or the weekly cap?*

> *Why am I burning through my quota so much faster this week?*

**Where your tokens go**

> *Where did my tokens actually go this week?*

> *Which of my projects is eating my quota?*

> *Which model is costing me the most?*

> *What's the most expensive file Claude keeps re-reading?*

> *How much did that giant session yesterday actually cost me?*

> *Are my MCP servers adding a lot to my context?*

> *How much overhead do subagents and skills add?*

**Before you start something big**

> *How full is my 5-hour window right now?*

> *Is it safe to start a big refactor now, or should I wait for my window to
> clear?*

**Checking for things you forgot**

> *Is something running Claude Code in the background?*

> *Was Claude Code active while I was asleep?*

> *I set up an automation last month — what is it costing me?*

**Tuning your setup**

> *I started using /clear between tasks — did it actually help? Compare this
> week to last.*

> *Did the latest Claude Code update change my usage?*

> *What would my usage cost if I were paying for the API directly?*

> *What one change would save me the most?*

**Reports and sharing**

> *Make me a usage report I can open in my browser.*

> *Give me a summary I can post publicly — with nothing private in it.*

> *Export my usage to a spreadsheet.*

## What you get back

Not a wall of numbers — a cause. The answer to "why did I hit my limit
yesterday?" looks like this:

> **99% of yesterday's usage came from a tool you're running, not from you.**
> Something spawned 1,553 short Claude Code sessions in your website project
> — 9,022 requests, up to 51 sessions running at once. Your own hands-on
> work that day was 93 requests. Each fresh session rebuilds its context
> from scratch, which is the most expensive way to spend tokens...

...followed by the evidence, what to check, and what to change. And when
everything is actually fine, it says that: usage proportionate, no anomaly,
here's what's normal for you.

## What it knows that a raw token count doesn't

- **Correct totals.** Claude Code's log format repeats each API response
  several times over; naive counting inflates totals — by 86% on the data
  this was built against. tare deduplicates properly.
- **The real cost of context.** A file read early in a long session gets
  re-sent with every later message. tare charges tools for what they
  *caused*, not just what they returned — which is how one big file read
  early can quietly dominate a week.
- **The rolling window.** Limits don't reset when you walk away; work from
  four hours ago still counts. tare can tell you how full your window was at
  the exact moment you were locked out.
- **The shape of automation.** Hundreds of short parallel sessions is a
  script, not a person. tare recognises the signature and says so.

## Private by design

Everything runs on your machine and nothing leaves it. The scripts make no
network connections at all. When you ask for a shareable summary, it
contains totals, dates and tool names only — no prompts, no file paths or
contents, no commands, no session or account identifiers — so you can post
it publicly or send it to a colleague and ask "what am I missing?"

## Requirements

- Claude Code on macOS or Linux
- Python 3.9+ — already present on every Mac; no packages to install
- Currently reads Claude Code's logs only, not other coding agents'

## For developers

Everything the skill does, you can also do by hand: three dependency-free
Python scripts with recipes for scripting, cron, CSV export, live
per-request telemetry and more — see [CLI.md](CLI.md).

Issues and PRs welcome. Useful directions: a live TUI, Windows paths,
aggregating anonymised summaries across users to spot patterns no single
person can see, and better token estimation for tool results.

MIT licensed.

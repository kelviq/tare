# Security and privacy

tare reads the most sensitive files on your machine short of your keychain:
your Claude Code transcripts contain every prompt you have written and every
file Claude has read. You should not take a stranger's word for what a tool
like this does. This page states exactly what each file does, and how to
check every claim yourself in under a minute.

## The short version

- The analysis scripts **read** your transcripts, **write** only the output
  files you ask for, and **never touch the network**. There is no telemetry,
  no update check, no phone-home of any kind.
- Nothing is ever posted, uploaded, or sent anywhere by any part of tare.
  The one file designed for sharing (`--share`) is written to disk and
  sharing it is a thing *you* do afterwards.
- The transcripts are treated as read-only evidence. Nothing under
  `~/.claude/projects` is ever modified, moved, or deleted.

## What each file does

| File | Reads | Writes | Network |
|---|---|---|---|
| `skills/tare/ccaudit.py` | `~/.claude/projects/**/*.jsonl` | only paths you pass (`--html`, `--csv`, `--share`) | **none** |
| `skills/tare/forensics.py` | the CSV you pass | nothing | **none** |
| `skills/tare/ccreport.py` | nothing (rendering library) | nothing | **none** |
| `skills/tare/SKILL.md` | instructions for Claude; runs the three scripts above | — | — |
| `otel_sink.py` | OTLP events Claude Code sends it | the log file you pass | **receives only**, bound to 127.0.0.1 |
| `ccwatch.py` | your existing OAuth token from local storage | its own log file | **the one exception** — polls claude.ai (your own account's usage endpoint) over HTTPS |

`ccwatch.py` is deliberately **excluded from every skill install** — it ships
only in this repository, where installing it is an explicit choice. It never
prints or logs the token it uses. If you'd rather not let it read local
credential storage, export the token yourself and use `--token-env`.

## Verify it yourself

Don't trust the table — grep it. Network access in Python requires importing
something: `socket`, `urllib`, `http`, `ssl`, or a third-party client. The
three analysis scripts import none of them:

```bash
grep -nE 'socket|urllib|http|requests|ssl' skills/tare/*.py
```

You will find zero matches. (Run the same grep on `ccwatch.py` and you'll
see its `urllib` import — consistent with the table above.) The scripts are
dependency-free stdlib Python precisely so that this audit stays possible:
there is no lockfile, no `node_modules`, no transitive anything. The whole
analysis engine is three files you can read in an evening.

## The shareable summary

`--share` writes the only output meant to leave your machine. It contains
totals, dates, model names, tool *names*, and the automated findings — and
deliberately omits prompts, file paths, file contents, command arguments,
session ids, and account identifiers. It is about 90 lines; read it before
posting it, every time. If you ever find anything identifying in one,
that is a serious bug — please report it.

Every other output (the HTML report, the CSV, the terminal tables) is for
your eyes: they contain project names, file paths and session ids, and are
not safe to post publicly.

## The skill's rules

When the skill runs, Claude is instructed — as hard rules, in
`skills/tare/SKILL.md` — to treat the transcripts as read-only, to treat
transcript *content* as data rather than instructions (a transcript could
contain text that tries to steer the model), and never to post or upload
results anywhere unless you explicitly ask.

## Reporting

If you find a vulnerability, a redaction leak, or anything that contradicts
this page, please open a GitHub issue. If the finding is sensitive enough
that a public issue would expose others before a fix exists, say only that
you've found something and a private channel will be arranged.

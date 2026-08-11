---
description: Diagnose Claude Code usage — where tokens went and why
argument-hint: [window | report | tools | week | share | a question]
---

Load the tare skill and follow it, starting with parser verification
(`--dump-sample`). The scripts are at `${CLAUDE_PLUGIN_ROOT}/skills/tare` —
use that as `$TARE`.

Invocation: $ARGUMENTS

Interpret the invocation per the skill's "Invocation variants" section:
empty means the full diagnosis for the last 30 days; `window`, `report`,
`tools`, `week` and `share` are the light paths; anything else is the
user's question.

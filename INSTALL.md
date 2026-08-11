# Installing tare

## Personal skill (simplest — works immediately)

The repository root is the skill: `SKILL.md` sits next to the scripts it
runs. So installing is one step:

```bash
git clone https://github.com/OWNER/tare ~/.claude/skills/tare
```

(Or copy the files by hand — `SKILL.md`, `ccaudit.py`, `ccreport.py` and
`forensics.py` must end up in the same directory.)

Claude Code watches `~/.claude/skills/` and picks up valid `SKILL.md` files
automatically — no restart needed. Type `/` and confirm `tare` appears.
Updating is `git pull` in that directory.

Two ways to use it:

- **Directly**: type `/tare`
- **By description**: say "why did I hit my usage limit yesterday" and Claude
  loads the skill on its own, because the description names that situation.

The second path is the one that matters. At startup Claude reads only the name
and description of each installed skill, so the description *is* the trigger.

## Project skill

`.claude/skills/tare/` inside a repo, committed to version control. Same
structure. Travels with the codebase for a team.

## Distributing it as a plugin

For other people, ship a plugin from a GitHub repo. They then run:

```
/plugin marketplace add <your-github-user>/<repo>
/plugin install tare@<marketplace-name>
```

Plugin layout:

```
tare-plugin/
├── .claude-plugin/
│   └── plugin.json          # name, version, description, author
├── skills/
│   └── tare/
│       ├── SKILL.md
│       ├── ccaudit.py
│       ├── ccreport.py
│       └── forensics.py
└── commands/
    └── tare.md              # see the note below
```

**Add the `commands/tare.md` file even though it looks redundant.** There is a
known Claude Code issue where plugin `skills/` directories don't reliably
register as slash commands depending on how the plugin was loaded, while
`commands/` always works. A one-line command file that points at the skill
costs nothing and avoids the failure mode.

Test locally before publishing — this loads the plugin for one session without
installing anything:

```bash
claude --plugin-dir ./tare-plugin
```

## Verifying it works

Ask Claude "why did I hit my Claude Code limit yesterday" in a fresh session.
If it runs `--dump-sample` first rather than jumping to the report, the skill
is being followed. If it improvises its own analysis, the description needs to
be more specific about the trigger situation.

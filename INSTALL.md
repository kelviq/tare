# Installing tare

The repository is laid out as a Claude Code plugin, with the skill and its
scripts self-contained under `skills/tare/`. That one directory is the whole
product — every install method below just gets it in front of Claude Code.

## With the skills CLI (simplest)

```bash
npx skills add kelviq/tare -g -y --copy --agent claude-code
```

The flags matter: `--agent claude-code` targets Claude Code explicitly,
`-g` installs user-level so it works in every project, `--copy` puts real
files in `~/.claude/skills/tare` rather than a symlink, and `-y` skips
prompts. Then start a **new** Claude Code session — skills are read at
session start.

Note: tare only reads Claude Code's own logs under `~/.claude/projects` —
installed into any other agent it will find nothing.

### Troubleshooting

**Installed, but Claude doesn't know the skill?** Check whether the files
actually reached Claude Code:

```bash
ls ~/.claude/skills/tare
```

If that's empty but `~/.agents/skills/tare` exists, the installer put the
files in its shared location without linking them to Claude Code — this
happens on machines where `~/.claude/skills` doesn't exist yet, so Claude
Code isn't detected as an installed agent. Fix it by re-running the
install command above (the explicit `--agent claude-code` flag targets it
regardless of detection), or copy the files yourself:

```bash
mkdir -p ~/.claude/skills && cp -r ~/.agents/skills/tare ~/.claude/skills/tare
```

Either way, the skill loads in the **next** session, not the current one.

## Personal skill, by hand

```bash
git clone https://github.com/kelviq/tare /tmp/tare &&
  cp -r /tmp/tare/skills/tare ~/.claude/skills/tare
```

Claude Code watches `~/.claude/skills/` and picks up valid `SKILL.md` files
automatically — no restart needed. Type `/` and confirm `tare` appears.

Two ways to use it:

- **By description**: say "why did I hit my usage limit yesterday" — Claude
  loads the skill on its own, because the description names that situation.
- **Directly**: type `/tare`

The first path is the one that matters. At startup Claude reads only the
name and description of each installed skill, so the description *is* the
trigger — no command needed.

## Project skill

Copy `skills/tare/` to `.claude/skills/tare/` inside a repo and commit it.
Travels with the codebase for a team.

## As a plugin

This repository is itself a plugin marketplace:

```
/plugin marketplace add kelviq/tare
/plugin install tare@tare
```

However installed, `/tare` is one command with variants — `window`,
`report [days]`, `tools [days]`, `week`, `share [days]`, or any question as
the argument. The full table is in the [README](README.md); plain-English
questions work identically without it.

The plugin also ships `commands/tare.md` even though it mirrors the skill:
there is a known Claude Code issue where plugin `skills/` directories don't
reliably register as slash commands depending on how the plugin was loaded,
while `commands/` always works. Depending on load path it may appear
namespaced (`/tare:tare`) — type `/tare` and let completion show what
registered. Skill-by-description triggering is unaffected either way.

Test locally before publishing — this loads the plugin for one session
without installing anything:

```bash
claude --plugin-dir .
```

## Verifying it works

Ask Claude "why did I hit my Claude Code limit yesterday" in a fresh session.
If it runs `--dump-sample` first rather than jumping to the report, the skill
is being followed. If it improvises its own analysis, the description needs
to be more specific about the trigger situation.

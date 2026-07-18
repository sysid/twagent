# Quick Start

Get your first agent provisioned in under 10 minutes.

## Prerequisites

- Python 3.13+
- `uv` (or `pip`) for install
- At least one AI coding agent already installed (Claude Code is assumed
  in the examples; substitute paths for Copilot CLI / Pi as needed)

## Step 1 — Install

```bash
uv tool install twagent
# or, from a clone:
make install
```

Verify:

```bash
twagent --version
```

## Step 2 — Bootstrap the config

```bash
twagent edit --init
```

This creates `~/.config/twagent/config.toml` with a commented starter and
opens it in `$EDITOR`. The starter wires up one agent (claude-code), one
skill placeholder, and one profile (`minimal`).

## Step 3 — Point at a real skill

Find a skill directory you already have (e.g. a `bkmr-memory` skill at
`~/dev/skills/bkmr-memory`) and update the stub:

```toml
[skills.bkmr-memory]
source = "~/dev/skills/bkmr-memory"
```

If you don't have one yet, create a placeholder:

```bash
mkdir -p ~/dev/skills/bkmr-memory
echo "# bkmr-memory" > ~/dev/skills/bkmr-memory/SKILL.md
```

## Step 4 — Preview, then deploy

Preview first (writes nothing, masks secrets):

```bash
twagent apply --global --dry-run
```

You should see a plan: a symlink for `bkmr-memory`, an instruction render,
and (if you uncommented one) an MCP file compile.

Do it:

```bash
twagent apply --global
```

✓ **Verify** Claude Code now sees the skill:

```bash
ls -la ~/.claude/skills/
# expect: bkmr-memory -> /Users/you/dev/skills/bkmr-memory
```

🎉 You've just deployed your first artifact. Every change you make to
`config.toml` from here is one `twagent apply --global` away from being
live across every agent that supports it.

## What just happened

| Step | What twagent did |
|---|---|
| Read config | Parsed `~/.config/twagent/config.toml`, validated schema v4. |
| Resolved profile | `claude-code`'s `global_profile = "minimal"` → its skill list. |
| Materialised | Created `~/.claude/skills/bkmr-memory` as a symlink. |
| Rendered | Filled in `~/.claude/CLAUDE.md` from the Jinja template with your `[common.vars]` overlaid by `[agents.claude-code.vars]`. |

## Common stumbles

| Symptom | Fix |
|---|---|
| `Config not found. Use --init to create.` | Run `twagent edit --init` first. |
| `templates_dir is not supported in schema_version=3` | Old config format — delete the `templates_dir` line; declare templates as `[instructions.<name>]` instead. |
| `source does not exist` warning at load | The path in a `[skills.X] source = ...` doesn't exist on disk. Either create the directory or remove the entry. |
| `[[scopes]] blocks are not supported` | Same — old v1/v2 format. Use per-agent `global_profile` instead. |

## Next

→ [Tutorial](tutorial.md) — build a realistic two-agent setup with project
overlays and MCP servers.
→ [Reference: Commands](reference/commands.md) — every command, every flag.

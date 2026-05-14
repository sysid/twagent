# twagent documentation

One canonical TOML, one CLI, every AI coding agent on your machine.

## Why twagent?

You probably have Claude Code, Copilot CLI, Pi (or VS Code, opencode...) each
expecting their own files in their own places:

```
  ~/.claude/CLAUDE.md          ┐
  ~/.claude/skills/...         │
  ~/.claude.json               │   …and the equivalents for every other agent
  ~/.copilot/...               │   you use. Same skills, same MCP servers,
  ~/.pi/...                    │   three different file layouts.
                               ┘
```

Keeping them in sync by hand is the kind of toil that quietly breaks: a new
skill gets copied to two agents and forgotten on the third; an MCP token
rotates and one client keeps the old value. **twagent** is one canonical
TOML that owns the truth, plus one CLI that materialises it onto disk.

## Choose your path

| I want to... | Start here |
|---|---|
| Understand what this does | [Overview](overview.md) |
| Get my first deploy working | [Quick Start](quick-start.md) |
| Walk through a real setup end-to-end | [Tutorial](tutorial.md) |
| Look up a command or flag | [Reference: Commands](reference/commands.md) |
| Look up TOML schema details | [Reference: Configuration](reference/config.md) |
| Get a question answered | [FAQ](faq.md) |

## At a glance

```
┌──────────────────────────────────────────────────────────────┐
│   ~/.config/twagent/config.toml         ← single source       │
│                                                               │
│   [instructions]  [skills]  [subagents]  [prompts]  [servers] │
│              └──────────┬──────────┘                          │
│                      profiles                                 │
│                         │                                     │
└─────────────────────────┼─────────────────────────────────────┘
                          │
                  twagent apply
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   claude-code        copilot-cli           pi
   ~/.claude/...      ~/.copilot/...        ~/.pi/...
```

See the [Overview](overview.md) for the moving parts in detail.

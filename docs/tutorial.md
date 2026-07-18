# Tutorial — A realistic two-agent setup

| | |
|---|---|
| **Time** | ~30 minutes |
| **Difficulty** | Beginner → Intermediate |
| **Prerequisites** | Completed [Quick Start](quick-start.md) |
| **You'll learn** | Per-agent vars, profile composition, MCP secrets, project-local deploys |
| **You'll build** | A working twagent setup for Claude Code + Copilot CLI sharing one skill set and one MCP server, plus a project-specific overlay |

## What we're building

```
   one canonical TOML
   ──────────────────
          │
          ▼
   ┌──────────────────────────────────────┐
   │  profile "tw"                        │
   │    instructions:  AGENT-md           │
   │    skills:        bkmr-memory        │
   │    servers:       github             │
   └──────────────────────────────────────┘
          │
   apply --global
          │
   ┌──────┴──────┐
   ▼             ▼
 claude-code  copilot-cli
   │             │
   ~/.claude/    ~/.copilot/
```

Plus, in `~/dev/myrepo`:

```
   apply -s tw,project-foo -a claude-code   (inside ~/dev/myrepo)
          │
          ▼
   ~/dev/myrepo/.claude/skills/...
   ~/dev/myrepo/.mcp.json
```

## Step 1 — Define the agents

Open the config:

```bash
twagent edit
```

Replace the agent section with both agents:

```toml
[agents.claude-code]
capabilities   = ["instructions", "skills", "subagents", "mcp"]
mcp_format     = "claude-code"
global_profile = "tw"

[agents.claude-code.paths.global]
instructions = ["~/.claude/CLAUDE.md"]
skills       = ["~/.claude/skills"]
subagents    = ["~/.claude/agents"]
mcp          = ["~/.claude.json"]

[agents.claude-code.paths.project]
skills    = [".claude/skills"]
subagents = [".claude/agents"]
mcp       = [".mcp.json"]

[agents.claude-code.vars]
agent_name = "Claude"
extra_instructions = ["Prefer terse output"]

[agents.copilot-cli]
capabilities   = ["instructions", "skills", "mcp"]
mcp_format     = "copilot-cli"
global_profile = "tw"

[agents.copilot-cli.paths.global]
instructions = ["~/.copilot/copilot-instructions.md"]
skills       = ["~/.copilot/skills"]
mcp          = ["~/.copilot/mcp-config.json"]

[agents.copilot-cli.paths.project]
skills = [".github/skills"]
mcp    = [".github/copilot/mcp.json"]

[agents.copilot-cli.vars]
agent_name = "Copilot"
extra_instructions = []
```

**What's happening:**

- Both agents share `global_profile = "tw"`, so a single `apply --global`
  populates both.
- `paths.global` is where `apply --global` writes; `paths.project` is what
  `apply -s ... -a <agent>` joins under the current directory.
- `mcp_format` picks the per-agent translator — claude-code writes a
  different JSON shape than copilot-cli, but you only write one
  `[servers.<name>]` block.

## Step 2 — A shared instruction template

Create the template directory and a Jinja file:

```bash
mkdir -p ~/.config/twagent/templates
$EDITOR ~/.config/twagent/templates/AGENT.md.j2
```

Paste:

```jinja
# {{ agent_name }} instructions for {{ user_name }}

Reach me at {{ work_email }}.
{% for note in extra_instructions %}
- {{ note }}
{% endfor %}
```

Register it in the config:

```toml
[common.vars]
user_name  = "Tom"
work_email = "tom@example.com"

[instructions.AGENT-md]
source      = "~/.config/twagent/templates/AGENT.md.j2"
description = "Default instruction template"
```

**Why this matters:** the same template renders differently per agent
because `[agents.<id>.vars]` overlays `[common.vars]`. `Claude` lands in
`~/.claude/CLAUDE.md`; `Copilot` lands in `~/.copilot/copilot-instructions.md`.
One template, two rendered files.

## Step 3 — Add a skill and an MCP server

```toml
[skills.bkmr-memory]
source      = "~/dev/skills/bkmr-memory"
description = "Persistent memory via the bkmr CLI"

[servers.github]
type    = "stdio"
command = "npx"
args    = ["-y", "@modelcontextprotocol/server-github"]
env     = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }
```

`twagent` preserves `${GITHUB_TOKEN}` in the generated file. Claude Code and
Copilot resolve it from the environment that launches the agent; Codex receives
the equivalent `env_vars = ["GITHUB_TOKEN"]`. Export the variable before
starting the agent. Defaults such as `${GITHUB_TOKEN:-XXX}` are rejected.

## Step 4 — Wire it into a profile

```toml
[profiles.tw]
description  = "Tom's default loadout"
instructions = ["AGENT-md"]
skills       = ["bkmr-memory"]
servers      = ["github"]
```

## Step 5 — Preview and deploy

```bash
twagent apply --global --dry-run
```

Inspect the plan — it should list:

- 2 instruction renders (one per agent)
- 2 skill symlinks (one per agent's skills dir)
- 2 MCP file compiles (each in the agent-specific JSON shape)

Then deploy:

```bash
twagent apply --global
twagent status            # confirm both agents are populated
twagent diff              # should show no drift
```

## Checkpoint ✓

- [ ] `~/.claude/CLAUDE.md` exists and starts with `# Claude instructions for Tom`
- [ ] `~/.copilot/copilot-instructions.md` exists and starts with `# Copilot instructions for Tom`
- [ ] `~/.claude/skills/bkmr-memory` is a symlink to your source dir
- [ ] `~/.copilot/skills/bkmr-memory` is a symlink to the same source
- [ ] `~/.claude.json` and `~/.copilot/mcp-config.json` both reference the github server

## Step 6 — A project-specific overlay

You want one repo to get an extra MCP server (a project-specific GitHub PAT,
or a different skill set). Define another profile and deploy it into the repo:

```toml
[servers.github-project]
type    = "stdio"
command = "npx"
args    = ["-y", "@modelcontextprotocol/server-github"]
env     = { GITHUB_TOKEN = "${PROJECT_GITHUB_TOKEN}" }

[profiles.project-foo]
description = "Per-repo additions for myrepo"
servers     = ["github-project"]
```

Now, inside the repo:

```bash
cd ~/dev/myrepo
twagent apply -s tw,project-foo -a claude-code -n   # preview
twagent apply -s tw,project-foo -a claude-code      # do it
```

This drops `.claude/skills/bkmr-memory` and `.mcp.json` (with **both**
servers — `tw`'s `github` plus `project-foo`'s `github-project`) into the
current directory. Commit `.mcp.json` if your team standardises on the
toolset; `.gitignore` it if individual.

## Understanding what we did

| Concept | In this tutorial |
|---|---|
| Two-layer Jinja vars | `[common.vars]` + `[agents.<id>.vars]` rendered the same template differently per agent. |
| `extends` (not shown) | `[profiles.tw]` could `extends = ["minimal"]` to inherit everything from another profile, parent-first dedup. |
| Polymorphic `--select` | `tw,project-foo` mixed two profile names; `--select tw-claude,github` would mix a profile and an artifact name. |
| Exhaustive `--select` | `-s project-foo` (servers-only) would touch ONLY the MCP file — no skill symlinks, no instruction render. |
| Per-agent `mcp_format` | One `[servers.github]` block; two different JSON shapes on disk. |

## Challenges (optional)

1. **Compose profiles via `extends`.** Make a `base` profile, then a `tw`
   profile that `extends = ["base"]` and adds extras. Run `twagent profiles`
   to see the expansion.
2. **Add Pi as a third agent.** It supports `instructions` and `skills`, but
   not `subagents` or MCP without a selected extension. twagent silently skips
   profile kinds the Pi agent does not declare.
3. **Use `extract`** to import an existing per-agent MCP file:
   `twagent extract ~/.claude.json >> ~/.config/twagent/config.toml`,
   then edit out duplicates.

## Next

→ [Reference: Commands](reference/commands.md) — every flag and exit code.
→ [Reference: Configuration](reference/config.md) — every TOML key.
→ [FAQ](faq.md) — common questions and gotchas.

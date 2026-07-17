# Overview

## What twagent is

A small CLI that owns a single canonical TOML at `~/.config/twagent/config.toml`
and materialises it onto disk in whatever shape each AI coding agent expects:

- **renders** Jinja2 instruction files (one template, per-agent variables)
- **symlinks** file artifacts — skills, subagents, prompts — into each agent's
  directory
- **compiles** MCP server configuration into each agent's specific JSON shape

## What it is not

- Not an MCP runtime. twagent writes configuration; the agent runs the server.
- Not an agent itself. It produces files; your assistant reads them.
- Not a package manager for skills. You point it at directories you already
  maintain; it doesn't fetch or version them.

## The moving parts

```
  ┌──────────────┐    ┌─────────────┐
  │  Registries  │ →  │   Profiles  │ →  apply (local | --global)
  │              │    │ (composable │        │
  │ instructions │    │   bundles)  │        ▼
  │ skills       │    │             │   per-agent paths
  │ subagents    │    │  extends... │   (global or cwd-relative)
  │ prompts      │    │             │
  │ servers      │    │             │
  └──────────────┘    └─────────────┘
```

- **Artifacts** live in five registries. Each has a globally unique `name`
  and a `source` path. They're agent-agnostic.
- **Profiles** bundle artifacts by kind. Profiles can `extend` other
  profiles — depth-first, parent-first, first-occurrence wins on duplicates.
- **Plugins** are downloaded Claude Code plugins. Register one with
  `[plugins.<name>] source = "..."`, reference it atomically from a profile
  (`plugins = ["bmw-common"]`), and twagent expands its skills/agents/prompts/
  MCP-servers into ordinary artifacts and fans them out to *every* agent.
  twagent points at the unpacked dir (it never fetches), and deliberately does
  not defer to Claude's native `/plugin` manager — the bundle is materialised
  uniformly for all agents, same as any other artifact.
- **Agents** declare which capabilities they support, where their files go
  (global *and* per-project paths), and one optional `global_profile` —
  the default loadout deployed by `apply --global`.

### The `global_profile` concept

Each agent block can set `global_profile = "<profile-name>"`. This is the
single most important field for understanding what an agent gets by default:

1. `twagent apply --global` iterates over all agents that have a `global_profile`.
2. For each, it resolves that profile's full `extends` chain (depth-first,
   parent-first, first-occurrence wins on duplicates).
3. The resulting set of instructions, skills, subagents, prompts, and servers
   is deployed to the agent's `paths.global.*` locations.

This lets you share building blocks (profiles like `core`, `pr_review`,
`web_research`) across agents while giving each agent a distinct top-level
profile that selects which subsets apply:

```toml
[agents.claude-code]
global_profile = "tw-claude"      # Claude gets everything

[agents.copilot-cli]
global_profile = "tw-copilot"     # Copilot gets the same, minus writing tools

[agents.pi]
global_profile = "tw-pi"          # Pi gets a lighter set (no BMW skills)
```

If an agent has **no** `global_profile`, `apply --global` skips it entirely.
You can still deploy to it via `--select` + `--agent`.

## The two deploy modes

| Mode | Trigger | What it writes |
|---|---|---|
| **global** | `twagent apply --global` | Each agent's `global_profile` to canonical paths (`~/.claude/...`, `~/.copilot/...`, `~/.pi/...`). |
| **local** (default) | `twagent apply --select <names>` | A CLI-supplied selection into the **current directory** via each agent's `paths.project.*`. |

`--select` is **exhaustive**: only the artifact kinds derivable from the
selection are deployed. Selecting a servers-only profile (`-s e2e-emea`)
rewrites the MCP file and nothing else — no instruction render, no skill
symlinks.

## Who twagent is for

**Perfect for** developers who run multiple AI coding agents on the same
machine and are tired of copy-pasting skill folders / MCP JSON between them,
or who want to drop project-specific instruction sets into individual repos.

**Maybe not for** users running a single agent with no plans to add another —
the value comes from one config feeding many destinations.

## Supported agents

Out of the box: **claude-code**, **copilot-cli**, **pi**, **codex**,
**vscode**, **opencode**. The list is extensible — an "agent" is just a TOML
block naming paths, capabilities, and an `mcp_format` translator. Adding a new
agent doesn't require code changes unless its MCP format is genuinely new.

**codex** is the standing example of "genuinely new": it is the only target
whose MCP config is TOML (`~/.codex/config.toml`) rather than JSON, and it
names different keys, so it carries a dedicated builder in `mcp.py`. That file
is also codex's own state file — it holds `[projects]` trust levels and `[tui]`
settings — so twagent replaces only the `mcp_servers` table and leaves the rest
untouched.

## Two patterns at a glance

**Pattern 1 — keep one machine in sync.** Edit the canonical TOML; run
`twagent apply --global`; every agent on this machine now has the same
skills, the same subagents, the same MCP servers. `twagent diff` tells you
what's drifted between config and disk.

**Pattern 2 — project-specific overlay.** `cd ~/dev/foo && twagent apply
-s project-foo -a claude-code` drops Claude-specific skills + an MCP server
into the repo as `.claude/skills/...` + `.mcp.json`. Commit it or `.gitignore`
it — twagent doesn't care.

## Next

→ [Quick Start](quick-start.md) — first deploy in ~10 minutes.
→ [Tutorial](tutorial.md) — build a real two-agent setup step by step.

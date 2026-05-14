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
- **Agents** declare which capabilities they support, where their files go
  (global *and* per-project paths), and one optional `global_profile` —
  the default loadout deployed by `apply --global`.

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

Out of the box: **claude-code**, **copilot-cli**, **pi**, **vscode**,
**opencode**. The list is extensible — an "agent" is just a TOML block
naming paths, capabilities, and an `mcp_format` translator. Adding a new
agent doesn't require code changes unless its MCP format is genuinely new.

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

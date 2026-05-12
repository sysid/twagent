<p align="left">
  <img src="doc/twagent-logo.png" alt="twagent logo" width="300">
</p>

Unified configuration framework for AI coding agents — Claude Code, Copilot CLI,
and Pi today; extensible to others. One canonical TOML; one CLI.

Replaces and supersedes two earlier tools:
- `twmcp` (MCP servers only, multiple agents)
- `devops-binx/agent/render.py` (instructions + skills, multiple agents)

## What it does

A maintainer edits a single canonical TOML at
`~/.config/twagent/config.toml` describing every:
- agent (Claude Code, Copilot CLI, Pi, …)
- file artifact (skills, subagents, prompts)
- MCP server
- profile (composable bundle)
- scope (deployment binding)

Then runs:

```sh
twagent apply
```

…and twagent renders per-agent instruction files, symlinks file artifacts into
the right per-agent directories, and compiles MCP server configuration into each
agent's expected JSON shape — for every agent in every enabled scope, in one
command.

## Quickstart

```sh
# Bootstrap an empty config
twagent edit --init

# Migrate existing per-agent MCP from disk into canonical TOML (stdout-only)
twagent extract ~/.claude.json >> ~/.config/twagent/config.toml

# Inspect before deploying
twagent agents              # resolved per-agent paths + capabilities
twagent profiles            # profiles with their expanded contents
twagent scopes              # active scopes + state
twagent apply --dry-run     # show every write/symlink/render that WOULD happen
                            # (resolved secrets are masked by default)

# Deploy
twagent apply               # bare apply = sync every enabled scope

# Continuous use
twagent diff                # see what changed
twagent doctor              # find dangling links / missing sources / drift
```

## Commands

| Command | Purpose |
|---|---|
| `apply` | Deploy resolved configuration to disk. |
| `diff` | Show pending changes between config and on-disk state. Read-only. |
| `status` / `scopes` | List active scopes + deployment summary. |
| `agents` | List agents with their resolved paths and capabilities (`--json` for machine output). |
| `profiles` | List profiles with their `extends`-expanded contents. |
| `doctor` | Health check: dangling symlinks, missing artifact sources, capability mismatches. |
| `extract <mcp.json>` | Convert an existing per-agent MCP config to canonical TOML on stdout. |
| `edit` | Open the canonical config (`--init` to create) or an agent's template (`--template <agent>`). |
| `version` | Print the installed version. |

### Global flags

`--config <path>` — alternate config location.
`-v / --verbose` — debug logging.

### `apply` flags

| Flag | Effect |
|---|---|
| `--scope <name>` | Restrict to one or more named scopes (repeatable). |
| `--agent <name>` | Restrict to one or more named agents (repeatable). |
| `--only <list>` | csv of capabilities (`instructions,skills,subagents,prompts,mcp`). |
| `--select <list>` | csv of artifact names; `none` = empty selection. Mutex with `--interactive`. |
| `--interactive` / `-i` | Open interactive picker. Mutex with `--select`. |
| `--dry-run` | Print plan, write nothing. |
| `--show-secrets` | Reveal `${VAR}` values in dry-run / diff output (masked by default). |

## Design

Full design lives in `thoughts/refactor.md` (history) and the spec under
`specs/001-twagent-unified-config/`. Headlines:

- **One canonical TOML, agent-specific deployment.** Per-agent paths, templates,
  vars, MCP-format selection all live in the same file.
- **Symlinks for file artifacts** (skills/subagents/prompts).
- **Render for instructions** (Jinja2, `StrictUndefined`).
- **Compile for MCP** (per-format translator handles agent-specific quirks).
- **Two-layer Jinja vars:** `[common.vars]` overlaid by `[agents.<id>.vars]`.
- **`${VAR:-default}` interpolation** inside MCP `env` and `headers` only.
- **Secrets masked in dry-run / diff output by default.** Opt in with
  `--show-secrets`.

## Install

```bash
make install
```

## Develop

```bash
uv sync
make test
make format
make lint
make build
```

Python 3.13+. `uv` for everything else.

## License

BSD-3-Clause.

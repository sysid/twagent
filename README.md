<p align="left">
  <img src="doc/twagent-logo.png" alt="twagent logo" width="300">
</p>

Unified configuration framework for AI coding agents — Claude Code, Copilot CLI,
and Pi today; extensible to others. **One canonical TOML, one CLI, two deploy
modes.**

Replaces and supersedes two earlier tools:
- `twmcp` (MCP servers only, multiple agents)
- `devops-binx/agent/render.py` (instructions + skills, multiple agents)

## What it does

You edit a single canonical TOML at `~/.config/twagent/config.toml` describing
every artifact you care about — instruction templates, skills, subagents,
prompts, MCP servers — plus the agents that consume them and the profiles
that bundle them. Then:

```sh
twagent apply                                    # global sync
twagent apply --here --select tw-claude          # ad-hoc local deploy
twagent apply -s e2e-emea -a copilot-cli         # swap MCP env for one agent
```

twagent renders per-agent instruction files (Jinja2), symlinks file artifacts
into the right per-agent directories, and compiles MCP server configuration
into each agent's expected JSON shape.

## Mental model

```
  ┌──────────────┐    ┌─────────────┐
  │  Registries  │ →  │   Profiles  │ →  apply (--global | --here)
  │              │    │  (composable│        │
  │ instructions │    │   bundles)  │        ▼
  │ skills       │    │             │   per-agent paths
  │ subagents    │    │  extends... │   (global or cwd-relative)
  │ prompts      │    │             │
  │ servers      │    │             │
  └──────────────┘    └─────────────┘
```

- **Artifacts** live in registries: each one has a `name` + `source` path.
- **Profiles** bundle artifacts (skills, subagents, prompts, instructions,
  servers) and compose via `extends`.
- **Agents** declare which capabilities they support and where their files go.
  Each agent has an optional `global_profile` — what bare `apply` deploys.
- **`--select`** is polymorphic: it accepts profile names AND artifact names,
  mixed. It's exhaustive — only the kinds derivable from the selection deploy.

## Quickstart

```sh
twagent edit --init               # creates a stub at ~/.config/twagent/config.toml

# Migrate existing per-agent MCP into the canonical TOML (stdout-only)
twagent extract ~/.claude.json >> ~/.config/twagent/config.toml

# Inspect
twagent agents                    # capabilities + global_profile + paths
twagent profiles                  # extends-expanded contents per profile
twagent status                    # per-agent global deployment view

# Preview, then deploy
twagent apply -n                  # dry-run, secrets masked
twagent apply                     # do it

# Maintenance
twagent diff                      # what diverges from config?
twagent doctor                    # dangling links / missing sources
```

## `apply` — non-trivial examples

The deploy command is the surface area. Two modes:

| Mode | Default | What it writes |
|---|---|---|
| `--global` (default, `-G`) | yes | Each agent's `global_profile`, to canonical paths (`~/.claude/...`, `~/.copilot/...`, `~/.pi/...`) |
| `--here` (`-H`) | no | A CLI-supplied selection, into the **current directory** via each agent's `paths.project.*` |

### Inspect first, deploy second

```sh
twagent apply -n                                # full preview, masked secrets
twagent apply -n -S                             # same, secrets revealed
twagent apply -n -a claude-code                 # preview ONE agent
twagent apply -n -a claude-code -a copilot-cli  # preview a subset (repeatable)
```

### Override the default profile, globally

When you want to swap your MCP environment "for the day" without editing
config:

```sh
twagent apply -s e2e-emea                       # swap globally (all 3 agents
                                                # with mcp capability)
twagent apply -s e2e-emea -a copilot-cli        # only copilot's MCP file
                                                # NOTHING ELSE rewritten —
                                                # no instruction render,
                                                # no skill symlinks
twagent apply -s e2e-emea,bkmr-memory           # mix: e2e MCP set + one skill
```

`--select` is **exhaustive**: kinds that aren't in the selection are not
touched. This is why `-s e2e-emea` (servers-only) doesn't render CLAUDE.md.

### Mixed profile + artifact selection

```sh
twagent apply -s tw-claude                      # the tw-claude profile in full
twagent apply -s tw-claude,tw-cucumber-to-http  # profile + an extra one-off
                                                # skill — dedup'd, profile
                                                # contents win on collision
twagent apply -s core,pr_review,e2e-us          # three profiles composed
                                                # at the CLI level (no need
                                                # to define a wrapper profile)
```

### Project-local deploys with `--here`

```sh
cd ~/dev/los/los-cha

twagent apply -H -s tw-claude,tw-cucumber-to-http -a claude-code
twagent apply -H -s tw-copilot,tw-cucumber-to-http -a copilot-cli

# Faster: just the project-specific delta
twagent apply -H -s tw-cucumber-to-http -a claude-code
```

`--here` writes under cwd via each agent's `paths.project.*`. Subdirs are
created if missing — this is an explicit user act.

### Just the instruction templates

In v3 instructions are first-class artifacts. Render only the templates:

```sh
twagent apply -s AGENT-md                       # render the AGENT-md template
                                                # to every agent's instruction
                                                # path — nothing else
twagent apply -s AGENT-md -a claude-code        # one agent's instructions only
```

### Interactive picker

```sh
twagent apply -i                                # pick from a TUI menu
                                                # of all profiles + artifacts
twagent apply -s tw-claude -i                   # picker pre-checked with
                                                # tw-claude's contents — add
                                                # or remove from there
```

`-i` cancellation (Esc) exits without deploying.

### Day-to-day flow

```sh
$EDITOR ~/.config/twagent/config.toml           # tweak something
twagent diff                                    # what would change?
twagent apply                                   # do it
```

## `apply` flags

| Long | Short | Effect |
|---|---|---|
| `--global` | `-G` | Default mode. Deploy each agent's `global_profile` to canonical paths. |
| `--here` | `-H` | Deploy `--select` set into current directory via `paths.project.*`. |
| `--agent <id>` | `-a` | Repeatable. Restrict to specific agents. |
| `--select <names>` | `-s` | csv of profile names AND/OR artifact names. `none` deploys empty. |
| `--interactive` | `-i` | Open TUI picker. `--select`, if also given, pre-checks items. |
| `--dry-run` | `-n` | Show plan, write nothing. Secrets masked unless `--show-secrets`. |
| `--show-secrets` | `-S` | Reveal `${VAR}`-resolved values in dry-run / diff output. |

## All commands

| Command | Purpose |
|---|---|
| `apply` | Deploy resolved configuration to disk. |
| `diff` | Show pending changes between config and on-disk state. Read-only. |
| `status` | Per-agent global deployment view (capabilities + `global_profile`). |
| `agents` | List agents with resolved paths and capabilities (`-j` for JSON). |
| `profiles` | List profiles with their `extends`-expanded contents. |
| `doctor` | Health check: dangling symlinks, missing sources, capability mismatches. |
| `extract <mcp.json>` | Convert an existing per-agent MCP file to canonical TOML on stdout. |
| `edit` | Open the canonical config in `$EDITOR`. `--init` creates a stub. `-t <name>` opens an instruction template. |
| `version` | Print the installed version. |

### Global flags

`--config <path>` (`-c`) — alternate config location.
`--verbose` (`-v`) — debug logging via `RichHandler` to stderr.

## Design

Headlines (v3 schema):

- **One canonical TOML.** Per-agent paths, vars, MCP-format selection,
  and `global_profile` all live in the same file.
- **Five artifact registries**: `instructions`, `skills`, `subagents`,
  `prompts`, `servers`. All names are globally unique (shadow rule).
- **Profiles bundle artifact references** by kind, and compose via `extends`
  (depth-first, parent-first, dedup'd per kind, first-occurrence wins).
- **No `[[scopes]]` blocks.** Global deployment is per-agent
  (`global_profile`); local deployment is ad-hoc CLI (`apply --here --select`).
- **Symlinks for file artifacts** (skills/subagents/prompts).
- **Render for instructions** (Jinja2, `StrictUndefined`).
- **Compile for MCP** (per-format translator handles agent-specific quirks).
- **Two-layer Jinja vars:** `[common.vars]` overlaid by `[agents.<id>.vars]`.
- **`${VAR:-default}` interpolation** inside MCP `env` and `headers` only.
- **Secrets masked in dry-run / diff output by default.** Opt in with
  `--show-secrets`.
- **`--select` is exhaustive.** Deploys ONLY the capability kinds the
  selection touches. Bare `apply` (no `--select`) deploys everything in
  the agent's `global_profile`.

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

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
twagent apply --select tw-claude                 # local deploy to cwd (default)
twagent apply --global                           # global sync
twagent apply --global -s e2e-emea -a copilot-cli  # swap MCP env for one agent
```

twagent renders per-agent instruction files (Jinja2), symlinks file artifacts
into the right per-agent directories, and compiles MCP server configuration
into each agent's expected JSON shape.

## Mental model

```
  ┌──────────────┐    ┌─────────────┐
  │  Registries  │ →  │   Profiles  │ →  apply (--here | --global)
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
  Each agent has an optional `global_profile` — what `apply --global` deploys.
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

# Preview, then deploy (--here is the default; --select is required)
twagent apply -s tw-claude -n     # dry-run, secrets masked
twagent apply -s tw-claude        # do it (local, into cwd)
twagent apply --global -n         # preview global deploy
twagent apply --global            # global deploy

# Maintenance
twagent diff                      # what diverges from config?
twagent doctor                    # dangling links / missing sources
```

## `apply` — non-trivial examples

The deploy command is the surface area. Two modes:

| Mode | Default | What it writes |
|---|---|---|
| `--here` (default, `-H`) | yes | A CLI-supplied selection, into the **current directory** via each agent's `paths.project.*` |
| `--global` (`-G`) | no | Each agent's `global_profile`, to canonical paths (`~/.claude/...`, `~/.copilot/...`, `~/.pi/...`) |

### Inspect first, deploy second

```sh
twagent apply --global -n                                # full preview, masked secrets
twagent apply --global -n -S                             # same, secrets revealed
twagent apply --global -n -a claude-code                 # preview ONE agent
twagent apply --global -n -a claude-code -a copilot-cli  # preview a subset (repeatable)
```

### Override the default profile, globally

When you want to swap your MCP environment "for the day" without editing
config:

```sh
twagent apply --global -s e2e-emea                 # swap globally (all 3 agents
                                                   # with mcp capability)
twagent apply --global -s e2e-emea -a copilot-cli  # only copilot's MCP file
                                                   # NOTHING ELSE rewritten —
                                                   # no instruction render,
                                                   # no skill symlinks
twagent apply --global -s e2e-emea,bkmr-memory     # mix: e2e MCP set + one skill
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

### Project-local deploys (the default — `--here`)

```sh
cd ~/dev/los/los-cha

twagent apply -s tw-claude,tw-cucumber-to-http -a claude-code
twagent apply -s tw-copilot,tw-cucumber-to-http -a copilot-cli

# Faster: just the project-specific delta
twagent apply -s tw-cucumber-to-http -a claude-code
```

`--here` is the default — pass `-H` only for clarity. It writes under cwd
via each agent's `paths.project.*`. Subdirs are created if missing — this
is an explicit user act.

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
twagent diff                                    # what would change globally?
twagent apply --global                          # do it (global)
```

## `apply` flags

| Long | Short | Effect |
|---|---|---|
| `--here` | `-H` | Default mode. Deploy `--select` set into current directory via `paths.project.*`. |
| `--global` | `-G` | Deploy each agent's `global_profile` to canonical paths. |
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

## Configuration (`config.toml`)

The canonical config lives at `~/.config/twagent/config.toml`. It has six top-level
sections, all keyed by name. Names are **globally unique across all artifact registries**
(the "shadow rule").

| Section | Purpose |
|---|---|
| `schema_version` | Required. Currently `3`. |
| `env_file` | Optional. Path (relative to the config) to a dotenv file used for `${VAR}` interpolation. |
| `[common.vars]` | Jinja vars shared across agents (overlaid by per-agent vars). |
| `[agents.<id>]` | An agent: capabilities, MCP format, deploy paths, vars, default global profile. |
| `[instructions.<name>]` | Jinja2 instruction template (first-class artifact in v3). |
| `[skills.<name>]` `[subagents.<name>]` `[prompts.<name>]` | File artifacts (symlinked into per-agent dirs). |
| `[servers.<name>]` | MCP server definition (compiled per `mcp_format`). |
| `[profiles.<name>]` | Bundles of artifact names; composable via `extends`. |

### Field reference

**Agent** (`[agents.<id>]`):
- `capabilities`: subset of `["instructions", "skills", "subagents", "prompts", "mcp"]`.
- `mcp_format`: translator key — `claude-code` | `copilot-cli` | `pi`. Required if `mcp` is in capabilities.
- `global_profile`: profile name deployed by `twagent apply --global`.
- `paths.global.<kind>`: list of canonical destination paths (per capability).
- `paths.project.<kind>`: list of cwd-relative destinations used by `apply --here`.
- `vars`: Jinja vars layered over `[common.vars]`.

**File artifact** (`[skills.<name>]`, `[subagents.<name>]`, `[prompts.<name>]`, `[instructions.<name>]`):
- `source`: absolute path to the file or directory.
- `description`: optional.

**Server** (`[servers.<name>]`):
- `type`: `stdio` (default) or `http`.
- stdio: `command`, `args`, `env` (supports `${VAR:-default}`).
- http: `url`, `tools`, `[servers.<name>.headers]` (supports `${VAR}`).

**Profile** (`[profiles.<name>]`):
- `extends`: list of parent profile names. Depth-first, parent-first, first-occurrence wins on collisions.
- `instructions`, `skills`, `subagents`, `prompts`, `servers`: lists of artifact names.

### Example

```toml
schema_version = 3
env_file = "secrets.env"

[common.vars]
user_name  = "Tom"
work_email = "tom@example.com"

# ─── Agents ─────────────────────────────────────────────────────────────
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

[agents.copilot-cli]
capabilities   = ["instructions", "skills", "subagents", "mcp"]
mcp_format     = "copilot-cli"
global_profile = "tw"

[agents.copilot-cli.paths.global]
instructions = ["~/.copilot/copilot-instructions.md"]
skills       = ["~/.copilot/skills"]
subagents    = ["~/.copilot/agents"]
mcp          = ["~/.copilot/mcp-config.json"]

[agents.copilot-cli.paths.project]
skills    = [".github/skills"]
subagents = [".github/agents"]
mcp       = [".github/copilot/mcp.json"]

[agents.copilot-cli.vars]
agent_name = "Copilot"

[agents.pi]
capabilities   = ["instructions", "skills", "mcp"]
mcp_format     = "pi"
global_profile = "minimal"

[agents.pi.paths.global]
instructions = ["~/.pi/agent/AGENTS.md"]
skills       = ["~/.pi/agent/skills"]
mcp          = ["~/.pi/mcp.json"]

[agents.pi.paths.project]
skills = [".pi/skills"]
mcp    = [".pi/mcp.json"]

[agents.pi.vars]
agent_name = "Pi"

# ─── Instructions (Jinja2 templates) ───────────────────────────────────
[instructions.AGENT-md]
source = "~/.config/twagent/templates/AGENT.md.j2"

# ─── File artifacts ─────────────────────────────────────────────────────
[skills.bkmr-memory]
source      = "~/dev/skills/bkmr-memory"
description = "Persistent memory via bkmr CLI"

[skills.tw-review]
source = "~/dev/skills/tw-review"

[subagents.code-reviewer]
source = "~/dev/agents/code-reviewer.md"

[prompts.adr]
source = "~/dev/prompts/adr.prompt.md"

# ─── MCP servers ────────────────────────────────────────────────────────
[servers.github]
type    = "stdio"
command = "npx"
args    = ["-y", "@modelcontextprotocol/server-github"]
env     = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }

[servers.atlassian]
type  = "http"
url   = "https://example.com/mcp/"
tools = ["*"]
[servers.atlassian.headers]
X-Atlassian-Token = "${CONFLUENCE_TOKEN}"
X-Atlassian-Url   = "https://example.com/confluence"

# ─── Profiles ───────────────────────────────────────────────────────────
[profiles.minimal]
description  = "Bare-minimum daily set"
instructions = ["AGENT-md"]
skills       = ["bkmr-memory"]
servers      = ["github"]

[profiles.tw]
extends      = ["minimal"]
description  = "Tom's default loadout"
skills       = ["tw-review"]
subagents    = ["code-reviewer"]
prompts      = ["adr"]
servers      = ["atlassian"]
```

### Interpolation & secrets

- `${VAR}` and `${VAR:-default}` resolve inside `servers.*.env` and `servers.*.headers` only.
- Sources are `os.environ` plus the dotenv at `env_file`.
- Secrets are **masked by default** in `apply -n` / `diff` output. Use `-S` / `--show-secrets` to reveal.

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

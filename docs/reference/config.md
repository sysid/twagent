# Reference — Configuration (`config.toml`)

The canonical config lives at `~/.config/twagent/config.toml`. Edit it with
`twagent edit`, or `twagent edit --init` to bootstrap a commented stub.

## Top-level sections

| Section | Purpose |
|---|---|
| `schema_version` | Required. Currently `3`. |
| `env_file` | Optional. Path (relative to the config) to a dotenv file used for `${VAR}` interpolation. |
| `[common.vars]` | Jinja vars shared across agents (overlaid by per-agent vars). |
| `[agents.<id>]` | An agent: capabilities, MCP format, paths, vars, default global profile. |
| `[instructions.<name>]` | Jinja2 instruction template (first-class artifact in v3). |
| `[skills.<name>]` `[subagents.<name>]` `[prompts.<name>]` | File artifacts (symlinked into per-agent dirs). |
| `[servers.<name>]` | MCP server definition (compiled per `mcp_format`). |
| `[profiles.<name>]` | Bundles of artifact names; composable via `extends`. |

**Shadow rule:** every artifact `name` (across all five registries) AND every
profile name must be globally unique. The config refuses to load otherwise.

## Field reference

### Agent — `[agents.<id>]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `capabilities` | list of strings | yes | Subset of `instructions`, `skills`, `subagents`, `prompts`, `mcp`. |
| `mcp_format` | string | iff `mcp` in capabilities | One of: `claude-code`, `copilot-cli`, `pi`, `vscode`, `opencode`. |
| `global_profile` | string | no | Profile name deployed by `apply --global`. |
| `paths.global.<kind>` | list of paths | per capability | Canonical destinations. Always a list (1+ entries). |
| `paths.project.<kind>` | list of paths | per capability | cwd-relative destinations for `apply --here`. `instructions` is optional here. |
| `vars` | dict | no | Jinja vars layered over `[common.vars]`. Per-agent values win on key clash. |

Multi-target example (the same instructions written to two places at once):

```toml
[agents.copilot-cli.paths.global]
instructions = [
  "~/.copilot/copilot-instructions.md",
  "~/.config/github-copilot/intellij/global-copilot-instructions.md",
]
```

### File artifacts — `[instructions.<name>]`, `[skills.<name>]`, `[subagents.<name>]`, `[prompts.<name>]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `source` | path | yes | Absolute or `~`-prefixed. Missing source → warning at load, error at deploy / `doctor`. |
| `description` | string | no | Free text, shown in `twagent artefacts`. |

Skills and subagents may be files or directories — twagent symlinks the
`source` as-is.

### Server — `[servers.<name>]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `type` | string | no | `stdio` (default) or `http`/`sse`. |
| `command` | string | stdio only | Executable. |
| `args` | list of strings | no | Args to `command`. |
| `env` | dict | no | Env vars; `${VAR}` and `${VAR:-default}` are interpolated. |
| `url` | string | http/sse only | Endpoint URL. |
| `tools` | list of strings | no | Whitelist (e.g. `["*"]`). |
| `headers` | dict | http/sse only | Headers; same interpolation as `env`. Use a nested table. |

Per-agent quirks (e.g. copilot-cli rewriting stdio → local in the compiled
JSON) live in the `mcp_format` translator — you only write one canonical
server block.

### Profile — `[profiles.<name>]`

| Field | Type | Notes |
|---|---|---|
| `description` | string | Free text. |
| `extends` | list of profile names | Composed depth-first, parent-first. First-occurrence wins on collisions. Per-kind, not cross-kind. |
| `instructions` | list of names | Artifact references. |
| `skills` | list of names | Artifact references. |
| `subagents` | list of names | Artifact references. |
| `prompts` | list of names | Artifact references. |
| `servers` | list of names | Artifact references. |

## A worked example

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
agent_name         = "Claude"
extra_instructions = ["Prefer terse output", "No emojis"]

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
agent_name         = "Copilot"
extra_instructions = []

# ─── Instructions (Jinja2 templates) ───────────────────────────────────
[instructions.AGENT-md]
source = "~/.config/twagent/templates/AGENT.md.j2"

# ─── File artifacts ─────────────────────────────────────────────────────
[skills.bkmr-memory]
source      = "~/dev/skills/bkmr-memory"
description = "Persistent memory via bkmr CLI"

[subagents.code-reviewer]
source = "~/dev/agents/code-reviewer.md"

# ─── MCP servers ────────────────────────────────────────────────────────
[servers.github]
type    = "stdio"
command = "npx"
args    = ["-y", "@modelcontextprotocol/server-github"]
env     = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }

[servers.atlassian]
type = "http"
url  = "https://example.com/mcp/"
tools = ["*"]
[servers.atlassian.headers]
X-Atlassian-Token = "${CONFLUENCE_TOKEN:-XXX}"

# ─── Profiles ───────────────────────────────────────────────────────────
[profiles.minimal]
description  = "Bare-minimum daily set"
instructions = ["AGENT-md"]
skills       = ["bkmr-memory"]
servers      = ["github"]

[profiles.tw]
extends      = ["minimal"]
description  = "Tom's default loadout"
subagents    = ["code-reviewer"]
servers      = ["atlassian"]
```

## Instruction templates (Jinja2)

Templates are rendered with `StrictUndefined` — a missing variable is a
hard error. The render context is `{**common.vars, **agents.<id>.vars}`,
so per-agent values win on clash.

`~/.config/twagent/templates/AGENT.md.j2`:

```jinja
# {{ agent_name }} instructions for {{ user_name }}

Reach me at {{ work_email }}.
{% for note in extra_instructions %}
- {{ note }}
{% endfor %}
```

Wire it up:

```toml
[instructions.AGENT-md]
source = "~/.config/twagent/templates/AGENT.md.j2"

[profiles.tw]
instructions = ["AGENT-md"]
```

`twagent apply --global` then writes the **same** template into each agent's
instructions path with that agent's own `agent_name` and `extra_instructions`.

## Interpolation & secrets

- `${VAR}` and `${VAR:-default}` are resolved inside `servers.*.env` and
  `servers.*.headers` only. (Not inside Jinja `{{ }}` — that's a different
  layer.)
- Sources: `os.environ` plus the dotenv pointed at by `env_file`. Env
  always wins over dotenv on key clash.
- Secrets are **masked by default** in `apply -n` / `diff` output. Use
  `-S` / `--show-secrets` to reveal.

## Profile composition rules

`extends` is **depth-first, parent-first, first-occurrence wins per kind.**
Example:

```
profile "base"      skills = ["a", "b"]
profile "tw"        extends = ["base"]
                    skills  = ["c", "b"]
```

`twagent profiles` will show `tw`'s expanded skills as `["a", "b", "c"]`:

- `a` from `base` first.
- `b` from `base` first (the duplicate from `tw` is deduped, base wins).
- `c` from `tw`.

Composition is **per kind**. A skill and a server with the same name would
collide via the shadow rule at load time, not via `extends`.

## Validation

Loaded by `twagent` (any command). On load failure, exit 2 with a message.

Common failure modes:

| Error | Cause |
|---|---|
| `schema_version` missing / wrong | Add `schema_version = 3` at top level. |
| `[[scopes]] blocks are not supported` | Old v1/v2 format. Replace with per-agent `global_profile`. |
| `templates_dir is not supported in schema_version=3` | Old format. Use `[instructions.<name>] source = …` instead. |
| `name X shadows another artifact` | Two registries declared the same name. Names are globally unique. |
| `mcp_format required when 'mcp' in capabilities` | Add `mcp_format = "claude-code"` (or another valid value) to the agent. |
| Source path missing (warning) | The path in a `source = …` doesn't exist on disk. |

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
| `[plugins.<name>]` | A downloaded Claude Code plugin; expands into ordinary artifacts. |
| `[profiles.<name>]` | Bundles of artifact (and plugin) names; composable via `extends`. |

**Shadow rule:** every artifact `name` (across all five registries), every
plugin name, AND every profile name must be globally unique. The config
refuses to load otherwise.

## Field reference

### Agent — `[agents.<id>]`

| Field | Type | Required | Notes |
|---|---|---|---|
| `capabilities` | list of strings | yes | Subset of `instructions`, `skills`, `subagents`, `prompts`, `mcp`. |
| `mcp_format` | string | iff `mcp` in capabilities | One of: `claude-code`, `copilot-cli`, `pi`, `codex`, `vscode`, `opencode`. |
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

`codex` diverges furthest, because its config is TOML rather than JSON and it
infers transport from the fields present:

| Canonical | Compiled for codex |
|---|---|
| `type` | *omitted* — codex infers stdio vs http from `command` vs `url` |
| `headers` | `http_headers` |
| `tools = ["*"]` | *omitted* — codex's `enabled_tools` is a literal tool-name allow-list with no wildcard syntax, so `["*"]` would mean a tool named `*`. Omitting is how codex spells "all tools". |
| `tools` (any other list) | `enabled_tools` — a real allow-list is translated as written; dropping it would silently widen the server to all tools. |
| `type = "sse"` | *server skipped, with a warning* — codex has no sse transport |

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
| `plugins` | list of names | Plugin references. Each pulls the whole bundle atomically — see Plugins below. |

### Plugin — `[plugins.<name>]`

A **plugin** is a downloaded [Claude Code plugin](https://docs.claude.com/en/docs/claude-code/plugins):
an unpacked directory with a `plugin.json` manifest plus conventional kind
dirs. Registering one lets you reference the whole bundle atomically and fan
its pieces out to **every** configured agent — twagent does not defer to
Claude's native `/plugin` manager.

| Field | Type | Required | Notes |
|---|---|---|---|
| `source` | path | yes | Absolute or `~`-prefixed dir holding `plugin.json`. Missing dir / unparseable manifest → hard error at load. twagent points at the dir; it never fetches or copies. You update plugins yourself (`git pull`). |
| `description` | string | no | Defaults to the manifest's `description`. |

At load, twagent reads `plugin.json`, walks the manifest-declared dirs, and
**injects each piece into the matching registry** as an ordinary artifact
(keyed by its on-disk basename):

| `plugin.json` key | twagent kind | One artifact per… | Injected name |
|---|---|---|---|
| `"skills"` | `skills` | subdir containing `SKILL.md` | dir name |
| `"agents"` | `subagents` | `*.md` file | filename |
| `"prompts"` | `prompts` | `*.md` file | filename |
| `mcpServers` (in `plugin.json`) and/or a `.mcp.json` file | `servers` | server entry | server key |

There is no `instructions` mapping — CC plugins don't ship that kind; if one
is present it is ignored with a warning.

**Reference a plugin** atomically from a profile (`plugins = ["bmw-common"]`)
or ad-hoc (`twagent apply --select bmw-common`). The bundle expands into its
member artifacts before deploy, so every agent that supports a kind receives
the matching pieces.

**Collisions are a hard error at load.** Names are bare (not namespaced): if a
plugin piece collides with another piece or a top-level artifact of the same
kind, the load fails with a message naming both contributors. Resolve it by
removing the conflicting artifact or not co-installing two plugins that fight.

```toml
[plugins.bmw-common]
source = "~/dev/bmw/cdlos/ai/plugins/bmw-common"

[profiles.bmw]
extends = ["minimal"]
plugins = ["bmw-common"]   # all its skills + agents, fanned out to every agent
```

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

# codex: no `subagents` (codex's are TOML, not Markdown) and no `prompts`
# (deprecated upstream). Its skills live under the cross-vendor `.agents/`
# convention, and its MCP target doubles as codex's own state file.
[agents.codex]
capabilities   = ["instructions", "skills", "mcp"]
mcp_format     = "codex"
global_profile = "tw"

[agents.codex.paths.global]
instructions = ["~/.codex/AGENTS.md"]
skills       = ["~/.agents/skills"]
mcp          = ["~/.codex/config.toml"]

[agents.codex.paths.project]
skills = [".agents/skills"]
mcp    = [".codex/config.toml"]

[agents.codex.vars]
agent_name         = "Codex"
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
- Resolved variable values are **masked by default** in `apply -n`, `diff`, and
  `info` output. Literal values are not recognized as secrets. Use
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
| `profiles.X: unknown key 'Y'` | A misspelled profile key (e.g. `pluings` for `plugins`). Profile keys are validated; the error suggests the nearest valid key. |
| Source path missing (warning) | The path in a `source = …` doesn't exist on disk. |

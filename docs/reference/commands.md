# Reference — Commands

Every command, every flag, every exit code. See [Configuration](config.md)
for TOML schema.

## Global flags

These apply to every subcommand:

| Long | Short | Effect |
|---|---|---|
| `--config <path>` | `-c` | Use this config instead of `~/.config/twagent/config.toml`. |
| `--verbose` | `-v` | Debug logging via `RichHandler` to stderr. |
| `--version` | — | Print the installed version and exit (eager; ignores config). |

## `apply` — deploy resolved configuration

The main command. Two modes; `--here` (local) is the default.

| Long | Short | Effect |
|---|---|---|
| _(no flag — default)_ | — | Local deploy: write `--select` into cwd via `paths.project.*`. |
| `--global` | `-G` | Global deploy: write each agent's `global_profile` to `paths.global.*`. |
| `--agent <id>` | `-a` | Repeatable. Restrict to specific agents. |
| `--select <names>` | `-s` | CSV of profile names AND/OR artifact names. `none` deploys empty. |
| `--interactive` | `-i` | Open a terminal picker. Honors `--select` as pre-checked items (expanded). |
| `--dry-run` | `-n` | Show the plan, write nothing. Runtime `${VAR}` references remain visible. |
| `--dedup` / `--no-dedup` | — | Local mode only. Skip skills/subagents/prompts already in the agent's `paths.global.*`. Default ON. |

MCP writes **merge** into the target file: twagent owns only the format's
top-level key (e.g. `mcpServers`) and replaces that subtree wholly; every
foreign top-level key is preserved. Targets like `~/.claude.json` are
harness-owned state files that merely also hold MCP config. An unparseable
target is an error — never overwritten.

### Examples

```bash
twagent apply --global                     # everything globally, idempotent
twagent apply --global -n                  # preview with runtime references
twagent apply --global -a claude-code      # one agent globally
twagent apply --global -s e2e-emea         # swap MCP env for the day, all agents
twagent apply --global -s e2e-emea -a copilot-cli  # one agent, MCP only
twagent apply -s tw-claude                 # local: deploy a profile into cwd
twagent apply -s core,tw-cucumber-to-http  # local: profile + extra skill
twagent apply -i                           # local: TUI picker
twagent apply -s tw-claude -i              # picker pre-checked w/ profile contents
```

### How `--select` works

- **Exhaustive by kind.** Only the artifact kinds derivable from the
  selection are deployed. Selecting a servers-only profile rewrites the
  MCP file and nothing else.
- **Polymorphic.** Each name resolves to either a profile (expanded via
  `extends`) or a single artifact.
- **`none`** is reserved: `--select none` deploys an empty set (useful
  for "remove everything for this agent").

### Dedup against the global layer

Local deploy is **deduplicated by default**: skills, subagents, and prompts
already present in the agent's `paths.global.*` dir are skipped, because agents
read both the global and project layers — a local copy would be a pure
duplicate. MCP files and instructions are never deduplicated. Pass `--no-dedup`
to force local copies of globally-present artifacts.

### Interactive picker

- Uses **fzf** when available (`fzf >= 0.35` required for preselect).
- Fuzzy-filter, multi-select. Keys: `Tab` toggle, `Enter` confirm,
  `Esc` cancel, `Ctrl-A` all, `Ctrl-D` none.
- Falls back to `simple-term-menu` if fzf is missing or
  `TWAGENT_NO_FZF=1` is set.
- When `--select` names a profile, the picker pre-checks the profile's
  **expanded members** (post-`extends`), not the profile name itself.
- Return order is stable: items come back in display order regardless of
  click order.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (or `--dry-run` completed). |
| 2 | Invalid flags / unknown names / config errors. |

---

## `diff` — show pending changes

Read-only. Shows what `apply --global` would change between config and
on-disk state.

```bash
twagent diff
twagent diff -a claude-code     # one agent
twagent diff -S                 # reveal secret values
```

Equivalent to `apply --global --dry-run` for the comparison subset.

MCP comparison covers only the twagent-owned top-level key (matching apply's
merge semantics) — harness state in shared files like `~/.claude.json` never
registers as drift. Secret (`${VAR}`-derived) values are masked on BOTH sides
before comparing, so they never drive drift either; literal env/header values
still compare for real.

---

## `status` — per-agent global deployment view

Read-only. For each agent shows: capabilities, `global_profile`, and the
canonical paths. Quick "what's wired up?" check.

```bash
twagent status
```

---

## `info` — deployed config at cwd

Read-only. Shows what is *actually deployed on disk* for the current
directory. **By default it scans only the local layer** (`cwd/paths.project.*`)
— the "what's live HERE" view. Pass `--global` to also include the global layer
(`paths.global.*`, e.g. `~/.claude`). Unlike `status`/`diff` (config-driven,
globals only), `info` reads disk reality and tags every entry:

- `managed` — symlink resolves to a known artifact source (artifact name shown)
- `unmanaged` — entry present but not deployed by twagent
- `⚠ dangling` — broken symlink

Instructions are reported present/absent (the rendered file's source name is
not recoverable from disk). MCP files use syntax highlighting matching their
JSON or TOML wire format. Runtime `${VAR}` references and Codex environment
variable names remain visible. Legacy resolved values in canonical
reference-backed fields are masked by default. Provenance is the **layer**
(global vs local); the deploying profile is not recoverable from disk and is
not shown. Exits 0 on success (drift never fails it); an unknown
`-a` agent id is a usage error and exits 2 with the list of valid agents.

`~/.claude.json` is **never shown** — it is Claude Code's own state file, not a
twagent artifact.

```bash
twagent info                    # local layer only (default)
twagent info --global           # also include the global layer
twagent info -a claude-code     # one agent (repeatable)
twagent info --json             # machine-readable
twagent info --show-secrets     # reveal exact raw MCP files
```

> **Security:** `info` masks stale values only at fields backed by canonical
> `${VAR}` expressions. Literal credentials and values belonging to foreign
> servers are not recognized as secrets. Use runtime references for credentials. Passing
> `--show-secrets` prints the exact raw file, so do not share that output or its
> terminal scrollback.

---

## `agents` — list agents

Read-only. For each agent: id, capabilities, paths, vars.

```bash
twagent agents          # human-readable table
twagent agents -j       # JSON (machine-readable)
```

---

## `profiles` — list profiles

Read-only. Each profile with its `extends`-expanded contents per kind
(instructions, skills, subagents, prompts, servers).

```bash
twagent profiles
```

Useful for verifying that profile composition does what you expect.

---

## `artefacts` — list or inspect artifacts

Read-only. Lists every artifact across all registries; optionally narrow
by kind or inspect one by name.

```bash
twagent artefacts                          # everything
twagent artefacts --skills                 # just skills
twagent artefacts --servers --instructions # combine filters
twagent artefacts bkmr-memory              # details for one artifact
```

---

## `doctor` — health check

Read-only. Reports problems:

- **errors** (exit 1): dangling symlinks under agent dirs, registered
  artifacts whose `source` is missing, profile references that don't resolve.
- **info** (exit 0): silently-skipped profile entries (e.g. a subagent
  in a profile deployed to an agent without `subagents` capability).

```bash
twagent doctor
```

| Exit | Meaning |
|---|---|
| 0 | No errors. (Info messages still possible.) |
| 1 | One or more errors found. |

---

## `extract` — migrate an existing MCP JSON

One-shot helper. Converts an existing per-agent MCP JSON file to canonical
TOML on stdout. Auto-detects wrapper formats (`mcpServers` / `servers` /
`mcp.servers`). Secret-looking keys (`TOKEN`, `KEY`, `PASSWORD`, ...) are
emitted as `${VAR}` placeholders, not literal values.

```bash
twagent extract ~/.claude.json                          # to stdout
twagent extract ~/.claude.json >> ~/.config/twagent/config.toml
```

Edit the appended block for duplicate names before saving.

---

## `edit` — open the config (or a template)

```bash
twagent edit                       # open config in $EDITOR
twagent edit --init                # bootstrap a starter config, then open
twagent edit --template AGENT-md   # open the source file of an instruction
twagent edit -t AGENT-md           # same, short form
```

`--init` is a no-op if the config already exists. `--init` and `--template`
are mutually exclusive.

`$EDITOR` falls back to `vi`.

---

## `--version` — print the installed version

A global eager flag (not a subcommand). Prints the version and exits before
config is loaded.

```bash
twagent --version
```

---

## Environment variables

| Variable | Effect |
|---|---|
| `EDITOR` | Used by `twagent edit`. Default: `vi`. |
| `TWAGENT_NO_FZF` | If `1`, force the simple-term-menu fallback even when fzf is installed. |
| (any `${VAR}` in MCP `env` / `headers`) | Read by the launched agent at runtime. See [Configuration § Runtime references](config.md#runtime-references--secrets). |

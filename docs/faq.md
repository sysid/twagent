# FAQ

## General

### Why "yet another config tool"?

Because every AI coding agent invents its own file layout, and keeping
N copies of the same skills / MCP servers in sync by hand fails predictably.
twagent owns one TOML and reduces the manual surface to: "edit the TOML;
run `apply`."

### Does this replace any earlier tools?

Yes. twagent supersedes:

- `twmcp` (MCP servers only, multiple agents)
- `devops-binx/agent/render.py` (instructions + skills, multiple agents)

If you still have either installed, removing them is safe once your
`config.toml` covers their use cases.

### Which agents are supported?

Out of the box: **claude-code**, **copilot-cli**, **pi**, **vscode**,
**opencode** (the values accepted in `mcp_format`). Adding a new agent
is a TOML-only operation unless its MCP JSON shape is genuinely new.

## Getting started

### What are the system requirements?

Python 3.13+ and an AI coding agent already installed. `uv` is the
recommended package manager. `fzf` is optional but improves the
`--interactive` picker UX (requires `>= 0.35`).

### Do I need to install on every machine?

Yes — twagent runs locally. The config file (`~/.config/twagent/config.toml`)
is portable across machines; sync it however you sync dotfiles.

## Configuration

### Where is the canonical config?

`~/.config/twagent/config.toml`. Override with `--config <path>` (or
`-c <path>`) on any command.

### Can I put twagent's config in my dotfiles repo?

Yes — that's the recommended pattern. Treat `~/.config/twagent/` like the
rest of your XDG config directories.

### How do `[common.vars]` and `[agents.<id>.vars]` interact?

The render context is `{**common.vars, **agents.<id>.vars}` — per-agent
values **override** common values on key clash. `StrictUndefined` means a
missing variable is a hard error, not a silent empty string.

### Can the same template render to multiple paths?

Yes. Set multiple paths in `paths.global.instructions` (or `paths.project.instructions`).
The same rendered output is written to all of them.

### Why are my secrets showing as `***` in `--dry-run`?

By design — terminal scrollback leaks. Real files on disk always contain
real values. Pass `--show-secrets` (`-S`) to reveal them in dry-run / diff
output when you actually need to inspect.

## Usage

### What does `apply` actually do?

For each agent in scope (filtered by `--agent`, then by which capabilities
the selection touches):

1. **Renders** Jinja templates for `instructions` to the agent's instruction paths.
2. **Symlinks** `source` paths for `skills` / `subagents` / `prompts` into the agent's directories.
3. **Compiles** the selected `servers` into the agent's MCP JSON shape and writes it.

All three are idempotent.

### How is `--select` "exhaustive"?

It only deploys the artifact **kinds** named in your selection.
`-s e2e-emea` (a servers-only profile) writes the MCP file and nothing
else — no skill symlinks, no instruction render. This is so you can swap
"MCP environment for the day" without churning unrelated files.

### What's the difference between a profile and `--select` of artifacts?

A profile is a named, persistent bundle in the config. `--select` accepts
profile names AND artifact names mixed, but the artifact names are
**ad-hoc** — they live only in the command. Use profiles for the loadouts
you want to remember; use direct names for one-offs.

### How do I deploy to a single repo without affecting global state?

`cd ~/dev/myrepo && twagent apply -s <profile> -a <agent>`. This is local
mode (the default — no `--global` flag). It writes under cwd via the
agent's `paths.project.*`.

### Can I deploy locally to multiple agents at once?

Yes. Either omit `--agent` (all agents in scope) or pass `-a` repeatedly:
`twagent apply -s tw -a claude-code -a copilot-cli`.

## Troubleshooting

### `Local deploy requires --select <names>`

By design — local mode has no default profile. Either pass `--select` or
use `--global` (which uses each agent's `global_profile`).

### `fzf X.Y is too old; twagent --interactive requires fzf >= 0.35`

Upgrade fzf (`brew upgrade fzf` / `cargo install fzf`) or fall back with
`TWAGENT_NO_FZF=1`.

### A skill source moved and now `doctor` flags it

Update the `[skills.<name>] source = …` path and re-run `apply --global`
to refresh the symlink. twagent never deletes an artifact's source — only
the symlink.

### `diff` is noisy after I changed `mcp_format`

Expected — different `mcp_format` values produce different JSON shapes,
so every compiled file is different. Run `apply --global` to converge.

### `extract` produced a TOML block that conflicts with an existing name

twagent enforces globally unique names across all registries. Rename one
of them (typically the new one) before saving.

## Internals

### Are skills copied or symlinked?

**Symlinked.** Editing a skill source updates every agent immediately —
no re-apply needed for content changes. (Re-apply only when you change
the *set* of skills in the profile.)

### What happens to files that were deployed but are no longer in the profile?

`apply` writes the current state; it does not clean up. Use `doctor` to
spot dangling symlinks (artifacts no longer in any profile but still in
the agent's directory) and remove them by hand. A future flag may automate
this — track issues for status.

### Does twagent ever touch the agent's data files?

No — twagent only writes configuration (instructions, skill symlinks, MCP
JSON). It never touches conversation history, caches, or anything else
under the agent's directory.

## Getting help

- `twagent <command> --help` for any command.
- `twagent doctor` for health checks.
- File an issue at the project's GitHub repo with output of `twagent doctor`,
  the relevant config block, and what you expected vs. saw.

"""twagent CLI — apply, diff, status, agents, profiles, scopes, doctor, extract, edit."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from twagent import __version__
from twagent.config import ConfigError, load
from twagent.deploy import apply_global, apply_here
from twagent.diff import compute_diff
from twagent.doctor import check as doctor_check
from twagent.extractor import extract_from_file
from twagent.selector import (
    is_interactive_terminal,
    parse_select_value,
    select_interactive,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path.home() / ".config" / "twagent" / "config.toml"

_APP_HELP = """\
twagent — one config, every AI agent.

Manages instructions, skills, subagents, prompts, and MCP servers across
Claude Code, Copilot CLI, Pi, and friends from a single canonical TOML.

  Edit config       :  twagent edit                (or 'edit --init' first time)
  See what's loaded :  twagent agents | profiles | status
  Preview locals    :  twagent apply --select <names> --dry-run
  Deploy locally    :  twagent apply --select <names>   (default: --here)
  Deploy globally   :  twagent apply --global           (each agent's global_profile)
  See what changed  :  twagent diff
  Find drift        :  twagent doctor

Glossary:
  agent    An AI assistant target with capabilities + paths + a default
           profile (`global_profile`). `apply --global` deploys this profile.
  profile  A reusable bundle of artifact references (skills/subagents/
           prompts/MCP servers). Profiles can `extends` other profiles.
  artifact A single skill/subagent/prompt/MCP server, registered by name.

  --select takes a mix of profile names AND artifact names.
  --here (default) vs --global: write to cwd vs canonical agent paths.
"""

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help=_APP_HELP,
)
console = Console()
err_console = Console(stderr=True)

CAPABILITIES = ("instructions", "skills", "subagents", "prompts", "mcp")


# ─── Global options ─────────────────────────────────────────────────────


class _GlobalOptions:
    config_path: Path = DEFAULT_CONFIG
    verbose: bool = False


_OPTS = _GlobalOptions()


@app.callback()
def _main(
    config: Path = typer.Option(
        DEFAULT_CONFIG,
        "--config",
        "-c",
        help=(
            "Path to your canonical TOML config. "
            "Defaults to ~/.config/twagent/config.toml. "
            "Override per-invocation when you need to test against a different file."
        ),
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help=(
            "Print debug-level logs to stderr. "
            "Shows which subsystem (config/deploy/mcp/...) did what, with "
            "timestamps. Useful when something is silently misbehaving."
        ),
    ),
) -> None:
    """twagent — unified AI-agent configuration framework."""
    _OPTS.config_path = config
    _OPTS.verbose = verbose
    # Rich-styled logging — matches render.py's pattern. -v upgrades to DEBUG;
    # default WARNING keeps normal output clean. Module logger names appear so
    # readers can trace which subsystem emitted what.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=err_console,
                show_time=verbose,
                show_path=False,
                rich_tracebacks=True,
                markup=False,
            )
        ],
        force=True,
    )
    logger.debug("cli init: config=%s verbose=%s", config, verbose)


def _load_config():
    try:
        return load(_OPTS.config_path)
    except (ConfigError, FileNotFoundError) as exc:
        err_console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2)


# ─── version ────────────────────────────────────────────────────────────


@app.command(help="Print the installed twagent version and exit.")
def version() -> None:
    typer.echo(__version__)


# ─── apply ──────────────────────────────────────────────────────────────


_APPLY_HELP = """\
Deploy your resolved configuration to disk.

Two modes:

  --here     (default)  Deploy a CLI-supplied selection to the CURRENT
                        directory via each agent's `paths.project.*`
                        joined under cwd. Requires --select. Auto-selects
                        agents whose capabilities serve at least one kind
                        in the selection (or use --agent to narrow). This
                        is what bare `twagent apply --select <names>` does.

  --global              Deploy each agent's `global_profile` to its
                        `paths.global.*` paths (~/.claude/, ~/.copilot/, ...).
                        Idempotent.

--select is exhaustive: only the kinds derivable from the selection are
deployed. `--select e2e-emea` (servers-only profile) writes the MCP file
and nothing else — no instruction render, no skill symlinks. To deploy
everything an agent has, use bare `apply` (no --select).

--select takes profile names AND/OR artifact names, mixed (skills,
subagents, prompts, servers, AND instructions are all selectable by name):

  twagent apply --here --select e2e-emea
                                          # one profile name
  twagent apply --here --select core,tw-cucumber-to-http,github
                                          # mix: profile + skill + server

Examples:
  cd ~/dev/myrepo
  twagent apply -s e2e-emea                  # local (default): deploy to cwd
  twagent apply -s core -a claude-code       # local, single agent
  twagent apply -s foo,bar -n                # preview local deploy, masked
  twagent apply -i                           # pick artifacts in a TUI (local)
  twagent apply -s tw-claude -i              # picker pre-checked with tw-claude
                                             # — add/remove from there
  twagent apply --global                     # sync everything globally
  twagent apply --global -n                  # preview globals, secrets masked
  twagent apply --global -a claude-code      # one agent globally
  twagent apply --global -s foo,bar          # override globals with this set

Short flags:
  -G/--global  -H/--here  -a/--agent  -s/--select  -i/--interactive
  -n/--dry-run -S/--show-secrets
"""


@app.command(help=_APPLY_HELP)
def apply(
    here: bool = typer.Option(
        False,
        "--here",
        "-H",
        help=(
            "Local mode (default): deploy the --select set to the current "
            "directory via each agent's paths.project.* joined under cwd. "
            "Mutually exclusive with --global. This is the default — pass "
            "explicitly only for clarity."
        ),
    ),
    global_mode: bool = typer.Option(
        False,
        "--global",
        "-G",
        help=(
            "Global mode: deploy each agent's `global_profile` to its "
            "paths.global.* paths. Mutually exclusive with --here."
        ),
    ),
    agent: Optional[list[str]] = typer.Option(
        None,
        "--agent",
        "-a",
        help=(
            "Restrict to one or more agents (by id, e.g. 'claude-code'). "
            "Repeatable. In --here mode, also forces inclusion of all "
            "capabilities the agent supports (including instructions)."
        ),
    ),
    select: Optional[str] = typer.Option(
        None,
        "--select",
        "-s",
        help=(
            "Comma-separated list of profile names AND/OR artifact names. "
            "Each name resolves to either a profile (expanded via `extends`) "
            "or a single artifact (skill/subagent/prompt/server). REQUIRED "
            "in --here mode (the default); optional in --global mode where "
            "it OVERRIDES each agent's global_profile. "
            "When combined with --interactive, the named items are "
            "PRE-CHECKED in the picker — you can add or remove from there."
        ),
        metavar="NAMES|none",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help=(
            "Open a terminal picker to choose artifacts. "
            "Honors --select as a pre-selection: pre-checks the named items "
            "and lets you add or remove. Cancelling exits without deploying."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help=(
            "Show every write/symlink/render that WOULD happen, but change "
            "nothing on disk. Resolved secrets are masked unless --show-secrets."
        ),
    ),
    show_secrets: bool = typer.Option(
        False,
        "--show-secrets",
        "-S",
        help=(
            "Reveal resolved values from ${VAR} interpolation in --dry-run "
            "and 'diff' output. OFF by default — terminal scrollback leaks "
            "secrets. Real files written to disk always contain real values."
        ),
    ),
) -> None:
    if here and global_mode:
        err_console.print(
            "[red]Pick one: --global OR --here[/red]\n"
            "  --global writes to canonical agent paths; --here writes to cwd."
        )
        raise typer.Exit(2)

    # --here is the default. Only enter global mode when --global is set.
    here = not global_mode

    config = _load_config()

    select_list: list[str] | None = None
    if select is not None:
        try:
            select_list = parse_select_value(select)
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)

    if interactive:
        if not is_interactive_terminal():
            err_console.print("[red]--interactive requires a TTY[/red]")
            raise typer.Exit(2)
        items: dict[str, str] = {}
        for n in config.profiles:
            items[n] = "[profile]"
        for n in config.instructions:
            items[n] = "[instruction]"
        for n in config.skills:
            items[n] = "[skill]"
        for n in config.subagents:
            items[n] = "[subagent]"
        for n in config.prompts:
            items[n] = "[prompt]"
        for n in config.servers:
            items[n] = "[mcp]"
        # When --select is also given, those names are pre-checked in the
        # picker — twmcp's original `--profile X --interactive` pattern,
        # generalized here to any --select <names> set.
        preselected = set(select_list) if select_list else None
        chosen = select_interactive(items, preselected=preselected)
        if chosen is None:
            err_console.print("Cancelled.")
            raise typer.Exit(0)
        select_list = chosen

    if here:
        if not select_list:
            err_console.print(
                "[red]--here requires --select <names> (or --interactive)[/red]\n"
                "  There is no per-cwd default profile; you must say what to deploy."
            )
            raise typer.Exit(2)
        try:
            result = apply_here(
                config,
                cwd=Path.cwd(),
                select=select_list,
                agent_filter=list(agent) if agent else None,
                dry_run=dry_run,
                show_secrets=show_secrets,
            )
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
    else:
        try:
            result = apply_global(
                config,
                agent_filter=list(agent) if agent else None,
                select=select_list,
                dry_run=dry_run,
                show_secrets=show_secrets,
            )
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)

    if dry_run:
        for line in result.dry_run_log:
            typer.echo(line)

    if result.warnings:
        for w in result.warnings:
            err_console.print(f"[yellow]Warning:[/yellow] {w}")

    if not dry_run and _OPTS.verbose:
        for path in result.written:
            err_console.print(path)

    if result.has_errors:
        err_console.print("\n[red]Errors:[/red]")
        for err in result.errors:
            err_console.print(f"  {err}")
        raise typer.Exit(1)


# ─── diff ───────────────────────────────────────────────────────────────


_DIFF_HELP = """\
Show what `twagent apply` would change, without changing anything.

Compares the resolved configuration to the current on-disk state per file:
missing symlinks, retargeted symlinks, content drift in rendered templates
or compiled MCP JSON, and real (non-symlink) entries that block deploy.

Exit codes:
  0  in sync (nothing to do)
  1  divergence found (something is out of date)

Resolved ${VAR} values are masked unless you pass --show-secrets.
This command never writes to disk.
"""


@app.command(help=_DIFF_HELP)
def diff(
    show_secrets: bool = typer.Option(
        False,
        "--show-secrets",
        "-S",
        help=(
            "Reveal resolved values from ${VAR} interpolation in the diff. "
            "OFF by default (terminal scrollback leaks secrets)."
        ),
    ),
) -> None:
    config = _load_config()
    report = compute_diff(config, show_secrets=show_secrets)
    for line in report.lines:
        typer.echo(line)
    raise typer.Exit(0 if report.in_sync else 1)


# ─── status ─────────────────────────────────────────────────────────────


_STATUS_HELP = """\
Print every agent's global deployment state.

For each agent: its capabilities, the profile attached as `global_profile`
(what bare `twagent apply` will deploy), and the mcp_format. Agents with
no `global_profile` are deployable only via `apply --here --select`.
"""


@app.command(help=_STATUS_HELP)
def status() -> None:
    config = _load_config()
    table = Table(title="Agents (global deployment)")
    table.add_column("Agent")
    table.add_column("Capabilities")
    table.add_column("global_profile")
    table.add_column("mcp_format")
    for agent_id, agent in config.agents.items():
        table.add_row(
            agent_id,
            ", ".join(agent.capabilities),
            agent.global_profile or "[dim]— (no default; use --here --select)[/dim]",
            agent.mcp_format or "—",
        )
    console.print(table)


# ─── agents | profiles | scopes ─────────────────────────────────────────


_AGENTS_HELP = """\
List every agent with its capabilities and resolved global paths.

Capabilities are which artifact types the agent supports
(instructions / skills / subagents / prompts / mcp). Paths are where on
disk twagent will write each capability's output.

Use --json for a machine-parseable dump (script-friendly).
"""


@app.command(help=_AGENTS_HELP)
def agents(
    output_json: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Emit machine-parseable JSON instead of a table. Useful in scripts.",
    ),
) -> None:
    config = _load_config()
    if output_json:
        out = {
            agent_id: {
                "capabilities": list(agent.capabilities),
                "mcp_format": agent.mcp_format,
                "global_profile": agent.global_profile,
                "paths_global": {
                    k: [str(p) for p in v] for k, v in agent.paths_global.items()
                },
                "paths_project": {
                    k: [str(p) for p in v] for k, v in agent.paths_project.items()
                },
            }
            for agent_id, agent in config.agents.items()
        }
        typer.echo(json.dumps(out, indent=2))
        return

    table = Table(title="Agents")
    table.add_column("Agent")
    table.add_column("Capabilities")
    table.add_column("MCP format")
    table.add_column("global_profile")
    table.add_column("Global paths")
    for agent_id, agent in config.agents.items():
        gp_lines = "\n".join(
            f"{cap}: {', '.join(str(p) for p in paths)}"
            for cap, paths in agent.paths_global.items()
        )
        table.add_row(
            agent_id,
            ", ".join(agent.capabilities),
            agent.mcp_format or "—",
            agent.global_profile or "—",
            gp_lines,
        )
    console.print(table)


_PROFILES_HELP = """\
List every profile with its `extends`-flattened contents.

A profile is a reusable bundle of artifact references. The columns show
the artifacts that would actually be deployed for that profile (after
walking its `extends` chain depth-first, parent-first, dedup'd per type).

Use this to look up the artifact NAMES you'd pass to 'apply --select'.
"""


@app.command(help=_PROFILES_HELP)
def profiles() -> None:
    config = _load_config()
    from twagent.deploy import expand_profile

    table = Table(title="Profiles")
    table.add_column("Profile")
    table.add_column("Extends")
    table.add_column("Skills")
    table.add_column("Subagents")
    table.add_column("Prompts")
    table.add_column("Servers")
    for name in config.profiles:
        expanded = expand_profile(config, name)
        prof = config.profiles[name]
        table.add_row(
            name,
            ", ".join(prof.extends) or "—",
            ", ".join(expanded["skills"]) or "—",
            ", ".join(expanded["subagents"]) or "—",
            ", ".join(expanded["prompts"]) or "—",
            ", ".join(expanded["servers"]) or "—",
        )
    console.print(table)


# `scopes` command removed in v2 — scopes don't exist anymore.
# Use `status` (per-agent global view) or `profiles` (composable bundles).


# ─── doctor ─────────────────────────────────────────────────────────────


_DOCTOR_HELP = """\
Health check the deployed state without touching anything.

Reports:
  errors  dangling symlinks under agent dirs, registered artifacts whose
          source path is missing, profile references that don't resolve.
  info    disabled scopes, profile entries the agent's capabilities can't
          serve (silently skipped at apply time).

Exit codes:
  0  no errors (info is OK)
  1  one or more errors found
"""


@app.command(help=_DOCTOR_HELP)
def doctor() -> None:
    config = _load_config()
    report = doctor_check(config)
    if report.errors:
        err_console.print("[red]Errors:[/red]")
        for err in report.errors:
            err_console.print(f"  {err}")
    if report.info:
        console.print("\n[blue]Info:[/blue]")
        for line in report.info:
            console.print(f"  {line}")
    if not report.errors and not report.info:
        console.print("[green]All checks passed.[/green]")
    raise typer.Exit(1 if report.has_errors else 0)


# ─── extract ────────────────────────────────────────────────────────────


_EXTRACT_HELP = """\
Convert an existing per-agent MCP JSON file to canonical TOML on stdout.

One-shot migration helper. Auto-detects the wrapper format (mcpServers /
servers / mcp.servers). Secret-looking keys (TOKEN, KEY, PASSWORD, ...)
are emitted as ${VAR} placeholders, not literal values.

Read-only: never modifies your config file. Pipe into it yourself:

  twagent extract ~/.claude.json >> ~/.config/twagent/config.toml
"""


@app.command(help=_EXTRACT_HELP)
def extract(
    mcp_json: Path = typer.Argument(
        ...,
        exists=True,
        readable=True,
        help=(
            "Path to an existing per-agent MCP JSON file "
            "(e.g. ~/.claude.json, ~/.copilot/mcp-config.json)."
        ),
    ),
) -> None:
    try:
        toml_text = extract_from_file(mcp_json)
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    typer.echo(toml_text, nl=False)


# ─── edit ───────────────────────────────────────────────────────────────


_EDIT_HELP = """\
Open your canonical config (or an instruction template) in $EDITOR.

Defaults to opening the canonical config at ~/.config/twagent/config.toml
(or whatever --config points at). Falls back to 'vi' if $EDITOR is unset.

  --init                 First-time setup: create a comprehensive, commented
                         starter config at the target path before opening it.
  --template <name>      Open the named instruction template's source file
                         (resolved via [instructions.<name>].source). Run
                         `twagent profiles` or look at config to discover names.

Examples:
  twagent edit                                 # open the config
  twagent edit --init                          # bootstrap then open
  twagent edit --template AGENT-md             # open instruction template by name
"""


@app.command(help=_EDIT_HELP)
def edit(
    init: bool = typer.Option(
        False,
        "--init",
        help=(
            "If the config file does not exist, create a comprehensive "
            "starter (with inline comments explaining every section) before "
            "opening it. No-op if the file already exists."
        ),
    ),
    template: Optional[str] = typer.Option(
        None,
        "--template",
        "-t",
        help=(
            "Open the source file of an instruction registered as "
            "[instructions.<name>]. Example: --template AGENT-md"
        ),
        metavar="NAME",
    ),
) -> None:
    if init and template is not None:
        err_console.print("[red]--init and --template are mutually exclusive[/red]")
        raise typer.Exit(2)

    if template is not None:
        config = _load_config()
        if template not in config.instructions:
            avail = ", ".join(sorted(config.instructions)) or "(none defined)"
            err_console.print(
                f"[red]Unknown instruction: {template}[/red]\n  Available: {avail}"
            )
            raise typer.Exit(2)
        target = config.instructions[template].source
    else:
        target = _OPTS.config_path
        if not target.exists():
            if not init:
                err_console.print(
                    f"[red]Config not found: {target}. Use --init to create.[/red]"
                )
                raise typer.Exit(2)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_STUB_CONFIG)

    editor = os.environ.get("EDITOR", "vi")
    try:
        result = subprocess.run([editor, str(target)])
    except FileNotFoundError:
        err_console.print(f"[red]Editor not found: {editor}[/red]")
        raise typer.Exit(1)
    raise typer.Exit(result.returncode)


_STUB_CONFIG = """\
# twagent canonical configuration
# Single source of truth for instructions, skills, subagents, prompts, and MCP
# servers across every AI-coding agent on this machine.
#
# Reference: ~/dev/s/private/twagent/specs/001-twagent-unified-config/
# Schema:    contracts/config-schema.md
# Data model: data-model.md

# Schema migration knob. v3 introduced first-class `[instructions.<name>]`
# registry; bump only when you upgrade twagent.
schema_version = 3

# Optional dotenv loaded BEFORE ${VAR} interpolation runs against MCP env/headers.
# Path is relative to this config file. Env vars from os.environ override dotenv.
# env_file = "secrets.env"


# ─── Common: shared variables ───────────────────────────────────────────
[common.vars]
# Variables visible to ALL instruction templates and agents.
# Per-agent [agents.<id>.vars] overrides any key listed here.
user_name = "you"
work_email = "you@example.com"


# ─── Agents ────────────────────────────────────────────────────────────
# An agent has:
#   capabilities    : subset of {instructions, skills, subagents, prompts, mcp}
#   mcp_format      : required iff "mcp" in capabilities; one of:
#                     claude-code | copilot-cli | pi | vscode | opencode
#   global_profile  : optional profile name; what bare `apply` deploys
#   paths.global    : per-capability list of destination paths (always a list)
#   paths.project   : same shape (used by `apply --here`); instructions may be omitted
#   vars            : per-agent Jinja context (overrides [common.vars])

[agents.claude-code]
capabilities   = ["instructions", "skills", "mcp"]
mcp_format     = "claude-code"
global_profile = "minimal"

[agents.claude-code.paths.global]
instructions = ["~/.claude/CLAUDE.md"]
skills       = ["~/.claude/skills"]
mcp          = ["~/.claude.json"]

[agents.claude-code.paths.project]
# When `apply --here` runs, project paths are joined under cwd.
skills = [".claude/skills"]
mcp    = [".mcp.json"]

[agents.claude-code.vars]
agent_name = "Claude"
extra_instructions = []   # template loops over this; add per-agent notes here


# Multi-target example: deploy copilot-cli instructions to BOTH the CLI
# location AND IntelliJ's global location at the same time.
# [agents.copilot-cli]
# capabilities   = ["instructions", "skills", "mcp"]
# mcp_format     = "copilot-cli"   # rewrites stdio → local in compiled JSON
# global_profile = "minimal"
#
# [agents.copilot-cli.paths.global]
# instructions = [
#   "~/.copilot/copilot-instructions.md",
#   "~/.config/github-copilot/intellij/global-copilot-instructions.md",
# ]
# skills = ["~/.copilot/skills"]
# mcp    = ["~/.copilot/mcp-config.json"]
#
# [agents.copilot-cli.paths.project]
# skills = [".github/skills"]
# mcp    = [".mcp.json"]
#
# [agents.copilot-cli.vars]
# agent_name = "Copilot"
# extra_instructions = [
#   "When running commands which output more than 4KB you MUST pipe to a file.",
# ]


# ─── Artifact registries ───────────────────────────────────────────────
# Each entry has a `source` (absolute or ~-prefixed) + optional `description`.
# Source-missing is a warning at load time; an error in `twagent doctor` and
# at deploy time. Names MUST be globally unique across all 5 registries
# (instructions, skills, subagents, prompts, servers) AND profiles.

[instructions.AGENT-md]
source      = "~/.config/twagent/templates/AGENT.md.j2"
description = "Default instructions template (Jinja2)"

[skills.bkmr-memory]
source      = "~/dev/s/private/skills/bkmr-memory"
description = "Persistent memory via the bkmr CLI"

# [subagents.code-reviewer]
# source = "~/dev/s/private/agents/code-reviewer.md"

# [prompts.adr]
# source = "~/dev/s/private/prompts/adr.prompt.md"


# ─── MCP servers (canonical, agent-agnostic) ───────────────────────────
# Two types: "stdio" (process) or "http"/"sse" (URL). Per-agent quirks
# (e.g. copilot-cli rewriting stdio → local) live in the mcp_format
# translator, NOT here. ${VAR} and ${VAR:-default} interpolate inside
# `env` and `headers` only.

# [servers.github]
# type    = "stdio"
# command = "npx"
# args    = ["-y", "@modelcontextprotocol/server-github"]
# env     = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }

# [servers.atlassian]
# type  = "http"
# url   = "https://example.com/mcp/"
# tools = ["*"]
# [servers.atlassian.headers]
# X-Atlassian-Token = "${CONFLUENCE_TOKEN:-XXX}"


# ─── Profiles: composable bundles ──────────────────────────────────────
# A profile names a set of artifact references (skills/subagents/prompts/servers).
# `extends` composes profiles depth-first, parent-first; first occurrence wins
# on dedup; per-type (not cross-type).

[profiles.minimal]
description  = "Bare-minimum daily set"
instructions = ["AGENT-md"]
skills       = ["bkmr-memory"]

# [profiles.tw]
# extends     = ["minimal"]
# description = "My default loadout"
# skills      = ["bkmr-memory"]
# subagents   = ["code-reviewer"]
# servers     = ["github", "atlassian"]


# ─── Scopes: deployment bindings ───────────────────────────────────────
# Each scope binds one profile to one or more agents.
#   - root unset → global scope (uses paths.global)
#   - root set   → project scope (uses paths.project joined under root)
#                  Skipped with warning if root does not exist on disk.
#   - enabled = false → skipped silently by `apply` and `diff`;
#                       still listed by `status` / `scopes` / `doctor`.
# Cross-scope rule: same (agent, root) pair MUST appear in at most one
# enabled scope (would otherwise cause symlink churn).

[[scopes]]
name    = "global"
profile = "minimal"
agents  = ["claude-code"]

# [[scopes]]
# name    = "project:my-repo"
# profile = "minimal"
# agents  = ["claude-code"]
# root    = "~/dev/work/my-repo"
# enabled = true
"""


if __name__ == "__main__":
    app()

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
from twagent.deploy import apply as run_apply
from twagent.diff import compute_diff
from twagent.doctor import check as doctor_check
from twagent.extractor import extract_from_file
from twagent.selector import (
    is_interactive_terminal,
    parse_select_value,
    select_interactive,
    validate_names,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path.home() / ".config" / "twagent" / "config.toml"

_APP_HELP = """\
twagent — one config, every AI agent.

Manages instructions, skills, subagents, prompts, and MCP servers across
Claude Code, Copilot CLI, Pi, and friends from a single canonical TOML.

  Edit config       :  twagent edit                (or 'edit --init' first time)
  See what's loaded :  twagent agents | profiles | scopes | status
  Preview a deploy  :  twagent apply --dry-run     (secrets masked by default)
  Deploy for real   :  twagent apply
  See what changed  :  twagent diff
  Find drift        :  twagent doctor

Glossary:
  scope    A binding that says "deploy profile X to agents [A,B] (under root R)".
           Without scopes, the rest of the config is just data.
  profile  A reusable bundle of artifact references (skills/subagents/
           prompts/MCP servers). Profiles can `extends` other profiles.
  artifact A single skill/subagent/prompt/MCP server, registered by name.
"""

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help=_APP_HELP,
)
console = Console()
err_console = Console(stderr=True)

CAPABILITIES = ("instructions", "skills", "subagents", "prompts", "mcp")
SELECTABLE = ("skills", "subagents", "prompts", "mcp")


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

By default ('twagent apply' with no flags) reconciles every enabled scope:
renders instruction files, symlinks file artifacts, and writes per-agent
MCP JSON. Idempotent — running it twice in a row should produce zero
filesystem changes the second time.

How the filters compose:
  --scope / --agent  pick WHICH scope(s) and WHICH agent(s) to act on.
  --only             picks the kind of artifact (instructions / skills /
                     subagents / prompts / mcp).
  --select           picks individual artifacts by name (e.g. 'tw-review').
                     Use 'none' to deploy zero of the selectable kinds.

Quick distinction (the part everyone gets wrong):
  --only mcp                 → deploy ALL servers in this scope's profile,
                               but NOTHING ELSE (no skills, no instructions).
  --select tw-review,tw-fix  → deploy ONLY those two named artifacts,
                               filtered out of the scope's profile.
  --only mcp --select github → deploy only the 'github' server.

Examples:
  twagent apply                                # sync everything
  twagent apply --dry-run                      # preview, secrets masked
  twagent apply --scope global:claude          # one scope only
  twagent apply --agent claude-code            # one agent across all scopes
  twagent apply --only skills,subagents        # only file-shaped artifacts
  twagent apply --select bkmr-memory,tw-review # only these two artifacts
  twagent apply --interactive                  # pick artifacts in a TUI
"""


@app.command(help=_APPLY_HELP)
def apply(
    scope: Optional[list[str]] = typer.Option(
        None,
        "--scope",
        help=(
            "Restrict to one or more scopes (by name). Repeatable. "
            "Without this flag, every enabled scope runs."
        ),
    ),
    agent: Optional[list[str]] = typer.Option(
        None,
        "--agent",
        help=(
            "Restrict to one or more agents (by id, e.g. 'claude-code'). "
            "Repeatable. Without this flag, every agent in the chosen scope(s) runs."
        ),
    ),
    only: Optional[str] = typer.Option(
        None,
        "--only",
        help=(
            "Restrict to one or more capability KINDS. "
            "Comma-separated; allowed values: "
            "instructions, skills, subagents, prompts, mcp. "
            "Example: --only mcp deploys MCP servers and nothing else."
        ),
        metavar="KINDS",
    ),
    select: Optional[str] = typer.Option(
        None,
        "--select",
        help=(
            "Restrict to specific NAMED artifacts (skill/subagent/prompt/server "
            "names — NOT scope or profile names). Comma-separated. "
            "Use the keyword 'none' to deploy zero artifacts of the selectable "
            "kinds. Example: --select bkmr-memory,github  "
            "Run 'twagent profiles' to see what names exist."
        ),
        metavar="NAMES|none",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        "-i",
        help=(
            "Open a terminal picker to choose artifacts to deploy. "
            "Mutually exclusive with --select."
        ),
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Show every write/symlink/render that WOULD happen, but change "
            "nothing on disk. Resolved secrets are masked unless --show-secrets."
        ),
    ),
    show_secrets: bool = typer.Option(
        False,
        "--show-secrets",
        help=(
            "Reveal resolved values from ${VAR} interpolation in --dry-run "
            "and 'diff' output. OFF by default — terminal scrollback leaks "
            "secrets. Real files written to disk always contain real values."
        ),
    ),
) -> None:
    if select is not None and interactive:
        err_console.print(
            "[red]Pick one: --select <names> OR --interactive[/red]\n"
            "  Both flags choose which artifacts to deploy; using both at once "
            "is ambiguous."
        )
        raise typer.Exit(2)

    config = _load_config()

    only_list: list[str] | None = None
    if only is not None:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
        bad = [c for c in only_list if c not in CAPABILITIES]
        if bad:
            err_console.print(
                f"[red]Unknown capability kind in --only:[/red] {', '.join(bad)}\n"
                f"  Allowed: {', '.join(CAPABILITIES)}"
            )
            raise typer.Exit(2)
        # FR-021: --select can't be used when --only restricts to instructions only
        if select is not None and only_list == ["instructions"]:
            err_console.print(
                "[red]--select cannot be combined with --only instructions[/red]\n"
                "  Instructions are template-rendered (one file per agent); "
                "there is no list of named artifacts to pick from."
            )
            raise typer.Exit(2)

    select_list: list[str] | None = None
    if select is not None:
        try:
            select_list = parse_select_value(select)
        except ValueError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
        # Validate names against the union of all selectable artifacts
        available = (
            set(config.skills)
            | set(config.subagents)
            | set(config.prompts)
            | set(config.servers)
        )
        if select_list:
            try:
                validate_names(select_list, available, "artifact")
            except ValueError as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(2)

    if interactive:
        if not is_interactive_terminal():
            err_console.print("[red]--interactive requires a TTY[/red]")
            raise typer.Exit(2)
        items: dict[str, str] = {}
        for n, s in config.skills.items():
            items[n] = "[skill]"
        for n in config.subagents:
            items[n] = "[subagent]"
        for n in config.prompts:
            items[n] = "[prompt]"
        for n in config.servers:
            items[n] = "[mcp]"
        chosen = select_interactive(items)
        if chosen is None:
            err_console.print("Cancelled.")
            raise typer.Exit(0)
        select_list = chosen

    result = run_apply(
        config,
        scope_filter=list(scope) if scope else None,
        agent_filter=list(agent) if agent else None,
        only=only_list,
        select=select_list,
        dry_run=dry_run,
        show_secrets=show_secrets,
    )

    if dry_run:
        for line in result.dry_run_log:
            typer.echo(line)
        if result.disabled_scopes:
            err_console.print(
                f"\n(disabled scopes skipped: {', '.join(result.disabled_scopes)})"
            )

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
Print every scope and its current state.

State is one of:
  enabled                 will run on the next 'apply'
  disabled                turned off via 'enabled = false' in config
  skipped (root missing)  project scope whose root directory does not exist
"""


@app.command(help=_STATUS_HELP)
def status() -> None:
    config = _load_config()
    table = Table(title="Scopes")
    table.add_column("Scope")
    table.add_column("Profile")
    table.add_column("Agents")
    table.add_column("State")
    for sc in config.scopes:
        if not sc.enabled:
            state = "disabled"
        elif sc.root is not None and not sc.root.exists():
            state = f"skipped (root missing: {sc.root})"
        else:
            state = "enabled" + (f" (root: {sc.root})" if sc.root else "")
        table.add_row(sc.name, sc.profile, ", ".join(sc.agents), state)
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
        help="Emit machine-parseable JSON instead of a table. Useful in scripts.",
    ),
) -> None:
    config = _load_config()
    if output_json:
        out = {
            agent_id: {
                "capabilities": list(agent.capabilities),
                "mcp_format": agent.mcp_format,
                "paths_global": {
                    k: [str(p) for p in v] for k, v in agent.paths_global.items()
                },
                "paths_project": {
                    k: [str(p) for p in v] for k, v in agent.paths_project.items()
                },
                "templates": agent.templates,
            }
            for agent_id, agent in config.agents.items()
        }
        typer.echo(json.dumps(out, indent=2))
        return

    table = Table(title="Agents")
    table.add_column("Agent")
    table.add_column("Capabilities")
    table.add_column("MCP format")
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


_SCOPES_HELP = """\
List every scope with its state. Alias of 'status'.

A scope is a deployment binding: it ties one profile to one or more
agents at a location (global or under a project root). Without scopes,
nothing gets deployed.
"""


@app.command(help=_SCOPES_HELP)
def scopes() -> None:
    # Alias of `status` (per FR-026); shares the same body to avoid drift.
    status()


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
Open your canonical config (or an agent's instruction template) in $EDITOR.

Defaults to opening the canonical config at ~/.config/twagent/config.toml
(or whatever --config points at). Falls back to 'vi' if $EDITOR is unset.

  --init               First-time setup: create a comprehensive, commented
                       starter config at the target path before opening it.
  --template <agent>   Open the named agent's instruction template instead
                       (resolved against [common] templates_dir).

Examples:
  twagent edit                              # open the config
  twagent edit --init                       # bootstrap then open
  twagent edit --template claude-code       # open Claude's instruction template
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
        help=(
            "Open the named agent's instruction template instead of the "
            "canonical config. Example: --template claude-code"
        ),
        metavar="AGENT",
    ),
) -> None:
    if init and template is not None:
        err_console.print("[red]--init and --template are mutually exclusive[/red]")
        raise typer.Exit(2)

    if template is not None:
        config = _load_config()
        if template not in config.agents:
            err_console.print(f"[red]Unknown agent: {template}[/red]")
            raise typer.Exit(2)
        agent = config.agents[template]
        tpl_name = agent.templates.get("instructions")
        if not tpl_name:
            err_console.print(
                f"[red]Agent {template} has no instructions template[/red]"
            )
            raise typer.Exit(2)
        if config.common.templates_dir is None:
            from twagent import __path__ as pkg_path

            tpl_path = Path(pkg_path[0]) / "templates" / tpl_name
        else:
            tpl_path = config.common.templates_dir / tpl_name
        target = tpl_path
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

# Schema migration knob. v1 ships at 1; bump only when you upgrade twagent.
schema_version = 1

# Optional dotenv loaded BEFORE ${VAR} interpolation runs against MCP env/headers.
# Path is relative to this config file. Env vars from os.environ override dotenv.
# env_file = "secrets.env"


# ─── Common: shared variables + tool-wide settings ─────────────────────
[common]
# Override the bundled templates dir if you keep your Jinja templates elsewhere.
# templates_dir = "~/.config/twagent/templates"

[common.vars]
# Variables visible to ALL agents' instruction templates.
# Per-agent [agents.<id>.vars] overrides any key listed here.
user_name = "you"
work_email = "you@example.com"


# ─── Agents ────────────────────────────────────────────────────────────
# An agent has:
#   capabilities  : subset of {instructions, skills, subagents, prompts, mcp}
#   mcp_format    : required iff "mcp" in capabilities; one of:
#                   claude-code | copilot-cli | pi | vscode | opencode
#   paths.global  : per-capability list of destination paths (always a list)
#   paths.project : same shape (instructions may be omitted)
#   templates     : per-capability template filename (today: instructions only)
#   vars          : per-agent Jinja context (overrides [common.vars])

[agents.claude-code]
capabilities = ["instructions", "skills", "mcp"]
mcp_format   = "claude-code"

[agents.claude-code.paths.global]
instructions = ["~/.claude/CLAUDE.md"]
skills       = ["~/.claude/skills"]
mcp          = ["~/.claude.json"]

[agents.claude-code.paths.project]
# When a [[scopes]] sets a `root`, project paths are joined under that root.
skills = [".claude/skills"]
mcp    = [".mcp.json"]

[agents.claude-code.templates]
instructions = "claude-code.md.j2"

[agents.claude-code.vars]
agent_name = "Claude"
extra_instructions = []   # template loops over this; add per-agent notes here


# Multi-target example: deploy copilot-cli instructions to BOTH the CLI
# location AND IntelliJ's global location at the same time.
# [agents.copilot-cli]
# capabilities = ["instructions", "skills", "mcp"]
# mcp_format   = "copilot-cli"   # rewrites stdio → local in compiled JSON
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
# [agents.copilot-cli.templates]
# instructions = "copilot-cli.md.j2"
#
# [agents.copilot-cli.vars]
# agent_name = "Copilot"
# extra_instructions = [
#   "When running commands which output more than 4KB you MUST pipe to a file.",
# ]


# ─── Artifact registries ───────────────────────────────────────────────
# Each entry has a `source` (absolute or ~-prefixed) and optional `description`.
# Source-missing is a warning at load time; an error in `twagent doctor` and
# at deploy time.

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
description = "Bare-minimum daily set"
skills      = ["bkmr-memory"]

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

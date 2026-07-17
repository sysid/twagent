"""twagent CLI — apply, diff, status, agents, profiles, scopes, doctor, extract, edit."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from twagent import __version__
from typing import Final

from twagent.config import (
    EXPANSION_KINDS,
    ConfigError,
    FileArtifact,
    Server,
    load,
)
from twagent.deploy import apply_global, apply_here
from twagent.diff import compute_diff
from twagent.doctor import check as doctor_check
from twagent.extractor import extract_from_file
from twagent.info import InfoReport, Section, collect_info
from twagent.selector import (
    is_interactive_terminal,
    parse_select_value,
    resolve_selection,
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
  Deploy locally    :  twagent apply --select <names>   (default: into cwd)
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
  Default mode writes to cwd via paths.project.*; --global writes to
  canonical paths.global.* paths.
"""

app = typer.Typer(
    add_completion=True,
    no_args_is_help=True,
    help=_APP_HELP,
)
console = Console()
err_console = Console(stderr=True)


# ─── Global options ─────────────────────────────────────────────────────


class _GlobalOptions:
    config_path: Path = DEFAULT_CONFIG
    verbose: bool = False


_OPTS = _GlobalOptions()


def _version_callback(value: bool) -> None:
    # Eager: print and exit before any subcommand runs (and before config load),
    # so `twagent --version` works even with no/invalid config present.
    if value:
        typer.echo(__version__)
        raise typer.Exit()


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
    version: bool = typer.Option(
        False,
        "--version",
        help="Print the installed twagent version and exit.",
        callback=_version_callback,
        is_eager=True,
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


# ─── apply ──────────────────────────────────────────────────────────────


_APPLY_HELP = """\
Deploy your resolved configuration to disk.

Two modes:

  default (no flag)  Deploy a CLI-supplied selection to the CURRENT
                     directory via each agent's `paths.project.*` joined
                     under cwd. Requires --select. Auto-selects agents
                     whose capabilities serve at least one kind in the
                     selection (or use --agent to narrow).

  --global           Deploy each agent's `global_profile` to its
                     `paths.global.*` paths (~/.claude/, ~/.copilot/, ...).
                     Idempotent.

--select is exhaustive: only the kinds derivable from the selection are
deployed. `--select e2e-emea` (servers-only profile) writes the MCP file
and nothing else — no instruction render, no skill symlinks. To deploy
everything an agent has globally, use `apply --global` (no --select).

--select takes profile names AND/OR artifact names, mixed (skills,
subagents, prompts, servers, AND instructions are all selectable by name):

  twagent apply --select e2e-emea
                                          # one profile name
  twagent apply --select core,tw-cucumber-to-http,github
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
  -G/--global  -a/--agent  -s/--select  -i/--interactive
  -n/--dry-run -S/--show-secrets
"""


@app.command(help=_APPLY_HELP)
def apply(
    global_mode: bool = typer.Option(
        False,
        "--global",
        "-G",
        help=(
            "Global mode: deploy each agent's `global_profile` to its "
            "paths.global.* paths. Default (no flag) deploys the --select "
            "set to the current directory via paths.project.*."
        ),
    ),
    agent: list[str] | None = typer.Option(
        None,
        "--agent",
        "-a",
        help=(
            "Restrict to one or more agents (by id, e.g. 'claude-code'). "
            "Repeatable. In local (default) mode, also forces inclusion of all "
            "capabilities the agent supports (including instructions)."
        ),
    ),
    select: str | None = typer.Option(
        None,
        "--select",
        "-s",
        help=(
            "Comma-separated list of profile names AND/OR artifact names. "
            "Each name resolves to either a profile (expanded via `extends`) "
            "or a single artifact (skill/subagent/prompt/server). REQUIRED "
            "in local mode (the default); optional in --global mode where "
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
    dedup: bool = typer.Option(
        True,
        "--dedup/--no-dedup",
        help=(
            "Local mode only: skip skills/subagents/prompts already present in "
            "the agent's paths.global.* dir, since agents read both layers. "
            "ON by default; --no-dedup forces local copies of global artifacts."
        ),
    ),
) -> None:
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
        # Group by kind, alphabetical within kind. Stable display order helps
        # muscle memory and makes the returned selection deterministic.
        items: dict[str, str] = {}
        for n in sorted(config.profiles):
            items[n] = "[profile]"
        for n in sorted(config.instructions):
            items[n] = "[instruction]"
        for n in sorted(config.skills):
            items[n] = "[skill]"
        for n in sorted(config.subagents):
            items[n] = "[subagent]"
        for n in sorted(config.prompts):
            items[n] = "[prompt]"
        for n in sorted(config.servers):
            items[n] = "[mcp]"
        # When --select is also given, pre-check the EXPANDED contents of
        # any profile in the selection (not just the profile name itself).
        # This makes `-s tw-claude -i` show the skills/servers the profile
        # would deploy, ready to be trimmed or extended in one screen.
        preselected: set[str] | None = None
        if select_list:
            try:
                expanded = resolve_selection(select_list, config)
            except ValueError as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(2)
            preselected = set()
            for kind in EXPANSION_KINDS:
                preselected.update(getattr(expanded, kind))
        try:
            chosen = select_interactive(items, preselected=preselected)
        except RuntimeError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2)
        if chosen is None:
            err_console.print("Cancelled.")
            raise typer.Exit(0)
        select_list = chosen

    if here:
        if not select_list:
            err_console.print(
                "[red]Local deploy requires --select <names> (or --interactive)[/red]\n"
                "  There is no per-cwd default profile; you must say what to deploy. "
                "Use --global to deploy each agent's global_profile instead."
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
                dedup=dedup,
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
            err_console.print(f"[yellow]Warning:[/yellow] {escape(w)}")

    if not dry_run and result.written:
        err_console.print("[green]Wrote:[/green]")
        for path in result.written:
            err_console.print(f"  {escape(path)}")

    if result.has_errors:
        err_console.print("\n[red]Errors:[/red]")
        for err in result.errors:
            err_console.print(f"  {escape(err)}")
        raise typer.Exit(1)

    # Always emit a one-line outcome so silent success is impossible.
    # `apply_here`/`apply_global` may have skipped every agent — the user
    # needs to see that, not stare at an empty terminal.
    mode = "dry-run" if dry_run else ("here" if here else "global")
    n_written = len(result.dry_run_log) if dry_run else len(result.written)
    label = "would change" if dry_run else "written"
    summary = (
        f"[green]Applied[/green] ({mode}): {n_written} {label}, "
        f"{len(result.warnings)} warning(s), {len(result.errors)} error(s)."
    )
    if n_written == 0 and not result.warnings and not result.errors:
        summary = (
            f"[yellow]No-op[/yellow] ({mode}): nothing to deploy. "
            f"Check `twagent agents` for paths.project.* coverage, "
            f"or re-run with --global / -v for details."
        )
    err_console.print(summary)


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
(what `twagent apply --global` will deploy), and the mcp_format. Agents
with no `global_profile` are deployable only via local `apply --select`.
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
            agent.global_profile
            or "[dim]— (no default; use local apply --select)[/dim]",
            agent.mcp_format or "—",
        )
    console.print(table)


# ─── info ───────────────────────────────────────────────────────────────


_INFO_HELP = """\
Show the deployed agent config for the CURRENT directory.

By default scans only the LOCAL layer (cwd/paths.project.*) — the
"what's live HERE" view. Pass --global to ALSO include the global layer
(paths.global.*, e.g. ~/.claude, ~/.copilot).

Every entry is tagged:

  managed    symlink resolves to a known artifact source
  unmanaged  entry not deployed by twagent
  dangling   broken symlink

Read-only; never writes; always exits 0. Use --json for scripting.
Note: ~/.claude.json is never shown (it is Claude Code's own state file,
not a twagent artifact).

SECURITY: MCP files are printed VERBATIM, including resolved ${VAR}
secrets (API keys, tokens). This is intentional (full content for human
inspection) and deviates from `diff`/`apply`, which redact by default.
Do not paste `info` output into issues or share your terminal scrollback.
"""

_STATUS_STYLE = {
    "managed": "green",
    "unmanaged": "yellow",
    "dangling": "red",
}


@app.command(help=_INFO_HELP)
def info(
    agent: list[str] | None = typer.Option(
        None,
        "--agent",
        "-a",
        help="Restrict to one or more agents (by id). Repeatable.",
    ),
    global_mode: bool = typer.Option(
        False,
        "--global",
        "-G",
        help="Also include the global layer (paths.global.*). Default: local only.",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Emit machine-parseable JSON instead of the Rich view.",
    ),
) -> None:
    config = _load_config()
    if agent:
        unknown = [a for a in agent if a not in config.agents]
        if unknown:
            err_console.print(f"[red]Unknown agent(s):[/red] {', '.join(unknown)}")
            err_console.print(f"  Available: {', '.join(sorted(config.agents))}")
            raise typer.Exit(2)
    report = collect_info(
        config, Path.cwd(), agent_filter=agent, include_global=global_mode
    )
    if output_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
        return
    _render_info(report)


def _render_info(report: InfoReport) -> None:
    console.print(
        Panel(
            f"twagent info · {report.cwd}\nglobal = paths.global.*    local = ./ (cwd)",
            expand=False,
        )
    )
    for agent_info in report.agents:
        console.print(
            f"\n[bold]▌ {escape(agent_info.agent_id)}[/bold]   "
            f"caps: {' · '.join(agent_info.capabilities)}"
        )
        for section in agent_info.sections:
            _render_section(section)
            console.print()  # blank line between sections for readability


def _render_section(section: Section) -> None:
    header = f"  [bold]{section.kind}[/bold] [dim]({section.layer})[/dim]"
    if section.error:
        console.print(f"{header}  [red]⚠ {escape(section.error)}[/red]")
        return
    if section.render_as == "linked":
        if not section.entries:
            console.print(
                f"{header}  [dim](not deployed: {escape(section.path)})[/dim]"
            )
            return
        table = Table(show_edge=False, pad_edge=False)
        table.add_column("Artifact")
        table.add_column("Status")
        table.add_column("Layer")
        table.add_column("→")
        for e in section.entries:
            style = _STATUS_STYLE.get(e.status, "white")
            mark = "⚠ dangling" if e.status == "dangling" else e.status
            table.add_row(
                escape(e.artifact or e.name),
                f"[{style}]{mark}[/{style}]",
                section.layer,
                escape(e.target) if e.target else "[dim]—[/dim]",
            )
        console.print(table)
    elif section.render_as == "instructions":
        mark = "[green]✓ present[/green]" if section.present else "[dim]✗ absent[/dim]"
        console.print(f"{header}  {mark}  [dim]{escape(section.path)}[/dim]")
    elif section.render_as == "mcp":
        if section.content is None:
            console.print(f"{header}  [dim](no mcp file: {escape(section.path)})[/dim]")
            return
        assert section.content_format is not None
        console.print(
            f"{header}  [cyan]{section.content_format.upper()}[/cyan]  "
            f"[red]⚠ raw — secrets shown[/red]  "
            f"[dim]{escape(section.path)}[/dim]"
        )
        console.print(
            Syntax(
                section.content,
                section.content_format,
                theme="ansi_dark",
                word_wrap=True,
            )
        )


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
    from twagent.expansion import expand_profile

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
            ", ".join(expanded.skills) or "—",
            ", ".join(expanded.subagents) or "—",
            ", ".join(expanded.prompts) or "—",
            ", ".join(expanded.servers) or "—",
        )
    console.print(table)


# `scopes` command removed in v2 — scopes don't exist anymore.
# Use `status` (per-agent global view) or `profiles` (composable bundles).


# ─── artefacts ──────────────────────────────────────────────────────────


_ARTEFACTS_HELP = """\
List or inspect registered artefacts.

Artefacts live in five registries: instructions, skills, subagents, prompts,
and servers. Use this command to discover the NAMES you'd pass to
`apply --select`, or to look up where an artefact's source lives.

  twagent artefacts                       # list every artefact, all kinds
  twagent artefacts bkmr                  # details for one artefact by name
  twagent artefacts --skills              # restrict to skills
  twagent artefacts --skills --servers    # multiple filters combine (OR)
"""


# Kind → registry attribute. For the `artefacts` command we list/lookup
# across these five registries (same set as EXPANSION_KINDS).
_ARTEFACT_KINDS: Final[tuple[str, ...]] = EXPANSION_KINDS


@app.command(help=_ARTEFACTS_HELP)
def artefacts(
    name: str | None = typer.Argument(
        None,
        help="If given, print details for this artefact instead of listing.",
    ),
    instructions: bool = typer.Option(False, "--instructions"),
    skills: bool = typer.Option(False, "--skills"),
    subagents: bool = typer.Option(False, "--subagents"),
    prompts: bool = typer.Option(False, "--prompts"),
    servers: bool = typer.Option(False, "--servers"),
) -> None:
    config = _load_config()
    filters = {
        "instructions": instructions,
        "skills": skills,
        "subagents": subagents,
        "prompts": prompts,
        "servers": servers,
    }
    kinds = (
        [k for k, on in filters.items() if on]
        if any(filters.values())
        else list(_ARTEFACT_KINDS)
    )

    if name is not None:
        for kind in kinds:
            registry = config.registry(kind)
            if name in registry:
                _print_artefact_details(kind, registry[name])
                return
        err_console.print(f"[red]Unknown artefact:[/red] {name!r}")
        all_in_scope = sorted({n for k in kinds for n in config.registry(k)})
        if all_in_scope:
            err_console.print(
                f"  Available in {', '.join(kinds)}: {', '.join(all_in_scope)}"
            )
        raise typer.Exit(2)

    table = Table(title="Artefacts")
    table.add_column("Kind")
    table.add_column("Name")
    table.add_column("Source / Type")
    table.add_column("Description")
    for kind in kinds:
        for art_name, item in sorted(config.registry(kind).items()):
            if isinstance(item, Server):
                src_or_type = item.type + (f" ({item.command})" if item.command else "")
                desc = "—"
            else:
                src_or_type = str(item.source)
                desc = item.description or "—"
            table.add_row(kind, art_name, src_or_type, desc)
    console.print(table)


def _print_artefact_details(kind: str, item: "FileArtifact | Server") -> None:
    """Print all relevant fields of a single artefact to stdout."""
    table = Table(title=f"{kind}.{item.name}")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("kind", kind)
    table.add_row("name", item.name)
    if isinstance(item, Server):
        table.add_row("type", item.type)
        if item.command:
            table.add_row("command", item.command)
        if item.args:
            table.add_row("args", " ".join(item.args))
        if item.url:
            table.add_row("url", item.url)
        if item.tools:
            table.add_row("tools", ", ".join(item.tools))
        if item.env:
            # Show keys only — values may be ${VAR}-interpolated secrets.
            table.add_row("env (keys)", ", ".join(item.env))
        if item.headers:
            table.add_row("headers (keys)", ", ".join(item.headers))
    else:
        table.add_row("source", str(item.source))
        if item.description:
            table.add_row("description", item.description)
    console.print(table)


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
    template: str | None = typer.Option(
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
#                     claude-code | copilot-cli | pi | codex | vscode | opencode
#                     (codex is the only TOML target; it omits `type` and
#                      renames headers -> http_headers. See docs/reference/config.md)
#   global_profile  : optional profile name; what `apply --global` deploys
#   paths.global    : per-capability list of destination paths (always a list)
#   paths.project   : same shape (used by local `apply`); instructions may be omitted
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
# When local `apply` runs, project paths are joined under cwd.
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


# ─── Deployment ────────────────────────────────────────────────────────
# v3 has no [[scopes]] block. Two modes instead:
#
#   global  Each agent's `global_profile` (above) is deployed by
#             twagent apply --global
#           Idempotent. Writes to paths.global.*.
#
#   local   Ad-hoc per-cwd deploy with an explicit selection:
#             cd ~/dev/myrepo
#             twagent apply --select <profile-or-artifact-names> -a <agent>
#           Writes under cwd via paths.project.*.
"""


if __name__ == "__main__":
    app()

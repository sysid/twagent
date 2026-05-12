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

app = typer.Typer(add_completion=True, no_args_is_help=True)
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
        help="Path to canonical TOML config (default: ~/.config/twagent/config.toml).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """twagent — unified AI-agent configuration framework."""
    _OPTS.config_path = config
    _OPTS.verbose = verbose
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _load_config():
    try:
        return load(_OPTS.config_path)
    except (ConfigError, FileNotFoundError) as exc:
        err_console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(2)


# ─── version ────────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


# ─── apply ──────────────────────────────────────────────────────────────


@app.command()
def apply(
    scope: Optional[list[str]] = typer.Option(None, "--scope"),
    agent: Optional[list[str]] = typer.Option(None, "--agent"),
    only: Optional[str] = typer.Option(None, "--only", help="csv of capabilities."),
    select: Optional[str] = typer.Option(
        None, "--select", help="csv of artifact names; 'none' = empty selection."
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Open interactive picker."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan, write nothing."),
    show_secrets: bool = typer.Option(False, "--show-secrets"),
) -> None:
    """Deploy resolved configuration to disk."""
    if select is not None and interactive:
        err_console.print(
            "[red]--select and --interactive are mutually exclusive[/red]"
        )
        raise typer.Exit(2)

    config = _load_config()

    only_list: list[str] | None = None
    if only is not None:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
        bad = [c for c in only_list if c not in CAPABILITIES]
        if bad:
            err_console.print(
                f"[red]Unknown capability in --only:[/red] {', '.join(bad)}"
            )
            raise typer.Exit(2)
        # FR-021: --select can't be used when --only restricts to instructions only
        if select is not None and only_list == ["instructions"]:
            err_console.print(
                "[red]--select does not apply to 'instructions' capability[/red]"
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


@app.command()
def diff(
    show_secrets: bool = typer.Option(False, "--show-secrets"),
) -> None:
    """Show pending changes between resolved config and on-disk state. Read-only."""
    config = _load_config()
    report = compute_diff(config, show_secrets=show_secrets)
    for line in report.lines:
        typer.echo(line)
    raise typer.Exit(0 if report.in_sync else 1)


# ─── status ─────────────────────────────────────────────────────────────


@app.command()
def status() -> None:
    """Print active scopes + deployment summary."""
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


@app.command()
def agents(
    output_json: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """List agents with their resolved paths and capabilities."""
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


@app.command()
def profiles() -> None:
    """List profiles with their expanded contents."""
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


@app.command()
def scopes() -> None:
    """List scopes with their state."""
    # Same shape as `status` but standalone command for FR-026.
    status()


# ─── doctor ─────────────────────────────────────────────────────────────


@app.command()
def doctor() -> None:
    """Health check: dangling links, missing sources, capability mismatches."""
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


@app.command()
def extract(
    mcp_json: Path = typer.Argument(..., exists=True, readable=True),
) -> None:
    """Print canonical TOML for an existing per-agent MCP config file (stdout-only)."""
    try:
        toml_text = extract_from_file(mcp_json)
    except (ValueError, FileNotFoundError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2)
    typer.echo(toml_text, nl=False)


# ─── edit ───────────────────────────────────────────────────────────────


@app.command()
def edit(
    init: bool = typer.Option(False, "--init", help="Create stub config if missing."),
    template: Optional[str] = typer.Option(
        None, "--template", help="Open agent's instruction template instead of config."
    ),
) -> None:
    """Open the canonical config or an agent's template in $EDITOR."""
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
schema_version = 1

[common]
[common.vars]
user_name = "you"

# Add agents, artifacts, profiles, scopes here.
# See: https://github.com/sysid/twagent
"""


if __name__ == "__main__":
    app()

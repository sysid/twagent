"""Deploy orchestration: symlink + render + compile.

Schema v2: scopes are gone. Two entry points:

    apply_global(config, ...)       — deploy each agent's `global_profile` to
                                       its `paths.global.*` paths.
    apply_here(config, cwd, ...)    — deploy a CLI-supplied selection (profiles
                                       and/or artifacts) to cwd via `paths.project.*`.

A `select` argument may be passed to either entry point to override the
default selection (overrides `global_profile` for `apply_global`).

Single module owns three deploy modes — kept together until tests want a split.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from twagent.config import (
    EXPANSION_KINDS,
    Agent,
    Configuration,
    FileArtifact,
    Server,
)
from twagent.interpolate import resolve_variables
from twagent.mcp import get_format, transform_for_format, write_config

logger = logging.getLogger(__name__)


# ─── Result types ───────────────────────────────────────────────────────


@dataclass
class LinkResult:
    """Outcome of a link_artifacts() call for one target directory."""

    created: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    relinked: list[str] = field(default_factory=list)
    dangling: list[str] = field(default_factory=list)
    skipped_real: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ApplyResult:
    """Aggregated outcome of an apply_*() run.

    For FR-032: end-of-run failure aggregation drives non-zero exit.
    """

    written: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run_log: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ─── Profile expansion ──────────────────────────────────────────────────


def expand_profile(config: Configuration, profile_name: str) -> dict[str, list[str]]:
    """Expand a profile's `extends` chain.

    Per data-model.md § Composition semantics: depth-first, parent-first then
    child; first occurrence wins on dedup; per-type (not cross-type).
    """
    logger.debug("deploy.expand_profile: profile=%s", profile_name)
    out: dict[str, list[str]] = {kind: [] for kind in EXPANSION_KINDS}
    visited: set[str] = set()

    def _walk(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        prof = config.profiles[name]
        for parent in prof.extends:
            _walk(parent)
        for kind in out:
            for ref in getattr(prof, kind):
                if ref not in out[kind]:
                    out[kind].append(ref)

    _walk(profile_name)
    logger.debug(
        "deploy.expand_profile %s → instructions=%d skills=%d subagents=%d "
        "prompts=%d servers=%d",
        profile_name,
        len(out["instructions"]),
        len(out["skills"]),
        len(out["subagents"]),
        len(out["prompts"]),
        len(out["servers"]),
    )
    return out


# ─── Selection-derived capability set ───────────────────────────────────


def _needed_capabilities(expanded: dict[str, list[str]]) -> set[str]:
    """Map a per-kind selection to the set of CAPABILITY names it touches.

    `servers` selection kind → `mcp` capability; everything else maps 1:1.
    Used by both `apply_global` (when --select overrides) and `apply_here`
    to skip capabilities the selection doesn't contribute to. Replaces the
    instructions-special-case logic of the v2 code path.
    """
    needed: set[str] = set()
    for kind, members in expanded.items():
        if not members:
            continue
        needed.add("mcp" if kind == "servers" else kind)
    return needed


# ─── Render (instructions) ──────────────────────────────────────────────


def render_template(
    template_path: Path,
    common_vars: dict[str, object],
    agent_vars: dict[str, object],
) -> str:
    """Render a Jinja template with two-layer var precedence.

    - Agent vars override common vars on key clash (FR-007).
    - StrictUndefined: missing variable → hard error (FR-008).
    - Output is rstrip()-ed then `+ "\\n"` written (FR-009).
    """
    logger.debug(
        "deploy.render_template: template=%s common_keys=%s agent_keys=%s",
        template_path,
        sorted(common_vars),
        sorted(agent_vars),
    )
    env = Environment(
        loader=FileSystemLoader(template_path.parent),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    template = env.get_template(template_path.name)
    context = {**common_vars, **agent_vars}
    rendered = template.render(**context)
    out = rendered.rstrip() + "\n"
    logger.debug(
        "deploy.render_template: %d byte(s) rendered from %s",
        len(out),
        template_path.name,
    )
    return out


# ─── Symlink hygiene ────────────────────────────────────────────────────


def link_artifacts(
    sources: dict[str, Path],
    target_dir: Path,
    dry_run: bool = False,
) -> LinkResult:
    """Symlink each `name → source_path` into target_dir.

    Hygiene rules per data-model.md § State transitions.
    """
    logger.debug(
        "deploy.link_artifacts: target=%s sources=%d dry_run=%s",
        target_dir,
        len(sources),
        dry_run,
    )
    result = LinkResult()

    if not target_dir.exists() and not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        for entry in target_dir.iterdir():
            if entry.is_symlink() and not entry.exists() and entry.name not in sources:
                result.dangling.append(entry.name)

    for name, source in sources.items():
        target = target_dir / name
        if not source.exists():
            result.errors.append(f"missing source for {name!r}: {source}")
            continue

        if target.is_symlink():
            current = target.resolve()
            if current == source.resolve():
                result.kept.append(name)
                continue
            if not dry_run:
                target.unlink()
                target.symlink_to(source)
            result.relinked.append(name)
        elif target.exists():
            result.skipped_real.append(name)
        else:
            if not dry_run:
                target.symlink_to(source)
            result.created.append(name)

    return result


# ─── Compile (MCP) ──────────────────────────────────────────────────────


def compile_mcp_for_agent(
    config: Configuration,
    agent: Agent,
    server_names: list[str],
    dry_run: bool = False,
    show_secrets: bool = False,
) -> dict:
    """Resolve interpolation, transform per agent.mcp_format, return JSON dict.

    When dry_run AND not show_secrets, secret values from `${VAR}` interpolation
    are masked in the returned dict (FR-023a).
    """
    logger.debug(
        "deploy.compile_mcp_for_agent: agent=%s mcp_format=%s servers=%d "
        "dry_run=%s show_secrets=%s",
        agent.id,
        agent.mcp_format,
        len(server_names),
        dry_run,
        show_secrets,
    )
    if agent.mcp_format is None:
        raise ValueError(f"agents.{agent.id}: mcp_format unset")

    if dry_run and not show_secrets:
        variables = {k: "***" for k in config.env_vars}
    else:
        variables = dict(config.env_vars)

    resolved_servers: dict[str, Server] = {}
    for name in server_names:
        server = config.servers[name]
        env = {
            k: _resolve_or_mask(v, variables, dry_run, show_secrets)
            for k, v in (server.env or {}).items()
        } or None
        headers = {
            k: _resolve_or_mask(v, variables, dry_run, show_secrets)
            for k, v in (server.headers or {}).items()
        } or None
        resolved_servers[name] = Server(
            name=server.name,
            type=server.type,
            command=server.command,
            args=server.args,
            url=server.url,
            tools=server.tools,
            env=env,
            headers=headers,
        )

    profile = get_format(agent.mcp_format)
    return transform_for_format(resolved_servers, profile)


def _resolve_or_mask(
    value: str,
    variables: dict[str, str],
    dry_run: bool,
    show_secrets: bool,
) -> str:
    """Resolve ${VAR}; mask the resolved value in dry-run+mask mode."""
    if dry_run and not show_secrets:
        try:
            return resolve_variables(value, variables)
        except ValueError:
            return value
    return resolve_variables(value, variables)


# ─── Top-level orchestrators (NEW in v2) ────────────────────────────────


def apply_global(
    config: Configuration,
    agent_filter: list[str] | None = None,
    select: list[str] | None = None,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> ApplyResult:
    """Deploy each agent's global_profile to its paths.global.* paths.

    `select`: if not None, OVERRIDES the agent's `global_profile`. The selection
    (per-kind expanded) is what gets deployed instead. Useful for ad-hoc
    "deploy this profile/artifact set globally for the day" overrides.
    """
    logger.debug(
        "deploy.apply_global: agent_filter=%s select=%s dry_run=%s show_secrets=%s",
        agent_filter,
        select,
        dry_run,
        show_secrets,
    )
    result = ApplyResult()

    selection_override: dict[str, list[str]] | None = None
    if select is not None:
        from twagent.selector import resolve_selection  # avoid cycle

        selection_override = resolve_selection(select, config)

    for agent_id, agent in config.agents.items():
        if agent_filter and agent_id not in agent_filter:
            logger.debug("deploy.apply_global: agent %s excluded by --agent filter", agent_id)
            continue

        if selection_override is not None:
            expanded = selection_override
        elif agent.global_profile is not None:
            expanded = expand_profile(config, agent.global_profile)
        else:
            msg = (
                f"{agent_id}: no global_profile set and no --select override — skipped."
            )
            logger.debug("deploy.apply_global: %s", msg)
            if agent_filter and agent_id in agent_filter:
                result.warnings.append(msg)
            continue

        if selection_override is not None:
            allowed_caps = _needed_capabilities(expanded)
            cap_iter = [c for c in agent.capabilities if c in allowed_caps]
        else:
            cap_iter = list(agent.capabilities)
        logger.debug(
            "deploy.apply_global: agent=%s caps_to_deploy=%s",
            agent_id,
            cap_iter,
        )

        for capability in cap_iter:
            targets = _global_targets(agent, capability)
            if not targets:
                logger.debug(
                    "deploy.apply_global: agent=%s cap=%s has no paths.global.%s; skipping",
                    agent_id,
                    capability,
                    capability,
                )
                continue
            logger.debug(
                "deploy.apply_global: deploying agent=%s cap=%s targets=%d",
                agent_id,
                capability,
                len(targets),
            )
            _apply_one(
                config,
                agent,
                capability,
                expanded,
                targets,
                result,
                dry_run=dry_run,
                show_secrets=show_secrets,
            )

    logger.debug(
        "deploy.apply_global DONE: written=%d errors=%d warnings=%d",
        len(result.written),
        len(result.errors),
        len(result.warnings),
    )
    return result


def apply_here(
    config: Configuration,
    cwd: Path,
    select: list[str],
    agent_filter: list[str] | None = None,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> ApplyResult:
    """Ad-hoc local deployment: render the selection under cwd via paths.project.

    `select` is required (there is no per-cwd default profile).

    Agent auto-selection: if `agent_filter` is None, deploys to every agent
    whose capabilities can serve at least one kind present in the selection
    (including instructions — instructions are now first-class artifacts
    selected via the same name resolution as everything else).
    """
    logger.debug(
        "deploy.apply_here: cwd=%s select=%s agent_filter=%s dry_run=%s show_secrets=%s",
        cwd,
        select,
        agent_filter,
        dry_run,
        show_secrets,
    )
    result = ApplyResult()

    from twagent.selector import resolve_selection  # avoid cycle

    expanded = resolve_selection(select, config)
    needed_caps = _needed_capabilities(expanded)

    for agent_id, agent in config.agents.items():
        if agent_filter and agent_id not in agent_filter:
            logger.debug("deploy.apply_here: agent %s excluded by --agent filter", agent_id)
            continue
        if not needed_caps & set(agent.capabilities):
            msg = (
                f"{agent_id}: capabilities {list(agent.capabilities)} do not "
                f"intersect selection kinds {sorted(needed_caps)} — skipped."
            )
            logger.debug("deploy.apply_here: %s", msg)
            # User-visible only if they explicitly asked for this agent.
            if agent_filter and agent_id in agent_filter:
                result.warnings.append(msg)
            continue

        for capability in agent.capabilities:
            if capability not in needed_caps:
                logger.debug(
                    "deploy.apply_here: agent %s capability %s not in needed_caps %s; skipping",
                    agent_id,
                    capability,
                    sorted(needed_caps),
                )
                continue
            targets = _project_targets(agent, capability, cwd)
            if not targets:
                msg = (
                    f"{agent_id}/{capability}: no `paths.project.{capability}` "
                    f"configured — selection won't deploy under cwd. "
                    f"Add it under [agents.{agent_id}.paths.project], or use --global."
                )
                logger.debug("deploy.apply_here: %s", msg)
                result.warnings.append(msg)
                continue
            logger.debug(
                "deploy.apply_here: deploying agent=%s cap=%s targets=%d",
                agent_id,
                capability,
                len(targets),
            )
            _apply_one(
                config,
                agent,
                capability,
                expanded,
                targets,
                result,
                dry_run=dry_run,
                show_secrets=show_secrets,
            )

    logger.debug(
        "deploy.apply_here DONE: written=%d errors=%d warnings=%d",
        len(result.written),
        len(result.errors),
        len(result.warnings),
    )
    return result


# ─── Per-(agent, capability) deploy dispatch ────────────────────────────


def _apply_one(
    config: Configuration,
    agent: Agent,
    capability: str,
    expanded: dict[str, list[str]],
    targets: list[Path],
    result: ApplyResult,
    *,
    dry_run: bool,
    show_secrets: bool,
) -> None:
    if capability == "instructions":
        _deploy_instructions(config, agent, expanded, targets, result, dry_run=dry_run)
    elif capability in ("skills", "subagents", "prompts"):
        _deploy_file_artifacts(
            config,
            agent,
            capability,
            expanded,
            targets,
            result,
            dry_run=dry_run,
        )
    elif capability == "mcp":
        _deploy_mcp(
            config,
            agent,
            expanded,
            targets,
            result,
            dry_run=dry_run,
            show_secrets=show_secrets,
        )


def _global_targets(agent: Agent, capability: str) -> list[Path]:
    return list(agent.paths_global.get(capability, []))


def _project_targets(agent: Agent, capability: str, cwd: Path) -> list[Path]:
    """Project-relative paths joined under cwd. cwd is the user's chosen root."""
    relative = agent.paths_project.get(capability, [])
    return [cwd / p for p in relative]


def _deploy_instructions(
    config: Configuration,
    agent: Agent,
    expanded: dict[str, list[str]],
    targets: list[Path],
    result: ApplyResult,
    *,
    dry_run: bool,
) -> None:
    """Render the (single) selected instruction template to each agent path.

    v3: instructions are first-class artifacts in `config.instructions`
    referenced by name from `profile.instructions`. The expanded selection
    contains 0..N instruction names; an agent has at most one instructions
    output per `paths.global.instructions` entry, so we enforce ≤ 1.
    """
    members = expanded.get("instructions", [])
    if not members:
        return
    if len(members) > 1:
        result.errors.append(
            f"{agent.id}/instructions: profile selection contains "
            f"{len(members)} instructions ({', '.join(members)}); "
            f"agents render at most ONE instruction per path."
        )
        return
    name = members[0]
    if name not in config.instructions:
        result.errors.append(f"{agent.id}/instructions: unknown instruction {name!r}")
        return
    tpl_path = config.instructions[name].source
    try:
        rendered = render_template(tpl_path, config.common.vars, agent.vars)
    except Exception as exc:
        result.errors.append(f"render {agent.id}/instructions/{name}: {exc}")
        return
    for target in targets:
        if dry_run:
            result.dry_run_log.append(f"render → {target}\n{_indent(rendered)}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered)
            result.written.append(str(target))


def _deploy_file_artifacts(
    config: Configuration,
    agent: Agent,
    capability: str,
    expanded: dict[str, list[str]],
    targets: list[Path],
    result: ApplyResult,
    *,
    dry_run: bool,
) -> None:
    registry: dict[str, FileArtifact] = getattr(config, capability)
    members = expanded.get(capability, [])
    sources = {name: registry[name].source for name in members if name in registry}
    for target_dir in targets:
        link_result = link_artifacts(sources, target_dir, dry_run=dry_run)
        for name in link_result.created + link_result.relinked:
            label = f"{agent.id}/{capability}/{name} → {target_dir}"
            if dry_run:
                result.dry_run_log.append(f"symlink {label}")
            else:
                result.written.append(label)
        for warning_name in link_result.dangling:
            msg = (
                f"{agent.id}/{capability}: dangling link at {target_dir / warning_name}"
            )
            result.warnings.append(msg)
            warnings.warn(msg, UserWarning, stacklevel=2)
        for err in link_result.errors:
            result.errors.append(f"{agent.id}/{capability}: {err}")


def _deploy_mcp(
    config: Configuration,
    agent: Agent,
    expanded: dict[str, list[str]],
    targets: list[Path],
    result: ApplyResult,
    *,
    dry_run: bool,
    show_secrets: bool,
) -> None:
    server_names = expanded.get("servers", [])
    try:
        compiled = compile_mcp_for_agent(
            config,
            agent,
            server_names,
            dry_run=dry_run,
            show_secrets=show_secrets,
        )
    except Exception as exc:
        result.errors.append(f"{agent.id}/mcp: {exc}")
        return
    for target in targets:
        if dry_run:
            import json

            preview = json.dumps(compiled, indent=2)
            result.dry_run_log.append(f"mcp → {target}\n{_indent(preview)}")
        else:
            write_config(compiled, target)
            result.written.append(str(target))


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


__all__ = [
    "ApplyResult",
    "LinkResult",
    "apply_global",
    "apply_here",
    "compile_mcp_for_agent",
    "expand_profile",
    "link_artifacts",
    "render_template",
]

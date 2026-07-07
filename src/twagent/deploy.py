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

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from twagent.config import (
    Agent,
    Configuration,
    FileArtifact,
    ProfileExpansion,
    Server,
)
from twagent.expansion import expand_profile, needed_capabilities
from twagent.interpolate import resolve_variables
from twagent.mcp import get_format, transform_for_format, write_config
from twagent.selector import resolve_selection

logger = logging.getLogger(__name__)


# ─── Result types ───────────────────────────────────────────────────────


@dataclass
class LinkResult:
    """Outcome of a link_artifacts() call for one target directory."""

    created: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    relinked: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
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


@dataclass
class DeployContext:
    """Bundle of values threaded through every per-(agent, capability) helper.

    Internal-only: orchestrators (`apply_global` / `apply_here`) build a
    `DeployContext` then dispatch per-capability handlers against it. Public
    APIs are unchanged.
    """

    config: Configuration
    agent: Agent
    expanded: ProfileExpansion
    result: ApplyResult
    dry_run: bool = False
    show_secrets: bool = False
    dedup_global: set[str] | None = None


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
            if entry.is_symlink() and entry.name not in sources:
                if not dry_run:
                    entry.unlink()
                result.removed.append(entry.name)

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
    selection_override: ProfileExpansion | None = (
        resolve_selection(select, config) if select is not None else None
    )
    result = _apply_to_agents(
        config,
        agent_filter=agent_filter,
        dry_run=dry_run,
        show_secrets=show_secrets,
        mode_label="global",
        resolve_expanded=lambda agent: _resolve_global_expansion(
            config, agent, selection_override, agent_filter
        ),
        cap_iter=lambda agent, expanded: _global_cap_iter(
            agent, expanded, selection_override
        ),
        targets_of=lambda agent, cap: _global_targets(agent, cap),
        warn_on_no_targets=False,
    )
    return result


def apply_here(
    config: Configuration,
    cwd: Path,
    select: list[str],
    agent_filter: list[str] | None = None,
    dry_run: bool = False,
    show_secrets: bool = False,
    dedup: bool = True,
) -> ApplyResult:
    """Ad-hoc local deployment: render the selection under cwd via paths.project.

    `select` is required (there is no per-cwd default profile).

    Agent auto-selection: if `agent_filter` is None, deploys to every agent
    whose capabilities can serve at least one kind present in the selection
    (including instructions — instructions are now first-class artifacts
    selected via the same name resolution as everything else).

    `dedup`: when True (default), skip symlinked artifacts (skills/subagents/
    prompts) already present on-disk in the agent's `paths.global.*` dir, since
    agents read both layers and a local copy would duplicate them.
    """
    logger.debug(
        "deploy.apply_here: cwd=%s select=%s agent_filter=%s dry_run=%s "
        "show_secrets=%s dedup=%s",
        cwd,
        select,
        agent_filter,
        dry_run,
        show_secrets,
        dedup,
    )
    expanded = resolve_selection(select, config)
    needed_caps = needed_capabilities(expanded)
    result = _apply_to_agents(
        config,
        agent_filter=agent_filter,
        dry_run=dry_run,
        show_secrets=show_secrets,
        mode_label="here",
        resolve_expanded=lambda agent: _resolve_here_expansion(
            agent, expanded, needed_caps, agent_filter
        ),
        cap_iter=lambda agent, _exp: [
            c for c in agent.capabilities if c in needed_caps
        ],
        targets_of=lambda agent, cap: _project_targets(agent, cap, cwd),
        warn_on_no_targets=True,
        dedup_of=(lambda agent: _global_artifact_names(agent)) if dedup else None,
    )
    return result


# ─── Shared orchestration ───────────────────────────────────────────────


def _apply_to_agents(
    config: Configuration,
    *,
    agent_filter: list[str] | None,
    dry_run: bool,
    show_secrets: bool,
    mode_label: str,
    resolve_expanded: Callable[[Agent], "ProfileExpansion | _Skip | None"],
    cap_iter: Callable[[Agent, ProfileExpansion], list[str]],
    targets_of: Callable[[Agent, str], list[Path]],
    warn_on_no_targets: bool,
    dedup_of: Callable[[Agent], set[str]] | None = None,
) -> ApplyResult:
    """Drive the per-(agent, capability) deploy loop shared by both modes.

    Mode-specific behaviour is injected via callbacks:
      - `resolve_expanded`: produce the ProfileExpansion for an agent, or a
        `_Skip(msg)` sentinel to surface a warning when explicitly requested,
        or None to skip silently.
      - `cap_iter`: yield the capabilities of an agent to deploy for, in order.
      - `targets_of`: resolve the target paths for one (agent, capability).
      - `warn_on_no_targets`: when True (here mode), emit a "no paths.project"
        warning if `targets_of` returns []; when False (global mode), silently skip.
    """
    result = ApplyResult()
    for agent_id, agent in config.agents.items():
        if agent_filter and agent_id not in agent_filter:
            logger.debug(
                "deploy.apply_%s: agent %s excluded by --agent filter",
                mode_label,
                agent_id,
            )
            continue
        outcome = resolve_expanded(agent)
        if outcome is None:
            continue
        if isinstance(outcome, _Skip):
            logger.debug("deploy.apply_%s: %s", mode_label, outcome.msg)
            if outcome.user_visible:
                result.warnings.append(outcome.msg)
            continue
        expanded = outcome
        caps = cap_iter(agent, expanded)
        logger.debug(
            "deploy.apply_%s: agent=%s caps_to_deploy=%s",
            mode_label,
            agent_id,
            caps,
        )
        for capability in caps:
            targets = targets_of(agent, capability)
            if not targets:
                if warn_on_no_targets:
                    msg = (
                        f"{agent_id}/{capability}: no `paths.project.{capability}` "
                        f"configured — selection won't deploy under cwd. "
                        f"Add it under [agents.{agent_id}.paths.project], or use --global."
                    )
                    logger.debug("deploy.apply_%s: %s", mode_label, msg)
                    result.warnings.append(msg)
                else:
                    logger.debug(
                        "deploy.apply_%s: agent=%s cap=%s has no paths.%s.%s; skipping",
                        mode_label,
                        agent_id,
                        capability,
                        "project" if warn_on_no_targets else "global",
                        capability,
                    )
                continue
            logger.debug(
                "deploy.apply_%s: deploying agent=%s cap=%s targets=%d",
                mode_label,
                agent_id,
                capability,
                len(targets),
            )
            ctx = DeployContext(
                config=config,
                agent=agent,
                expanded=expanded,
                result=result,
                dry_run=dry_run,
                show_secrets=show_secrets,
                dedup_global=dedup_of(agent) if dedup_of else None,
            )
            _apply_one(ctx, capability, targets)
    logger.debug(
        "deploy.apply_%s DONE: written=%d errors=%d warnings=%d",
        mode_label,
        len(result.written),
        len(result.errors),
        len(result.warnings),
    )
    return result


@dataclass
class _Skip:
    """Sentinel returned from a mode's resolve_expanded callback to skip an agent.

    `user_visible` controls whether the message is added to result.warnings
    (used only when the user explicitly named this agent via --agent).
    """

    msg: str
    user_visible: bool = False


def _resolve_global_expansion(
    config: Configuration,
    agent: Agent,
    selection_override: "ProfileExpansion | None",
    agent_filter: list[str] | None,
) -> "ProfileExpansion | _Skip | None":
    if selection_override is not None:
        return selection_override
    if agent.global_profile is not None:
        return expand_profile(config, agent.global_profile)
    return _Skip(
        msg=f"{agent.id}: no global_profile set and no --select override — skipped.",
        user_visible=bool(agent_filter and agent.id in agent_filter),
    )


def _global_cap_iter(
    agent: Agent,
    expanded: ProfileExpansion,
    selection_override: "ProfileExpansion | None",
) -> list[str]:
    if selection_override is not None:
        allowed_caps = needed_capabilities(expanded)
        return [c for c in agent.capabilities if c in allowed_caps]
    return list(agent.capabilities)


def _resolve_here_expansion(
    agent: Agent,
    expanded: ProfileExpansion,
    needed_caps: set[str],
    agent_filter: list[str] | None,
) -> "ProfileExpansion | _Skip | None":
    if not needed_caps & set(agent.capabilities):
        return _Skip(
            msg=(
                f"{agent.id}: capabilities {list(agent.capabilities)} do not "
                f"intersect selection kinds {sorted(needed_caps)} — skipped."
            ),
            user_visible=bool(agent_filter and agent.id in agent_filter),
        )
    return expanded


# ─── Per-(agent, capability) deploy dispatch ────────────────────────────


def _apply_one(ctx: DeployContext, capability: str, targets: list[Path]) -> None:
    handler = _DEPLOY_DISPATCH.get(capability)
    if handler is None:
        return
    handler(ctx, capability, targets)


def _global_targets(agent: Agent, capability: str) -> list[Path]:
    return list(agent.paths_global.get(capability, []))


def _project_targets(agent: Agent, capability: str, cwd: Path) -> list[Path]:
    """Project-relative paths joined under cwd. cwd is the user's chosen root."""
    relative = agent.paths_project.get(capability, [])
    return [cwd / p for p in relative]


_DEDUP_KINDS = ("skills", "subagents", "prompts")


def _global_artifact_names(agent: Agent) -> set[str]:
    """Names of symlinked artifacts already present on-disk at the global layer.

    Local apply skips these: agents read both layers, so a project copy of a
    globally-deployed skill/subagent/prompt is a pure duplicate. MCP and
    instructions are excluded — they are merged/rendered files, not dirs.
    """
    names: set[str] = set()
    for capability in _DEDUP_KINDS:
        for dir_path in agent.paths_global.get(capability, []):
            if dir_path.is_dir():
                names.update(entry.name for entry in dir_path.iterdir())
    return names


def _deploy_instructions(ctx: DeployContext, targets: list[Path]) -> None:
    """Render the (single) selected instruction template to each agent path.

    v3: instructions are first-class artifacts in `config.instructions`
    referenced by name from `profile.instructions`. The expanded selection
    contains 0..N instruction names; an agent has at most one instructions
    output per `paths.global.instructions` entry, so we enforce ≤ 1.
    """
    config, agent, expanded, result = ctx.config, ctx.agent, ctx.expanded, ctx.result
    members = expanded.instructions
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
        if ctx.dry_run:
            result.dry_run_log.append(f"render → {target}\n{_indent(rendered)}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered)
            result.written.append(str(target))


def _deploy_file_artifacts(
    ctx: DeployContext, capability: str, targets: list[Path]
) -> None:
    config, agent, expanded, result = ctx.config, ctx.agent, ctx.expanded, ctx.result
    # Dispatch table only routes file kinds here; cast narrows the union.
    registry = cast(dict[str, FileArtifact], config.registry(capability))
    members = expanded.get(capability)
    if ctx.dedup_global:
        skipped = [n for n in members if n in ctx.dedup_global]
        members = [n for n in members if n not in ctx.dedup_global]
        for name in skipped:
            label = f"{agent.id}/{capability}/{name} (already global; skipped)"
            if ctx.dry_run:
                result.dry_run_log.append(f"dedup {label}")
            logger.debug("deploy.dedup: %s", label)
    sources = {name: registry[name].source for name in members if name in registry}
    for target_dir in targets:
        link_result = link_artifacts(sources, target_dir, dry_run=ctx.dry_run)
        for name in link_result.created + link_result.relinked:
            label = f"{agent.id}/{capability}/{name} → {target_dir}"
            if ctx.dry_run:
                result.dry_run_log.append(f"symlink {label}")
            else:
                result.written.append(label)
        for name in link_result.removed:
            label = f"removed {agent.id}/{capability}/{name} ← {target_dir / name}"
            if ctx.dry_run:
                result.dry_run_log.append(label)
            else:
                result.written.append(label)
        for err in link_result.errors:
            result.errors.append(f"{agent.id}/{capability}: {err}")


def _deploy_mcp(ctx: DeployContext, targets: list[Path]) -> None:
    config, agent, expanded, result = ctx.config, ctx.agent, ctx.expanded, ctx.result
    server_names = expanded.servers
    try:
        compiled = compile_mcp_for_agent(
            config,
            agent,
            server_names,
            dry_run=ctx.dry_run,
            show_secrets=ctx.show_secrets,
        )
    except Exception as exc:
        result.errors.append(f"{agent.id}/mcp: {exc}")
        return
    for target in targets:
        if ctx.dry_run:
            preview = json.dumps(compiled, indent=2)
            result.dry_run_log.append(f"mcp → {target}\n{_indent(preview)}")
        else:
            write_config(compiled, target)
            result.written.append(str(target))


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


# ─── Dispatch table ─────────────────────────────────────────────────────
# Defined after handlers so name resolution works at module load.


def _dispatch_instructions(
    ctx: DeployContext, _capability: str, targets: list[Path]
) -> None:
    _deploy_instructions(ctx, targets)


def _dispatch_mcp(ctx: DeployContext, _capability: str, targets: list[Path]) -> None:
    _deploy_mcp(ctx, targets)


_DEPLOY_DISPATCH: dict[str, Callable[[DeployContext, str, list[Path]], None]] = {
    "instructions": _dispatch_instructions,
    "skills": _deploy_file_artifacts,
    "subagents": _deploy_file_artifacts,
    "prompts": _deploy_file_artifacts,
    "mcp": _dispatch_mcp,
}


__all__ = [
    "ApplyResult",
    "LinkResult",
    "apply_global",
    "apply_here",
    "compile_mcp_for_agent",
    "link_artifacts",
    "render_template",
]

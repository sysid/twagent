"""Deploy orchestration: symlink + render + compile per (scope, agent, capability).

Single module owns three deploy modes — kept together until tests want a split.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from twagent.config import (
    Agent,
    Configuration,
    FileArtifact,
    Scope,
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
    """Aggregated outcome of an apply() run.

    For FR-032: end-of-run failure aggregation drives non-zero exit.
    """

    written: list[str] = field(default_factory=list)
    skipped_scopes: list[str] = field(default_factory=list)
    disabled_scopes: list[str] = field(default_factory=list)
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
    out: dict[str, list[str]] = {
        "skills": [],
        "subagents": [],
        "prompts": [],
        "servers": [],
    }
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
        "deploy.expand_profile %s → skills=%d subagents=%d prompts=%d servers=%d",
        profile_name,
        len(out["skills"]),
        len(out["subagents"]),
        len(out["prompts"]),
        len(out["servers"]),
    )
    return out


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

    # Detect dangling links unrelated to the deployment
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
            # Real file/dir — never overwrite
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

    # Build per-call variable map. For dry-run+mask, use sentinel values
    # so any ${VAR} in env/headers becomes a literal mask in the output.
    if dry_run and not show_secrets:
        # Mask: every interpolation becomes "***"
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
        # variables map already contains "***" for every known key.
        # For unknown vars, we can't mask without resolving them first;
        # the resolver will raise for missing-default refs, which is fine.
        try:
            return resolve_variables(value, variables)
        except ValueError:
            return value  # leave literal unresolved for preview
    return resolve_variables(value, variables)


# ─── Top-level orchestrator ─────────────────────────────────────────────


@dataclass
class DeployTarget:
    """One concrete deployment unit: a path + the operation that produces it."""

    scope: str
    agent: str
    capability: str
    mode: str  # "symlink" | "render" | "compile"
    target_path: Path
    description: str = ""


def apply(
    config: Configuration,
    scope_filter: list[str] | None = None,
    agent_filter: list[str] | None = None,
    only: list[str] | None = None,
    select: list[str] | None = None,
    dry_run: bool = False,
    show_secrets: bool = False,
) -> ApplyResult:
    """Walk every (enabled scope, agent, capability) and deploy.

    `select`: if not None, narrow expanded artifact lists to ONLY these names.
    Applies across all list-shaped types (skills/subagents/prompts/servers).
    Per FR-021 it does NOT apply to instructions.
    """
    logger.debug(
        "deploy.apply: scope_filter=%s agent_filter=%s only=%s select=%s "
        "dry_run=%s show_secrets=%s",
        scope_filter,
        agent_filter,
        only,
        select,
        dry_run,
        show_secrets,
    )
    result = ApplyResult()

    for scope in config.scopes:
        if not scope.enabled:
            logger.debug("deploy.apply: scope %s disabled — skipping", scope.name)
            result.disabled_scopes.append(scope.name)
            continue
        if scope_filter and scope.name not in scope_filter:
            logger.debug("deploy.apply: scope %s filtered out", scope.name)
            continue
        if scope.root is not None and not scope.root.exists():
            msg = f"scope {scope.name!r}: root {scope.root} does not exist — skipped"
            result.skipped_scopes.append(scope.name)
            result.warnings.append(msg)
            warnings.warn(msg, UserWarning, stacklevel=2)
            continue

        expanded = expand_profile(config, scope.profile)
        if select is not None:
            expanded = {
                kind: [n for n in members if n in select]
                for kind, members in expanded.items()
            }

        for agent_id in scope.agents:
            if agent_filter and agent_id not in agent_filter:
                logger.debug(
                    "deploy.apply: agent %s filtered out in scope %s",
                    agent_id,
                    scope.name,
                )
                continue
            agent = config.agents[agent_id]
            for capability in agent.capabilities:
                if only is not None and capability not in only:
                    continue
                logger.debug(
                    "deploy.apply: deploying %s/%s/%s",
                    scope.name,
                    agent_id,
                    capability,
                )
                _apply_one(
                    config,
                    scope,
                    agent,
                    capability,
                    expanded,
                    result,
                    dry_run=dry_run,
                    show_secrets=show_secrets,
                )

    logger.debug(
        "deploy.apply DONE: written=%d errors=%d warnings=%d "
        "skipped_scopes=%d disabled_scopes=%d",
        len(result.written),
        len(result.errors),
        len(result.warnings),
        len(result.skipped_scopes),
        len(result.disabled_scopes),
    )
    return result


def _apply_one(
    config: Configuration,
    scope: Scope,
    agent: Agent,
    capability: str,
    expanded: dict[str, list[str]],
    result: ApplyResult,
    *,
    dry_run: bool,
    show_secrets: bool,
) -> None:
    paths = _resolve_target_paths(scope, agent, capability)
    if not paths:
        return

    if capability == "instructions":
        _deploy_instructions(config, scope, agent, paths, result, dry_run=dry_run)
    elif capability in ("skills", "subagents", "prompts"):
        _deploy_file_artifacts(
            config,
            scope,
            agent,
            capability,
            expanded,
            paths,
            result,
            dry_run=dry_run,
        )
    elif capability == "mcp":
        _deploy_mcp(
            config,
            scope,
            agent,
            expanded,
            paths,
            result,
            dry_run=dry_run,
            show_secrets=show_secrets,
        )


def _resolve_target_paths(scope: Scope, agent: Agent, capability: str) -> list[Path]:
    if scope.root is None:
        paths = agent.paths_global.get(capability, [])
    else:
        # Project-scope paths are joined under root (FR-013/FR-014)
        relative = agent.paths_project.get(capability, [])
        paths = [scope.root / p for p in relative]
    return paths


def _deploy_instructions(
    config: Configuration,
    scope: Scope,
    agent: Agent,
    targets: list[Path],
    result: ApplyResult,
    *,
    dry_run: bool,
) -> None:
    template_name = agent.templates.get("instructions")
    if not template_name:
        return
    if config.common.templates_dir is None:
        # Try the package's bundled templates dir
        from twagent import __path__ as pkg_path

        templates_dir = Path(pkg_path[0]) / "templates"
    else:
        templates_dir = config.common.templates_dir
    tpl_path = templates_dir / template_name
    try:
        rendered = render_template(tpl_path, config.common.vars, agent.vars)
    except Exception as exc:
        result.errors.append(f"render {scope.name}/{agent.id}/instructions: {exc}")
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
    scope: Scope,
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
            label = f"{scope.name}/{agent.id}/{capability}/{name} → {target_dir}"
            if dry_run:
                result.dry_run_log.append(f"symlink {label}")
            else:
                result.written.append(label)
        for warning_name in link_result.dangling:
            msg = f"{scope.name}/{agent.id}/{capability}: dangling link at {target_dir / warning_name}"
            result.warnings.append(msg)
            warnings.warn(msg, UserWarning, stacklevel=2)
        for err in link_result.errors:
            result.errors.append(f"{scope.name}/{agent.id}/{capability}: {err}")


def _deploy_mcp(
    config: Configuration,
    scope: Scope,
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
        result.errors.append(f"{scope.name}/{agent.id}/mcp: {exc}")
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
    "DeployTarget",
    "LinkResult",
    "apply",
    "compile_mcp_for_agent",
    "expand_profile",
    "link_artifacts",
    "render_template",
]

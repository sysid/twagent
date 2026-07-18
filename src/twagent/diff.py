"""Diff: report per-file divergence between resolved global config and on-disk state.

Schema v2: only the global side has a stable "intended state" (each agent's
`global_profile`). Ad-hoc local deployments via `apply --select` have no
persistent intended state to diff against, so `diff` covers globals only.

Read-only. Never modifies the filesystem.
"""

from __future__ import annotations

import difflib
import json
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from twagent.config import Agent, Configuration, FileArtifact, ProfileExpansion
from twagent.deploy import (
    _global_targets,
    compile_mcp_for_agent,
    render_template,
)
from twagent.expansion import expand_profile
from twagent.mcp import get_format, redact_legacy_runtime_values, serialize

logger = logging.getLogger(__name__)


@dataclass
class DiffReport:
    lines: list[str] = field(default_factory=list)
    in_sync: bool = True


@dataclass
class DiffContext:
    """Bundle threaded through the per-(agent, cap) diff helpers.

    Mirror of `deploy.DeployContext` minus dry_run (diff never writes).
    """

    config: Configuration
    agent: Agent
    expanded: ProfileExpansion
    report: DiffReport


def compute_diff(config: Configuration) -> DiffReport:
    logger.debug("diff.compute_diff: agents=%d", len(config.agents))
    report = DiffReport()
    for agent_id, agent in config.agents.items():
        if agent.global_profile is None:
            logger.debug("diff: agent %s has no global_profile; skipping", agent_id)
            continue
        expanded = expand_profile(config, agent.global_profile)
        ctx = DiffContext(
            config=config,
            agent=agent,
            expanded=expanded,
            report=report,
        )
        for cap in agent.capabilities:
            _diff_one(ctx, cap)
    logger.debug(
        "diff.compute_diff DONE: lines=%d in_sync=%s",
        len(report.lines),
        report.in_sync,
    )
    return report


def _diff_one(ctx: DiffContext, cap: str) -> None:
    logger.debug("diff._diff_one: agent=%s cap=%s", ctx.agent.id, cap)
    targets = _global_targets(ctx.agent, cap)
    if not targets:
        return
    handler = _DIFF_DISPATCH.get(cap)
    if handler is None:
        return
    handler(ctx, cap, targets)


def _diff_instructions(ctx: DiffContext, targets: list[Path]) -> None:
    config, agent, expanded, report = ctx.config, ctx.agent, ctx.expanded, ctx.report
    members = expanded.instructions
    if not members or members[0] not in config.instructions:
        return
    tpl_path = config.instructions[members[0]].source
    intended = render_template(tpl_path, config.common.vars, agent.vars)
    for target in targets:
        current = target.read_text() if target.exists() else ""
        if current != intended:
            report.in_sync = False
            label = f"{agent.id}/instructions {target}"
            diff = "\n".join(
                difflib.unified_diff(
                    current.splitlines(),
                    intended.splitlines(),
                    fromfile=f"{label} (current)",
                    tofile=f"{label} (intended)",
                    lineterm="",
                )
            )
            report.lines.append(diff)


def _diff_links(ctx: DiffContext, cap: str, targets: list[Path]) -> None:
    config, agent, expanded, report = ctx.config, ctx.agent, ctx.expanded, ctx.report
    # Dispatch table only routes file kinds here; cast narrows the union.
    registry = cast(dict[str, FileArtifact], config.registry(cap))
    members = expanded.get(cap)
    intended = {n: registry[n].source for n in members if n in registry}
    for target_dir in targets:
        for name, src in intended.items():
            link = target_dir / name
            label = f"{agent.id}/{cap}/{name}"
            if not link.exists() and not link.is_symlink():
                report.in_sync = False
                report.lines.append(f"+ {label} → {src} (missing)")
            elif not link.is_symlink():
                report.in_sync = False
                report.lines.append(f"! {label}: real entry at {link} blocks deploy")
            elif link.resolve() != src.resolve():
                report.in_sync = False
                report.lines.append(f"~ {label}: was → {link.resolve()}; now → {src}")


def _parse_current(text: str, serializer: str) -> dict:
    """Parse a deployed config into plain data. Raises ValueError if unparseable.

    Unlike write_config's parser this uses stdlib tomllib, not tomlkit: diff
    never writes back, so it wants plain dicts for structural comparison.
    """
    if serializer == "toml":
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(str(e)) from e
    if serializer == "json":
        try:
            return json.loads(text or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(str(e)) from e
    raise ValueError(f"unknown serializer: {serializer!r}")


def _diff_mcp(ctx: DiffContext, targets: list[Path]) -> None:
    config, agent, expanded, report = ctx.config, ctx.agent, ctx.expanded, ctx.report
    server_names = expanded.servers
    intended_dict = compile_mcp_for_agent(config, agent, server_names)
    # twagent owns ONLY this subtree; targets like ~/.claude.json and
    # ~/.codex/config.toml also hold harness state that must never register as
    # drift (mirrors write_config).
    assert agent.mcp_format is not None  # compile_mcp_for_agent raised otherwise
    profile = get_format(agent.mcp_format)
    top_key = profile.top_level_key
    intended_text = serialize(intended_dict, profile.serializer)
    for target in targets:
        label = f"{agent.id}/mcp {target}"
        if target.exists():
            try:
                current_data = _parse_current(target.read_text(), profile.serializer)
            except ValueError:
                report.in_sync = False
                report.lines.append(
                    f"! {label}: unparseable {profile.serializer.upper()} blocks merge"
                )
                continue
            current_dict: dict = {top_key: current_data.get(top_key, {})}
            canonical = {
                name: config.servers[name]
                for name in server_names
                if name in config.servers
            }
            redact_legacy_runtime_values(current_dict, canonical, profile)
            compare_current = serialize(current_dict, profile.serializer)
        else:
            compare_current = ""
        if compare_current != intended_text:
            report.in_sync = False
            diff = "\n".join(
                difflib.unified_diff(
                    compare_current.splitlines(),
                    intended_text.splitlines(),
                    fromfile=f"{label} (current, secrets masked)",
                    tofile=f"{label} (intended)",
                    lineterm="",
                )
            )
            report.lines.append(diff)


# ─── Dispatch table ─────────────────────────────────────────────────────


def _dispatch_diff_instructions(
    ctx: DiffContext, _cap: str, targets: list[Path]
) -> None:
    _diff_instructions(ctx, targets)


def _dispatch_diff_mcp(ctx: DiffContext, _cap: str, targets: list[Path]) -> None:
    _diff_mcp(ctx, targets)


_DIFF_DISPATCH: dict[str, Callable[[DiffContext, str, list[Path]], None]] = {
    "instructions": _dispatch_diff_instructions,
    "skills": _diff_links,
    "subagents": _diff_links,
    "prompts": _diff_links,
    "mcp": _dispatch_diff_mcp,
}

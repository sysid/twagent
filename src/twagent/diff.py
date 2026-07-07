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
from twagent.mcp import get_format

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
    show_secrets: bool = False


def compute_diff(
    config: Configuration,
    show_secrets: bool = False,
) -> DiffReport:
    logger.debug(
        "diff.compute_diff: agents=%d show_secrets=%s",
        len(config.agents),
        show_secrets,
    )
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
            show_secrets=show_secrets,
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


def _diff_mcp(ctx: DiffContext, targets: list[Path]) -> None:
    config, agent, expanded, report = ctx.config, ctx.agent, ctx.expanded, ctx.report
    show_secrets = ctx.show_secrets
    server_names = expanded.servers
    intended_dict = compile_mcp_for_agent(
        config,
        agent,
        server_names,
        dry_run=not show_secrets,
        show_secrets=show_secrets,
    )
    intended_text = json.dumps(intended_dict, indent=2) + "\n"
    # twagent owns ONLY this subtree; targets like ~/.claude.json also hold
    # harness state that must never register as drift (mirrors write_config).
    assert agent.mcp_format is not None  # compile_mcp_for_agent raised otherwise
    top_key = get_format(agent.mcp_format).top_level_key
    for target in targets:
        label = f"{agent.id}/mcp {target}"
        if target.exists():
            try:
                current_data = json.loads(target.read_text() or "{}")
            except json.JSONDecodeError:
                report.in_sync = False
                report.lines.append(f"! {label}: unparseable JSON blocks merge")
                continue
            current_dict: dict = {top_key: current_data.get(top_key, {})}
            if not show_secrets:
                _mask_like_intended(current_dict, intended_dict)
            compare_current = json.dumps(current_dict, indent=2) + "\n"
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


def _mask_like_intended(current: object, intended: object) -> None:
    """Mask current-side values to '***' exactly where the intended side is masked.

    The intended side masks only ${VAR}-derived values (deploy dry-run). Masking
    the current side symmetrically means secret VALUES never drive drift, while
    literal values (e.g. a URL header) still compare for real. A blanket
    key-based mask would hide genuine edits to literal env/header values.
    """
    if isinstance(current, dict) and isinstance(intended, dict):
        current_d = cast(dict[str, object], current)
        intended_d = cast(dict[str, object], intended)
        for key, value in current_d.items():
            intended_value = intended_d.get(key)
            if intended_value == "***" and isinstance(value, str):
                current_d[key] = "***"
            else:
                _mask_like_intended(value, intended_value)
    elif isinstance(current, list) and isinstance(intended, list):
        for current_item, intended_item in zip(current, intended):
            _mask_like_intended(current_item, intended_item)


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

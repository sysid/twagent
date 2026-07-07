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
    for target in targets:
        current = target.read_text() if target.exists() else ""
        compare_current = _mask_json_text(current) if not show_secrets else current
        if compare_current != intended_text:
            report.in_sync = False
            label = f"{agent.id}/mcp {target}"
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


def _mask_json_text(text: str) -> str:
    """Re-emit current JSON masking env/header values to '***'.

    Cheap implementation: parse, walk env/headers, replace strings.
    Returns text unchanged on parse error.
    """
    if not text:
        return ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    _mask_in_place(data)
    return json.dumps(data, indent=2) + "\n"


def _mask_in_place(node: object) -> None:
    if isinstance(node, dict):
        node_d = cast(dict[str, object], node)
        for key, value in node_d.items():
            if key in ("env", "headers") and isinstance(value, dict):
                value_d = cast(dict[str, object], value)
                for k, v in value_d.items():
                    if isinstance(v, str):
                        value_d[k] = "***"
            else:
                _mask_in_place(value)
    elif isinstance(node, list):
        for item in node:
            _mask_in_place(item)


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

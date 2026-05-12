"""Diff: report per-file divergence between resolved config and on-disk state.

Read-only. Reuses deploy.apply() in dry-run mode to derive the intended state,
then compares against current disk contents.
"""

from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from twagent.config import Configuration
from twagent.deploy import (
    _resolve_target_paths,
    compile_mcp_for_agent,
    expand_profile,
    render_template,
)

logger = logging.getLogger(__name__)


@dataclass
class DiffReport:
    lines: list[str] = field(default_factory=list)
    in_sync: bool = True


def compute_diff(
    config: Configuration,
    show_secrets: bool = False,
) -> DiffReport:
    logger.debug(
        "diff.compute_diff: scopes=%d show_secrets=%s",
        len(config.scopes),
        show_secrets,
    )
    report = DiffReport()
    for scope in config.scopes:
        if not scope.enabled:
            logger.debug("diff: skipping disabled scope %s", scope.name)
            continue
        if scope.root is not None and not scope.root.exists():
            logger.debug(
                "diff: skipping scope %s — root %s missing",
                scope.name,
                scope.root,
            )
            continue
        expanded = expand_profile(config, scope.profile)
        for agent_id in scope.agents:
            agent = config.agents[agent_id]
            for cap in agent.capabilities:
                _diff_one(
                    config,
                    scope,
                    agent,
                    cap,
                    expanded,
                    report,
                    show_secrets=show_secrets,
                )
    logger.debug(
        "diff.compute_diff DONE: lines=%d in_sync=%s",
        len(report.lines),
        report.in_sync,
    )
    return report


def _diff_one(
    config: Configuration,
    scope,
    agent,
    cap: str,
    expanded: dict[str, list[str]],
    report: DiffReport,
    *,
    show_secrets: bool,
) -> None:
    logger.debug(
        "diff._diff_one: scope=%s agent=%s cap=%s",
        scope.name,
        agent.id,
        cap,
    )
    targets = _resolve_target_paths(scope, agent, cap)
    if not targets:
        return
    if cap == "instructions":
        _diff_instructions(config, scope, agent, targets, report)
    elif cap in ("skills", "subagents", "prompts"):
        _diff_links(config, scope, agent, cap, expanded, targets, report)
    elif cap == "mcp":
        _diff_mcp(
            config, scope, agent, expanded, targets, report, show_secrets=show_secrets
        )


def _diff_instructions(config, scope, agent, targets, report):
    template_name = agent.templates.get("instructions")
    if not template_name:
        return
    if config.common.templates_dir is None:
        from twagent import __path__ as pkg_path

        templates_dir = Path(pkg_path[0]) / "templates"
    else:
        templates_dir = config.common.templates_dir
    tpl = templates_dir / template_name
    intended = render_template(tpl, config.common.vars, agent.vars)
    for target in targets:
        current = target.read_text() if target.exists() else ""
        if current != intended:
            report.in_sync = False
            label = f"{scope.name}/{agent.id}/instructions {target}"
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


def _diff_links(config, scope, agent, cap, expanded, targets, report):
    registry = getattr(config, cap)
    members = expanded.get(cap, [])
    intended = {n: registry[n].source for n in members if n in registry}
    for target_dir in targets:
        for name, src in intended.items():
            link = target_dir / name
            label = f"{scope.name}/{agent.id}/{cap}/{name}"
            if not link.exists() and not link.is_symlink():
                report.in_sync = False
                report.lines.append(f"+ {label} → {src} (missing)")
            elif not link.is_symlink():
                report.in_sync = False
                report.lines.append(f"! {label}: real entry at {link} blocks deploy")
            elif link.resolve() != src.resolve():
                report.in_sync = False
                report.lines.append(f"~ {label}: was → {link.resolve()}; now → {src}")


def _diff_mcp(config, scope, agent, expanded, targets, report, *, show_secrets):
    server_names = expanded.get("servers", [])
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
        # When masking, also mask current to avoid spurious diffs from secret values.
        compare_current = (
            _mask_json_text(current, intended_dict) if not show_secrets else current
        )
        if compare_current != intended_text:
            report.in_sync = False
            label = f"{scope.name}/{agent.id}/mcp {target}"
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


def _mask_json_text(text: str, intended: dict) -> str:
    """Re-emit current JSON with same field structure as intended, masking
    values whose key shape matches an env/header in the intended doc.

    Cheap implementation: parse current as JSON, walk env/headers and replace
    string values with '***'. Falls back to returning text unchanged on errors.
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
        for key, value in node.items():
            if key in ("env", "headers") and isinstance(value, dict):
                for k in list(value):
                    if isinstance(value[k], str):
                        value[k] = "***"
            else:
                _mask_in_place(value)
    elif isinstance(node, list):
        for item in node:
            _mask_in_place(item)

"""Health check: drift / dangling links / missing sources / capability mismatches."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from twagent.config import Configuration

logger = logging.getLogger(__name__)


@dataclass
class DoctorReport:
    errors: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def check(config: Configuration) -> DoctorReport:
    """Run health checks against config + on-disk deployed state."""
    logger.debug("doctor.check: starting")
    report = DoctorReport()
    _check_artifact_sources(config, report)
    _check_dangling_symlinks(config, report)
    _check_capability_mismatches(config, report)
    _check_agents_without_global_profile(config, report)
    logger.debug(
        "doctor.check DONE: errors=%d info=%d",
        len(report.errors),
        len(report.info),
    )
    return report


def _check_artifact_sources(config: Configuration, report: DoctorReport) -> None:
    logger.debug("doctor._check_artifact_sources")
    for kind, registry in (
        ("instructions", config.instructions),
        ("skills", config.skills),
        ("subagents", config.subagents),
        ("prompts", config.prompts),
    ):
        for name, art in registry.items():
            if not art.source.exists():
                report.errors.append(
                    f"{kind}.{name}: source does not exist: {art.source}"
                )


def _check_dangling_symlinks(config: Configuration, report: DoctorReport) -> None:
    """Walk every per-agent capability directory and warn on dangling links."""
    logger.debug("doctor._check_dangling_symlinks")
    seen: set[Path] = set()
    for agent in config.agents.values():
        for cap in ("skills", "subagents", "prompts"):
            if cap not in agent.capabilities:
                continue
            for d in agent.paths_global.get(cap, []):
                if d in seen or not d.exists():
                    continue
                seen.add(d)
                for entry in d.iterdir():
                    if entry.is_symlink() and not entry.exists():
                        report.errors.append(
                            f"dangling symlink: {entry} → {entry.readlink()}"
                        )


def _check_capability_mismatches(config: Configuration, report: DoctorReport) -> None:
    """Info: per-agent global_profile entries the agent's capabilities can't serve.

    Schema v2: there are no scopes; mismatches are derived from each agent's
    own `global_profile`. We expand the profile and report any kind it
    contains that the agent doesn't have a matching capability for.
    """
    logger.debug("doctor._check_capability_mismatches")
    from twagent.deploy import expand_profile  # avoid cycle

    for agent_id, agent in config.agents.items():
        if agent.global_profile is None:
            continue
        expanded = expand_profile(config, agent.global_profile)
        for kind, members in expanded.items():
            cap_name = "mcp" if kind == "servers" else kind
            if members and cap_name not in agent.capabilities:
                report.info.append(
                    f"agent {agent_id!r}: global_profile {agent.global_profile!r} "
                    f"contributes {len(members)} {kind} but agent lacks "
                    f"{cap_name!r} capability — silently skipped at apply time"
                )


def _check_agents_without_global_profile(
    config: Configuration, report: DoctorReport
) -> None:
    """Info: agents with no `global_profile` are deployable only via --here --select."""
    logger.debug("doctor._check_agents_without_global_profile")
    for agent_id, agent in config.agents.items():
        if agent.global_profile is None:
            report.info.append(
                f"agent {agent_id!r}: no global_profile set — bare "
                f"`twagent apply` will skip this agent. Use --here --select "
                f"or attach a global_profile."
            )

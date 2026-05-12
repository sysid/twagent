"""Health check: drift / dangling links / missing sources / capability mismatches."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from twagent.config import Configuration


@dataclass
class DoctorReport:
    errors: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


def check(config: Configuration) -> DoctorReport:
    """Run health checks against config + on-disk deployed state."""
    report = DoctorReport()
    _check_artifact_sources(config, report)
    _check_dangling_symlinks(config, report)
    _check_capability_mismatches(config, report)
    _check_disabled_scopes(config, report)
    return report


def _check_artifact_sources(config: Configuration, report: DoctorReport) -> None:
    for kind, registry in (
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


def _check_capability_mismatches(
    config: Configuration, report: DoctorReport
) -> None:
    """Info: profile entries the agent's capabilities don't support."""
    for scope in config.scopes:
        if not scope.enabled:
            continue
        prof = config.profiles.get(scope.profile)
        if prof is None:
            continue
        for agent_id in scope.agents:
            agent = config.agents[agent_id]
            for kind in ("skills", "subagents", "prompts", "servers"):
                cap_name = "mcp" if kind == "servers" else kind
                members = getattr(prof, kind)
                if members and cap_name not in agent.capabilities:
                    report.info.append(
                        f"scope {scope.name!r} agent {agent_id!r}: "
                        f"profile lists {len(members)} {kind} but agent lacks "
                        f"{cap_name!r} capability — silently skipped"
                    )


def _check_disabled_scopes(config: Configuration, report: DoctorReport) -> None:
    for scope in config.scopes:
        if not scope.enabled:
            report.info.append(f"scope {scope.name!r}: disabled")

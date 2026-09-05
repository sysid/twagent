"""Health check: drift / dangling links / missing sources / capability mismatches."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from twagent.config import EXPANSION_KINDS, Configuration

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
    _check_plugin_sources(config, report)
    _check_dangling_symlinks(config, report)
    _check_capability_mismatches(config, report)
    _check_agents_without_global_profile(config, report)
    _check_unreferenced_artifacts(config, report)
    _check_unreachable_profiles(config, report)
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
            if art.source.exists():
                continue
            if art.optional:
                report.info.append(
                    f"{kind}.{name}: expected absent on this machine "
                    f"(optional): {art.source}"
                )
            else:
                report.errors.append(
                    f"{kind}.{name}: source does not exist: {art.source}"
                )


def _check_plugin_sources(config: Configuration, report: DoctorReport) -> None:
    """Plugins whose source dir isn't on this machine.

    `_build_plugins` degrades these to placeholders so the config still
    loads; doctor is where they surface.
    """
    logger.debug("doctor._check_plugin_sources")
    for name, plugin in config.plugins.items():
        if plugin.available:
            continue
        if plugin.optional:
            report.info.append(
                f"plugins.{name}: expected absent on this machine "
                f"(optional): {plugin.source}"
            )
        else:
            report.errors.append(
                f"plugins.{name}: source dir does not exist: {plugin.source}"
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
    from twagent.expansion import expand_profile

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
    """Info: agents with no `global_profile` are deployable only via local apply --select."""
    logger.debug("doctor._check_agents_without_global_profile")
    for agent_id, agent in config.agents.items():
        if agent.global_profile is None:
            report.info.append(
                f"agent {agent_id!r}: no global_profile set — "
                f"`twagent apply --global` will skip this agent. Use "
                f"local `apply --select` or attach a global_profile."
            )


def _check_unreferenced_artifacts(config: Configuration, report: DoctorReport) -> None:
    """Info: registered artifacts that no profile names.

    Registration is only half of "make this available" — an artifact outside
    every profile is inert, and nothing else says so. Plugin members are
    excluded: a profile references them via `plugins = [...]`, never by their
    individual names.
    """
    logger.debug("doctor._check_unreferenced_artifacts")
    referenced: dict[str, set[str]] = {kind: set() for kind in EXPANSION_KINDS}
    for prof in config.profiles.values():
        for kind in referenced:
            referenced[kind].update(getattr(prof, kind))
    for plugin in config.plugins.values():
        for kind in referenced:
            referenced[kind].update(getattr(plugin, kind, []))

    for kind in EXPANSION_KINDS:
        for name in sorted(getattr(config, kind)):
            if name not in referenced[kind]:
                report.info.append(
                    f"{kind}.{name}: registered but named by no profile — "
                    f"`apply` will never deploy it. Add it to a profile, or "
                    f"drop the registry entry."
                )


def _check_unreachable_profiles(config: Configuration, report: DoctorReport) -> None:
    """Info: profiles no agent's `global_profile` closure reaches.

    Such a profile only ever deploys through `apply --select`. That is a
    legitimate design (environment swaps, per-repo bundles), so a profile can
    declare the intent with `adhoc = true`; anything left unmarked is a
    stranding worth seeing.
    """
    logger.debug("doctor._check_unreachable_profiles")
    reachable: set[str] = set()

    def _walk(name: str) -> None:
        if name in reachable or name not in config.profiles:
            return
        reachable.add(name)
        for parent in config.profiles[name].extends:
            _walk(parent)

    for agent in config.agents.values():
        if agent.global_profile is not None:
            _walk(agent.global_profile)

    for name, prof in config.profiles.items():
        if name in reachable or prof.adhoc:
            continue
        report.info.append(
            f"profile {name!r}: not reachable from any agent's global_profile "
            f"— deployable only via `apply --select`. Mark it `adhoc = true` "
            f"if that is intended."
        )

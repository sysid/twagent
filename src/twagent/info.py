"""Info: read-only snapshot of the effective deployed agent config at cwd.

Unlike `diff` (config-vs-disk divergence, globals only) and `doctor` (health
checks), `info` is a pure READ-OUT of on-disk reality across BOTH layers:

  global  -> agent.paths_global[capability]
  local   -> cwd / agent.paths_project[capability]

Provenance is the LAYER (global vs local). Profile attribution is deliberately
omitted: local deploys keep no recorded state and a single artifact may come
from many profiles, so the deploying profile is not recoverable from disk.

Read-only. Never modifies the filesystem.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import tomlkit
from tomlkit.exceptions import ParseError as TOMLParseError

from twagent.config import Agent, Configuration, FileArtifact
from twagent.mcp import get_format, redact_legacy_runtime_values, serialize

logger = logging.getLogger(__name__)

# Capabilities whose artifacts are symlinked name -> registry source on disk,
# so the artifact name and managed/unmanaged status are recoverable by readlink.
LINKED_KINDS: tuple[str, ...] = ("skills", "subagents", "prompts")

# WHY: ~/.claude.json is Claude Code's own state file (projects, history, auth,
# editor state) that merely also holds MCP config. It is huge and serves none of
# `info`'s purpose of showing twagent-deployed artifacts, so it is NEVER shown —
# not even under --global. (Tom, 2026-06-20.)
_EXCLUDED_PATHS: frozenset[Path] = frozenset({Path.home() / ".claude.json"})


@dataclass
class Entry:
    """One filesystem entry inside a scanned capability directory."""

    name: str
    status: str  # "managed" | "unmanaged" | "dangling"
    artifact: str | None = None  # registry name when managed
    target: str | None = None  # resolved symlink target, if any

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "artifact": self.artifact,
            "target": self.target,
        }


@dataclass
class Section:
    """One (agent, capability, layer) view of disk.

    render_as drives presentation:
      "linked"       -> `entries` populated (skills/subagents/prompts)
      "instructions" -> `present` set (rendered file: name not recoverable)
      "mcp"          -> `content`, `content_format`, and `variables_masked` set
    `error` is set (and the scan continues) when a path can't be read.
    """

    kind: str
    layer: str  # "global" | "local"
    path: str
    render_as: str  # "linked" | "instructions" | "mcp"
    entries: list[Entry] = field(default_factory=list)
    present: bool | None = None
    content: str | None = None
    content_format: str | None = None
    variables_masked: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "layer": self.layer,
            "path": self.path,
            "render_as": self.render_as,
            "entries": [e.as_dict() for e in self.entries],
            "present": self.present,
            "content": self.content,
            "content_format": self.content_format,
            "variables_masked": self.variables_masked,
            "error": self.error,
        }


@dataclass
class AgentInfo:
    agent_id: str
    capabilities: list[str]
    sections: list[Section] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "capabilities": list(self.capabilities),
            "sections": [s.as_dict() for s in self.sections],
        }


@dataclass
class InfoReport:
    cwd: str
    agents: list[AgentInfo] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "cwd": self.cwd,
            "agents": [a.as_dict() for a in self.agents],
        }


# ─── Classification ──────────────────────────────────────────────────────


def _build_source_index(
    registries: dict[str, dict[str, FileArtifact]],
) -> dict[Path, tuple[str, str]]:
    """Map every linked-artifact source path -> (kind, name).

    Keyed by the RESOLVED source so a deployed symlink's resolved target can be
    reverse-looked-up to the artifact that owns it.
    """
    index: dict[Path, tuple[str, str]] = {}
    for kind, registry in registries.items():
        for name, art in registry.items():
            index[art.source.resolve()] = (kind, name)
    return index


def _classify_entry(entry: Path, index: dict[Path, tuple[str, str]]) -> Entry:
    """Classify one directory entry as managed / unmanaged / dangling."""
    name = entry.name
    if entry.is_symlink():
        if not entry.exists():  # broken link target
            return Entry(name=name, status="dangling", target=str(entry.readlink()))
        resolved = entry.resolve()
        hit = index.get(resolved)
        if hit is not None:
            return Entry(
                name=name, status="managed", artifact=hit[1], target=str(resolved)
            )
        return Entry(name=name, status="unmanaged", target=str(resolved))
    # Plain file or directory that twagent never deploys as a link.
    return Entry(name=name, status="unmanaged", target=None)


# ─── Per-capability scanners ─────────────────────────────────────────────


def _scan_linked_dir(
    dir_path: Path, kind: str, layer: str, index: dict[Path, tuple[str, str]]
) -> Section:
    section = Section(kind=kind, layer=layer, path=str(dir_path), render_as="linked")
    if not dir_path.exists():
        return section  # absent dir => empty section (rendered as "not deployed")
    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: p.name)
    except OSError as exc:  # permissions etc. — fail soft, keep scanning
        section.error = f"unreadable: {exc}"
        return section
    section.entries = [_classify_entry(e, index) for e in entries]
    return section


def _scan_instructions(file_path: Path, layer: str) -> Section:
    # Instructions are RENDERED files, not symlinks: the source artifact name
    # is not recoverable from disk without re-rendering (that is `diff`'s job).
    # So info reports presence only.
    return Section(
        kind="instructions",
        layer=layer,
        path=str(file_path),
        render_as="instructions",
        present=file_path.exists(),
    )


def _parse_mcp_content(content: str, content_format: str) -> dict:
    if content_format == "json":
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            # Parser diagnostics are deliberately omitted: malformed input may
            # contain the credential this safe-by-default path is withholding.
            raise ValueError("unparseable JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("unparseable JSON: top level must be an object")
        return parsed
    if content_format == "toml":
        try:
            return tomlkit.parse(content)
        except TOMLParseError as exc:
            # Keep the same non-echoing failure contract as JSON above.
            raise ValueError("unparseable TOML") from exc
    raise ValueError(f"unknown MCP content format: {content_format!r}")


def _redact_mcp_content(
    content: str,
    config: Configuration,
    agent: Agent,
) -> str:
    assert agent.mcp_format is not None
    profile = get_format(agent.mcp_format)
    current = _parse_mcp_content(content, profile.serializer)
    current_servers = current.get(profile.top_level_key, {})
    server_names = current_servers if isinstance(current_servers, dict) else {}
    canonical = {
        name: config.servers[name] for name in server_names if name in config.servers
    }

    redact_legacy_runtime_values(current, canonical, profile)
    return serialize(current, profile.serializer)


def _scan_mcp(
    file_path: Path,
    layer: str,
    config: Configuration,
    agent: Agent,
    show_secrets: bool,
) -> Section:
    assert agent.mcp_format is not None
    content_format = get_format(agent.mcp_format).serializer
    section = Section(
        kind="mcp",
        layer=layer,
        path=str(file_path),
        render_as="mcp",
        content_format=content_format,
        variables_masked=not show_secrets,
    )
    if not file_path.exists():
        return section  # absent => no content
    try:
        content = file_path.read_text()
        section.content = (
            content if show_secrets else _redact_mcp_content(content, config, agent)
        )
    except ValueError as exc:
        section.error = f"{exc}; content withheld (use --show-secrets to inspect raw)"
    except OSError as exc:  # fail soft
        section.error = f"unreadable: {exc}"
    return section


# ─── Orchestration ───────────────────────────────────────────────────────


def _layer_paths(
    agent: Agent, capability: str, cwd: Path, include_global: bool
) -> list[tuple[str, Path]]:
    """Return (layer, path) pairs for a capability.

    Local (cwd/paths.project) is always included; global (paths.global) only
    when `include_global` is set (default: local-only — the "what's live HERE"
    view).
    """
    pairs: list[tuple[str, Path]] = []
    if include_global:
        for p in agent.paths_global.get(capability, []):
            pairs.append(("global", p))
    for p in agent.paths_project.get(capability, []):
        pairs.append(("local", cwd / p))
    return pairs


def collect_info(
    config: Configuration,
    cwd: Path,
    agent_filter: list[str] | None = None,
    include_global: bool = False,
    show_secrets: bool = False,
) -> InfoReport:
    """Build the on-disk snapshot of every agent's deployed config at cwd.

    By default only the LOCAL layer (cwd/paths.project) is scanned; pass
    `include_global=True` to also scan paths.global. Resolved variable values
    are masked unless `show_secrets=True`. Paths in `_EXCLUDED_PATHS` are never
    shown.
    """
    logger.debug(
        "info.collect_info: agents=%d cwd=%s filter=%s include_global=%s "
        "show_secrets=%s",
        len(config.agents),
        cwd,
        agent_filter,
        include_global,
        show_secrets,
    )
    index = _build_source_index(
        {k: cast(dict[str, FileArtifact], config.registry(k)) for k in LINKED_KINDS}
    )
    excluded = {p.resolve() for p in _EXCLUDED_PATHS}
    report = InfoReport(cwd=str(cwd))
    for agent_id, agent in config.agents.items():
        if agent_filter and agent_id not in agent_filter:
            continue
        agent_info = AgentInfo(agent_id=agent_id, capabilities=list(agent.capabilities))
        for capability in agent.capabilities:
            for layer, path in _layer_paths(agent, capability, cwd, include_global):
                if path.resolve() in excluded:
                    continue
                if capability in LINKED_KINDS:
                    agent_info.sections.append(
                        _scan_linked_dir(path, capability, layer, index)
                    )
                elif capability == "instructions":
                    agent_info.sections.append(_scan_instructions(path, layer))
                elif capability == "mcp":
                    agent_info.sections.append(
                        _scan_mcp(path, layer, config, agent, show_secrets)
                    )
        report.agents.append(agent_info)
    return report

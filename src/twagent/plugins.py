"""Claude Code plugin discovery.

A plugin is an unpacked directory with a `plugin.json` manifest mapping kinds
to subdirs (`skills/`, `agents/`, `prompts/`) plus optional `mcpServers`.

This module ONLY does filesystem discovery + manifest parsing. It returns a
neutral `PluginContents` and deliberately does not import `twagent.config`
(that would create an import cycle — config imports this module). The loader
in config.py turns this output into FileArtifact/Server/Plugin entries.
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginContents:
    """Pieces discovered in one plugin, keyed by on-disk basename.

    `skills`/`subagents`/`prompts` map name -> source path (dir for skills,
    file for the others). `servers` maps name -> the raw mcp blob from the
    manifest, to be validated/built by config._build_server.
    """

    name: str
    source: Path
    description: str | None
    skills: dict[str, Path] = field(default_factory=dict)
    subagents: dict[str, Path] = field(default_factory=dict)
    prompts: dict[str, Path] = field(default_factory=dict)
    servers: dict[str, dict] = field(default_factory=dict)


def discover_plugin(name: str, source: Path) -> PluginContents:
    """Parse `<source>/plugin.json` and discover all declared pieces.

    Raises FileNotFoundError if the source dir or plugin.json is missing,
    ValueError if the manifest is malformed or declares a non-existent dir.
    """
    logger.debug("plugins.discover_plugin: name=%s source=%s", name, source)
    manifest_path = source / "plugin.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"plugin {name!r}: no plugin.json at {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"plugin {name!r}: cannot parse {manifest_path}: {exc}")

    description = manifest.get("description")

    skills = _discover_skills(name, source, manifest)
    subagents = _discover_files(name, source, manifest, key="agents")
    prompts = _discover_files(name, source, manifest, key="prompts")
    servers = _discover_servers(source, manifest)

    return PluginContents(
        name=name,
        source=source,
        description=description,
        skills=skills,
        subagents=subagents,
        prompts=prompts,
        servers=servers,
    )


def _kind_dir(name: str, source: Path, manifest: dict, key: str) -> Path | None:
    """Resolve a manifest-declared kind dir; None if the key is absent."""
    rel = manifest.get(key)
    if rel is None:
        return None
    kind_dir = source / rel
    if not kind_dir.is_dir():
        raise ValueError(
            f"plugin {name!r}: manifest declares {key} dir {rel!r} "
            f"but {kind_dir} does not exist"
        )
    return kind_dir


def _discover_skills(name: str, source: Path, manifest: dict) -> dict[str, Path]:
    kind_dir = _kind_dir(name, source, manifest, "skills")
    if kind_dir is None:
        return {}
    out: dict[str, Path] = {}
    # Sorted for deterministic injection/collision ordering.
    for entry in sorted(kind_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        if not (entry / "SKILL.md").exists():
            warnings.warn(
                f"plugin {name!r}: skill dir {entry.name!r} has no SKILL.md — skipped",
                UserWarning,
                stacklevel=2,
            )
            continue
        out[entry.name] = entry
    return out


def _discover_files(
    name: str, source: Path, manifest: dict, *, key: str
) -> dict[str, Path]:
    kind_dir = _kind_dir(name, source, manifest, key)
    if kind_dir is None:
        return {}
    out: dict[str, Path] = {}
    for entry in sorted(kind_dir.glob("*.md"), key=lambda p: p.name):
        out[entry.name] = entry
    return out


def _discover_servers(source: Path, manifest: dict) -> dict[str, dict]:
    """Collect MCP servers from `mcpServers` in plugin.json and/or .mcp.json.

    A server name appearing in both sources is an intra-plugin collision.
    """
    out: dict[str, dict] = {}
    for server_name, blob in (manifest.get("mcpServers") or {}).items():
        out[server_name] = dict(blob)
    mcp_json = source / ".mcp.json"
    if mcp_json.exists():
        external = json.loads(mcp_json.read_text()).get("mcpServers") or {}
        for server_name, blob in external.items():
            if server_name in out:
                raise ValueError(
                    f"plugin server {server_name!r} defined in both "
                    f"plugin.json and .mcp.json"
                )
            out[server_name] = dict(blob)
    return dict(sorted(out.items()))

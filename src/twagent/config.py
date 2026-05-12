"""Canonical TOML schema: load, validate, build in-memory entities.

Single source of truth for the model. Per data-model.md, this module owns
Configuration, Common, Agent, Capability, FileArtifact, Server, Profile, Scope.

Module is intentionally one file. Will SPLIT only when LOC pressure or test
isolation demands it (Constitution Principle I — YAGNI).
"""

from __future__ import annotations

import logging
import os
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from twagent.interpolate import load_dotenv

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 1

CAPABILITIES = ("instructions", "skills", "subagents", "prompts", "mcp")
Capability = Literal["instructions", "skills", "subagents", "prompts", "mcp"]

MCP_FORMATS = ("claude-code", "copilot-cli", "pi", "vscode", "opencode")

# Capabilities whose paths.project entry MAY be omitted from per-agent config.
PROJECT_OPTIONAL_CAPABILITIES = ("instructions",)


class ConfigError(ValueError):
    """Raised when the canonical TOML fails validation at load time."""


# ─── Entities ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Common:
    templates_dir: Path | None = None
    vars: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Agent:
    id: str
    capabilities: tuple[Capability, ...]
    paths_global: dict[str, list[Path]]
    paths_project: dict[str, list[Path]]
    templates: dict[str, str]
    vars: dict[str, object]
    mcp_format: str | None = None


@dataclass(frozen=True)
class FileArtifact:
    name: str
    source: Path
    description: str | None = None


@dataclass(frozen=True)
class Server:
    name: str
    type: str = "stdio"
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    tools: list[str] | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class Profile:
    name: str
    description: str | None = None
    extends: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Scope:
    name: str
    profile: str
    agents: list[str]
    root: Path | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Configuration:
    schema_version: int
    common: Common
    agents: dict[str, Agent]
    skills: dict[str, FileArtifact]
    subagents: dict[str, FileArtifact]
    prompts: dict[str, FileArtifact]
    servers: dict[str, Server]
    profiles: dict[str, Profile]
    scopes: list[Scope]
    env_file: Path | None = None
    env_vars: dict[str, str] = field(default_factory=dict)


# ─── Loader ─────────────────────────────────────────────────────────────


def load(path: Path) -> Configuration:
    """Load and validate a canonical TOML config file."""
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    return _build(raw, path.parent)


def _build(raw: dict, base_dir: Path) -> Configuration:
    schema_version = raw.get("schema_version")
    if schema_version is None:
        raise ConfigError("Missing required field: schema_version")
    if not isinstance(schema_version, int):
        raise ConfigError("schema_version must be an integer")
    if schema_version > SUPPORTED_SCHEMA_VERSION:
        raise ConfigError(
            f"schema_version {schema_version} is newer than supported "
            f"({SUPPORTED_SCHEMA_VERSION}). Upgrade twagent."
        )

    env_file = None
    dotenv_vars: dict[str, str] = {}
    if "env_file" in raw:
        env_file = (base_dir / raw["env_file"]).resolve()
        # Hard error if declared but missing — matches spec edge case.
        dotenv_vars = load_dotenv(env_file)
    # Process env always available; dotenv layered first, real env wins on clash
    # (matches twmcp's interpolate semantics — see test_interpolate.py).
    env_vars: dict[str, str] = {**dotenv_vars, **dict(os.environ)}

    common = _build_common(raw.get("common", {}))
    agents = _build_agents(raw.get("agents", {}))
    skills = _build_artifacts(raw.get("skills", {}))
    subagents = _build_artifacts(raw.get("subagents", {}))
    prompts = _build_artifacts(raw.get("prompts", {}))
    servers = _build_servers(raw.get("servers", {}))
    profiles = _build_profiles(raw.get("profiles", {}))
    scopes = _build_scopes(raw.get("scopes", []))

    config = Configuration(
        schema_version=schema_version,
        common=common,
        agents=agents,
        skills=skills,
        subagents=subagents,
        prompts=prompts,
        servers=servers,
        profiles=profiles,
        scopes=scopes,
        env_file=env_file,
        env_vars=env_vars,
    )
    _validate(config)
    return config


def _build_common(raw: dict) -> Common:
    templates_dir = None
    if "templates_dir" in raw:
        templates_dir = Path(raw["templates_dir"]).expanduser()
    return Common(templates_dir=templates_dir, vars=dict(raw.get("vars", {})))


def _build_agents(raw: dict) -> dict[str, Agent]:
    out: dict[str, Agent] = {}
    for agent_id, blob in raw.items():
        caps = tuple(blob.get("capabilities", []))
        for c in caps:
            if c not in CAPABILITIES:
                raise ConfigError(
                    f"agents.{agent_id}: unknown capability {c!r}. "
                    f"Allowed: {', '.join(CAPABILITIES)}"
                )
        paths_blob = blob.get("paths", {})
        paths_global = _build_paths_section(paths_blob.get("global", {}))
        paths_project = _build_paths_section(paths_blob.get("project", {}))
        templates = dict(blob.get("templates", {}))
        vars_ = dict(blob.get("vars", {}))
        mcp_format = blob.get("mcp_format")
        out[agent_id] = Agent(
            id=agent_id,
            capabilities=caps,
            paths_global=paths_global,
            paths_project=paths_project,
            templates=templates,
            vars=vars_,
            mcp_format=mcp_format,
        )
    return out


def _build_paths_section(raw: dict) -> dict[str, list[Path]]:
    return {
        cap: [Path(p).expanduser() for p in paths]
        for cap, paths in raw.items()
    }


def _build_artifacts(raw: dict) -> dict[str, FileArtifact]:
    return {
        name: FileArtifact(
            name=name,
            source=Path(blob["source"]).expanduser(),
            description=blob.get("description"),
        )
        for name, blob in raw.items()
    }


def _build_servers(raw: dict) -> dict[str, Server]:
    out: dict[str, Server] = {}
    for name, blob in raw.items():
        server = Server(
            name=name,
            type=blob.get("type", "stdio"),
            command=blob.get("command"),
            args=list(blob["args"]) if "args" in blob else None,
            url=blob.get("url"),
            tools=list(blob["tools"]) if "tools" in blob else None,
            env=dict(blob["env"]) if "env" in blob else None,
            headers=dict(blob["headers"]) if "headers" in blob else None,
        )
        if server.type not in ("stdio", "http"):
            raise ConfigError(
                f"servers.{name}: type must be 'stdio' or 'http', got {server.type!r}"
            )
        if server.type == "stdio" and not server.command:
            raise ConfigError(
                f"servers.{name}: type=stdio requires 'command'"
            )
        if server.type == "http" and not server.url:
            raise ConfigError(
                f"servers.{name}: type=http requires 'url'"
            )
        out[name] = server
    return out


def _build_profiles(raw: dict) -> dict[str, Profile]:
    return {
        name: Profile(
            name=name,
            description=blob.get("description"),
            extends=list(blob.get("extends", [])),
            skills=list(blob.get("skills", [])),
            subagents=list(blob.get("subagents", [])),
            prompts=list(blob.get("prompts", [])),
            servers=list(blob.get("servers", [])),
        )
        for name, blob in raw.items()
    }


def _build_scopes(raw: list) -> list[Scope]:
    return [
        Scope(
            name=blob["name"],
            profile=blob["profile"],
            agents=list(blob["agents"]),
            root=Path(blob["root"]).expanduser() if "root" in blob else None,
            enabled=blob.get("enabled", True),
        )
        for blob in raw
    ]


# ─── Validation ─────────────────────────────────────────────────────────


def _validate(config: Configuration) -> None:
    _validate_agents(config)
    _validate_profiles(config)
    _validate_scopes(config)
    _check_artifact_sources(config)


def _validate_agents(config: Configuration) -> None:
    for agent_id, agent in config.agents.items():
        for cap in agent.capabilities:
            if cap not in agent.paths_global:
                raise ConfigError(
                    f"agents.{agent_id}: missing paths.global.{cap} for declared capability"
                )
            if cap not in PROJECT_OPTIONAL_CAPABILITIES and cap not in agent.paths_project:
                raise ConfigError(
                    f"agents.{agent_id}: missing paths.project.{cap} for declared capability"
                )
        if "mcp" in agent.capabilities:
            if not agent.mcp_format:
                raise ConfigError(
                    f"agents.{agent_id}: mcp_format required when 'mcp' in capabilities"
                )
            if agent.mcp_format not in MCP_FORMATS:
                raise ConfigError(
                    f"agents.{agent_id}: unknown mcp_format {agent.mcp_format!r}. "
                    f"Allowed: {', '.join(MCP_FORMATS)}"
                )
        if "instructions" in agent.capabilities:
            if "instructions" not in agent.templates:
                raise ConfigError(
                    f"agents.{agent_id}: templates.instructions required when "
                    f"'instructions' in capabilities"
                )
            if config.common.templates_dir is not None:
                tpl = config.common.templates_dir / agent.templates["instructions"]
                if not tpl.exists():
                    raise ConfigError(
                        f"agents.{agent_id}: instructions template not found: {tpl}"
                    )


def _validate_profiles(config: Configuration) -> None:
    # Reference resolution per type.
    for prof in config.profiles.values():
        for ref in prof.skills:
            if ref not in config.skills:
                raise ConfigError(
                    f"profiles.{prof.name}: unknown skill {ref!r}"
                )
        for ref in prof.subagents:
            if ref not in config.subagents:
                raise ConfigError(
                    f"profiles.{prof.name}: unknown subagent {ref!r}"
                )
        for ref in prof.prompts:
            if ref not in config.prompts:
                raise ConfigError(
                    f"profiles.{prof.name}: unknown prompt {ref!r}"
                )
        for ref in prof.servers:
            if ref not in config.servers:
                raise ConfigError(
                    f"profiles.{prof.name}: unknown server {ref!r}"
                )
        for parent in prof.extends:
            if parent not in config.profiles:
                raise ConfigError(
                    f"profiles.{prof.name}: extends unknown profile {parent!r}"
                )
    # Cycle detection on extends.
    for prof_name in config.profiles:
        _check_no_cycle(config.profiles, prof_name, [])


def _check_no_cycle(profiles: dict[str, Profile], current: str, stack: list[str]) -> None:
    if current in stack:
        raise ConfigError(
            f"profiles: cyclic extends chain: {' → '.join(stack + [current])}"
        )
    for parent in profiles[current].extends:
        _check_no_cycle(profiles, parent, stack + [current])


def _validate_scopes(config: Configuration) -> None:
    seen_names: set[str] = set()
    for scope in config.scopes:
        if scope.name in seen_names:
            raise ConfigError(f"scopes: duplicate name {scope.name!r}")
        seen_names.add(scope.name)
        if scope.profile not in config.profiles:
            raise ConfigError(
                f"scopes.{scope.name}: unknown profile {scope.profile!r}"
            )
        if not scope.agents:
            raise ConfigError(
                f"scopes.{scope.name}: agents list must be non-empty"
            )
        for agent_id in scope.agents:
            if agent_id not in config.agents:
                raise ConfigError(
                    f"scopes.{scope.name}: unknown agent {agent_id!r}"
                )
    # Cross-scope: an (agent, target-location) pair must be unique among enabled
    # scopes — otherwise two scopes write to the same physical paths and cause
    # symlink churn. A "target location" is the scope's root (None for globals).
    seen: dict[tuple[str, Path | None], str] = {}
    for scope in config.scopes:
        if not scope.enabled:
            continue
        for agent_id in scope.agents:
            key = (agent_id, scope.root)
            if key in seen:
                where = "globally" if scope.root is None else f"under {scope.root}"
                raise ConfigError(
                    f"scopes: agent {agent_id!r} appears {where} in two enabled "
                    f"scopes ({seen[key]!r} and {scope.name!r}); "
                    f"would cause symlink churn"
                )
            seen[key] = scope.name


def _check_artifact_sources(config: Configuration) -> None:
    """Missing source = warning, not hard error (FR-005)."""
    for registry, kind in (
        (config.skills, "skills"),
        (config.subagents, "subagents"),
        (config.prompts, "prompts"),
    ):
        for name, art in registry.items():
            if not art.source.exists():
                warnings.warn(
                    f"{kind}.{name}: source does not exist: {art.source}",
                    UserWarning,
                    stacklevel=3,
                )

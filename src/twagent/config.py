"""Canonical TOML schema: load, validate, build in-memory entities.

Single source of truth for the model. Per data-model.md, this module owns
Configuration, Common, Agent, Capability, FileArtifact, Server, Profile.

Schema v2 dropped the Scope entity. Global deployment is now driven by
each agent's `global_profile` attribute; ad-hoc local deployment is
driven by the CLI (`apply --select ...`). See plan file:
~/.claude/plans/maybe-we-need-to-cheerful-liskov.md

Module is intentionally one file. Will SPLIT only when LOC pressure or test
isolation demands it (Constitution Principle I — YAGNI).
"""

from __future__ import annotations

import difflib
import logging
import os
import tomllib
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, overload

from twagent.interpolate import load_dotenv
from twagent.plugins import discover_plugin

logger = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 3

CAPABILITIES: Final[tuple[str, ...]] = (
    "instructions",
    "skills",
    "subagents",
    "prompts",
    "mcp",
)

# Keys of the profile-expansion dict returned by `expand_profile` /
# `resolve_selection`. Differs from CAPABILITIES: "mcp" (capability) ↔
# "servers" (artifact registry name).
EXPANSION_KINDS: Final[tuple[str, ...]] = (
    "instructions",
    "skills",
    "subagents",
    "prompts",
    "servers",
)
Capability = Literal["instructions", "skills", "subagents", "prompts", "mcp"]

MCP_FORMATS = ("claude-code", "copilot-cli", "pi", "vscode", "opencode", "codex")

# Capabilities whose paths.project entry MAY be omitted from per-agent config.
PROJECT_OPTIONAL_CAPABILITIES: Final[tuple[str, ...]] = ("instructions",)


class ConfigError(ValueError):
    """Raised when the canonical TOML fails validation at load time."""


# ─── Entities ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Common:
    vars: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Agent:
    id: str
    capabilities: tuple[Capability, ...]
    paths_global: dict[str, list[Path]]
    paths_project: dict[str, list[Path]]
    vars: dict[str, object]
    mcp_format: str | None = None
    # v2: name of the profile that defines what this agent gets when
    # deployed globally (`twagent apply` with no flags). MUST resolve to an
    # existing entry in Configuration.profiles; None means "agent has no
    # global default; only deployable via --select".
    global_profile: str | None = None


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
    instructions: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)
    plugins: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Plugin:
    """A registered Claude Code plugin and the artifact names it injected.

    The member lists hold the bare names of pieces injected into the five
    artifact registries. There is no `instructions` list — CC plugins don't
    ship that kind.
    """

    name: str
    source: Path
    description: str | None = None
    skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileExpansion:
    """Result of expanding a profile (or selection) into per-kind member lists.

    Direct attribute access (`expanded.skills`) is preferred. `get(kind)` and
    `items()` exist for the cases where the kind is a runtime value bound
    from `EXPANSION_KINDS` or `agent.capabilities`.
    """

    instructions: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)

    def get(self, key: str) -> list[str]:
        return getattr(self, key, [])

    def items(self) -> list[tuple[str, list[str]]]:
        """Iterate (kind, members) pairs in EXPANSION_KINDS order."""
        return [(k, getattr(self, k)) for k in EXPANSION_KINDS]


@dataclass(frozen=True)
class Configuration:
    schema_version: int
    common: Common
    agents: dict[str, Agent]
    instructions: dict[str, FileArtifact]
    skills: dict[str, FileArtifact]
    subagents: dict[str, FileArtifact]
    prompts: dict[str, FileArtifact]
    servers: dict[str, Server]
    profiles: dict[str, Profile]
    plugins: dict[str, Plugin] = field(default_factory=dict)
    env_file: Path | None = None
    env_vars: dict[str, str] = field(default_factory=dict)

    @overload
    def registry(self, kind: Literal["servers"]) -> dict[str, Server]: ...
    @overload
    def registry(
        self, kind: Literal["instructions", "skills", "subagents", "prompts"]
    ) -> dict[str, FileArtifact]: ...
    @overload
    def registry(self, kind: str) -> dict[str, FileArtifact] | dict[str, Server]: ...

    def registry(self, kind: str) -> dict[str, FileArtifact] | dict[str, Server]:
        """Return the artefact registry for `kind` (one of EXPANSION_KINDS).

        Centralises the stringly-typed `getattr(config, kind)` pattern used
        across cli/deploy/diff/selector. Raises KeyError on unknown kinds —
        intentional: callers always pass a value from EXPANSION_KINDS, so a
        bad key is a programming error, not a user input.
        """
        registries: dict[str, dict[str, FileArtifact] | dict[str, Server]] = {
            "instructions": self.instructions,
            "skills": self.skills,
            "subagents": self.subagents,
            "prompts": self.prompts,
            "servers": self.servers,
        }
        return registries[kind]


# ─── Loader ─────────────────────────────────────────────────────────────


def load(path: Path) -> Configuration:
    """Load and validate a canonical TOML config file."""
    logger.debug("config.load: path=%s", path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    config = _build(raw, path.parent)
    logger.debug(
        "config.load OK: %d agents, %d instructions, %d skills, %d subagents, "
        "%d prompts, %d servers, %d profiles",
        len(config.agents),
        len(config.instructions),
        len(config.skills),
        len(config.subagents),
        len(config.prompts),
        len(config.servers),
        len(config.profiles),
    )
    return config


def _build(raw: dict, base_dir: Path) -> Configuration:
    logger.debug("config._build: base_dir=%s top-level keys=%s", base_dir, list(raw))
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
        logger.debug("config._build: loading env_file=%s", env_file)
        # Hard error if declared but missing — matches spec edge case.
        dotenv_vars = load_dotenv(env_file)
        logger.debug(
            "config._build: dotenv loaded %d keys from %s",
            len(dotenv_vars),
            env_file,
        )
    # Process env always available; dotenv layered first, real env wins on clash
    # (matches twmcp's interpolate semantics — see test_interpolate.py).
    env_vars: dict[str, str] = {**dotenv_vars, **dict(os.environ)}

    common = _build_common(raw.get("common", {}))
    agents = _build_agents(raw.get("agents", {}))
    instructions = _build_artifacts(raw.get("instructions", {}))
    skills = _build_artifacts(raw.get("skills", {}))
    subagents = _build_artifacts(raw.get("subagents", {}))
    prompts = _build_artifacts(raw.get("prompts", {}))
    servers = _build_servers(raw.get("servers", {}))
    profiles = _build_profiles(raw.get("profiles", {}))
    plugins = _build_plugins(
        raw.get("plugins", {}),
        skills=skills,
        subagents=subagents,
        prompts=prompts,
        servers=servers,
    )

    if "scopes" in raw:
        raise ConfigError(
            "[[scopes]] blocks are not supported (removed in v2). "
            "Use per-agent `global_profile` for global deployment + "
            "`twagent apply --select <names>` for ad-hoc local."
        )

    config = Configuration(
        schema_version=schema_version,
        common=common,
        agents=agents,
        instructions=instructions,
        skills=skills,
        subagents=subagents,
        prompts=prompts,
        servers=servers,
        profiles=profiles,
        plugins=plugins,
        env_file=env_file,
        env_vars=env_vars,
    )
    _validate(config)
    return config


def _build_common(raw: dict) -> Common:
    if "templates_dir" in raw:
        raise ConfigError(
            "[common] templates_dir is not supported in schema_version=3. "
            "Templates are now first-class artifacts: declare them with "
            "[instructions.<name>] source = '<absolute-or-~-prefixed-path>'."
        )
    return Common(vars=dict(raw.get("vars", {})))


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
        if "templates" in blob:
            raise ConfigError(
                f"agents.{agent_id}: [agents.{agent_id}.templates] is not "
                f"supported in schema_version=3. Instructions are now "
                f"registry-backed; declare [instructions.<name>] and add "
                f'`instructions = ["<name>"]` to the relevant profile.'
            )
        paths_blob = blob.get("paths", {})
        paths_global = _build_paths_section(paths_blob.get("global", {}))
        paths_project = _build_paths_section(paths_blob.get("project", {}))
        vars_ = dict(blob.get("vars", {}))
        mcp_format = blob.get("mcp_format")
        global_profile = blob.get("global_profile")
        out[agent_id] = Agent(
            id=agent_id,
            capabilities=caps,
            paths_global=paths_global,
            paths_project=paths_project,
            vars=vars_,
            mcp_format=mcp_format,
            global_profile=global_profile,
        )
    return out


def _build_paths_section(raw: dict) -> dict[str, list[Path]]:
    return {cap: [Path(p).expanduser() for p in paths] for cap, paths in raw.items()}


def _build_artifacts(raw: dict) -> dict[str, FileArtifact]:
    return {
        name: FileArtifact(
            name=name,
            source=Path(blob["source"]).expanduser(),
            description=blob.get("description"),
        )
        for name, blob in raw.items()
    }


def _build_server(name: str, blob: dict) -> Server:
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
    if server.type not in ("stdio", "http", "sse"):
        raise ConfigError(
            f"servers.{name}: type must be 'stdio', 'http', or 'sse', "
            f"got {server.type!r}"
        )
    if server.type == "stdio" and not server.command:
        raise ConfigError(f"servers.{name}: type=stdio requires 'command'")
    if server.type in ("http", "sse") and not server.url:
        raise ConfigError(f"servers.{name}: type={server.type} requires 'url'")
    return server


def _build_servers(raw: dict) -> dict[str, Server]:
    return {name: _build_server(name, blob) for name, blob in raw.items()}


# Keys a [profiles.<name>] block may contain. A misspelled key (e.g.
# `pluings`) was previously dropped silently — turning a typo into a silent
# no-op. Reject unknown keys so the failure is loud, per fail-fast.
_PROFILE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "description",
        "extends",
        "instructions",
        "skills",
        "subagents",
        "prompts",
        "servers",
        "plugins",
    }
)


def _check_unknown_keys(where: str, blob: dict, allowed: frozenset[str]) -> None:
    for key in blob:
        if key not in allowed:
            suggestion = difflib.get_close_matches(key, allowed, n=1)
            hint = f" (did you mean {suggestion[0]!r}?)" if suggestion else ""
            raise ConfigError(
                f"{where}: unknown key {key!r}{hint}. "
                f"Valid keys: {', '.join(sorted(allowed))}"
            )


def _build_profiles(raw: dict) -> dict[str, Profile]:
    for name, blob in raw.items():
        _check_unknown_keys(f"profiles.{name}", blob, _PROFILE_KEYS)
    return {
        name: Profile(
            name=name,
            description=blob.get("description"),
            extends=list(blob.get("extends", [])),
            instructions=list(blob.get("instructions", [])),
            skills=list(blob.get("skills", [])),
            subagents=list(blob.get("subagents", [])),
            prompts=list(blob.get("prompts", [])),
            servers=list(blob.get("servers", [])),
            plugins=list(blob.get("plugins", [])),
        )
        for name, blob in raw.items()
    }


def _build_plugins(
    raw: dict,
    *,
    skills: dict[str, FileArtifact],
    subagents: dict[str, FileArtifact],
    prompts: dict[str, FileArtifact],
    servers: dict[str, Server],
) -> dict[str, Plugin]:
    """Discover each plugin and inject its pieces into the registries.

    The registry dicts are mutated in place. Origins are tracked so a
    same-kind name collision raises ConfigError naming both contributors.
    Plugins are processed in sorted name order for deterministic errors.
    """
    # origin[(kind, name)] = human-readable source of the existing entry
    origin: dict[tuple[str, str], str] = {}
    for kind, registry in (
        ("skills", skills),
        ("subagents", subagents),
        ("prompts", prompts),
        ("servers", servers),
    ):
        for name in registry:
            origin[(kind, name)] = f"[{kind}] table"

    out: dict[str, Plugin] = {}
    for plugin_name in sorted(raw):
        blob = raw[plugin_name]
        source = Path(blob["source"]).expanduser()
        try:
            contents = discover_plugin(plugin_name, source)
        except (FileNotFoundError, ValueError) as exc:
            raise ConfigError(f"plugins.{plugin_name}: {exc}")

        description = blob.get("description") or contents.description

        skill_names = _inject_files(
            "skills", contents.skills, skills, origin, plugin_name
        )
        subagent_names = _inject_files(
            "subagents", contents.subagents, subagents, origin, plugin_name
        )
        prompt_names = _inject_files(
            "prompts", contents.prompts, prompts, origin, plugin_name
        )
        server_names = _inject_servers(contents.servers, servers, origin, plugin_name)

        out[plugin_name] = Plugin(
            name=plugin_name,
            source=source,
            description=description,
            skills=skill_names,
            subagents=subagent_names,
            prompts=prompt_names,
            servers=server_names,
        )
    return out


def _inject_files(
    kind: str,
    pieces: dict[str, Path],
    registry: dict[str, FileArtifact],
    origin: dict[tuple[str, str], str],
    plugin_name: str,
) -> list[str]:
    names: list[str] = []
    for name, src in pieces.items():
        _guard_collision(kind, name, origin, plugin_name)
        registry[name] = FileArtifact(name=name, source=src, description=None)
        origin[(kind, name)] = f"plugin {plugin_name!r}"
        names.append(name)
    return names


def _inject_servers(
    pieces: dict[str, dict],
    registry: dict[str, Server],
    origin: dict[tuple[str, str], str],
    plugin_name: str,
) -> list[str]:
    names: list[str] = []
    for name, blob in pieces.items():
        _guard_collision("servers", name, origin, plugin_name)
        registry[name] = _build_server(name, blob)
        origin[("servers", name)] = f"plugin {plugin_name!r}"
        names.append(name)
    return names


def _guard_collision(
    kind: str,
    name: str,
    origin: dict[tuple[str, str], str],
    plugin_name: str,
) -> None:
    prior = origin.get((kind, name))
    if prior is not None:
        raise ConfigError(
            f"{kind} {name!r} from plugin {plugin_name!r} collides with the "
            f"same {kind} from {prior}; artifact names must be unique"
        )


# ─── Validation ─────────────────────────────────────────────────────────


def _validate(config: Configuration) -> None:
    logger.debug("config._validate: starting validation passes")
    _validate_agents(config)
    _validate_profiles(config)
    _validate_no_name_shadow(config)
    _check_artifact_sources(config)
    logger.debug("config._validate: all passes complete")


def _validate_agents(config: Configuration) -> None:
    logger.debug("config._validate_agents: %d agent(s)", len(config.agents))
    for agent_id, agent in config.agents.items():
        for cap in agent.capabilities:
            if cap not in agent.paths_global:
                raise ConfigError(
                    f"agents.{agent_id}: missing paths.global.{cap} for declared capability"
                )
            if (
                cap not in PROJECT_OPTIONAL_CAPABILITIES
                and cap not in agent.paths_project
            ):
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
        # In v3, instructions is a registry-backed artifact like skills.
        # The agent only needs `instructions` in capabilities + a paths.global
        # entry — both already enforced above. The "what template" decision
        # lives on profiles (`profile.instructions = ["<name>"]`).
        # global_profile reference resolution.
        if agent.global_profile is not None:
            if agent.global_profile not in config.profiles:
                raise ConfigError(
                    f"agents.{agent_id}: global_profile {agent.global_profile!r} "
                    f"is not a defined profile"
                )


def _validate_profiles(config: Configuration) -> None:
    logger.debug("config._validate_profiles: %d profile(s)", len(config.profiles))
    # Reference resolution per type.
    for prof in config.profiles.values():
        for ref in prof.instructions:
            if ref not in config.instructions:
                raise ConfigError(f"profiles.{prof.name}: unknown instruction {ref!r}")
        for ref in prof.skills:
            if ref not in config.skills:
                raise ConfigError(f"profiles.{prof.name}: unknown skill {ref!r}")
        for ref in prof.subagents:
            if ref not in config.subagents:
                raise ConfigError(f"profiles.{prof.name}: unknown subagent {ref!r}")
        for ref in prof.prompts:
            if ref not in config.prompts:
                raise ConfigError(f"profiles.{prof.name}: unknown prompt {ref!r}")
        for ref in prof.servers:
            if ref not in config.servers:
                raise ConfigError(f"profiles.{prof.name}: unknown server {ref!r}")
        for ref in prof.plugins:
            if ref not in config.plugins:
                raise ConfigError(f"profiles.{prof.name}: unknown plugin {ref!r}")
        for parent in prof.extends:
            if parent not in config.profiles:
                raise ConfigError(
                    f"profiles.{prof.name}: extends unknown profile {parent!r}"
                )
    # Cycle detection on extends.
    for prof_name in config.profiles:
        _check_no_cycle(config.profiles, prof_name, [])


def _check_no_cycle(
    profiles: dict[str, Profile], current: str, stack: list[str]
) -> None:
    if current in stack:
        raise ConfigError(
            f"profiles: cyclic extends chain: {' → '.join(stack + [current])}"
        )
    for parent in profiles[current].extends:
        _check_no_cycle(profiles, parent, stack + [current])


def _validate_no_name_shadow(config: Configuration) -> None:
    """A name MUST NOT exist as both a profile AND an artifact.

    `--select` is polymorphic: each name can resolve to a profile (expanded)
    or to an artifact (literal). Allowing the same name in both registries
    would silently change behaviour depending on lookup order.
    """
    logger.debug("config._validate_no_name_shadow: scanning for collisions")
    artifact_names: dict[str, str] = {}
    for kind, registry in (
        ("instructions", config.instructions),
        ("skills", config.skills),
        ("subagents", config.subagents),
        ("prompts", config.prompts),
        ("servers", config.servers),
    ):
        for name in registry:
            if name in artifact_names:
                raise ConfigError(
                    f"name {name!r} is defined both as {artifact_names[name]!r} "
                    f"and {kind!r}; artifact names MUST be unique across all "
                    f"registries (skills/subagents/prompts/servers)"
                )
            artifact_names[name] = kind
    for prof_name in config.profiles:
        if prof_name in artifact_names:
            raise ConfigError(
                f"name {prof_name!r} is defined as both a profile and "
                f"a {artifact_names[prof_name]} artifact; --select uses "
                f"polymorphic resolution — names MUST be unambiguous"
            )
    # Plugin names join the polymorphic-resolution namespace (--select),
    # so they must not collide with any artifact or profile name.
    for plugin_name in config.plugins:
        if plugin_name in artifact_names:
            raise ConfigError(
                f"name {plugin_name!r} is defined as both a plugin and a "
                f"{artifact_names[plugin_name]} artifact; names must be unambiguous"
            )
        if plugin_name in config.profiles:
            raise ConfigError(
                f"name {plugin_name!r} is defined as both a plugin and a "
                f"profile; names must be unambiguous"
            )


def _check_artifact_sources(config: Configuration) -> None:
    """Missing source = warning, not hard error (FR-005)."""
    logger.debug("config._check_artifact_sources: scanning registries")
    for registry, kind in (
        (config.instructions, "instructions"),
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

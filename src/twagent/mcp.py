"""MCP server compilation: canonical → per-agent config.

Ported from twmcp/compiler.py. Differences:
  - No per-server overrides (spec rejects them; agent-specific quirks live here).
  - Operates on twagent.config.Server (no `overrides` field).
  - Format registry keyed by mcp_format string from agent config, not the older
    twmcp-internal "agent name" notion.
"""

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit.exceptions import ParseError as TOMLParseError

from twagent.interpolate import (
    contains_variable_default,
    contains_variable_reference,
    exact_variable_reference,
)

if TYPE_CHECKING:
    from twagent.config import Server

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormatProfile:
    """Per-mcp_format translator description.

    Most agents differ only in the on-disk JSON shape — top-level key,
    type-mapping (e.g. copilot-cli rewrites stdio → local), and how HTTP
    headers nest — so those stay declarative axes shared by one builder.

    `builder` exists for formats that are not a point in that space at all
    (codex names different keys and omits `type` entirely); their quirks live
    in a dedicated function rather than as per-format flags the other formats
    would have to step over. `serializer` stays declarative because it is
    consumed by different code paths than shape: write_config, diff, and the
    apply --dry-run preview.
    """

    name: str
    top_level_key: str
    type_mapping: dict[str, str] = field(default_factory=dict)
    header_style: str = "flat"  # "flat", "nested"
    serializer: str = "json"  # "json", "toml"
    builder: "Callable[[Server, FormatProfile], dict | None] | None" = None


def _build_server_dict(server: "Server", profile: FormatProfile) -> dict:
    """Build the shared JSON shape for a single server."""
    result: dict = {}
    result["type"] = profile.type_mapping.get(server.type, server.type)

    if server.command:
        result["command"] = server.command
    if server.args:
        result["args"] = list(server.args)
    if server.url:
        result["url"] = server.url

    if server.headers:
        if profile.header_style == "flat":
            result["headers"] = dict(server.headers)
        elif profile.header_style == "nested":
            result["requestInit"] = {"headers": dict(server.headers)}

    if server.tools is not None:
        result["tools"] = list(server.tools)
    if server.env:
        result["env"] = dict(server.env)

    return result


def _codex_enabled_tools(tools: list[str] | None) -> list[str] | None:
    """Map canonical `tools` onto codex's `enabled_tools`. None means omit.

    codex's `enabled_tools` is an allow-list of literal tool names with no
    wildcard syntax, and omitting it is how codex spells "all tools" (the field
    is `Option<Vec<String>>`; None skips the filter). So the canonical `["*"]`
    wildcard must be dropped — emitting it would register a single tool
    literally named `*`.

    Everything else is translated as-is, because dropping a real allow-list
    would silently WIDEN the server to all tools — failing in the one direction
    a whitelist exists to prevent. An empty list is honoured as written
    ("expose nothing"), not treated as absent.
    """
    if tools is None or "*" in tools:
        return None
    return list(tools)


def _build_codex_server(server: "Server", _profile: FormatProfile) -> dict | None:
    """Build the codex shape for a single server. Returns None to skip.

    Codex diverges from every JSON format, which is why it has
    its own builder rather than four more flags on FormatProfile. Each is
    pinned to codex's own McpServerConfig/McpServerTransportConfig
    (codex-rs/config/src/mcp_types.rs):
      - no `type` key: McpServerTransportConfig is `#[serde(untagged)]`, so
        transport is INFERRED from command vs url — and `deny_unknown_fields`
        means an emitted `type` would be a hard parse error, not noise.
      - static `headers` → `http_headers`; runtime references use
        `env_http_headers` or `bearer_token_env_var`
      - same-name stdio `${VAR}` values → `env_vars`; literals stay in `env`
      - stdio and streamable-http only; there is no sse variant
      - `tools` → `enabled_tools`, which is "an explicit allow-list of tools
        exposed from this server" — literal tool NAMES with no wildcard
        syntax. See _codex_enabled_tools for why ["*"] is the one value that
        must be dropped rather than translated.
    """
    if server.type == "sse":
        return None
    if server.type == "stdio" and server.headers:
        raise ValueError(
            f"servers.{server.name}.headers: mcp_format 'codex' supports headers "
            "only for HTTP servers"
        )
    if server.type == "http" and server.env:
        raise ValueError(
            f"servers.{server.name}.env: mcp_format 'codex' supports env and "
            "env_vars only for stdio servers"
        )

    result: dict = {}
    if server.command:
        result["command"] = server.command
    if server.args:
        result["args"] = list(server.args)
    if server.url:
        result["url"] = server.url
    if server.headers:
        static_headers: dict[str, str] = {}
        env_headers: dict[str, str] = {}
        for header, value in server.headers.items():
            bearer_prefix = "Bearer "
            bearer_var = (
                exact_variable_reference(value[len(bearer_prefix) :])
                if header == "Authorization" and value.startswith(bearer_prefix)
                else None
            )
            env_var = exact_variable_reference(value)
            if bearer_var is not None:
                result["bearer_token_env_var"] = bearer_var
            elif env_var is not None:
                env_headers[header] = env_var
            elif contains_variable_reference(value):
                raise ValueError(
                    f"servers.{server.name}.headers.{header}: reference {value!r} "
                    "cannot be represented by mcp_format 'codex'"
                )
            else:
                static_headers[header] = value
        if static_headers:
            result["http_headers"] = static_headers
        if env_headers:
            result["env_http_headers"] = env_headers
    enabled_tools = _codex_enabled_tools(server.tools)
    if enabled_tools is not None:
        result["enabled_tools"] = enabled_tools
    if server.env:
        static_env: dict[str, str] = {}
        env_vars: list[str] = []
        for key, value in server.env.items():
            env_var = exact_variable_reference(value)
            if env_var == key:
                env_vars.append(key)
            elif contains_variable_reference(value):
                raise ValueError(
                    f"servers.{server.name}.env.{key}: reference {value!r} cannot "
                    "be represented by mcp_format 'codex'; the variable name must "
                    "match the environment key"
                )
            else:
                static_env[key] = value
        if static_env:
            result["env"] = static_env
        if env_vars:
            result["env_vars"] = env_vars

    return result


FORMAT_REGISTRY: dict[str, FormatProfile] = {
    "claude-code": FormatProfile(
        name="claude-code",
        top_level_key="mcpServers",
        type_mapping={},
        header_style="flat",
    ),
    "copilot-cli": FormatProfile(
        name="copilot-cli",
        top_level_key="mcpServers",
        type_mapping={"stdio": "local"},
        header_style="flat",
    ),
    # Literal-only until their runtime-reference behavior is verified.
    "vscode": FormatProfile(
        name="vscode",
        top_level_key="servers",
        type_mapping={},
        header_style="nested",
    ),
    "opencode": FormatProfile(
        name="opencode",
        top_level_key="mcpServers",
        type_mapping={},
        header_style="flat",
    ),
    # The only non-JSON target: codex's ~/.codex/config.toml holds MCP config
    # alongside codex's own [projects]/[tui] state. See _build_codex_server.
    "codex": FormatProfile(
        name="codex",
        top_level_key="mcp_servers",
        serializer="toml",
        builder=_build_codex_server,
    ),
}


def get_format(name: str) -> FormatProfile:
    """Return the FormatProfile for an mcp_format name; raise on unknown."""
    logger.debug("mcp.get_format: name=%s", name)
    try:
        return FORMAT_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(FORMAT_REGISTRY))
        raise KeyError(
            f"Unknown mcp_format: {name!r}. Available: {available}"
        ) from None


def transform_for_format(servers: dict[str, "Server"], profile: FormatProfile) -> dict:
    """Transform canonical servers into the format's owned config subtree."""
    logger.debug(
        "mcp.transform_for_format: format=%s top_level_key=%s servers_in=%d",
        profile.name,
        profile.top_level_key,
        len(servers),
    )
    build = profile.builder or _build_server_dict
    out: dict = {}
    for name, server in servers.items():
        for field_name, values in (("env", server.env), ("headers", server.headers)):
            for key, value in (values or {}).items():
                if contains_variable_default(value):
                    raise ValueError(
                        f"servers.{name}.{field_name}.{key}: variable defaults are "
                        "not supported"
                    )
                if profile.name in {"vscode", "opencode"} and contains_variable_reference(
                    value
                ):
                    raise ValueError(
                        f"servers.{name}.{field_name}.{key}: runtime references are "
                        f"not verified for mcp_format '{profile.name}'"
                    )
        server_dict = build(server, profile)
        if server_dict is None:
            print(
                f"Warning: Skipping server '{name}' "
                f"(type '{server.type}' not supported by mcp_format '{profile.name}')",
                file=sys.stderr,
            )
            continue
        out[name] = server_dict
    return {profile.top_level_key: out}


def redact_legacy_runtime_values(
    current: dict,
    servers: dict[str, "Server"],
    profile: FormatProfile,
) -> None:
    """Mask stale resolved values while leaving runtime references readable.

    This operates structurally from canonical reference-bearing fields, so it
    does not need the secret value or access to the launch environment.
    """
    current_servers = current.get(profile.top_level_key)
    if not isinstance(current_servers, dict):
        return

    for name, server in servers.items():
        deployed = current_servers.get(name)
        if not isinstance(deployed, dict):
            continue

        for key, canonical_value in (server.env or {}).items():
            if not contains_variable_reference(canonical_value):
                continue
            deployed_env = deployed.get("env")
            if (
                isinstance(deployed_env, dict)
                and key in deployed_env
                and deployed_env[key] != canonical_value
            ):
                deployed_env[key] = "***"

        for header, canonical_value in (server.headers or {}).items():
            if not contains_variable_reference(canonical_value):
                continue
            if profile.name == "codex":
                deployed_headers = deployed.get("http_headers")
            elif profile.header_style == "nested":
                request_init = deployed.get("requestInit")
                deployed_headers = (
                    request_init.get("headers")
                    if isinstance(request_init, dict)
                    else None
                )
            else:
                deployed_headers = deployed.get("headers")
            if (
                isinstance(deployed_headers, dict)
                and header in deployed_headers
                and deployed_headers[header] != canonical_value
            ):
                deployed_headers[header] = "***"


def serialize(data: dict, serializer: str) -> str:
    """Render `data` as the on-disk text for `serializer`.

    Single definition of "what the file looks like", shared by write_config,
    diff, and the apply --dry-run preview so the three cannot drift.
    """
    if serializer == "toml":
        # tomlkit.dumps already terminates with a newline; adding another would
        # drift the diff by one line on every apply.
        return tomlkit.dumps(data)
    if serializer == "json":
        return json.dumps(data, indent=2) + "\n"
    raise ValueError(f"unknown serializer: {serializer!r}")


def _parse_existing(text: str, serializer: str, path: Path) -> dict:
    """Parse the current on-disk config, preserving formatting for TOML."""
    if serializer == "toml":
        try:
            # tomlkit (not tomllib): TOMLDocument is a dict subclass that keeps
            # every foreign table's comments and formatting through the write.
            return tomlkit.parse(text)
        except TOMLParseError as e:
            # Never clobber a file we cannot merge into.
            raise ValueError(f"unparseable TOML at {path}: {e}") from e
    if serializer == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"unparseable JSON at {path}: {e}") from e
    raise ValueError(f"unknown serializer: {serializer!r}")


def write_config(
    compiled: dict,
    path: Path,
    serializer: str = "json",
    mode: int | None = None,
) -> None:
    """Merge compiled MCP config into the file at `path`.

    Merge, don't replace: targets like ~/.claude.json and ~/.codex/config.toml
    are HARNESS-OWNED state files (userID, projects, trust dialogs, ...) that
    merely also hold MCP config (same ownership model info.py documents for its
    exclusion list). twagent owns ONLY the top-level key(s) in `compiled` — that
    subtree is replaced wholly (servers dropped from config disappear); every
    foreign top-level key is preserved.

    The update-in-place below is what makes that guarantee hold: never rebuild
    the document from parsed data, or TOML comments and formatting are lost.
    When `mode` is set, the target is tightened before writing; global deploys
    use this to keep MCP state owner-readable only.
    """
    existing: dict = {}
    if path.exists():
        text = path.read_text()
        if text.strip():
            existing = _parse_existing(text, serializer, path)
    existing.update(compiled)
    payload = serialize(existing, serializer)
    logger.debug(
        "mcp.write_config: path=%s serializer=%s bytes=%d",
        path,
        serializer,
        len(payload),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        path.touch(mode=mode, exist_ok=True)
        path.chmod(mode)
    path.write_text(payload)

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

    Codex diverges from every JSON format on four points, which is why it has
    its own builder rather than four more flags on FormatProfile. Each is
    pinned to codex's own McpServerConfig/McpServerTransportConfig
    (codex-rs/config/src/mcp_types.rs):
      - no `type` key: McpServerTransportConfig is `#[serde(untagged)]`, so
        transport is INFERRED from command vs url — and `deny_unknown_fields`
        means an emitted `type` would be a hard parse error, not noise.
      - `http_headers`, not `headers`
      - stdio and streamable-http only; there is no sse variant
      - `tools` → `enabled_tools`, which is "an explicit allow-list of tools
        exposed from this server" — literal tool NAMES with no wildcard
        syntax. See _codex_enabled_tools for why ["*"] is the one value that
        must be dropped rather than translated.
    """
    if server.type == "sse":
        return None

    result: dict = {}
    if server.command:
        result["command"] = server.command
    if server.args:
        result["args"] = list(server.args)
    if server.url:
        result["url"] = server.url
    if server.headers:
        result["http_headers"] = dict(server.headers)
    enabled_tools = _codex_enabled_tools(server.tools)
    if enabled_tools is not None:
        result["enabled_tools"] = enabled_tools
    if server.env:
        result["env"] = dict(server.env)

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
    "pi": FormatProfile(
        name="pi",
        top_level_key="mcpServers",
        type_mapping={},
        header_style="flat",
    ),
    # Schema accepts these for forward-compat; v1 doesn't exercise them in tests.
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
    """Transform canonical servers dict into per-format JSON structure."""
    logger.debug(
        "mcp.transform_for_format: format=%s top_level_key=%s servers_in=%d",
        profile.name,
        profile.top_level_key,
        len(servers),
    )
    build = profile.builder or _build_server_dict
    out: dict = {}
    for name, server in servers.items():
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


def write_config(compiled: dict, path: Path, serializer: str = "json") -> None:
    """Merge compiled MCP config into the file at `path`.

    Merge, don't replace: targets like ~/.claude.json and ~/.codex/config.toml
    are HARNESS-OWNED state files (userID, projects, trust dialogs, ...) that
    merely also hold MCP config (same ownership model info.py documents for its
    exclusion list). twagent owns ONLY the top-level key(s) in `compiled` — that
    subtree is replaced wholly (servers dropped from config disappear); every
    foreign top-level key is preserved.

    The update-in-place below is what makes that guarantee hold: never rebuild
    the document from parsed data, or TOML comments and formatting are lost.
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
    path.write_text(payload)

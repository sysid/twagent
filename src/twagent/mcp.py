"""MCP server compilation: canonical → per-agent JSON.

Ported from twmcp/compiler.py. Differences:
  - No per-server overrides (spec rejects them; agent-specific quirks live here).
  - Operates on twagent.config.Server (no `overrides` field).
  - Format registry keyed by mcp_format string from agent config, not the older
    twmcp-internal "agent name" notion.
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from twagent.config import Server

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FormatProfile:
    """Per-mcp_format translator description.

    Encodes the on-disk JSON shape an agent expects: top-level key,
    type-mapping (e.g. copilot-cli rewrites stdio → local), and how
    HTTP-headers nest (flat / nested / not supported).
    """

    name: str
    top_level_key: str
    type_mapping: dict[str, str] = field(default_factory=dict)
    header_style: str = "flat"  # "flat", "nested", "none"


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
}


def get_format(name: str) -> FormatProfile:
    """Return the FormatProfile for an mcp_format name; raise on unknown."""
    try:
        return FORMAT_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(FORMAT_REGISTRY))
        raise KeyError(
            f"Unknown mcp_format: {name!r}. Available: {available}"
        ) from None


def _build_server_dict(server: "Server", profile: FormatProfile) -> dict | None:
    """Build agent-specific dict for a single server. Returns None to skip."""
    # Agents with header_style "none" don't support http servers.
    if server.type == "http" and profile.header_style == "none":
        return None

    result: dict = {}
    if profile.header_style != "none":
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


def transform_for_format(
    servers: dict[str, "Server"], profile: FormatProfile
) -> dict:
    """Transform canonical servers dict into per-format JSON structure."""
    out: dict = {}
    for name, server in servers.items():
        server_dict = _build_server_dict(server, profile)
        if server_dict is None:
            print(
                f"Warning: Skipping server '{name}' "
                f"(type '{server.type}' not supported by mcp_format '{profile.name}')",
                file=sys.stderr,
            )
            continue
        out[name] = server_dict
    return {profile.top_level_key: out}


def write_config(compiled: dict, path: Path) -> None:
    """Write compiled MCP config as JSON to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compiled, indent=2) + "\n")

"""Tests for src/twagent/mcp.py — ported from twmcp/test_compiler.py.

Override-merge tests deliberately omitted: twagent's Server has no `overrides`
field; agent-specific MCP quirks live in FormatProfile, not in TOML.
"""

import json

import pytest

from twagent.config import Server
from twagent.mcp import (
    FORMAT_REGISTRY,
    get_format,
    transform_for_format,
    write_config,
)


@pytest.fixture
def simple_servers() -> dict[str, Server]:
    return {
        "github": Server(
            name="github",
            type="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_TOKEN": "test-token"},
        ),
        "atlassian": Server(
            name="atlassian",
            type="http",
            url="https://example.com/mcp/",
            headers={
                "X-Atlassian-Token": "test-token",
                "X-Atlassian-Url": "https://example.com",
            },
            tools=["*"],
        ),
        "local-proxy": Server(
            name="local-proxy",
            type="stdio",
            command="mcp-proxy",
            args=["http://localhost:8113/sse"],
            env={"API_TOKEN": "test-api-token"},
        ),
    }


class TestGetFormat:
    def test_known_format(self):
        assert get_format("claude-code").name == "claude-code"

    def test_unknown_format_raises(self):
        with pytest.raises(KeyError, match="Unknown mcp_format"):
            get_format("definitely-not-a-format")

    def test_all_required_formats_registered(self):
        # spec lists 5: claude-code, copilot-cli, pi, vscode, opencode
        assert {"claude-code", "copilot-cli", "pi", "vscode", "opencode"} <= set(
            FORMAT_REGISTRY
        )


class TestTransformForFormat:
    def test_claude_code_top_level_key(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("claude-code"))
        assert "mcpServers" in result

    def test_claude_code_includes_all_servers(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("claude-code"))
        assert set(result["mcpServers"].keys()) == {
            "github",
            "atlassian",
            "local-proxy",
        }

    def test_claude_code_keeps_stdio_type(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("claude-code"))
        assert result["mcpServers"]["github"]["type"] == "stdio"

    def test_claude_code_flat_headers(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("claude-code"))
        atlassian = result["mcpServers"]["atlassian"]
        assert "headers" in atlassian
        assert "requestInit" not in atlassian

    def test_copilot_cli_rewrites_stdio_to_local(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("copilot-cli"))
        assert result["mcpServers"]["github"]["type"] == "local"
        assert result["mcpServers"]["local-proxy"]["type"] == "local"
        # http unchanged
        assert result["mcpServers"]["atlassian"]["type"] == "http"

    def test_pi_top_level_key(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("pi"))
        assert "mcpServers" in result

    def test_pi_keeps_stdio_type(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("pi"))
        assert result["mcpServers"]["github"]["type"] == "stdio"

    def test_vscode_top_level_key(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("vscode"))
        assert "servers" in result

    def test_vscode_nested_headers(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("vscode"))
        atlassian = result["servers"]["atlassian"]
        assert "requestInit" in atlassian
        assert "headers" in atlassian["requestInit"]
        assert atlassian["requestInit"]["headers"]["X-Atlassian-Token"] == "test-token"
        assert "headers" not in atlassian

    def test_server_field_passthrough(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("claude-code"))
        github = result["mcpServers"]["github"]
        assert github["command"] == "npx"
        assert github["args"] == ["-y", "@modelcontextprotocol/server-github"]
        assert github["env"] == {"GITHUB_TOKEN": "test-token"}

    def test_http_server_fields(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("claude-code"))
        atlassian = result["mcpServers"]["atlassian"]
        assert atlassian["url"] == "https://example.com/mcp/"
        assert atlassian["tools"] == ["*"]
        # http server should not have command/args
        assert "command" not in atlassian
        assert "args" not in atlassian

    def test_empty_env_not_included(self):
        servers = {"minimal": Server(name="minimal", type="stdio", command="test-cmd")}
        result = transform_for_format(servers, get_format("claude-code"))
        assert "env" not in result["mcpServers"]["minimal"]


class TestWriteConfig:
    def test_writes_valid_json(self, tmp_path):
        output = tmp_path / "test.json"
        data = {"mcpServers": {"github": {"command": "npx"}}}
        write_config(data, output)
        assert output.exists()
        assert json.loads(output.read_text()) == data

    def test_json_uses_indent_2(self, tmp_path):
        output = tmp_path / "test.json"
        data = {"mcpServers": {"github": {"command": "npx"}}}
        write_config(data, output)
        text = output.read_text()
        assert '\n  "mcpServers"' in text

    def test_creates_parent_directories(self, tmp_path):
        output = tmp_path / "sub" / "dir" / "test.json"
        write_config({"mcpServers": {}}, output)
        assert output.exists()

    def test_trailing_newline(self, tmp_path):
        output = tmp_path / "test.json"
        write_config({"key": "value"}, output)
        assert output.read_text().endswith("\n")

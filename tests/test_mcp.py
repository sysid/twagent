"""Tests for src/twagent/mcp.py — ported from twmcp/test_compiler.py.

Override-merge tests deliberately omitted: twagent's Server has no `overrides`
field; agent-specific MCP quirks live in FormatProfile, not in TOML.
"""

import json
import tomllib

import pytest

from twagent.config import Server
from twagent.mcp import (
    FORMAT_REGISTRY,
    get_format,
    serialize,
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
        assert {
            "claude-code",
            "copilot-cli",
            "pi",
            "vscode",
            "opencode",
            "codex",
        } <= set(FORMAT_REGISTRY)


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

    def test_preserves_foreign_top_level_keys(self, tmp_path):
        # ~/.claude.json is Claude Code's own state file that merely also holds
        # MCP config — twagent owns ONLY its top_level_key subtree and must
        # never wipe harness state (userID, projects, ...).
        output = tmp_path / ".claude.json"
        output.write_text(
            json.dumps(
                {
                    "userID": "u-123",
                    "projects": {"/p": {"hasTrustDialogAccepted": True}},
                    "mcpServers": {"old": {"command": "gone"}},
                }
            )
        )
        write_config({"mcpServers": {"github": {"command": "npx"}}}, output)
        data = json.loads(output.read_text())
        assert data["userID"] == "u-123"
        assert data["projects"] == {"/p": {"hasTrustDialogAccepted": True}}
        # the owned subtree is replaced wholly — removed servers disappear
        assert data["mcpServers"] == {"github": {"command": "npx"}}

    def test_rejects_unparseable_target(self, tmp_path):
        # Never clobber a file we cannot merge into — fail fast instead.
        output = tmp_path / ".claude.json"
        output.write_text("{not json")
        with pytest.raises(ValueError, match="unparseable"):
            write_config({"mcpServers": {}}, output)
        assert output.read_text() == "{not json"


class TestCodexFormat:
    """Codex is the first non-JSON target and diverges from every JSON format.

    Its shape is defined by ~/.codex/config.toml: `[mcp_servers.<id>]` tables,
    transport INFERRED from command-vs-url (no `type` key at all), and
    `http_headers` rather than `headers`.
    """

    def test_codex_top_level_key(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("codex"))
        assert "mcp_servers" in result

    def test_codex_omits_type_key(self, simple_servers):
        # Codex infers transport from the presence of command vs url; a `type`
        # key is not part of its schema.
        result = transform_for_format(simple_servers, get_format("codex"))
        assert "type" not in result["mcp_servers"]["github"]
        assert "type" not in result["mcp_servers"]["atlassian"]

    def test_codex_stdio_passthrough(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("codex"))
        github = result["mcp_servers"]["github"]
        assert github["command"] == "npx"
        assert github["args"] == ["-y", "@modelcontextprotocol/server-github"]
        assert github["env"] == {"GITHUB_TOKEN": "test-token"}

    def test_codex_uses_http_headers_not_headers(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("codex"))
        atlassian = result["mcp_servers"]["atlassian"]
        assert atlassian["url"] == "https://example.com/mcp/"
        assert atlassian["http_headers"]["X-Atlassian-Token"] == "test-token"
        assert "headers" not in atlassian

    def test_codex_omits_wildcard_tools(self, simple_servers):
        # Codex's `enabled_tools` is a literal allow-list of tool NAMES with no
        # wildcard syntax, so the canonical ["*"] would be read as a tool
        # literally named "*". Omitting yields codex's default: all tools.
        result = transform_for_format(simple_servers, get_format("codex"))
        assert "tools" not in result["mcp_servers"]["atlassian"]
        assert "enabled_tools" not in result["mcp_servers"]["atlassian"]

    def test_codex_maps_explicit_tools_to_enabled_tools(self):
        # A real whitelist MUST survive translation: omitting it would fall back
        # to codex's "all tools" default and silently WIDEN what the server may
        # do — the opposite of what the user asked for.
        servers = {
            "restricted": Server(
                name="restricted",
                type="stdio",
                command="npx",
                tools=["read_file", "list_dir"],
            )
        }
        result = transform_for_format(servers, get_format("codex"))
        assert result["mcp_servers"]["restricted"]["enabled_tools"] == [
            "read_file",
            "list_dir",
        ]
        assert "tools" not in result["mcp_servers"]["restricted"]

    def test_codex_omits_tools_when_wildcard_mixed_with_names(self):
        # ["*", "foo"] already means "all tools"; codex expresses that by
        # omitting enabled_tools entirely, so this is faithful, not a widening.
        servers = {
            "mixed": Server(
                name="mixed", type="stdio", command="npx", tools=["*", "read_file"]
            )
        }
        result = transform_for_format(servers, get_format("codex"))
        assert "enabled_tools" not in result["mcp_servers"]["mixed"]

    def test_codex_empty_tools_list_registers_no_tools(self):
        # An empty whitelist means "expose nothing" — enabled_tools=[] says
        # exactly that to codex. Omitting would mean the opposite.
        servers = {
            "muzzled": Server(name="muzzled", type="stdio", command="npx", tools=[])
        }
        result = transform_for_format(servers, get_format("codex"))
        assert result["mcp_servers"]["muzzled"]["enabled_tools"] == []

    def test_codex_omits_enabled_tools_when_unset(self, simple_servers):
        result = transform_for_format(simple_servers, get_format("codex"))
        assert "enabled_tools" not in result["mcp_servers"]["github"]

    def test_codex_keeps_http_servers(self, simple_servers):
        # Codex supports streamable HTTP; only sse is unsupported.
        result = transform_for_format(simple_servers, get_format("codex"))
        assert "atlassian" in result["mcp_servers"]

    def test_codex_skips_sse_with_warning(self, capsys):
        # Codex speaks stdio and streamable-http only — there is no sse
        # transport, so an sse server must be dropped, loudly.
        servers = {
            "los-mcp-local": Server(
                name="los-mcp-local",
                type="sse",
                url="http://localhost:8113/mcp/sse",
            )
        }
        result = transform_for_format(servers, get_format("codex"))
        assert result["mcp_servers"] == {}
        stderr = capsys.readouterr().err
        assert "los-mcp-local" in stderr
        assert "codex" in stderr


class TestSerialize:
    def test_json_serializer(self):
        assert serialize({"a": 1}, "json") == '{\n  "a": 1\n}\n'

    def test_toml_serializer(self):
        assert serialize({"a": 1}, "toml") == "a = 1\n"

    def test_unknown_serializer_raises(self):
        # A typo in a FormatProfile must not silently write JSON into a .toml
        # target — that surfaces later as the agent failing to parse its own
        # config, far from the cause.
        with pytest.raises(ValueError, match="unknown serializer"):
            serialize({"a": 1}, "tolm")

    def test_write_config_rejects_unknown_serializer(self, tmp_path):
        # write_config parses before it serializes, so a typo'd serializer must
        # fail on the read side too — and must not touch the file.
        output = tmp_path / "config.toml"
        output.write_text("a = 1\n")
        with pytest.raises(ValueError, match="unknown serializer"):
            write_config({"mcp_servers": {}}, output, serializer="tolm")
        assert output.read_text() == "a = 1\n"


class TestWriteConfigToml:
    """~/.codex/config.toml is live harness state that merely also holds MCP.

    Codex writes [projects.*.trust_level] and [tui.*] into the same file, so a
    twagent write must replace only the `mcp_servers` table and leave every
    other table — and its comments — byte-intact.
    """

    def test_writes_standard_tables_not_inline(self, tmp_path):
        output = tmp_path / "config.toml"
        data = {"mcp_servers": {"github": {"command": "npx"}}}
        write_config(data, output, serializer="toml")
        assert "[mcp_servers.github]" in output.read_text()

    def test_roundtrips_through_tomllib(self, tmp_path):
        output = tmp_path / "config.toml"
        data = {"mcp_servers": {"github": {"command": "npx", "args": ["-y", "srv"]}}}
        write_config(data, output, serializer="toml")
        assert tomllib.loads(output.read_text()) == data

    def test_preserves_foreign_tables_and_comments(self, tmp_path):
        output = tmp_path / "config.toml"
        output.write_text(
            '# hand-written by Tom\n'
            '[projects."/p"]\n'
            'trust_level = "trusted"\n'
            '\n'
            '[tui.model_availability_nux]\n'
            '"gpt-5.6-sol" = 1\n'
            '\n'
            '[mcp_servers.old]\n'
            'command = "gone"\n'
        )
        write_config({"mcp_servers": {"github": {"command": "npx"}}}, output, serializer="toml")
        text = output.read_text()
        assert "# hand-written by Tom" in text
        parsed = tomllib.loads(text)
        assert parsed["projects"]["/p"]["trust_level"] == "trusted"
        assert parsed["tui"]["model_availability_nux"]["gpt-5.6-sol"] == 1
        # the owned subtree is replaced wholly — removed servers disappear
        assert parsed["mcp_servers"] == {"github": {"command": "npx"}}

    def test_rejects_unparseable_toml(self, tmp_path):
        # Never clobber a file we cannot merge into — fail fast instead.
        output = tmp_path / "config.toml"
        output.write_text("[not toml")
        with pytest.raises(ValueError, match="unparseable"):
            write_config({"mcp_servers": {}}, output, serializer="toml")
        assert output.read_text() == "[not toml"

    def test_toml_single_trailing_newline(self, tmp_path):
        # tomlkit.dumps already terminates with a newline; adding another would
        # drift the diff by one line on every apply.
        output = tmp_path / "config.toml"
        write_config({"mcp_servers": {}}, output, serializer="toml")
        text = output.read_text()
        assert text.endswith("\n")
        assert not text.endswith("\n\n")

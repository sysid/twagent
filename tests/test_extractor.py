"""US6: extract canonical TOML from per-agent MCP JSON (FR-028)."""

import json
import tomllib

import pytest

from twagent.extractor import (
    detect_servers,
    extract_from_file,
    is_secret_key,
    normalize_type,
    servers_to_toml,
)


class TestDetectServers:
    def test_mcpservers_format(self):
        assert detect_servers({"mcpServers": {"github": {"command": "npx"}}}) == {
            "github": {"command": "npx"}
        }

    def test_vscode_format(self):
        assert detect_servers({"mcp": {"servers": {"x": {"command": "y"}}}}) == {
            "x": {"command": "y"}
        }

    def test_flat_servers_format(self):
        assert detect_servers({"servers": {"x": {"command": "y"}}}) == {
            "x": {"command": "y"}
        }

    def test_priority_mcpservers_first(self):
        data = {
            "mcpServers": {"a": {"command": "a"}},
            "servers": {"b": {"command": "b"}},
        }
        assert detect_servers(data) == {"a": {"command": "a"}}

    def test_no_format_raises(self):
        with pytest.raises(ValueError, match="No MCP servers"):
            detect_servers({"unrelated": "data"})

    def test_empty_servers_raises(self):
        with pytest.raises(ValueError, match="No MCP servers"):
            detect_servers({"mcpServers": {}})


class TestIsSecretKey:
    @pytest.mark.parametrize(
        "key", ["GITHUB_TOKEN", "API_KEY", "DB_PASSWORD", "X-Atlassian-Token"]
    )
    def test_secret_keys_detected(self, key):
        assert is_secret_key(key)

    @pytest.mark.parametrize("key", ["NODE_ENV", "DEBUG", "USER_NAME"])
    def test_non_secret_keys(self, key):
        assert not is_secret_key(key)


class TestNormalizeType:
    def test_local_becomes_stdio(self):
        assert normalize_type("local") == "stdio"

    def test_unknown_passes_through(self):
        assert normalize_type("websocket") == "websocket"


class TestServersToToml:
    def test_round_trip_through_tomllib(self):
        servers = {
            "github": {
                "command": "npx",
                "args": ["-y", "x"],
                "type": "stdio",
                "env": {"GITHUB_TOKEN": "secret"},
            }
        }
        toml_text = servers_to_toml(servers, source="test.json")
        parsed = tomllib.loads(toml_text)
        assert parsed["servers"]["github"]["command"] == "npx"
        # Secret placeholder, not literal value
        assert parsed["servers"]["github"]["env"]["GITHUB_TOKEN"] == "${GITHUB_TOKEN}"

    def test_local_type_normalised(self):
        toml_text = servers_to_toml({"x": {"command": "y", "type": "local"}})
        parsed = tomllib.loads(toml_text)
        assert parsed["servers"]["x"]["type"] == "stdio"


class TestExtractFromFile:
    def test_round_trip_via_file(self, tmp_path):
        json_path = tmp_path / "mcp.json"
        json_path.write_text(json.dumps({"mcpServers": {"x": {"command": "y"}}}))
        toml_text = extract_from_file(json_path)
        parsed = tomllib.loads(toml_text)
        assert "x" in parsed["servers"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            extract_from_file(tmp_path / "ghost.json")

    def test_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {")
        with pytest.raises(ValueError, match="Invalid JSON"):
            extract_from_file(bad)

    def test_extract_does_not_modify_disk(self, tmp_path):
        """FR-028: extract is read-only."""
        json_path = tmp_path / "mcp.json"
        original = json.dumps({"mcpServers": {"x": {"command": "y"}}})
        json_path.write_text(original)
        before = sorted(p.name for p in tmp_path.iterdir())
        extract_from_file(json_path)
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after
        assert json_path.read_text() == original

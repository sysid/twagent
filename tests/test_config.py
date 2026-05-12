"""Validation matrix for config.py — one passing + one failing case per rule.

Rules from contracts/config-schema.md § 'Validation rules'.
"""

import pytest

from twagent.config import (
    SUPPORTED_SCHEMA_VERSION,
    Configuration,
    ConfigError,
    load,
)

# ─── Helpers ────────────────────────────────────────────────────────────


def _write_config(tmp_path, body: str, env_file: str | None = None) -> "Configuration":
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    if env_file is not None:
        (tmp_path / "secrets.env").write_text(env_file)
    return load(config_path)


MINIMAL_OK = """\
schema_version = 1

[agents.foo]
capabilities = []

[agents.foo.paths.global]

[agents.foo.paths.project]

[profiles.empty]

[[scopes]]
name = "global"
profile = "empty"
agents = ["foo"]
"""


# ─── schema_version ─────────────────────────────────────────────────────


def test_minimal_valid_loads(tmp_path):
    config = _write_config(tmp_path, MINIMAL_OK)
    assert config.schema_version == 1


def test_missing_schema_version_rejected(tmp_path):
    body = MINIMAL_OK.replace("schema_version = 1\n", "")
    with pytest.raises(ConfigError, match="schema_version"):
        _write_config(tmp_path, body)


def test_future_schema_version_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "schema_version = 1", f"schema_version = {SUPPORTED_SCHEMA_VERSION + 1}"
    )
    with pytest.raises(ConfigError, match="newer than supported"):
        _write_config(tmp_path, body)


# ─── capability enum ────────────────────────────────────────────────────


def test_unknown_capability_rejected(tmp_path):
    body = MINIMAL_OK.replace("capabilities = []", 'capabilities = ["bogus"]')
    with pytest.raises(ConfigError, match="unknown capability"):
        _write_config(tmp_path, body)


# ─── per-capability path requirements ───────────────────────────────────


def test_missing_paths_global_for_capability_rejected(tmp_path):
    body = """\
schema_version = 1
[agents.foo]
capabilities = ["skills"]
[agents.foo.paths.global]
[agents.foo.paths.project]
skills = [".skills"]
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    with pytest.raises(ConfigError, match="paths.global.skills"):
        _write_config(tmp_path, body)


def test_missing_paths_project_for_capability_rejected(tmp_path):
    body = """\
schema_version = 1
[agents.foo]
capabilities = ["skills"]
[agents.foo.paths.global]
skills = ["~/skills"]
[agents.foo.paths.project]
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    with pytest.raises(ConfigError, match="paths.project.skills"):
        _write_config(tmp_path, body)


def test_instructions_paths_project_optional(tmp_path):
    body = """\
schema_version = 1
[agents.foo]
capabilities = ["instructions"]
[agents.foo.paths.global]
instructions = ["~/AGENT.md"]
[agents.foo.paths.project]
[agents.foo.templates]
instructions = "foo.md.j2"
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    config = _write_config(tmp_path, body)
    assert "instructions" in config.agents["foo"].capabilities


# ─── mcp_format ─────────────────────────────────────────────────────────


def test_mcp_capability_requires_mcp_format(tmp_path):
    body = """\
schema_version = 1
[agents.foo]
capabilities = ["mcp"]
[agents.foo.paths.global]
mcp = ["~/mcp.json"]
[agents.foo.paths.project]
mcp = [".mcp.json"]
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    with pytest.raises(ConfigError, match="mcp_format required"):
        _write_config(tmp_path, body)


def test_unknown_mcp_format_rejected(tmp_path):
    body = """\
schema_version = 1
[agents.foo]
capabilities = ["mcp"]
mcp_format = "bogus-format"
[agents.foo.paths.global]
mcp = ["~/mcp.json"]
[agents.foo.paths.project]
mcp = [".mcp.json"]
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    with pytest.raises(ConfigError, match="unknown mcp_format"):
        _write_config(tmp_path, body)


# ─── instructions template existence ────────────────────────────────────


def test_instructions_capability_requires_template_key(tmp_path):
    body = """\
schema_version = 1
[agents.foo]
capabilities = ["instructions"]
[agents.foo.paths.global]
instructions = ["~/AGENT.md"]
[agents.foo.paths.project]
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    with pytest.raises(ConfigError, match="templates.instructions required"):
        _write_config(tmp_path, body)


def test_missing_template_file_rejected(tmp_path):
    body = f"""\
schema_version = 1
[common]
templates_dir = "{tmp_path}/templates"
[agents.foo]
capabilities = ["instructions"]
[agents.foo.paths.global]
instructions = ["~/AGENT.md"]
[agents.foo.paths.project]
[agents.foo.templates]
instructions = "missing.md.j2"
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["foo"]
"""
    with pytest.raises(ConfigError, match="instructions template not found"):
        _write_config(tmp_path, body)


# ─── server type validation ─────────────────────────────────────────────


def test_stdio_server_requires_command(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "stdio"
"""
    )
    with pytest.raises(ConfigError, match="requires 'command'"):
        _write_config(tmp_path, body)


def test_http_server_requires_url(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "http"
"""
    )
    with pytest.raises(ConfigError, match="requires 'url'"):
        _write_config(tmp_path, body)


def test_invalid_server_type_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "websocket"
"""
    )
    with pytest.raises(ConfigError, match="must be 'stdio', 'http', or 'sse'"):
        _write_config(tmp_path, body)


def test_sse_server_requires_url(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "sse"
"""
    )
    with pytest.raises(ConfigError, match="requires 'url'"):
        _write_config(tmp_path, body)


def test_sse_server_with_url_accepted(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "sse"
url = "http://localhost:8113/mcp/sse"
"""
    )
    config = _write_config(tmp_path, body)
    assert config.servers["s"].type == "sse"


# ─── profile reference resolution ───────────────────────────────────────


def test_profile_reference_to_unknown_skill_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\nskills = ["nonexistent"]\n',
    )
    with pytest.raises(ConfigError, match="unknown skill"):
        _write_config(tmp_path, body)


def test_profile_reference_to_unknown_server_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\nservers = ["nonexistent"]\n',
    )
    with pytest.raises(ConfigError, match="unknown server"):
        _write_config(tmp_path, body)


def test_profile_extends_unknown_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\nextends = ["ghost"]\n',
    )
    with pytest.raises(ConfigError, match="extends unknown profile"):
        _write_config(tmp_path, body)


# ─── profile cycle detection ────────────────────────────────────────────


def test_profile_cycle_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.a]\nextends = ["b"]\n[profiles.b]\nextends = ["a"]\n[profiles.empty]\n',
    )
    with pytest.raises(ConfigError, match="cyclic extends"):
        _write_config(tmp_path, body)


# ─── scope rules ────────────────────────────────────────────────────────


def test_duplicate_scope_name_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[[scopes]]
name = "global"
profile = "empty"
agents = ["foo"]
enabled = false
"""
    )
    with pytest.raises(ConfigError, match="duplicate name"):
        _write_config(tmp_path, body)


def test_unknown_scope_profile_rejected(tmp_path):
    body = MINIMAL_OK.replace('profile = "empty"', 'profile = "ghost"')
    with pytest.raises(ConfigError, match="unknown profile 'ghost'"):
        _write_config(tmp_path, body)


def test_unknown_scope_agent_rejected(tmp_path):
    body = MINIMAL_OK.replace('agents = ["foo"]', 'agents = ["ghost"]')
    with pytest.raises(ConfigError, match="unknown agent 'ghost'"):
        _write_config(tmp_path, body)


def test_empty_scope_agents_rejected(tmp_path):
    body = MINIMAL_OK.replace('agents = ["foo"]', "agents = []")
    with pytest.raises(ConfigError, match="non-empty"):
        _write_config(tmp_path, body)


def test_same_agent_in_two_enabled_scopes_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[[scopes]]
name = "second"
profile = "empty"
agents = ["foo"]
"""
    )
    with pytest.raises(ConfigError, match="two enabled scopes"):
        _write_config(tmp_path, body)


def test_same_agent_ok_if_one_scope_disabled(tmp_path):
    body = (
        MINIMAL_OK
        + """
[[scopes]]
name = "second"
profile = "empty"
agents = ["foo"]
enabled = false
"""
    )
    config = _write_config(tmp_path, body)
    assert len(config.scopes) == 2


# ─── env_file ───────────────────────────────────────────────────────────


def test_env_file_loaded_when_declared(tmp_path):
    body = 'env_file = "secrets.env"\n' + MINIMAL_OK
    config = _write_config(tmp_path, body, env_file="MY_KEY=my_value\n")
    assert config.env_vars["MY_KEY"] == "my_value"


def test_missing_env_file_raises(tmp_path):
    body = 'env_file = "ghost.env"\n' + MINIMAL_OK
    with pytest.raises(FileNotFoundError):
        _write_config(tmp_path, body)


# ─── source-missing is a WARNING, not an error ──────────────────────────


def test_missing_artifact_source_warns_not_errors(tmp_path):
    body = (
        MINIMAL_OK
        + """
[skills.s]
source = "/nonexistent/path"
"""
    )
    body = body.replace("[profiles.empty]\n", '[profiles.empty]\nskills = ["s"]\n')
    with pytest.warns(UserWarning, match="source does not exist"):
        config = _write_config(tmp_path, body)
    assert "s" in config.skills


# ─── full sample fixture loads ──────────────────────────────────────────


def test_full_sample_fixture_loads(fixtures_dir, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "test")
    # sample_config has source paths in /tmp — emit warnings, do not error.
    with pytest.warns(UserWarning):
        config = load(fixtures_dir / "sample_config.toml")
    assert config.schema_version == 1
    assert set(config.agents.keys()) == {"claude-code", "copilot-cli", "pi"}
    assert "tw" in config.profiles
    assert len(config.scopes) == 3
    # disabled scope is loaded but enabled=False
    disabled = [s for s in config.scopes if s.name == "project:disabled"][0]
    assert disabled.enabled is False

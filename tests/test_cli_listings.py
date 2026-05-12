"""US4: status, agents, profiles, scopes verbs."""

import json

import pytest
from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


@pytest.fixture
def listings_config(tmp_path):
    config_text = """\
schema_version = 1
[common]
[common.vars]
user_name = "tom"
[agents.claude-code]
capabilities = ["instructions", "skills", "mcp"]
mcp_format = "claude-code"
[agents.claude-code.paths.global]
instructions = ["~/.claude/CLAUDE.md"]
skills = ["~/.claude/skills"]
mcp = ["~/.claude.json"]
[agents.claude-code.paths.project]
skills = [".s"]
mcp = [".m"]
[agents.claude-code.templates]
instructions = "claude-code.md.j2"
[agents.claude-code.vars]
agent_name = "Claude"
[skills.x]
source = "/tmp/x"
[profiles.base]
skills = ["x"]
[profiles.full]
extends = ["base"]
[[scopes]]
name = "g"
profile = "full"
agents = ["claude-code"]
[[scopes]]
name = "off"
profile = "full"
agents = ["claude-code"]
enabled = false
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    return cfg


def test_status_shows_enabled_and_disabled_states(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "status"])
    assert result.exit_code == 0
    assert "g" in result.output
    assert "off" in result.output
    assert "disabled" in result.output


def test_scopes_alias_for_status(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "scopes"])
    assert result.exit_code == 0
    assert "g" in result.output


def test_agents_lists_capabilities(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "agents"])
    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "instructions" in result.output


def test_agents_json_output(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(
            app, ["--config", str(listings_config), "agents", "--json"]
        )
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "claude-code" in parsed
    assert "instructions" in parsed["claude-code"]["capabilities"]
    assert parsed["claude-code"]["mcp_format"] == "claude-code"


def test_profiles_shows_extends_expansion(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "profiles"])
    assert result.exit_code == 0
    assert "base" in result.output
    assert "full" in result.output
    # full extends base, so full's expanded skills should include x
    assert "x" in result.output

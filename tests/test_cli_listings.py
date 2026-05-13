"""Listing commands: status, agents, profiles. (`scopes` removed in v2.)"""

import json

import pytest
from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


@pytest.fixture
def listings_config(tmp_path):
    config_text = """\
schema_version = 3
[common.vars]
user_name = "tom"
[agents.claude-code]
capabilities = ["instructions", "skills", "mcp"]
mcp_format = "claude-code"
global_profile = "full"
[agents.claude-code.paths.global]
instructions = ["~/.claude/CLAUDE.md"]
skills = ["~/.claude/skills"]
mcp = ["~/.claude.json"]
[agents.claude-code.paths.project]
skills = [".s"]
mcp = [".m"]
[agents.claude-code.vars]
agent_name = "Claude"
[agents.no-default]
capabilities = ["skills"]
[agents.no-default.paths.global]
skills = ["~/no-default/skills"]
[agents.no-default.paths.project]
skills = [".s"]
[agents.no-default.vars]
[skills.x]
source = "/tmp/x"
[profiles.base]
skills = ["x"]
[profiles.full]
extends = ["base"]
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    return cfg


def test_status_shows_global_profile_per_agent(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "status"])
    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "full" in result.output  # the global_profile name
    assert "no-default" in result.output


def test_scopes_command_removed_in_v2(listings_config):
    # `scopes` is no longer a registered command. Typer rejects unknown
    # subcommands with exit code 2 BEFORE config is loaded — no warnings expected.
    result = runner.invoke(app, ["--config", str(listings_config), "scopes"])
    assert result.exit_code != 0


def test_agents_lists_capabilities(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "agents"])
    assert result.exit_code == 0
    assert "claude-code" in result.output
    assert "instructions" in result.output


def test_agents_json_output_includes_global_profile(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(
            app, ["--config", str(listings_config), "agents", "--json"]
        )
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "claude-code" in parsed
    assert "instructions" in parsed["claude-code"]["capabilities"]
    assert parsed["claude-code"]["mcp_format"] == "claude-code"
    assert parsed["claude-code"]["global_profile"] == "full"
    # Agent without global_profile has it as null
    assert parsed["no-default"]["global_profile"] is None


def test_profiles_shows_extends_expansion(listings_config):
    with pytest.warns(UserWarning):
        result = runner.invoke(app, ["--config", str(listings_config), "profiles"])
    assert result.exit_code == 0
    assert "base" in result.output
    assert "full" in result.output
    # full extends base, so full's expanded skills should include x
    assert "x" in result.output

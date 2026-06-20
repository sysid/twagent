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


# ─── artefacts command ─────────────────────────────────────────────────


@pytest.fixture
def artefacts_config(tmp_path):
    """Config exercising all five registries."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "instr.j2").write_text("hi {{ user_name }}")
    (src / "bkmr-skill").mkdir()
    (src / "reviewer").mkdir()
    (src / "release.md").write_text("# release")
    config_text = f"""\
schema_version = 3
[common.vars]
user_name = "tom"

[agents.claude-code]
capabilities = ["instructions", "skills", "subagents", "prompts", "mcp"]
mcp_format = "claude-code"
[agents.claude-code.paths.global]
instructions = ["~/.claude/CLAUDE.md"]
skills = ["~/.claude/skills"]
subagents = ["~/.claude/subagents"]
prompts = ["~/.claude/prompts"]
mcp = ["~/.claude.json"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
subagents = [".claude/subagents"]
prompts = [".claude/prompts"]
mcp = [".mcp.json"]
[agents.claude-code.vars]
agent_name = "Claude"

[instructions.AGENT-md]
source = "{src}/instr.j2"
description = "Top-level agent instructions"

[skills.bkmr]
source = "{src}/bkmr-skill"
description = "Bookmark manager skill"

[subagents.reviewer]
source = "{src}/reviewer"

[prompts.release]
source = "{src}/release.md"
description = "Release prompt"

[servers.github]
type = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = {{ GITHUB_TOKEN = "${{GITHUB_TOKEN}}" }}
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    return cfg


def test_artefacts_lists_all_registries(artefacts_config, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    result = runner.invoke(app, ["--config", str(artefacts_config), "artefacts"])
    assert result.exit_code == 0, result.output
    for name in ("AGENT-md", "bkmr", "reviewer", "release", "github"):
        assert name in result.output, f"{name} missing from listing"


def test_artefacts_sorted_alphabetically_within_kind(tmp_path):
    """Artefacts must list alphabetically by name within each kind,
    regardless of declaration order in the TOML."""
    src = tmp_path / "src"
    src.mkdir()
    for skill in ("zebra", "alpha", "mike"):
        (src / skill).mkdir()
    # Declared deliberately out of alphabetical order.
    config_text = f"""\
schema_version = 3
[agents.claude-code]
capabilities = ["skills"]
[agents.claude-code.paths.global]
skills = ["~/.claude/skills"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
[agents.claude-code.vars]
[skills.zebra]
source = "{src}/zebra"
[skills.alpha]
source = "{src}/alpha"
[skills.mike]
source = "{src}/mike"
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)

    result = runner.invoke(app, ["--config", str(cfg), "artefacts", "--skills"])
    assert result.exit_code == 0, result.output
    positions = [result.output.index(n) for n in ("alpha", "mike", "zebra")]
    assert positions == sorted(positions), result.output


def test_artefacts_filter_skills_excludes_others(artefacts_config, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    result = runner.invoke(
        app, ["--config", str(artefacts_config), "artefacts", "--skills"]
    )
    assert result.exit_code == 0, result.output
    assert "bkmr" in result.output
    assert "AGENT-md" not in result.output
    assert "github" not in result.output


def test_artefacts_multi_filter_combines(artefacts_config, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    result = runner.invoke(
        app,
        ["--config", str(artefacts_config), "artefacts", "--skills", "--servers"],
    )
    assert result.exit_code == 0, result.output
    assert "bkmr" in result.output
    assert "github" in result.output
    assert "AGENT-md" not in result.output


def test_artefacts_detail_for_file_artefact(artefacts_config, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("COLUMNS", "200")  # avoid Rich truncating the source path
    result = runner.invoke(
        app, ["--config", str(artefacts_config), "artefacts", "bkmr"]
    )
    assert result.exit_code == 0, result.output
    assert "bkmr" in result.output
    assert "skills" in result.output  # kind shown
    assert "Bookmark manager skill" in result.output
    assert "bkmr-skill" in result.output  # source path


def test_artefacts_detail_for_server_shows_command_and_env_keys(
    artefacts_config, monkeypatch
):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    result = runner.invoke(
        app, ["--config", str(artefacts_config), "artefacts", "github"]
    )
    assert result.exit_code == 0, result.output
    assert "github" in result.output
    assert "stdio" in result.output
    assert "npx" in result.output
    # env keys shown but token value masked (mirrors apply --dry-run convention)
    assert "GITHUB_TOKEN" in result.output


def test_artefacts_unknown_name_exits_two(artefacts_config, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    result = runner.invoke(
        app, ["--config", str(artefacts_config), "artefacts", "no-such-thing"]
    )
    assert result.exit_code == 2
    assert "no-such-thing" in result.output or "Unknown" in result.output


def test_artefacts_detail_respects_filter(artefacts_config, monkeypatch):
    """A name that exists but is the wrong kind under an active filter must miss."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    # github is a server; --skills only → not found
    result = runner.invoke(
        app,
        ["--config", str(artefacts_config), "artefacts", "github", "--skills"],
    )
    assert result.exit_code == 2

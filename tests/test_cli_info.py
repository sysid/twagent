"""CLI surface for `twagent info`."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from twagent.cli import _render_section, app
from twagent.info import Section

runner = CliRunner()


def _world(tmp_path: Path) -> Path:
    skill_src = tmp_path / "src" / "skills" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("ok")
    global_skills = tmp_path / "home" / "claude" / "skills"
    global_skills.mkdir(parents=True)
    (global_skills / "bkmr").symlink_to(skill_src)

    config_text = f"""\
schema_version = 3
[agents.claude-code]
capabilities = ["skills"]
[agents.claude-code.paths.global]
skills = ["{global_skills}"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
[agents.claude-code.vars]
[agents.copilot]
capabilities = ["skills"]
[agents.copilot.paths.global]
skills = ["{global_skills}"]
[agents.copilot.paths.project]
skills = [".copilot/skills"]
[agents.copilot.vars]
[skills.bkmr]
source = "{skill_src}"
[profiles.p]
skills = ["bkmr"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return config_path


def test_info_json_lists_all_agents(tmp_path):
    config_path = _world(tmp_path)
    result = runner.invoke(app, ["--config", str(config_path), "info", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    ids = {a["agent_id"] for a in data["agents"]}
    assert ids == {"claude-code", "copilot"}


def test_info_agent_filter_narrows_output(tmp_path):
    config_path = _world(tmp_path)
    result = runner.invoke(
        app, ["--config", str(config_path), "info", "-a", "claude-code", "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert [a["agent_id"] for a in data["agents"]] == ["claude-code"]


def test_info_human_output_is_exit_zero_and_mentions_agent(tmp_path):
    config_path = _world(tmp_path)
    result = runner.invoke(app, ["--config", str(config_path), "info"])
    assert result.exit_code == 0
    assert "claude-code" in result.stdout


def test_info_human_output_shows_status_and_layer(tmp_path):
    config_path = _world(tmp_path)
    # Add a dangling link locally to prove status rendering.
    local_dir = tmp_path / "proj" / ".claude" / "skills"
    local_dir.mkdir(parents=True)
    (local_dir / "ghost").symlink_to(tmp_path / "gone")

    cwd = tmp_path / "proj"
    old = os.getcwd()
    os.chdir(cwd)
    try:
        # --global so both global (managed bkmr) and local (dangling) render.
        result = runner.invoke(app, ["--config", str(config_path), "info", "--global"])
    finally:
        os.chdir(old)

    assert result.exit_code == 0
    assert "skills" in result.stdout
    assert "bkmr" in result.stdout
    assert "dangling" in result.stdout


def test_mcp_human_output_uses_content_format_and_terminal_theme(capsys):
    section = Section(
        kind="mcp",
        layer="global",
        path="/home/.codex/config.toml",
        render_as="mcp",
        content='[mcp_servers.docs]\nurl = "https://example.com/mcp"\n',
        content_format="toml",
        variables_masked=True,
    )

    with patch("twagent.cli.Syntax") as syntax:
        _render_section(section)

    syntax.assert_called_once_with(
        section.content,
        "toml",
        theme="ansi_dark",
        word_wrap=True,
    )
    output = capsys.readouterr().out
    assert "TOML" in output
    assert "resolved variables masked" in output


def test_info_masks_json_content_by_default_and_show_secrets_reveals(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TOKEN", "real_secret_value")
    mcp_file = tmp_path / "mcp.json"
    mcp_file.write_text(
        '{"mcpServers":{"gh":{"env":{"TOKEN":"real_secret_value"}}}}'
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""\
schema_version = 3
[agents.c]
capabilities = ["mcp"]
mcp_format = "claude-code"
[agents.c.paths.global]
mcp = ["{mcp_file}"]
[agents.c.paths.project]
mcp = [".mcp.json"]
[agents.c.vars]
[servers.gh]
type = "stdio"
command = "server"
env = {{ TOKEN = "${{TOKEN}}" }}
[profiles.p]
"""
    )

    masked = runner.invoke(
        app, ["--config", str(config_path), "info", "--global", "--json"]
    )
    raw = runner.invoke(
        app,
        [
            "--config",
            str(config_path),
            "info",
            "--global",
            "--json",
            "--show-secrets",
        ],
    )

    assert masked.exit_code == 0
    assert "real_secret_value" not in masked.output
    masked_section = json.loads(masked.output)["agents"][0]["sections"][0]
    assert masked_section["variables_masked"] is True
    assert raw.exit_code == 0
    assert "real_secret_value" in raw.output


def test_info_unknown_agent_errors_and_lists_available(tmp_path):
    config_path = _world(tmp_path)
    result = runner.invoke(
        app, ["--config", str(config_path), "info", "-G", "-a", "xx"]
    )
    assert result.exit_code == 2
    assert "xx" in result.output  # names the bad agent
    # lists the valid agents so the user knows what to pick
    assert "claude-code" in result.output
    assert "copilot" in result.output


def test_info_excludes_global_by_default(tmp_path):
    """Default run scans cwd only — global bkmr must NOT appear."""
    config_path = _world(tmp_path)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    old = os.getcwd()
    os.chdir(cwd)
    try:
        result = runner.invoke(app, ["--config", str(config_path), "info"])
        result_global = runner.invoke(
            app, ["--config", str(config_path), "info", "--global"]
        )
    finally:
        os.chdir(old)

    assert result.exit_code == 0
    assert "bkmr" not in result.stdout  # global hidden by default
    assert "bkmr" in result_global.stdout  # shown with --global

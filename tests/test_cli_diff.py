"""US4: diff verb."""

import pytest
from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


@pytest.fixture
def deployed_world(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "real_secret_value")
    skill_src = tmp_path / "src" / "x"
    skill_src.mkdir(parents=True)
    target_dir = tmp_path / "claude"
    target_dir.mkdir()
    config_text = f"""\
schema_version = 3
[agents.c]
capabilities = ["skills", "mcp"]
mcp_format = "claude-code"
global_profile = "p"
[agents.c.paths.global]
skills = ["{target_dir}/skills"]
mcp = ["{target_dir}/.claude.json"]
[agents.c.paths.project]
skills = [".s"]
mcp = [".m"]
[agents.c.vars]
[skills.x]
source = "{skill_src}"
[servers.gh]
type = "stdio"
command = "npx"
env = {{ GITHUB_TOKEN = "${{GITHUB_TOKEN}}" }}
[profiles.p]
skills = ["x"]
servers = ["gh"]
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    return cfg


def test_in_sync_after_apply_exits_zero(deployed_world):
    runner.invoke(app, ["--config", str(deployed_world), "apply"])
    result = runner.invoke(app, ["--config", str(deployed_world), "diff"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_divergence_exits_one(deployed_world):
    # Don't apply — diff against empty disk
    result = runner.invoke(app, ["--config", str(deployed_world), "diff"])
    assert result.exit_code == 1


def test_diff_masks_secrets_by_default(deployed_world):
    result = runner.invoke(app, ["--config", str(deployed_world), "diff"])
    assert "real_secret_value" not in result.output


def test_diff_show_secrets_reveals(deployed_world):
    result = runner.invoke(
        app, ["--config", str(deployed_world), "diff", "--show-secrets"]
    )
    assert "real_secret_value" in result.output

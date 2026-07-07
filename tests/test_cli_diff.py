"""US4: diff verb."""

import json

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
    runner.invoke(app, ["--config", str(deployed_world), "apply", "--global"])
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


# ─── MCP subtree ownership + masking symmetry ────────────────────────────


@pytest.fixture
def literal_header_world(tmp_path, monkeypatch):
    """Server with BOTH a ${VAR} secret header and a literal (non-secret) header —
    the combination that exposed masking asymmetry (mcp-atlassian, 2026-07-07)."""
    monkeypatch.setenv("ATLASSIAN_PAT", "real_secret_value")
    target_dir = tmp_path / "copilot"
    target_dir.mkdir()
    config_text = f"""\
schema_version = 3
[agents.c]
capabilities = ["mcp"]
mcp_format = "copilot-cli"
global_profile = "p"
[agents.c.paths.global]
mcp = ["{target_dir}/mcp-config.json"]
[agents.c.paths.project]
mcp = [".m"]
[agents.c.vars]
[servers.atlassian]
type = "http"
url = "https://example.com/mcp"
[servers.atlassian.headers]
X-Token = "${{ATLASSIAN_PAT}}"
X-Url = "https://example.com/confluence"
[profiles.p]
servers = ["atlassian"]
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    return cfg


def test_diff_ignores_foreign_top_level_keys(deployed_world, tmp_path):
    """Claude Code writes its own state keys into ~/.claude.json — diff must
    compare only the twagent-owned mcpServers subtree."""
    runner.invoke(app, ["--config", str(deployed_world), "apply", "--global"])
    target = tmp_path / "claude" / ".claude.json"
    data = json.loads(target.read_text())
    data["userID"] = "u-123"
    data["projects"] = {"/p": {"hasTrustDialogAccepted": True}}
    target.write_text(json.dumps(data, indent=2) + "\n")
    result = runner.invoke(app, ["--config", str(deployed_world), "diff"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_diff_in_sync_with_literal_and_var_headers(literal_header_world):
    """A literal header value must not false-drift against the masked intended side."""
    runner.invoke(app, ["--config", str(literal_header_world), "apply", "--global"])
    result = runner.invoke(app, ["--config", str(literal_header_world), "diff"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_diff_detects_literal_header_value_change(literal_header_world, tmp_path):
    """Masking must stay ${VAR}-scoped: a changed literal value is REAL drift."""
    runner.invoke(app, ["--config", str(literal_header_world), "apply", "--global"])
    target = tmp_path / "copilot" / "mcp-config.json"
    data = json.loads(target.read_text())
    data["mcpServers"]["atlassian"]["headers"]["X-Url"] = "https://changed.example.com"
    target.write_text(json.dumps(data, indent=2) + "\n")
    result = runner.invoke(app, ["--config", str(literal_header_world), "diff"])
    assert result.exit_code == 1


def test_diff_unparseable_current_reports_drift(literal_header_world, tmp_path):
    """Garbage in the target file is drift to report, not a crash."""
    target = tmp_path / "copilot" / "mcp-config.json"
    target.write_text("{not json")
    result = runner.invoke(app, ["--config", str(literal_header_world), "diff"])
    assert result.exit_code == 1
    # clean exit (SystemExit), not a crash bubbling up as e.g. JSONDecodeError
    assert isinstance(result.exception, SystemExit)
    assert "unparseable" in result.output

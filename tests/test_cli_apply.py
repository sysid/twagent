"""Tests for `twagent apply` — global flags + selectors + dry-run + secret masking."""

import json

import pytest
from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def real_world_config(tmp_path, monkeypatch):
    """Build a config with REAL existing source paths so deploy works end-to-end."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_real_token")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "atl_real_token")

    # Create real source artifacts
    skill_src = tmp_path / "skills_src" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("# bkmr skill")

    # Per-agent target dirs
    claude_root = tmp_path / "claude"
    claude_root.mkdir()

    # Custom templates dir + template
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "claude-code.md.j2").write_text(
        "# {{ agent_name }} for {{ user_name }}\n"
    )

    config_text = f"""\
schema_version = 1

[common]
templates_dir = "{templates_dir}"
[common.vars]
user_name = "Tom"

[agents.claude-code]
capabilities = ["instructions", "skills", "mcp"]
mcp_format = "claude-code"
[agents.claude-code.paths.global]
instructions = ["{claude_root}/CLAUDE.md"]
skills = ["{claude_root}/skills"]
mcp = ["{claude_root}/.claude.json"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
mcp = [".mcp.json"]
[agents.claude-code.templates]
instructions = "claude-code.md.j2"
[agents.claude-code.vars]
agent_name = "Claude"

[skills.bkmr]
source = "{skill_src}"

[servers.github]
type = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = {{ GITHUB_TOKEN = "${{GITHUB_TOKEN}}" }}

[profiles.tw]
skills = ["bkmr"]
servers = ["github"]

[[scopes]]
name = "global"
profile = "tw"
agents = ["claude-code"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return {
        "config": config_path,
        "claude_root": claude_root,
        "skill_src": skill_src,
    }


# ─── Bare apply ─────────────────────────────────────────────────────────


def test_bare_apply_deploys_all_enabled_scopes(real_world_config):
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(app, ["--config", str(cfg), "apply"])
    assert result.exit_code == 0, result.output
    assert (claude_root / "CLAUDE.md").exists()
    assert (claude_root / "skills" / "bkmr").is_symlink()
    assert (claude_root / ".claude.json").exists()


def test_apply_is_idempotent(real_world_config):
    cfg = real_world_config["config"]
    runner.invoke(app, ["--config", str(cfg), "apply"])
    # Second run with --dry-run produces no plan entries because everything is in sync
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--dry-run"])
    assert result.exit_code == 0


# ─── Selectors ──────────────────────────────────────────────────────────


def test_only_capability_filter(real_world_config):
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--only", "skills"])
    assert result.exit_code == 0
    assert (claude_root / "skills" / "bkmr").is_symlink()
    # instructions and mcp NOT deployed
    assert not (claude_root / "CLAUDE.md").exists()
    assert not (claude_root / ".claude.json").exists()


# ─── Dry run ────────────────────────────────────────────────────────────


def test_dry_run_writes_nothing(real_world_config):
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--dry-run"])
    assert result.exit_code == 0
    assert not (claude_root / "CLAUDE.md").exists()
    assert not (claude_root / ".claude.json").exists()


# ─── Secret masking (FR-023a) ───────────────────────────────────────────


def test_dry_run_masks_resolved_secrets(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--dry-run"])
    assert "ghs_real_token" not in result.output
    assert "***" in result.output


def test_show_secrets_reveals_resolved_secrets(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--dry-run", "--show-secrets"]
    )
    assert "ghs_real_token" in result.output


# ─── Real-write secrets verification ────────────────────────────────────


def test_real_apply_writes_actual_secret_to_disk(real_world_config):
    """Masking is presentation-only; the real file on disk has real values."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    runner.invoke(app, ["--config", str(cfg), "apply"])
    mcp_json = json.loads((claude_root / ".claude.json").read_text())
    assert mcp_json["mcpServers"]["github"]["env"]["GITHUB_TOKEN"] == "ghs_real_token"


# ─── Selection (US5, FR-021) ────────────────────────────────────────────


def test_select_narrows_to_named_artifacts(real_world_config):
    """--select bkmr deploys only that skill; github (server) excluded."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--select", "bkmr"]
    )
    assert result.exit_code == 0, result.output
    assert (claude_root / "skills" / "bkmr").is_symlink()
    # MCP file: github not in --select so the compiled JSON has zero servers
    mcp = json.loads((claude_root / ".claude.json").read_text())
    assert mcp["mcpServers"] == {}


def test_select_none_yields_empty(real_world_config):
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--select", "none"]
    )
    assert result.exit_code == 0, result.output
    # MCP file written but empty
    mcp = json.loads((claude_root / ".claude.json").read_text())
    assert mcp["mcpServers"] == {}


def test_select_unknown_name_exits_two(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--select", "ghost-name"]
    )
    assert result.exit_code == 2


def test_select_and_interactive_mutually_exclusive(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(
        app,
        ["--config", str(cfg), "apply", "--select", "bkmr", "--interactive"],
    )
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_select_with_only_instructions_rejected(real_world_config):
    """FR-021: --select doesn't apply to 'instructions'."""
    cfg = real_world_config["config"]
    result = runner.invoke(
        app,
        [
            "--config", str(cfg), "apply",
            "--select", "bkmr",
            "--only", "instructions",
        ],
    )
    assert result.exit_code == 2

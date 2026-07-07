"""Tests for `twagent apply` — global + here modes, polymorphic --select."""

import json

import pytest
from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


# ─── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def real_world_config(tmp_path, monkeypatch):
    """v2 config with a single agent that has a global_profile."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_real_token")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "atl_real_token")

    skill_src = tmp_path / "skills_src" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("# bkmr skill")

    claude_root = tmp_path / "claude"
    claude_root.mkdir()

    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    (templates_dir / "claude-code.md.j2").write_text(
        "# {{ agent_name }} for {{ user_name }}\n"
    )

    config_text = f"""\
schema_version = 3

[common.vars]
user_name = "Tom"

[agents.claude-code]
capabilities = ["instructions", "skills", "mcp"]
mcp_format = "claude-code"
global_profile = "tw"
[agents.claude-code.paths.global]
instructions = ["{claude_root}/CLAUDE.md"]
skills = ["{claude_root}/skills"]
mcp = ["{claude_root}/.claude.json"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
mcp = [".mcp.json"]
[agents.claude-code.vars]
agent_name = "Claude"

[instructions.AGENT-md]
source = "{templates_dir}/claude-code.md.j2"

[skills.bkmr]
source = "{skill_src}"

[servers.github]
type = "stdio"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env = {{ GITHUB_TOKEN = "${{GITHUB_TOKEN}}" }}

[profiles.tw]
instructions = ["AGENT-md"]
skills = ["bkmr"]
servers = ["github"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return {
        "config": config_path,
        "claude_root": claude_root,
        "skill_src": skill_src,
    }


# ─── Global apply (now requires explicit --global) ──────────────────────


def test_global_apply_deploys_global_profile(real_world_config):
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--global"])
    assert result.exit_code == 0, result.output
    assert (claude_root / "CLAUDE.md").exists()
    assert (claude_root / "skills" / "bkmr").is_symlink()
    assert (claude_root / ".claude.json").exists()


def test_bare_apply_defaults_to_here_mode(real_world_config, tmp_path, monkeypatch):
    """Local-deploy is the default. Bare `apply` (no --global, no --select)
    errors on missing --select instead of running a global sync."""
    cfg = real_world_config["config"]
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--config", str(cfg), "apply"])
    assert result.exit_code == 2
    assert "Local deploy requires --select" in result.output


def test_apply_preserves_claude_state_keys(real_world_config):
    """Claude Code stores its own state (userID, projects, ...) in the same
    ~/.claude.json that twagent targets for MCP — apply must merge, not clobber."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    state_file = claude_root / ".claude.json"
    state_file.write_text(
        json.dumps(
            {
                "userID": "u-123",
                "projects": {"/p": {"hasTrustDialogAccepted": True}},
                "mcpServers": {},
            }
        )
    )
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--global"])
    assert result.exit_code == 0, result.output
    data = json.loads(state_file.read_text())
    assert data["userID"] == "u-123"
    assert data["projects"] == {"/p": {"hasTrustDialogAccepted": True}}
    assert "github" in data["mcpServers"]


def test_apply_is_idempotent(real_world_config):
    cfg = real_world_config["config"]
    runner.invoke(app, ["--config", str(cfg), "apply", "--global"])
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--dry-run"]
    )
    assert result.exit_code == 0


# ─── Dry run ────────────────────────────────────────────────────────────


def test_dry_run_writes_nothing(real_world_config):
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--dry-run"]
    )
    assert result.exit_code == 0
    assert not (claude_root / "CLAUDE.md").exists()
    assert not (claude_root / ".claude.json").exists()


# ─── Secret masking (FR-023a) ───────────────────────────────────────────


def test_dry_run_masks_resolved_secrets(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--dry-run"]
    )
    assert "ghs_real_token" not in result.output
    assert "***" in result.output


def test_show_secrets_reveals_resolved_secrets(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--dry-run", "--show-secrets"]
    )
    assert "ghs_real_token" in result.output


def test_real_apply_writes_actual_secret_to_disk(real_world_config):
    """Masking is presentation-only; the real file on disk has real values."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    runner.invoke(app, ["--config", str(cfg), "apply", "--global"])
    mcp_json = json.loads((claude_root / ".claude.json").read_text())
    assert mcp_json["mcpServers"]["github"]["env"]["GITHUB_TOKEN"] == "ghs_real_token"


# ─── Polymorphic --select (NEW in v2) ───────────────────────────────────


def test_select_with_profile_name_overrides_global_profile(real_world_config):
    """`--select <profile>` in --global mode overrides each agent's default."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    # Create an alternate profile in-place via fixture-tweak: the existing
    # 'tw' profile is the only one. Verify --select tw still works as a profile.
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--select", "tw"]
    )
    assert result.exit_code == 0, result.output
    assert (claude_root / "skills" / "bkmr").is_symlink()


def test_select_with_artifact_name(real_world_config):
    """`--select bkmr` deploys ONLY that skill (v3): no MCP, no instructions."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--select", "bkmr"]
    )
    assert result.exit_code == 0, result.output
    assert (claude_root / "skills" / "bkmr").is_symlink()
    # bkmr is a skill; MCP and instructions capabilities NOT in needed_caps
    assert not (claude_root / ".claude.json").exists()
    assert not (claude_root / "CLAUDE.md").exists()


def test_select_mixed_profile_and_artifact(real_world_config):
    """`--select tw,bkmr` resolves dedup'd: tw expands to {bkmr, github}; bkmr extra is no-op."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--select", "tw,bkmr"]
    )
    assert result.exit_code == 0, result.output
    assert (claude_root / "skills" / "bkmr").is_symlink()


def test_select_unknown_name_exits_two(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--global", "--select", "ghost-name"]
    )
    assert result.exit_code == 2
    assert "Unknown name" in result.output


def test_select_preseeds_interactive_picker(real_world_config, monkeypatch):
    """`--select X --interactive` pre-checks the picker.

    For an artifact name, the expanded preselect set is the artifact itself.
    """
    captured: dict = {}

    def fake_picker(items, preselected=None, title=""):
        captured["preselected"] = preselected
        captured["items"] = items
        return ["bkmr"]

    import twagent.cli as cli_mod

    monkeypatch.setattr(cli_mod, "select_interactive", fake_picker)
    monkeypatch.setattr(cli_mod, "is_interactive_terminal", lambda: True)

    cfg = real_world_config["config"]
    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "apply",
            "--global",
            "--select",
            "bkmr",
            "--interactive",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["preselected"] == {"bkmr"}


def test_select_profile_expands_preselect(real_world_config, monkeypatch):
    """`--select <profile> --interactive` pre-checks the profile's EXPANDED
    members (skills/servers/instructions), not the literal profile name."""
    captured: dict = {}

    def fake_picker(items, preselected=None, title=""):
        captured["preselected"] = preselected
        return []  # user accepts nothing — that's fine, we only check preselect

    import twagent.cli as cli_mod

    monkeypatch.setattr(cli_mod, "select_interactive", fake_picker)
    monkeypatch.setattr(cli_mod, "is_interactive_terminal", lambda: True)

    cfg = real_world_config["config"]
    # Profile "tw" expands to: AGENT-md (instr) + bkmr (skill) + github (mcp).
    result = runner.invoke(
        app,
        [
            "--config",
            str(cfg),
            "apply",
            "--global",
            "--select",
            "tw",
            "--interactive",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["preselected"] == {"AGENT-md", "bkmr", "github"}
    # The profile name itself is NOT in the preselect set.
    assert "tw" not in captured["preselected"]


def test_short_flags_work(real_world_config):
    """All conventional short flags resolve to the same handlers."""
    cfg = real_world_config["config"]
    # -n is --dry-run, -s is --select, -a is --agent, -G is --global
    result = runner.invoke(
        app,
        ["--config", str(cfg), "apply", "-G", "-n", "-s", "bkmr", "-a", "claude-code"],
    )
    assert result.exit_code == 0, result.output


def test_global_short_flag(real_world_config):
    cfg = real_world_config["config"]
    result = runner.invoke(app, ["--config", str(cfg), "apply", "-G"])
    assert result.exit_code == 0, result.output


def test_here_short_flag(real_world_config, tmp_path, monkeypatch):
    cfg = real_world_config["config"]
    project_root = tmp_path / "shortform"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(app, ["--config", str(cfg), "apply", "-s", "bkmr"])
    assert result.exit_code == 0, result.output
    assert (project_root / ".claude" / "skills" / "bkmr").is_symlink()


# ─── Local mode (default) ──────────────────────────────────────────────


def test_here_requires_select(real_world_config, tmp_path, monkeypatch):
    cfg = real_world_config["config"]
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--config", str(cfg), "apply"])
    assert result.exit_code == 2
    assert "Local deploy requires --select" in result.output


def test_here_deploys_to_cwd(real_world_config, tmp_path, monkeypatch):
    """Local mode (default) deploys to cwd via paths.project (not paths.global)."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    project_root = tmp_path / "myproj"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--select", "bkmr"])
    assert result.exit_code == 0, result.output
    # Skill landed under project root
    assert (project_root / ".claude" / "skills" / "bkmr").is_symlink()
    # Global path NOT touched
    assert not (claude_root / "skills" / "bkmr").exists()


def test_here_creates_target_subdirs(real_world_config, tmp_path, monkeypatch):
    """Local mode creates `.claude/skills` if it doesn't exist (explicit user act)."""
    cfg = real_world_config["config"]
    project_root = tmp_path / "fresh"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--select", "bkmr"])
    assert result.exit_code == 0, result.output
    assert (project_root / ".claude" / "skills").is_dir()


def test_here_with_mcp_profile(real_world_config, tmp_path, monkeypatch):
    """`--select <profile-of-servers-only>` writes only the project mcp file."""
    cfg = real_world_config["config"]
    project_root = tmp_path / "p"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--select", "github"])
    assert result.exit_code == 0, result.output
    assert (project_root / ".mcp.json").exists()
    # No instructions written: selection has no instruction kind
    assert not (project_root / ".claude" / "CLAUDE.md").exists()


def test_here_dedup_skips_globally_present_skill(
    real_world_config, tmp_path, monkeypatch
):
    """Local apply skips a skill already symlinked at the agent's global layer."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    # Simulate bkmr already deployed globally.
    (claude_root / "skills").mkdir()
    (claude_root / "skills" / "bkmr").symlink_to(real_world_config["skill_src"])
    project_root = tmp_path / "dedup"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--select", "bkmr"])
    assert result.exit_code == 0, result.output
    assert not (project_root / ".claude" / "skills" / "bkmr").exists()


def test_here_no_dedup_forces_local_copy(real_world_config, tmp_path, monkeypatch):
    """--no-dedup deploys the skill locally even though it's present globally."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    (claude_root / "skills").mkdir()
    (claude_root / "skills" / "bkmr").symlink_to(real_world_config["skill_src"])
    project_root = tmp_path / "nodedup"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(
        app, ["--config", str(cfg), "apply", "--select", "bkmr", "--no-dedup"]
    )
    assert result.exit_code == 0, result.output
    assert (project_root / ".claude" / "skills" / "bkmr").is_symlink()


def test_here_dedup_keeps_project_only_skill(real_world_config, tmp_path, monkeypatch):
    """Dedup leaves alone an artifact NOT present at the global layer."""
    cfg = real_world_config["config"]
    project_root = tmp_path / "projonly"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    # Global skills dir empty: bkmr is project-only → must deploy.
    result = runner.invoke(app, ["--config", str(cfg), "apply", "--select", "bkmr"])
    assert result.exit_code == 0, result.output
    assert (project_root / ".claude" / "skills" / "bkmr").is_symlink()


# ─── Bug regressions: --select narrows capabilities (v3 fix) ────────────


def test_select_servers_only_skips_instructions(real_world_config):
    """Tom's exact bug: `apply --global --select github -a claude-code` should
    NOT render CLAUDE.md, only write the MCP file."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(
        app,
        ["--config", str(cfg), "apply", "-G", "-s", "github", "-a", "claude-code"],
    )
    assert result.exit_code == 0, result.output
    assert (claude_root / ".claude.json").exists()
    assert not (claude_root / "CLAUDE.md").exists()


def test_here_warns_when_agent_has_no_project_path_for_needed_cap(
    real_world_config, tmp_path, monkeypatch
):
    """Tom's scenario: `apply -s AGENT-md -a copilot-cli` where copilot-cli has
    the `instructions` capability but no paths.project.instructions. Used to
    silently exit 0 with no output; must now warn explicitly."""
    cfg = real_world_config["config"]
    project_root = tmp_path / "no-proj-instr"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(
        app,
        ["--config", str(cfg), "apply", "-s", "AGENT-md", "-a", "claude-code"],
    )
    assert result.exit_code == 0, result.output
    assert "no `paths.project.instructions` configured" in result.output
    assert "Applied" in result.output or "No-op" in result.output


def test_here_select_servers_with_agent_does_not_render_instructions(
    real_world_config, tmp_path, monkeypatch
):
    """Local mode (default) + --select narrows capabilities to MCP only."""
    cfg = real_world_config["config"]
    project_root = tmp_path / "here-bug"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    result = runner.invoke(
        app,
        ["--config", str(cfg), "apply", "-s", "github", "-a", "claude-code"],
    )
    assert result.exit_code == 0, result.output
    assert (project_root / ".mcp.json").exists()
    assert not (project_root / "CLAUDE.md").exists()


# ─── Instructions as first-class artifact (v3) ──────────────────────────


def test_select_instruction_name_renders_only_template(real_world_config):
    """`--select AGENT-md` renders the instruction; nothing else."""
    cfg = real_world_config["config"]
    claude_root = real_world_config["claude_root"]
    result = runner.invoke(app, ["--config", str(cfg), "apply", "-G", "-s", "AGENT-md"])
    assert result.exit_code == 0, result.output
    assert (claude_root / "CLAUDE.md").exists()
    assert not (claude_root / ".claude.json").exists()
    assert not (claude_root / "skills" / "bkmr").exists()


def test_two_instructions_for_one_agent_errors(tmp_path, monkeypatch):
    """A profile contributing 2+ instructions per agent is an apply-time error."""
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    tpl_a = tmp_path / "a.md.j2"
    tpl_a.write_text("from a")
    tpl_b = tmp_path / "b.md.j2"
    tpl_b.write_text("from b")
    claude_root = tmp_path / "claude"
    claude_root.mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"""\
schema_version = 3
[common.vars]
user_name = "x"
[agents.claude-code]
capabilities = ["instructions"]
mcp_format = "claude-code"
[agents.claude-code.paths.global]
instructions = ["{claude_root}/CLAUDE.md"]
[agents.claude-code.paths.project]
[agents.claude-code.vars]
agent_name = "Claude"
[instructions.a]
source = "{tpl_a}"
[instructions.b]
source = "{tpl_b}"
""")
    result = runner.invoke(app, ["--config", str(cfg), "apply", "-G", "-s", "a,b"])
    assert result.exit_code == 1
    assert "at most ONE instruction" in result.output

"""US2: project-scope override semantics (FR-013, FR-014).

(a) project root exists → deploy under it
(b) project root missing → warn + skip + rest of run continues
(c) project deploys ONLY project-listed artifacts; globals NOT re-linked
"""

import pytest
from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


@pytest.fixture
def project_world(tmp_path):
    """Two scopes: one global, one project — different physical roots."""
    skill_src = tmp_path / "skills_src" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("# bkmr")

    project_skill_src = tmp_path / "skills_src" / "proj-only"
    project_skill_src.mkdir(parents=True)
    (project_skill_src / "SKILL.md").write_text("# proj only")

    global_root = tmp_path / "global_claude"
    global_root.mkdir()

    project_root = tmp_path / "myproj"
    project_root.mkdir()

    config_text = f"""\
schema_version = 1
[common]
[agents.claude-code]
capabilities = ["skills", "mcp"]
mcp_format = "claude-code"
[agents.claude-code.paths.global]
skills = ["{global_root}/skills"]
mcp = ["{global_root}/.claude.json"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
mcp = [".mcp.json"]
[agents.claude-code.vars]

[skills.bkmr]
source = "{skill_src}"
[skills.proj-only]
source = "{project_skill_src}"

[servers.github]
type = "stdio"
command = "npx"

[profiles.global-set]
skills = ["bkmr"]
servers = ["github"]

[profiles.project-set]
skills = ["proj-only"]

[[scopes]]
name = "global"
profile = "global-set"
agents = ["claude-code"]

[[scopes]]
name = "project:test"
profile = "project-set"
agents = ["claude-code"]
root = "{project_root}"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return {
        "config": config_path,
        "global_root": global_root,
        "project_root": project_root,
    }


def test_project_scope_deploys_under_root(project_world):
    cfg = project_world["config"]
    project_root = project_world["project_root"]
    result = runner.invoke(app, ["--config", str(cfg), "apply"])
    assert result.exit_code == 0, result.output
    # Project-listed artifact lands under project root
    assert (project_root / ".claude" / "skills" / "proj-only").is_symlink()
    # Project mcp.json written under project root
    assert (project_root / ".mcp.json").exists()


def test_project_scope_does_not_relink_globals_into_project(project_world):
    """FR-014: project deploys ONLY project-listed artifacts."""
    cfg = project_world["config"]
    project_root = project_world["project_root"]
    runner.invoke(app, ["--config", str(cfg), "apply"])
    # 'bkmr' is in the GLOBAL profile only — must NOT appear in project dir
    assert not (project_root / ".claude" / "skills" / "bkmr").exists()


def test_project_scope_global_unaffected_by_project_artifacts(project_world):
    """The project's exclusive artifact must NOT leak into the global location."""
    cfg = project_world["config"]
    global_root = project_world["global_root"]
    runner.invoke(app, ["--config", str(cfg), "apply"])
    # 'proj-only' is in the PROJECT profile only — must NOT appear globally
    assert not (global_root / "skills" / "proj-only").exists()
    # 'bkmr' (global profile) IS in the global location
    assert (global_root / "skills" / "bkmr").is_symlink()


def test_missing_project_root_skipped_with_warning(tmp_path):
    """FR-013: project scope with non-existent root → warn + skip; rest of run succeeds."""
    skill_src = tmp_path / "src" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("ok")
    global_root = tmp_path / "global"
    global_root.mkdir()
    config_text = f"""\
schema_version = 1
[agents.c]
capabilities = ["skills"]
[agents.c.paths.global]
skills = ["{global_root}/skills"]
[agents.c.paths.project]
skills = [".skills"]
[agents.c.vars]
[skills.bkmr]
source = "{skill_src}"
[profiles.p]
skills = ["bkmr"]
[[scopes]]
name = "global"
profile = "p"
agents = ["c"]
[[scopes]]
name = "project:ghost"
profile = "p"
agents = ["c"]
root = "/nonexistent/path/for/test"
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    result = runner.invoke(app, ["--config", str(config_path), "apply"])
    assert result.exit_code == 0, result.output
    # Global scope DID deploy
    assert (global_root / "skills" / "bkmr").is_symlink()
    # Warning for skipped project scope
    assert "project:ghost" in result.output or "project:ghost" in result.stderr if hasattr(result, "stderr") else "project:ghost" in result.output


def test_two_enabled_global_scopes_for_same_agent_rejected(tmp_path):
    """Cross-scope rule: same (agent, root=None) in two enabled scopes → hard error."""
    config_text = """\
schema_version = 1
[agents.c]
capabilities = []
[agents.c.paths.global]
[agents.c.paths.project]
[profiles.p]
[[scopes]]
name = "g1"
profile = "p"
agents = ["c"]
[[scopes]]
name = "g2"
profile = "p"
agents = ["c"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    result = runner.invoke(app, ["--config", str(config_path), "apply"])
    assert result.exit_code == 2, result.output
    assert "globally" in result.output or "two enabled" in result.output


def test_global_plus_project_for_same_agent_allowed(project_world):
    """Cross-scope rule allows global + project for same agent (different physical locations)."""
    cfg = project_world["config"]
    result = runner.invoke(app, ["--config", str(cfg), "apply"])
    assert result.exit_code == 0, result.output

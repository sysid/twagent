"""US3: doctor reports introduced failures + clean state passes."""

from pathlib import Path

import pytest

from twagent.config import load
from twagent.doctor import check


@pytest.fixture
def base_world(tmp_path):
    """Build a config with one agent + one skill, deployed cleanly to disk."""
    skill_src = tmp_path / "skills_src" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("ok")
    skills_dir = tmp_path / "claude" / "skills"
    skills_dir.mkdir(parents=True)
    config_text = f"""\
schema_version = 1
[agents.c]
capabilities = ["skills"]
[agents.c.paths.global]
skills = ["{skills_dir}"]
[agents.c.paths.project]
skills = [".skills"]
[agents.c.vars]
[skills.bkmr]
source = "{skill_src}"
[profiles.p]
skills = ["bkmr"]
[[scopes]]
name = "g"
profile = "p"
agents = ["c"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return {
        "config_path": config_path,
        "skill_src": skill_src,
        "skills_dir": skills_dir,
    }


def test_clean_state_no_errors(base_world):
    config = load(base_world["config_path"])
    report = check(config)
    assert not report.has_errors


def test_dangling_symlink_reported(base_world):
    config = load(base_world["config_path"])
    skills_dir: Path = base_world["skills_dir"]
    # Plant a dangling symlink
    (skills_dir / "ghost").symlink_to(Path("/nonexistent/target"))
    report = check(config)
    assert report.has_errors
    assert any("dangling" in e and "ghost" in e for e in report.errors)


def test_missing_artifact_source_reported(base_world):
    config = load(base_world["config_path"])
    # Move the source out from under the registry
    base_world["skill_src"].rename(base_world["skill_src"].parent / "moved")
    # Re-load (load() re-validates and emits warning, but check() escalates to error)
    with pytest.warns(UserWarning):
        config = load(base_world["config_path"])
    report = check(config)
    assert report.has_errors
    assert any("bkmr" in e and "source does not exist" in e for e in report.errors)


def test_disabled_scope_in_info_not_errors(tmp_path):
    config_text = """\
schema_version = 1
[agents.c]
capabilities = []
[agents.c.paths.global]
[agents.c.paths.project]
[profiles.p]
[[scopes]]
name = "g"
profile = "p"
agents = ["c"]
[[scopes]]
name = "off"
profile = "p"
agents = ["c"]
enabled = false
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    config = load(config_path)
    report = check(config)
    assert not report.has_errors
    assert any("off" in i and "disabled" in i for i in report.info)


def test_capability_mismatch_in_info_not_errors(tmp_path):
    """Profile lists subagents but agent lacks subagents capability → info-level."""
    config_text = """\
schema_version = 1
[agents.c]
capabilities = ["skills"]
[agents.c.paths.global]
skills = ["~/skills"]
[agents.c.paths.project]
skills = [".skills"]
[agents.c.vars]
[subagents.reviewer]
source = "/tmp/x"
[profiles.p]
subagents = ["reviewer"]
[[scopes]]
name = "g"
profile = "p"
agents = ["c"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    with pytest.warns(UserWarning):  # source missing → load-time warning
        config = load(config_path)
    report = check(config)
    # Info-level mismatch, not an error
    assert any("subagents" in i and "lacks" in i for i in report.info)

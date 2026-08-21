"""US3: doctor reports introduced failures + clean state passes (v2)."""

import warnings
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
schema_version = 3
[agents.c]
capabilities = ["skills"]
global_profile = "p"
[agents.c.paths.global]
skills = ["{skills_dir}"]
[agents.c.paths.project]
skills = [".skills"]
[agents.c.vars]
[skills.bkmr]
source = "{skill_src}"
[profiles.p]
skills = ["bkmr"]
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
    (skills_dir / "ghost").symlink_to(Path("/nonexistent/target"))
    report = check(config)
    assert report.has_errors
    assert any("dangling" in e and "ghost" in e for e in report.errors)


def test_missing_artifact_source_reported(base_world):
    config = load(base_world["config_path"])
    base_world["skill_src"].rename(base_world["skill_src"].parent / "moved")
    with pytest.warns(UserWarning):
        config = load(base_world["config_path"])
    report = check(config)
    assert report.has_errors
    assert any("bkmr" in e and "source does not exist" in e for e in report.errors)


def test_agent_without_global_profile_in_info(tmp_path):
    """Agents with no global_profile are info-level (deployable via local apply --select only)."""
    config_text = """\
schema_version = 3
[agents.c]
capabilities = []
[agents.c.paths.global]
[agents.c.paths.project]
[profiles.p]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    config = load(config_path)
    report = check(config)
    assert not report.has_errors
    assert any("c" in i and "no global_profile" in i for i in report.info)


def test_capability_mismatch_in_info_not_errors(tmp_path):
    """global_profile contains subagents but agent lacks subagents capability → info."""
    config_text = """\
schema_version = 3
[agents.c]
capabilities = ["skills"]
global_profile = "p"
[agents.c.paths.global]
skills = ["~/skills"]
[agents.c.paths.project]
skills = [".skills"]
[agents.c.vars]
[subagents.reviewer]
source = "/tmp/x"
[profiles.p]
subagents = ["reviewer"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    with pytest.warns(UserWarning):
        config = load(config_path)
    report = check(config)
    assert any("subagents" in i and "lacks" in i for i in report.info)


# ─── portability: doctor is where per-machine gaps surface ──────────────


def _portable_config(tmp_path, *, optional: bool):
    flag = "\noptional = true" if optional else ""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""\
schema_version = 3
[skills.workonly]
source = "{tmp_path / "nope"}"{flag}
[plugins.ghost]
source = "{tmp_path / "no-plugin"}"{flag}
[profiles.p]
skills = ["workonly"]
plugins = ["ghost"]
"""
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load(config_path)


def test_optional_absent_sources_are_info_not_errors(tmp_path):
    report = check(_portable_config(tmp_path, optional=True))
    assert not report.has_errors
    assert any("skills.workonly" in i and "expected absent" in i for i in report.info)
    assert any("plugins.ghost" in i and "expected absent" in i for i in report.info)


def test_non_optional_absent_sources_are_errors(tmp_path):
    report = check(_portable_config(tmp_path, optional=False))
    assert report.has_errors
    assert any("skills.workonly" in e for e in report.errors)
    assert any("plugins.ghost" in e for e in report.errors)


def test_doctor_loads_despite_absent_plugin_dir(tmp_path):
    """Regression: an absent plugin dir used to make doctor itself unusable."""
    report = check(_portable_config(tmp_path, optional=True))
    assert report.info  # it ran at all

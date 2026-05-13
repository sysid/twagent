"""US3: doctor verb at the CLI boundary."""

from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


def test_clean_state_exit_zero(tmp_path):
    skill_src = tmp_path / "src"
    skill_src.mkdir()
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
skills = [".s"]
[agents.c.vars]
[skills.x]
source = "{skill_src}"
[profiles.p]
skills = ["x"]
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    result = runner.invoke(app, ["--config", str(cfg), "doctor"])
    assert result.exit_code == 0


def test_introduced_failure_exit_one(tmp_path):
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
skills = [".s"]
[agents.c.vars]
[skills.ghost]
source = "/nonexistent/source/path"
[profiles.p]
skills = ["ghost"]
"""
    cfg = tmp_path / "config.toml"
    cfg.write_text(config_text)
    result = runner.invoke(app, ["--config", str(cfg), "doctor"])
    assert result.exit_code == 1

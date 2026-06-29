"""Integration: a plugin-bearing profile deploys + reaps via the existing
deploy machinery (Task 8). No production deploy code changes — if these pass,
the "zero deploy changes" design claim holds.
"""

import textwrap
from pathlib import Path

from twagent.config import load
from twagent.deploy import apply_here

FIXTURE_PLUGINS = Path(__file__).parent / "fixtures" / "plugins"


def _setup(tmp_path: Path):
    """A config with one agent, the alpha plugin, and a plain `keeper` skill.

    Profile `p` pulls the plugin atomically; profile `q` engages the skills
    capability with a non-plugin skill, so switching p -> q exercises orphan
    reaping of the plugin's deployed skill.
    """
    keeper = tmp_path / "keeper_skill"
    keeper.mkdir()
    (keeper / "SKILL.md").write_text("---\ndescription: keeper skill\n---\n")

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        textwrap.dedent(
            f"""\
            schema_version = 3

            [agents.claude-code]
            capabilities = ["skills", "subagents"]

            [agents.claude-code.paths.global]
            skills = ["~/.claude/skills"]
            subagents = ["~/.claude/agents"]

            [agents.claude-code.paths.project]
            skills = [".claude/skills"]
            subagents = [".claude/agents"]

            [skills.keeper]
            source = "{keeper}"

            [plugins.alpha]
            source = "{FIXTURE_PLUGINS / "alpha"}"

            [profiles.p]
            plugins = ["alpha"]

            [profiles.q]
            skills = ["keeper"]
            """
        )
    )
    return load(cfg)


def test_apply_here_symlinks_plugin_pieces(tmp_path):
    config = _setup(tmp_path)
    cwd = tmp_path / "work"
    cwd.mkdir()

    apply_here(config, cwd, select=["p"])

    assert (cwd / ".claude" / "skills" / "greet").is_symlink()
    assert (cwd / ".claude" / "agents" / "helper.agent.md").is_symlink()


def test_switching_profile_reaps_plugin_skill(tmp_path):
    config = _setup(tmp_path)
    cwd = tmp_path / "work"
    cwd.mkdir()

    apply_here(config, cwd, select=["p"])
    assert (cwd / ".claude" / "skills" / "greet").is_symlink()

    # Switch to a profile that still engages the skills capability but does
    # not include the plugin: the plugin's skill is reaped as an orphan.
    apply_here(config, cwd, select=["q"])
    assert not (cwd / ".claude" / "skills" / "greet").exists()
    assert (cwd / ".claude" / "skills" / "keeper").is_symlink()

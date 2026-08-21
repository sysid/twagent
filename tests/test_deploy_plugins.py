"""Integration: a plugin-bearing profile deploys + reaps via the existing
deploy machinery (Task 8). No production deploy code changes — if these pass,
the "zero deploy changes" design claim holds.
"""

import textwrap
import warnings
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


# ─── portability: sources absent on this machine ────────────────────────


def _setup_optional(tmp_path: Path, *, optional: bool):
    """`keeper` exists; `workonly` and plugin `ghost` point nowhere.

    Mirrors one canonical config synced to a machine that doesn't check out
    the work repos.
    """
    keeper = tmp_path / "keeper_skill"
    keeper.mkdir()
    (keeper / "SKILL.md").write_text("---\ndescription: keeper skill\n---\n")

    flag = "\noptional = true" if optional else ""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        textwrap.dedent(
            f"""\
            schema_version = 3

            [agents.claude-code]
            capabilities = ["skills"]

            [agents.claude-code.paths.global]
            skills = ["~/.claude/skills"]

            [agents.claude-code.paths.project]
            skills = [".claude/skills"]

            [skills.keeper]
            source = "{keeper}"

            [skills.workonly]
            source = "{tmp_path / "nope"}"{flag}

            [plugins.ghost]
            source = "{tmp_path / "no-plugin"}"{flag}

            [profiles.p]
            skills = ["keeper", "workonly"]
            plugins = ["ghost"]
            """
        )
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return load(cfg)


def test_optional_absent_source_is_skipped_not_an_error(tmp_path):
    config = _setup_optional(tmp_path, optional=True)
    cwd = tmp_path / "work"
    cwd.mkdir()

    result = apply_here(config, cwd, select=["p"])

    assert not result.has_errors
    assert any("workonly" in s for s in result.skipped_optional)
    assert (cwd / ".claude" / "skills" / "keeper").is_symlink()
    assert not (cwd / ".claude" / "skills" / "workonly").exists()


def test_non_optional_absent_source_still_errors(tmp_path):
    config = _setup_optional(tmp_path, optional=False)
    cwd = tmp_path / "work"
    cwd.mkdir()

    result = apply_here(config, cwd, select=["p"])

    assert result.has_errors
    assert any("workonly" in e for e in result.errors)
    assert result.skipped_optional == []


def test_optional_absent_source_reaps_stale_symlink(tmp_path):
    """A broken link synced in from the machine where it did exist."""
    config = _setup_optional(tmp_path, optional=True)
    cwd = tmp_path / "work"
    target = cwd / ".claude" / "skills"
    target.mkdir(parents=True)
    (target / "workonly").symlink_to(tmp_path / "gone-with-the-other-machine")

    apply_here(config, cwd, select=["p"])

    assert not (target / "workonly").is_symlink()


def test_optional_skips_are_counted_per_artifact_not_per_agent(tmp_path):
    """One absent skill wanted by three agents is ONE absent source.

    The summary line reports what the user thinks in — artifacts — not the
    agent x capability slots the deploy loop happens to iterate.
    """
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        textwrap.dedent(
            f"""\
            schema_version = 3

            [agents.a]
            capabilities = ["skills"]
            [agents.a.paths.global]
            skills = ["~/.a/skills"]
            [agents.a.paths.project]
            skills = [".a/skills"]

            [agents.b]
            capabilities = ["skills"]
            [agents.b.paths.global]
            skills = ["~/.b/skills"]
            [agents.b.paths.project]
            skills = [".b/skills"]

            [agents.c]
            capabilities = ["skills"]
            [agents.c.paths.global]
            skills = ["~/.c/skills"]
            [agents.c.paths.project]
            skills = [".c/skills"]

            [skills.workonly]
            source = "{tmp_path / "nope"}"
            optional = true

            [profiles.p]
            skills = ["workonly"]
            """
        )
    )
    config = load(cfg)
    cwd = tmp_path / "work"
    cwd.mkdir()

    result = apply_here(config, cwd, select=["p"])

    assert result.skipped_optional == ["workonly"]

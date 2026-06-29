"""Plugin flattening in expand_profile (Task 6)."""

from pathlib import Path

from twagent.config import (
    Common,
    Configuration,
    FileArtifact,
    Plugin,
    Profile,
    Server,
)
from twagent.expansion import expand_profile


def _config_with_plugin() -> Configuration:
    skills = {"greet": FileArtifact("greet", Path("/p/skills/greet"))}
    subagents = {"helper.agent.md": FileArtifact("helper.agent.md", Path("/p/a"))}
    servers = {"alpha-server": Server("alpha-server", command="echo")}
    plugin = Plugin(
        name="alpha",
        source=Path("/p"),
        description=None,
        skills=["greet"],
        subagents=["helper.agent.md"],
        prompts=[],
        servers=["alpha-server"],
    )
    profile = Profile(name="p", plugins=["alpha"], skills=["mine"])
    skills["mine"] = FileArtifact("mine", Path("/x/mine"))
    return Configuration(
        schema_version=3,
        common=Common(),
        agents={},
        instructions={},
        skills=skills,
        subagents=subagents,
        prompts={},
        servers=servers,
        profiles={"p": profile},
        plugins={"alpha": plugin},
    )


def test_expand_profile_flattens_plugin_members():
    config = _config_with_plugin()
    expanded = expand_profile(config, "p")

    assert expanded.skills == ["greet", "mine"]  # plugin first, then explicit
    assert expanded.subagents == ["helper.agent.md"]
    assert expanded.servers == ["alpha-server"]


def test_expand_profile_dedups_plugin_and_explicit_overlap():
    config = _config_with_plugin()
    # Profile also explicitly lists the plugin's skill — must not duplicate.
    config.profiles["p"] = Profile(name="p", plugins=["alpha"], skills=["greet"])
    expanded = expand_profile(config, "p")
    assert expanded.skills == ["greet"]

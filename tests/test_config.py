"""Validation matrix for config.py — one passing + one failing case per rule.

Schema v2: scopes are gone. Global deployment is per-agent `global_profile`.
Polymorphic --select forces a name-shadow validation rule across registries.
"""

import pytest

from twagent.config import (
    SUPPORTED_SCHEMA_VERSION,
    Configuration,
    ConfigError,
    load,
)

# ─── Helpers ────────────────────────────────────────────────────────────


def _write_config(tmp_path, body: str, env_file: str | None = None) -> "Configuration":
    config_path = tmp_path / "config.toml"
    config_path.write_text(body)
    if env_file is not None:
        (tmp_path / "secrets.env").write_text(env_file)
    return load(config_path)


MINIMAL_OK = """\
schema_version = 3

[agents.foo]
capabilities = []

[agents.foo.paths.global]

[agents.foo.paths.project]

[profiles.empty]
"""


# ─── schema_version ─────────────────────────────────────────────────────


def test_minimal_valid_loads(tmp_path):
    config = _write_config(tmp_path, MINIMAL_OK)
    assert config.schema_version == 3


def test_missing_schema_version_rejected(tmp_path):
    body = MINIMAL_OK.replace("schema_version = 3\n", "")
    with pytest.raises(ConfigError, match="schema_version"):
        _write_config(tmp_path, body)


def test_future_schema_version_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "schema_version = 3", f"schema_version = {SUPPORTED_SCHEMA_VERSION + 1}"
    )
    with pytest.raises(ConfigError, match="newer than supported"):
        _write_config(tmp_path, body)


# ─── Legacy [[scopes]] rejected loudly ──────────────────────────────────


def test_legacy_scopes_blocks_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[[scopes]]
name = "global"
profile = "empty"
agents = ["foo"]
"""
    )
    with pytest.raises(ConfigError, match="scopes.*not supported"):
        _write_config(tmp_path, body)


# ─── capability enum ────────────────────────────────────────────────────


def test_unknown_capability_rejected(tmp_path):
    body = MINIMAL_OK.replace("capabilities = []", 'capabilities = ["bogus"]')
    with pytest.raises(ConfigError, match="unknown capability"):
        _write_config(tmp_path, body)


# ─── per-capability path requirements ───────────────────────────────────


def test_missing_paths_global_for_capability_rejected(tmp_path):
    body = """\
schema_version = 3
[agents.foo]
capabilities = ["skills"]
[agents.foo.paths.global]
[agents.foo.paths.project]
skills = [".skills"]
[profiles.p]
"""
    with pytest.raises(ConfigError, match="paths.global.skills"):
        _write_config(tmp_path, body)


def test_missing_paths_project_for_capability_rejected(tmp_path):
    body = """\
schema_version = 3
[agents.foo]
capabilities = ["skills"]
[agents.foo.paths.global]
skills = ["~/skills"]
[agents.foo.paths.project]
[profiles.p]
"""
    with pytest.raises(ConfigError, match="paths.project.skills"):
        _write_config(tmp_path, body)


def test_instructions_paths_project_optional(tmp_path):
    body = """\
schema_version = 3
[agents.foo]
capabilities = ["instructions"]
[agents.foo.paths.global]
instructions = ["~/AGENT.md"]
[agents.foo.paths.project]
[profiles.p]
"""
    config = _write_config(tmp_path, body)
    assert "instructions" in config.agents["foo"].capabilities


# ─── mcp_format ─────────────────────────────────────────────────────────


def test_mcp_capability_requires_mcp_format(tmp_path):
    body = """\
schema_version = 3
[agents.foo]
capabilities = ["mcp"]
[agents.foo.paths.global]
mcp = ["~/mcp.json"]
[agents.foo.paths.project]
mcp = [".mcp.json"]
[profiles.p]
"""
    with pytest.raises(ConfigError, match="mcp_format required"):
        _write_config(tmp_path, body)


def test_unknown_mcp_format_rejected(tmp_path):
    body = """\
schema_version = 3
[agents.foo]
capabilities = ["mcp"]
mcp_format = "bogus-format"
[agents.foo.paths.global]
mcp = ["~/mcp.json"]
[agents.foo.paths.project]
mcp = [".mcp.json"]
[profiles.p]
"""
    with pytest.raises(ConfigError, match="unknown mcp_format"):
        _write_config(tmp_path, body)


def test_codex_mcp_format_accepted(tmp_path):
    body = """\
schema_version = 3
[agents.foo]
capabilities = ["mcp"]
mcp_format = "codex"
[agents.foo.paths.global]
mcp = ["~/.codex/config.toml"]
[agents.foo.paths.project]
mcp = [".codex/config.toml"]
[profiles.p]
"""
    config = _write_config(tmp_path, body)
    assert config.agents["foo"].mcp_format == "codex"


# ─── v3 migration: legacy fields rejected ──────────────────────────────


def test_legacy_agent_templates_block_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[agents.foo.templates]
instructions = "AGENT.md.j2"
"""
    )
    with pytest.raises(ConfigError, match="templates.*not supported"):
        _write_config(tmp_path, body)


def test_legacy_common_templates_dir_rejected(tmp_path):
    body = (
        'schema_version = 3\n[common]\ntemplates_dir = "/tmp/x"\n[common.vars]\n'
        + MINIMAL_OK.split("schema_version = 3\n", 1)[1]
    )
    with pytest.raises(ConfigError, match="templates_dir.*not supported"):
        _write_config(tmp_path, body)


# ─── instructions registry (NEW in v3) ─────────────────────────────────


def test_instructions_registry_loads(tmp_path):
    tpl = tmp_path / "AGENT.md.j2"
    tpl.write_text("hello")
    body = (
        MINIMAL_OK
        + f"""
[instructions.AGENT-md]
source = "{tpl}"
"""
    )
    config = _write_config(tmp_path, body)
    assert "AGENT-md" in config.instructions
    assert config.instructions["AGENT-md"].source == tpl


def test_profile_referencing_unknown_instruction_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\ninstructions = ["ghost"]\n',
    )
    with pytest.raises(ConfigError, match="unknown instruction"):
        _write_config(tmp_path, body)


def test_instruction_and_skill_same_name_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[instructions.collide]
source = "/tmp/x"
[skills.collide]
source = "/tmp/y"
"""
    )
    with pytest.raises(ConfigError, match="defined both as"):
        _write_config(tmp_path, body)


# ─── server type validation ─────────────────────────────────────────────


def test_stdio_server_requires_command(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "stdio"
"""
    )
    with pytest.raises(ConfigError, match="requires 'command'"):
        _write_config(tmp_path, body)


def test_http_server_requires_url(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "http"
"""
    )
    with pytest.raises(ConfigError, match="requires 'url'"):
        _write_config(tmp_path, body)


def test_invalid_server_type_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "websocket"
"""
    )
    with pytest.raises(ConfigError, match="must be 'stdio', 'http', or 'sse'"):
        _write_config(tmp_path, body)


def test_sse_server_requires_url(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "sse"
"""
    )
    with pytest.raises(ConfigError, match="requires 'url'"):
        _write_config(tmp_path, body)


def test_sse_server_with_url_accepted(tmp_path):
    body = (
        MINIMAL_OK
        + """
[servers.s]
type = "sse"
url = "http://localhost:8113/mcp/sse"
"""
    )
    config = _write_config(tmp_path, body)
    assert config.servers["s"].type == "sse"


# ─── profile reference resolution ───────────────────────────────────────


def test_profile_reference_to_unknown_skill_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\nskills = ["nonexistent"]\n',
    )
    with pytest.raises(ConfigError, match="unknown skill"):
        _write_config(tmp_path, body)


def test_profile_reference_to_unknown_server_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\nservers = ["nonexistent"]\n',
    )
    with pytest.raises(ConfigError, match="unknown server"):
        _write_config(tmp_path, body)


def test_profile_extends_unknown_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.empty]\nextends = ["ghost"]\n',
    )
    with pytest.raises(ConfigError, match="extends unknown profile"):
        _write_config(tmp_path, body)


# ─── profile cycle detection ────────────────────────────────────────────


def test_profile_cycle_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[profiles.empty]\n",
        '[profiles.a]\nextends = ["b"]\n[profiles.b]\nextends = ["a"]\n[profiles.empty]\n',
    )
    with pytest.raises(ConfigError, match="cyclic extends"):
        _write_config(tmp_path, body)


# ─── global_profile (NEW in v2) ─────────────────────────────────────────


def test_global_profile_resolves(tmp_path):
    body = MINIMAL_OK.replace(
        "[agents.foo]\ncapabilities = []\n",
        '[agents.foo]\ncapabilities = []\nglobal_profile = "empty"\n',
    )
    config = _write_config(tmp_path, body)
    assert config.agents["foo"].global_profile == "empty"


def test_global_profile_unknown_rejected(tmp_path):
    body = MINIMAL_OK.replace(
        "[agents.foo]\ncapabilities = []\n",
        '[agents.foo]\ncapabilities = []\nglobal_profile = "ghost"\n',
    )
    with pytest.raises(ConfigError, match="global_profile.*not a defined profile"):
        _write_config(tmp_path, body)


def test_global_profile_optional(tmp_path):
    config = _write_config(tmp_path, MINIMAL_OK)
    assert config.agents["foo"].global_profile is None


# ─── name shadow rule (NEW in v2) ───────────────────────────────────────


def test_profile_and_skill_same_name_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[skills.collide]
source = "/tmp/x"
"""
    )
    body = body.replace("[profiles.empty]\n", "[profiles.empty]\n[profiles.collide]\n")
    with pytest.raises(ConfigError, match="defined as both a profile and"):
        _write_config(tmp_path, body)


def test_skill_and_server_same_name_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[skills.collide]
source = "/tmp/x"
[servers.collide]
type = "stdio"
command = "noop"
"""
    )
    with pytest.raises(ConfigError, match="defined both as"):
        _write_config(tmp_path, body)


def test_subagent_and_prompt_same_name_rejected(tmp_path):
    body = (
        MINIMAL_OK
        + """
[subagents.collide]
source = "/tmp/x"
[prompts.collide]
source = "/tmp/y"
"""
    )
    with pytest.raises(ConfigError, match="defined both as"):
        _write_config(tmp_path, body)


# ─── env_file ───────────────────────────────────────────────────────────


def test_env_file_loaded_when_declared(tmp_path):
    body = 'env_file = "secrets.env"\n' + MINIMAL_OK
    config = _write_config(tmp_path, body, env_file="MY_KEY=my_value\n")
    assert config.env_vars["MY_KEY"] == "my_value"


def test_missing_env_file_raises(tmp_path):
    body = 'env_file = "ghost.env"\n' + MINIMAL_OK
    with pytest.raises(FileNotFoundError):
        _write_config(tmp_path, body)


# ─── source-missing is a WARNING, not an error ──────────────────────────


def test_missing_artifact_source_warns_not_errors(tmp_path):
    body = (
        MINIMAL_OK
        + """
[skills.s]
source = "/nonexistent/path"
"""
    )
    body = body.replace("[profiles.empty]\n", '[profiles.empty]\nskills = ["s"]\n')
    with pytest.warns(UserWarning, match="source does not exist"):
        config = _write_config(tmp_path, body)
    assert "s" in config.skills


# ─── full sample fixture loads (uses fixtures_dir + GH/CONF env vars) ──


def test_full_sample_fixture_loads(fixtures_dir, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "test")
    with pytest.warns(UserWarning):
        config = load(fixtures_dir / "sample_config.toml")
    assert config.schema_version == 3
    assert set(config.agents.keys()) == {"claude-code", "copilot-cli", "pi", "codex"}
    assert "tw" in config.profiles
    # Each agent has its global_profile attached now (no scopes).
    assert config.agents["claude-code"].global_profile == "tw"


# ─── plugins: dataclasses (Task 3) ──────────────────────────────────────

from pathlib import Path  # noqa: E402

FIXTURE_PLUGINS = Path(__file__).parent / "fixtures" / "plugins"


def test_profile_parses_plugins_field():
    from twagent.config import _build_profiles

    profiles = _build_profiles({"p": {"plugins": ["alpha"]}})
    assert profiles["p"].plugins == ["alpha"]


def test_profile_plugins_defaults_empty():
    from twagent.config import Profile

    assert Profile(name="p").plugins == []


def test_plugin_dataclass_holds_member_names():
    from twagent.config import Plugin

    plugin = Plugin(
        name="alpha",
        source=Path("/x"),
        description=None,
        skills=["greet"],
        subagents=["helper.agent.md"],
        prompts=["explain.prompt.md"],
        servers=["alpha-server"],
    )
    assert plugin.skills == ["greet"]
    assert plugin.servers == ["alpha-server"]


# ─── plugins: loader wiring + collisions (Task 4) ───────────────────────


def test_plugin_pieces_injected_into_registries(tmp_path):
    body = f"""\
schema_version = 3

[plugins.alpha]
source = "{FIXTURE_PLUGINS / "alpha"}"
"""
    config = _write_config(tmp_path, body)

    assert "greet" in config.skills
    assert "helper.agent.md" in config.subagents
    assert "explain.prompt.md" in config.prompts
    assert "alpha-server" in config.servers

    # Plugin record remembers what it contributed.
    assert config.plugins["alpha"].skills == ["greet"]
    assert config.plugins["alpha"].servers == ["alpha-server"]
    # Description falls back to the manifest.
    assert config.plugins["alpha"].description == "Alpha fixture plugin"


def test_plugin_skill_collides_with_top_level_skill(tmp_path):
    body = f"""\
schema_version = 3

[skills.greet]
source = "{FIXTURE_PLUGINS / "alpha" / "skills" / "greet"}"

[plugins.alpha]
source = "{FIXTURE_PLUGINS / "alpha"}"
"""
    with pytest.raises(ConfigError, match="greet"):
        _write_config(tmp_path, body)


def test_two_plugins_colliding_skill_names_error_names_both(tmp_path):
    body = f"""\
schema_version = 3

[plugins.alpha]
source = "{FIXTURE_PLUGINS / "alpha"}"

[plugins.beta]
source = "{FIXTURE_PLUGINS / "beta"}"
"""
    with pytest.raises(ConfigError, match="alpha.*beta|beta.*alpha"):
        _write_config(tmp_path, body)


def test_plugin_missing_source_dir_is_hard_error(tmp_path):
    body = f"""\
schema_version = 3

[plugins.ghost]
source = "{tmp_path / "nonexistent"}"
"""
    with pytest.raises(ConfigError, match="ghost"):
        _write_config(tmp_path, body)


# ─── plugins: validation extensions (Task 5) ────────────────────────────


def test_plugin_name_shadowing_a_profile_errors(tmp_path):
    body = f"""\
schema_version = 3

[plugins.dup]
source = "{FIXTURE_PLUGINS / "alpha"}"

[profiles.dup]
"""
    with pytest.raises(ConfigError, match="dup"):
        _write_config(tmp_path, body)


def test_profile_referencing_unknown_plugin_errors(tmp_path):
    body = """\
schema_version = 3

[profiles.p]
plugins = ["ghost"]
"""
    with pytest.raises(ConfigError, match="unknown plugin 'ghost'"):
        _write_config(tmp_path, body)


def test_profile_referencing_known_plugin_validates(tmp_path):
    body = f"""\
schema_version = 3

[plugins.alpha]
source = "{FIXTURE_PLUGINS / "alpha"}"

[profiles.p]
plugins = ["alpha"]
"""
    config = _write_config(tmp_path, body)  # must not raise
    assert config.profiles["p"].plugins == ["alpha"]


# ─── plugins: determinism (Task 9) ──────────────────────────────────────


def test_collision_error_is_deterministic(tmp_path):
    body = f"""\
schema_version = 3

[plugins.beta]
source = "{FIXTURE_PLUGINS / "beta"}"

[plugins.alpha]
source = "{FIXTURE_PLUGINS / "alpha"}"
"""
    messages = []
    for _ in range(3):
        try:
            _write_config(tmp_path, body)
        except ConfigError as exc:
            messages.append(str(exc))
    # alpha sorts before beta → alpha injected first → beta reported as the
    # colliding contributor every time.
    assert len(set(messages)) == 1
    assert "beta" in messages[0]


# ─── profiles: unknown-key hardening ────────────────────────────────────


def test_profile_unknown_key_is_rejected_with_suggestion(tmp_path):
    # Real-world footgun: `pluings` typo silently dropped the plugin ref.
    body = """\
schema_version = 3

[profiles.wiz]
pluings = ["aaa-security-remediation"]
"""
    with pytest.raises(ConfigError) as excinfo:
        _write_config(tmp_path, body)
    msg = str(excinfo.value)
    assert "profiles.wiz" in msg
    assert "pluings" in msg
    assert "plugins" in msg  # nearest-match suggestion


def test_profile_all_valid_keys_accepted(tmp_path):
    body = f"""\
schema_version = 3

[skills.s1]
source = "{FIXTURE_PLUGINS / "alpha" / "skills" / "greet"}"

[profiles.base]

[profiles.p]
description = "every valid key"
extends = ["base"]
skills = ["s1"]
subagents = []
prompts = []
servers = []
plugins = []
"""
    config = _write_config(tmp_path, body)  # must not raise
    assert config.profiles["p"].skills == ["s1"]

from pathlib import Path

import pytest

from twagent.plugins import PluginContents, discover_plugin

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"


def test_discover_plugin_reads_all_kinds_from_manifest():
    contents = discover_plugin("alpha", FIXTURES / "alpha")

    assert isinstance(contents, PluginContents)
    assert contents.name == "alpha"
    assert contents.description == "Alpha fixture plugin"

    # skills: keyed by directory name, source is the skill dir
    assert set(contents.skills) == {"greet"}
    assert contents.skills["greet"] == (FIXTURES / "alpha" / "skills" / "greet")

    # subagents: keyed by full filename, source is the file
    assert set(contents.subagents) == {"helper.agent.md"}
    assert contents.subagents["helper.agent.md"] == (
        FIXTURES / "alpha" / "agents" / "helper.agent.md"
    )

    # prompts: keyed by full filename
    assert set(contents.prompts) == {"explain.prompt.md"}

    # servers: keyed by manifest key, raw blob preserved
    assert set(contents.servers) == {"alpha-server"}
    assert contents.servers["alpha-server"]["command"] == "echo"


def test_discover_plugin_missing_manifest_raises():
    with pytest.raises(FileNotFoundError):
        discover_plugin("nope", FIXTURES / "does-not-exist")


def test_discover_plugin_declared_dir_absent_raises(tmp_path):
    (tmp_path / "plugin.json").write_text('{"name": "x", "skills": "skills/"}')
    with pytest.raises(ValueError, match="declares skills dir"):
        discover_plugin("x", tmp_path)


def test_discover_plugin_skill_without_skill_md_is_skipped(tmp_path, recwarn):
    (tmp_path / "plugin.json").write_text('{"name": "x", "skills": "skills/"}')
    (tmp_path / "skills" / "broken").mkdir(parents=True)
    contents = discover_plugin("x", tmp_path)
    assert contents.skills == {}
    assert any("SKILL.md" in str(w.message) for w in recwarn.list)


def test_discover_plugin_malformed_manifest_raises(tmp_path):
    (tmp_path / "plugin.json").write_text("{ not valid json")
    with pytest.raises(ValueError, match="cannot parse"):
        discover_plugin("x", tmp_path)


def test_discover_plugin_reads_servers_from_mcp_json(tmp_path):
    (tmp_path / "plugin.json").write_text('{"name": "x"}')
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"ext-server": {"command": "run"}}}'
    )
    contents = discover_plugin("x", tmp_path)
    assert contents.servers["ext-server"]["command"] == "run"


def test_discover_plugin_server_in_both_sources_raises(tmp_path):
    (tmp_path / "plugin.json").write_text(
        '{"name": "x", "mcpServers": {"dup": {"command": "a"}}}'
    )
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"dup": {"command": "b"}}}')
    with pytest.raises(ValueError, match="both"):
        discover_plugin("x", tmp_path)

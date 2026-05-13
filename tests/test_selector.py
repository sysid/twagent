"""US5: selection across all four list-shaped artifact types + polymorphic --select."""

import pytest

from twagent.config import load
from twagent.selector import (
    parse_select_value,
    resolve_profile,
    resolve_selection,
    validate_names,
)


class TestParseSelectValue:
    def test_simple_csv(self):
        assert parse_select_value("a,b,c") == ["a", "b", "c"]

    def test_with_whitespace(self):
        assert parse_select_value(" a , b ") == ["a", "b"]

    def test_none_keyword_returns_empty(self):
        assert parse_select_value("none") == []

    def test_none_combined_with_others_rejected(self):
        with pytest.raises(ValueError, match="reserved"):
            parse_select_value("none,a")

    def test_empty_rejected(self):
        with pytest.raises(ValueError, match="No names"):
            parse_select_value("")


class TestValidateNames:
    def test_all_known(self):
        assert validate_names(["a"], {"a", "b"}, "skill") == ["a"]

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown skill"):
            validate_names(["ghost"], {"a", "b"}, "skill")


@pytest.fixture
def selector_config(tmp_path):
    """Module-level fixture used by both ResolveProfile and ResolveSelection tests."""
    config_text = """\
schema_version = 3
[agents.c]
capabilities = []
[agents.c.paths.global]
[agents.c.paths.project]
[skills.s1]
source = "/tmp/x1"
[skills.s2]
source = "/tmp/x2"
[subagents.r1]
source = "/tmp/r1"
[prompts.p1]
source = "/tmp/p1"
[servers.srv1]
type = "stdio"
command = "x"
[profiles.base]
skills = ["s1"]
servers = ["srv1"]
[profiles.full]
extends = ["base"]
skills = ["s2"]
subagents = ["r1"]
prompts = ["p1"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    with pytest.warns(UserWarning):  # /tmp source paths missing
        return load(config_path)


class TestResolveProfile:
    @pytest.fixture
    def config(self, selector_config):
        return selector_config

    def test_resolve_skills_includes_extends_chain(self, config):
        # base contributes s1; full adds s2 → both, parent-first
        assert resolve_profile("full", "skills", config) == ["s1", "s2"]

    def test_resolve_servers(self, config):
        assert resolve_profile("full", "servers", config) == ["srv1"]

    def test_resolve_subagents(self, config):
        assert resolve_profile("full", "subagents", config) == ["r1"]

    def test_resolve_prompts(self, config):
        assert resolve_profile("full", "prompts", config) == ["p1"]

    def test_unknown_profile_rejected(self, config):
        with pytest.raises(ValueError, match="Unknown profile"):
            resolve_profile("ghost", "skills", config)

    def test_unknown_kind_rejected(self, config):
        # `instructions` is now a valid kind in v3; pick a string that isn't.
        with pytest.raises(ValueError, match="Unknown kind"):
            resolve_profile("full", "ghost-kind", config)


class TestResolveSelection:
    """Polymorphic --select: profile names AND artifact names, mixed."""

    def test_single_profile_name(self, selector_config):
        result = resolve_selection(["full"], selector_config)
        # full extends base: parent-first, dedup'd → s1, s2 (skills); srv1 (servers)
        assert result.skills == ["s1", "s2"]
        assert result.subagents == ["r1"]
        assert result.prompts == ["p1"]
        assert result.servers == ["srv1"]

    def test_single_artifact_name_skill(self, selector_config):
        result = resolve_selection(["s1"], selector_config)
        assert result.skills == ["s1"]
        assert result.servers == []

    def test_single_artifact_name_server(self, selector_config):
        result = resolve_selection(["srv1"], selector_config)
        assert result.servers == ["srv1"]
        assert result.skills == []

    def test_mixed_profile_and_artifact(self, selector_config):
        # base profile contributes s1 + srv1; tw-cucumber-style extra adds p1
        result = resolve_selection(["base", "p1"], selector_config)
        assert result.skills == ["s1"]
        assert result.servers == ["srv1"]
        assert result.prompts == ["p1"]

    def test_dedup_across_inputs(self, selector_config):
        # base contains s1; selecting both base AND s1 should not duplicate
        result = resolve_selection(["base", "s1"], selector_config)
        assert result.skills == ["s1"]

    def test_unknown_name_raises(self, selector_config):
        with pytest.raises(ValueError, match="Unknown name"):
            resolve_selection(["definitely-not-a-thing"], selector_config)

    def test_unknown_listing_includes_known_alternatives(self, selector_config):
        with pytest.raises(ValueError) as excinfo:
            resolve_selection(["ghost"], selector_config)
        msg = str(excinfo.value)
        assert "Available profiles" in msg
        assert "Available artifacts" in msg

    def test_empty_selection_yields_empty_lists(self, selector_config):
        result = resolve_selection([], selector_config)
        assert result.instructions == []
        assert result.skills == []
        assert result.subagents == []
        assert result.prompts == []
        assert result.servers == []

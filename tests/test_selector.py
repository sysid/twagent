"""US5: selection across all four list-shaped artifact types."""

import pytest

from twagent.config import load
from twagent.selector import parse_select_value, resolve_profile, validate_names


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


class TestResolveProfile:
    @pytest.fixture
    def config(self, tmp_path):
        config_text = """\
schema_version = 1
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
[[scopes]]
name = "g"
profile = "full"
agents = ["c"]
"""
        config_path = tmp_path / "config.toml"
        config_path.write_text(config_text)
        with pytest.warns(UserWarning):  # /tmp source paths missing
            return load(config_path)

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
        with pytest.raises(ValueError, match="Unknown kind"):
            resolve_profile("full", "instructions", config)

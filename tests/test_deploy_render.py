"""Tests for instruction rendering: two-layer vars + StrictUndefined + newline norm."""

import pytest

from twagent.deploy import render_template


class TestTwoLayerVars:
    def test_common_vars_used(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("Hello {{ user_name }}")
        result = render_template(tpl, {"user_name": "Tom"}, {})
        assert result == "Hello Tom\n"

    def test_agent_vars_used(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("I am {{ agent_name }}")
        result = render_template(tpl, {}, {"agent_name": "Claude"})
        assert result == "I am Claude\n"

    def test_agent_overrides_common_on_clash(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("{{ x }}")
        result = render_template(tpl, {"x": "common"}, {"x": "agent"})
        assert result == "agent\n"

    def test_agent_and_common_combined(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("{{ user_name }} / {{ agent_name }}")
        result = render_template(tpl, {"user_name": "Tom"}, {"agent_name": "Claude"})
        assert result == "Tom / Claude\n"


class TestStrictUndefined:
    def test_missing_var_raises_hard_error(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("{{ ghost }}")
        with pytest.raises(
            Exception
        ):  # jinja2.UndefinedError; bare Exception keeps test loose
            render_template(tpl, {}, {})

    def test_silent_empty_substitution_does_not_happen(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("Hello {{ ghost }}")
        with pytest.raises(Exception):
            render_template(tpl, {}, {})


class TestNewlineNormalization:
    def test_trailing_whitespace_stripped(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("hello\n\n\n")
        result = render_template(tpl, {}, {})
        assert result == "hello\n"

    def test_single_trailing_newline_added(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("no newline")
        result = render_template(tpl, {}, {})
        assert result == "no newline\n"

    def test_loop_with_empty_list_does_not_leave_dangling_blank(self, tmp_path):
        tpl = tmp_path / "t.j2"
        tpl.write_text("hello\n{% for x in items %}- {{ x }}\n{% endfor %}")
        result = render_template(tpl, {}, {"items": []})
        assert result == "hello\n"

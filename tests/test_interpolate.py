import os

import pytest

from twagent.interpolate import load_dotenv, resolve_variables


class TestResolveVariables:
    def test_simple_variable(self):
        assert resolve_variables("${FOO}", {"FOO": "bar"}) == "bar"

    def test_variable_with_default_uses_value(self):
        assert resolve_variables("${FOO:-fallback}", {"FOO": "bar"}) == "bar"

    def test_variable_with_default_uses_default(self):
        assert resolve_variables("${FOO:-fallback}", {}) == "fallback"

    def test_multiple_variables_in_string(self):
        result = resolve_variables(
            "https://${HOST}:${PORT}/api",
            {"HOST": "example.com", "PORT": "8080"},
        )
        assert result == "https://example.com:8080/api"

    def test_literal_string_unchanged(self):
        assert (
            resolve_variables("no variables here", {"FOO": "bar"})
            == "no variables here"
        )

    def test_empty_string(self):
        assert resolve_variables("", {}) == ""

    def test_unresolved_variable_raises(self):
        with pytest.raises(ValueError, match="MISSING_VAR"):
            resolve_variables("${MISSING_VAR}", {})

    def test_unresolved_lists_all_missing(self):
        with pytest.raises(ValueError, match="VAR_A") as exc_info:
            resolve_variables("${VAR_A} and ${VAR_B}", {})
        assert "VAR_B" in str(exc_info.value)

    def test_variable_with_underscore(self):
        assert resolve_variables("${MY_VAR}", {"MY_VAR": "value"}) == "value"

    def test_variable_with_empty_default(self):
        assert resolve_variables("${VAR:-}", {}) == ""

    def test_default_with_special_chars(self):
        assert (
            resolve_variables("${VAR:-https://example.com/path}", {})
            == "https://example.com/path"
        )


class TestLoadDotenv:
    def test_loads_key_value_pairs(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\nBAZ=qux\n")
        assert load_dotenv(env_file) == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nFOO=bar\n")
        assert load_dotenv(env_file) == {"FOO": "bar"}

    def test_skips_blank_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n\n\nBAZ=qux\n")
        assert load_dotenv(env_file) == {"FOO": "bar", "BAZ": "qux"}

    def test_strips_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=\"bar\"\nBAZ='qux'\n")
        assert load_dotenv(env_file) == {"FOO": "bar", "BAZ": "qux"}

    def test_value_with_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("URL=https://example.com?foo=bar\n")
        assert load_dotenv(env_file) == {"URL": "https://example.com?foo=bar"}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_dotenv(tmp_path / "nonexistent.env")

    def test_empty_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        assert load_dotenv(env_file) == {}


class TestEnvVarPrecedence:
    def test_env_var_wins_over_dotenv(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=from-dotenv\n")
        dotenv_vars = load_dotenv(env_file)
        monkeypatch.setenv("FOO", "from-env")
        variables = {**dotenv_vars, **dict(os.environ)}
        assert resolve_variables("${FOO}", variables) == "from-env"

    def test_dotenv_used_when_env_not_set(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=from-dotenv\n")
        dotenv_vars = load_dotenv(env_file)
        monkeypatch.delenv("FOO", raising=False)
        assert resolve_variables("${FOO}", {**dotenv_vars}) == "from-dotenv"

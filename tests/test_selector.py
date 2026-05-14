"""US5: selection across all four list-shaped artifact types + polymorphic --select."""

from types import SimpleNamespace

import pytest

from twagent.config import load
from twagent import selector
from twagent.selector import (
    parse_select_value,
    resolve_profile,
    resolve_selection,
    select_interactive,
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


# ─── Interactive picker backends ────────────────────────────────────────


class TestFzfBackend:
    """fzf-based picker. We never actually exec fzf in tests — we
    monkeypatch `shutil.which` to claim fzf exists and `subprocess.run`
    to return canned `CompletedProcess` results."""

    @pytest.fixture
    def items(self):
        return {
            "tw-claude": "[profile]",
            "bkmr-memory": "[skill]",
            "github": "[mcp]",
        }

    def _patch_fzf(self, monkeypatch, version="0.46.0", run_results=None):
        """Pretend fzf is on $PATH with the given version. `run_results` is
        a list of CompletedProcess instances yielded in sequence: the first
        is consumed by `_detect_fzf`'s `--version` probe; the second is the
        real picker invocation.
        """
        monkeypatch.delenv("TWAGENT_NO_FZF", raising=False)
        monkeypatch.setattr(selector.shutil, "which", lambda name: "/fake/fzf")
        version_proc = SimpleNamespace(
            stdout=f"{version} (brew)\n", returncode=0, stderr=""
        )
        results = [version_proc] + list(run_results or [])
        calls: list[dict] = []

        def fake_run(args, **kwargs):
            calls.append({"args": args, **kwargs})
            return results.pop(0)

        monkeypatch.setattr(selector.subprocess, "run", fake_run)
        return calls

    def test_user_accepts_two_items(self, items, monkeypatch):
        run_proc = SimpleNamespace(
            stdout="tw-claude   [profile]\ngithub      [mcp]\n",
            stderr="",
            returncode=0,
        )
        self._patch_fzf(monkeypatch, run_results=[run_proc])
        chosen = select_interactive(items)
        assert chosen == ["tw-claude", "github"]

    def test_return_order_matches_display_not_click_order(self, items, monkeypatch):
        """fzf prints items in click order; we sort back to display order
        so the deploy plan is deterministic regardless of how the user
        clicked. items dict order: tw-claude, bkmr-memory, github."""
        # User clicked github first, then tw-claude — fzf prints in that order
        run_proc = SimpleNamespace(
            stdout="github      [mcp]\ntw-claude   [profile]\n",
            stderr="",
            returncode=0,
        )
        self._patch_fzf(monkeypatch, run_results=[run_proc])
        chosen = select_interactive(items)
        # Returned in display order (tw-claude before github)
        assert chosen == ["tw-claude", "github"]

    def test_cancel_returns_none(self, items, monkeypatch):
        run_proc = SimpleNamespace(stdout="", stderr="", returncode=130)
        self._patch_fzf(monkeypatch, run_results=[run_proc])
        assert select_interactive(items) is None

    def test_no_match_returns_empty(self, items, monkeypatch):
        run_proc = SimpleNamespace(stdout="", stderr="", returncode=1)
        self._patch_fzf(monkeypatch, run_results=[run_proc])
        assert select_interactive(items) == []

    def test_preselect_is_wired_into_load_bind(self, items, monkeypatch):
        run_proc = SimpleNamespace(stdout="", stderr="", returncode=0)
        calls = self._patch_fzf(monkeypatch, run_results=[run_proc])
        select_interactive(items, preselected={"tw-claude", "github"})
        picker_call = calls[1]
        bind_arg = next(a for a in picker_call["args"] if a.startswith("--bind="))
        # tw-claude is at index 0 → pos(1); github at index 2 → pos(3).
        # `load:` (not `start:`) — start fires before stdin is read, leaving
        # nothing to toggle. Trailing `+first` resets cursor to top.
        assert "load:pos(1)+toggle+pos(3)+toggle+first" in bind_arg

    def test_too_old_fzf_raises(self, items, monkeypatch):
        # _detect_fzf reads --version then raises on < 0.35
        self._patch_fzf(monkeypatch, version="0.30.0", run_results=[])
        with pytest.raises(RuntimeError, match="fzf 0.30 is too old"):
            select_interactive(items)

    def test_no_fzf_env_var_skips_detection(self, items, monkeypatch):
        """TWAGENT_NO_FZF=1 forces the simple-term-menu fallback path."""
        monkeypatch.setenv("TWAGENT_NO_FZF", "1")
        # shutil.which / subprocess.run must not be called for fzf at all;
        # but simple-term-menu's TerminalMenu *will* be called — stub it out.
        called = {"fallback": False}

        class FakeMenu:
            chosen_accept_key = "enter"

            def __init__(self, *a, **kw):
                called["fallback"] = True

            def show(self):
                return None

        monkeypatch.setattr(selector, "TerminalMenu", FakeMenu)
        select_interactive(items)
        assert called["fallback"] is True

    def test_fzf_invocation_failure_falls_back(self, items, monkeypatch):
        """If `subprocess.run` for the picker raises OSError, we fall back."""
        monkeypatch.delenv("TWAGENT_NO_FZF", raising=False)
        monkeypatch.setattr(selector.shutil, "which", lambda n: "/fake/fzf")
        version_proc = SimpleNamespace(stdout="0.46.0\n", returncode=0, stderr="")

        def fake_run(args, **kwargs):
            if "--version" in args:
                return version_proc
            raise OSError("kaboom")

        monkeypatch.setattr(selector.subprocess, "run", fake_run)

        called = {"fallback": False}

        class FakeMenu:
            chosen_accept_key = "enter"

            def __init__(self, *a, **kw):
                called["fallback"] = True

            def show(self):
                return ()

        monkeypatch.setattr(selector, "TerminalMenu", FakeMenu)
        select_interactive(items)
        assert called["fallback"] is True

"""Tests for symlink hygiene — 5 state transitions from data-model.md.

Plus FR-017 missing-source aggregation case.
"""

from pathlib import Path

from twagent.deploy import link_artifacts


def _make_source(tmp_path: Path, name: str = "source") -> Path:
    src = tmp_path / "sources" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("source content")
    return src


class TestSymlinkHygiene:
    def test_creates_link_when_target_empty(self, tmp_path):
        src = _make_source(tmp_path)
        target_dir = tmp_path / "target"
        result = link_artifacts({"foo": src}, target_dir)
        link = target_dir / "foo"
        assert link.is_symlink()
        assert link.resolve() == src.resolve()
        assert "foo" in result.created

    def test_idempotent_when_link_already_correct(self, tmp_path):
        src = _make_source(tmp_path)
        target_dir = tmp_path / "target"
        link_artifacts({"foo": src}, target_dir)
        result = link_artifacts({"foo": src}, target_dir)
        # Second run does no destructive work — counted as kept (no-op)
        assert "foo" in result.kept
        assert "foo" not in result.created

    def test_relinks_when_pointing_elsewhere(self, tmp_path):
        old = _make_source(tmp_path, "old")
        new = _make_source(tmp_path, "new")
        target_dir = tmp_path / "target"
        # Pre-place a link to the old source
        target_dir.mkdir()
        (target_dir / "foo").symlink_to(old)
        result = link_artifacts({"foo": new}, target_dir)
        assert (target_dir / "foo").resolve() == new.resolve()
        assert "foo" in result.relinked

    def test_warns_on_dangling_symlink_left_behind(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "ghost").symlink_to(tmp_path / "missing")
        # We only deploy "foo"; the unrelated "ghost" link is dangling.
        src = _make_source(tmp_path)
        result = link_artifacts({"foo": src}, target_dir)
        assert "ghost" in result.dangling

    def test_skips_real_non_symlink_silently(self, tmp_path):
        src = _make_source(tmp_path)
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        # User has a real file/dir at target — must NOT be overwritten
        (target_dir / "foo").mkdir()
        result = link_artifacts({"foo": src}, target_dir)
        assert (target_dir / "foo").is_dir()
        assert not (target_dir / "foo").is_symlink()
        assert "foo" in result.skipped_real

    def test_missing_source_recorded_as_error(self, tmp_path):
        target_dir = tmp_path / "target"
        result = link_artifacts({"ghost": tmp_path / "no-such-source"}, target_dir)
        assert any("ghost" in e for e in result.errors)


class TestDryRun:
    def test_dry_run_creates_nothing(self, tmp_path):
        src = _make_source(tmp_path)
        target_dir = tmp_path / "target"
        result = link_artifacts({"foo": src}, target_dir, dry_run=True)
        assert not (target_dir / "foo").exists()
        assert "foo" in result.created  # planned, but not actually written

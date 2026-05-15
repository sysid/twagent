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

    def test_removes_broken_orphan_symlink(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "ghost").symlink_to(tmp_path / "missing")
        # We only deploy "foo"; the unrelated "ghost" link is an orphan.
        src = _make_source(tmp_path)
        result = link_artifacts({"foo": src}, target_dir)
        assert "ghost" in result.removed
        assert not (target_dir / "ghost").is_symlink()
        assert not (target_dir / "ghost").exists()

    def test_removes_working_orphan_symlink(self, tmp_path):
        # Real-world case: a previously-deployed skill is removed from the
        # registry/profile. Its source file still exists on disk, but its
        # name no longer appears in `sources` — the symlink must go, the
        # source file must stay.
        stale_src = _make_source(tmp_path, "stale")
        live_src = _make_source(tmp_path, "live")
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "stale").symlink_to(stale_src)
        result = link_artifacts({"live": live_src}, target_dir)
        assert "stale" in result.removed
        assert not (target_dir / "stale").is_symlink()
        assert stale_src.exists()  # source file is untouched
        assert (target_dir / "live").resolve() == live_src.resolve()

    def test_does_not_remove_real_files_or_dirs(self, tmp_path):
        src = _make_source(tmp_path)
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        real_file = target_dir / "manual.md"
        real_file.write_text("hand-placed")
        real_dir = target_dir / "manual_dir"
        real_dir.mkdir()
        result = link_artifacts({"foo": src}, target_dir)
        assert real_file.exists() and real_file.read_text() == "hand-placed"
        assert real_dir.is_dir()
        assert "manual.md" not in result.removed
        assert "manual_dir" not in result.removed

    def test_dry_run_does_not_remove_orphans(self, tmp_path):
        stale_src = _make_source(tmp_path, "stale")
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / "stale").symlink_to(stale_src)
        result = link_artifacts({}, target_dir, dry_run=True)
        assert "stale" in result.removed
        assert (target_dir / "stale").is_symlink()  # still on disk

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

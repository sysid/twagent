"""`twagent info`: disk-reality read-out with provenance + status."""

import os
import shutil
import sys
from pathlib import Path

import pytest

from twagent.config import FileArtifact, load
from twagent.info import (
    AgentInfo,
    Entry,
    InfoReport,
    Section,
    _build_source_index,
    _classify_entry,
    collect_info,
)


# ─── Task 1: data model ──────────────────────────────────────────────────


def test_entry_as_dict_round_trips_fields():
    entry = Entry(
        name="tw-review",
        status="managed",
        artifact="tw-review",
        target="/src/skills/tw-review",
    )
    assert entry.as_dict() == {
        "name": "tw-review",
        "status": "managed",
        "artifact": "tw-review",
        "target": "/src/skills/tw-review",
    }


def test_inforeport_as_dict_nests_agents_sections_entries():
    report = InfoReport(
        cwd="/proj",
        agents=[
            AgentInfo(
                agent_id="claude-code",
                capabilities=["skills"],
                sections=[
                    Section(
                        kind="skills",
                        layer="global",
                        path="/home/.claude/skills",
                        render_as="linked",
                        entries=[Entry("s", "managed", "s", "/src/s")],
                    )
                ],
            )
        ],
    )
    out = report.as_dict()
    assert out["cwd"] == "/proj"
    assert out["agents"][0]["agent_id"] == "claude-code"
    section = out["agents"][0]["sections"][0]
    assert section["kind"] == "skills"
    assert section["layer"] == "global"
    assert section["render_as"] == "linked"
    assert section["entries"][0]["name"] == "s"


# ─── Task 2: reverse index + classification ──────────────────────────────


def _registry(tmp_path: Path) -> dict[str, dict[str, FileArtifact]]:
    """Minimal stand-in for the per-kind registries collect_info reads."""
    src = tmp_path / "src" / "skills" / "tw-review"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("ok")
    return {"skills": {"tw-review": FileArtifact("tw-review", src)}}


def test_classify_managed_symlink_resolves_to_registry_name(tmp_path):
    reg = _registry(tmp_path)
    index = _build_source_index({"skills": reg["skills"]})
    deployed = tmp_path / "claude" / "skills"
    deployed.mkdir(parents=True)
    link = deployed / "tw-review"
    link.symlink_to(reg["skills"]["tw-review"].source)

    entry = _classify_entry(link, index)
    assert entry.status == "managed"
    assert entry.artifact == "tw-review"
    assert entry.name == "tw-review"


def test_classify_dangling_symlink(tmp_path):
    index = _build_source_index({"skills": {}})
    deployed = tmp_path / "claude" / "skills"
    deployed.mkdir(parents=True)
    ghost = deployed / "ghost"
    ghost.symlink_to(tmp_path / "nonexistent")

    entry = _classify_entry(ghost, index)
    assert entry.status == "dangling"
    assert entry.name == "ghost"


def test_classify_foreign_symlink_is_unmanaged(tmp_path):
    index = _build_source_index({"skills": {}})
    real = tmp_path / "elsewhere"
    real.mkdir()
    deployed = tmp_path / "claude" / "skills"
    deployed.mkdir(parents=True)
    foreign = deployed / "handmade"
    foreign.symlink_to(real)

    entry = _classify_entry(foreign, index)
    assert entry.status == "unmanaged"
    assert entry.artifact is None


def test_classify_plain_file_is_unmanaged(tmp_path):
    index = _build_source_index({"skills": {}})
    deployed = tmp_path / "claude" / "skills"
    deployed.mkdir(parents=True)
    plain = deployed / "notes.md"
    plain.write_text("hi")

    entry = _classify_entry(plain, index)
    assert entry.status == "unmanaged"
    assert entry.target is None


# ─── Task 3: collect_info linked kinds across layers ─────────────────────


def _linked_world(tmp_path: Path):
    """One agent, one skill, deployed both globally and locally under cwd."""
    skill_src = tmp_path / "src" / "skills" / "bkmr"
    skill_src.mkdir(parents=True)
    (skill_src / "SKILL.md").write_text("ok")

    global_skills = tmp_path / "home" / "claude" / "skills"
    global_skills.mkdir(parents=True)
    (global_skills / "bkmr").symlink_to(skill_src)

    cwd = tmp_path / "proj"
    local_skills = cwd / ".claude" / "skills"
    local_skills.mkdir(parents=True)
    (local_skills / "bkmr").symlink_to(skill_src)

    config_text = f"""\
schema_version = 3
[agents.claude-code]
capabilities = ["skills"]
[agents.claude-code.paths.global]
skills = ["{global_skills}"]
[agents.claude-code.paths.project]
skills = [".claude/skills"]
[agents.claude-code.vars]
[skills.bkmr]
source = "{skill_src}"
[profiles.p]
skills = ["bkmr"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return load(config_path), cwd


def test_collect_info_local_only_by_default(tmp_path):
    config, cwd = _linked_world(tmp_path)
    report = collect_info(config, cwd)

    assert report.cwd == str(cwd)
    agent = report.agents[0]
    assert agent.agent_id == "claude-code"

    layers = {s.layer for s in agent.sections if s.kind == "skills"}
    assert layers == {"local"}  # global excluded by default
    local = [s for s in agent.sections if s.kind == "skills"][0]
    assert [e.name for e in local.entries] == ["bkmr"]
    assert local.entries[0].status == "managed"
    assert local.entries[0].artifact == "bkmr"


def test_collect_info_include_global_adds_global_layer(tmp_path):
    config, cwd = _linked_world(tmp_path)
    report = collect_info(config, cwd, include_global=True)

    layers = {s.layer: s for s in report.agents[0].sections if s.kind == "skills"}
    assert set(layers) == {"global", "local"}
    for section in layers.values():
        assert section.render_as == "linked"
        assert [e.name for e in section.entries] == ["bkmr"]
        assert section.entries[0].status == "managed"


def test_collect_info_absent_dir_yields_empty_section(tmp_path):
    config, cwd = _linked_world(tmp_path)
    shutil.rmtree(cwd / ".claude" / "skills")
    report = collect_info(config, cwd)
    local = [
        s
        for s in report.agents[0].sections
        if s.kind == "skills" and s.layer == "local"
    ][0]
    assert local.entries == []
    assert local.error is None


# ─── Task 4: instructions present/absent ─────────────────────────────────


def _instructions_world(tmp_path: Path):
    global_md = tmp_path / "home" / "claude" / "CLAUDE.md"
    global_md.parent.mkdir(parents=True)
    global_md.write_text("# rendered instructions")

    instr_src = tmp_path / "src" / "AGENTS.md"
    instr_src.parent.mkdir(parents=True, exist_ok=True)
    instr_src.write_text("# template")

    cwd = tmp_path / "proj"
    cwd.mkdir()
    config_text = f"""\
schema_version = 3
[agents.claude-code]
capabilities = ["instructions"]
[agents.claude-code.paths.global]
instructions = ["{global_md}"]
[agents.claude-code.paths.project]
instructions = ["CLAUDE.md"]
[agents.claude-code.vars]
[instructions.main]
source = "{instr_src}"
[profiles.p]
instructions = ["main"]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return load(config_path), cwd


def test_instructions_present_globally_absent_locally(tmp_path):
    config, cwd = _instructions_world(tmp_path)
    report = collect_info(config, cwd, include_global=True)
    sections = {
        s.layer: s for s in report.agents[0].sections if s.kind == "instructions"
    }

    assert sections["global"].render_as == "instructions"
    assert sections["global"].present is True
    assert sections["local"].present is False


# ─── Task 5: raw MCP capture ─────────────────────────────────────────────


def _mcp_world(tmp_path: Path, mcp_body: str):
    mcp_file = tmp_path / "home" / "claude" / "mcp.json"
    mcp_file.parent.mkdir(parents=True)
    mcp_file.write_text(mcp_body)

    cwd = tmp_path / "proj"
    cwd.mkdir()
    config_text = f"""\
schema_version = 3
[agents.claude-code]
capabilities = ["mcp"]
mcp_format = "claude-code"
[agents.claude-code.paths.global]
mcp = ["{mcp_file}"]
[agents.claude-code.paths.project]
mcp = [".mcp.json"]
[agents.claude-code.vars]
[profiles.p]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return load(config_path), cwd


def test_mcp_section_captures_raw_content_including_secrets(tmp_path):
    body = '{"mcpServers": {"gh": {"env": {"TOKEN": "ghp_SECRET123"}}}}'
    config, cwd = _mcp_world(tmp_path, body)
    report = collect_info(config, cwd, include_global=True)

    global_mcp = [
        s for s in report.agents[0].sections if s.kind == "mcp" and s.layer == "global"
    ][0]
    assert global_mcp.render_as == "mcp"
    assert global_mcp.content_format == "json"
    # Deliberate per spec Q2=B: raw content is shown verbatim, secrets included.
    assert global_mcp.content == body
    assert "ghp_SECRET123" in global_mcp.content


def test_codex_mcp_section_records_toml_content_format(tmp_path):
    mcp_file = tmp_path / "home" / "codex" / "config.toml"
    mcp_file.parent.mkdir(parents=True)
    mcp_file.write_text('[mcp_servers.docs]\nurl = "https://example.com/mcp"\n')

    cwd = tmp_path / "proj"
    cwd.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""\
schema_version = 3
[agents.codex]
capabilities = ["mcp"]
mcp_format = "codex"
[agents.codex.paths.global]
mcp = ["{mcp_file}"]
[agents.codex.paths.project]
mcp = [".codex/config.toml"]
[agents.codex.vars]
[profiles.p]
"""
    )

    report = collect_info(load(config_path), cwd, include_global=True)

    global_mcp = [
        section
        for section in report.agents[0].sections
        if section.kind == "mcp" and section.layer == "global"
    ][0]
    assert global_mcp.content_format == "toml"


def test_mcp_section_absent_file_has_no_content(tmp_path):
    config, cwd = _mcp_world(tmp_path, "{}")
    report = collect_info(config, cwd)
    local_mcp = [
        s for s in report.agents[0].sections if s.kind == "mcp" and s.layer == "local"
    ][0]
    assert local_mcp.content is None
    assert local_mcp.error is None


def test_global_mcp_excluded_by_default(tmp_path):
    config, cwd = _mcp_world(tmp_path, '{"mcpServers": {}}')
    report = collect_info(config, cwd)  # no include_global
    layers = {s.layer for s in report.agents[0].sections if s.kind == "mcp"}
    assert layers == {"local"}  # global mcp not scanned by default


def test_excluded_path_is_never_shown(tmp_path, monkeypatch):
    """Files in _EXCLUDED_PATHS (e.g. ~/.claude.json) must never appear."""
    import twagent.info as info_mod

    config, cwd = _mcp_world(tmp_path, '{"mcpServers": {}}')
    global_mcp = config.agents["claude-code"].paths_global["mcp"][0]
    monkeypatch.setattr(info_mod, "_EXCLUDED_PATHS", frozenset({global_mcp.resolve()}))

    report = collect_info(config, cwd, include_global=True)
    paths = {s.path for s in report.agents[0].sections}
    assert str(global_mcp) not in paths


# ─── Task 6: fail-soft on unreadable dir ─────────────────────────────────


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses permission bits",
)
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_unreadable_dir_is_flagged_not_fatal(tmp_path):
    config, cwd = _linked_world(tmp_path)
    global_skills = config.agents["claude-code"].paths_global["skills"][0]
    global_skills.chmod(0o000)
    try:
        report = collect_info(config, cwd, include_global=True)  # must NOT raise
    finally:
        global_skills.chmod(0o755)

    global_section = [
        s
        for s in report.agents[0].sections
        if s.kind == "skills" and s.layer == "global"
    ][0]
    assert global_section.error is not None
    assert "unreadable" in global_section.error

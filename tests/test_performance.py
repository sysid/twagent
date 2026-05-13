"""SC-008 / plan Performance Goals: full apply ≤ 5 s for the standard config."""

import time

import pytest

from twagent.config import load
from twagent.deploy import apply_global


# Generous budget: spec SC-008 said "few seconds"; plan tightened to ≤ 5 s wall-clock.
BUDGET_SECONDS = 5.0


@pytest.fixture
def standard_world(tmp_path, monkeypatch):
    """Build a config at the documented "standard" scale:
    5 agents-shaped × 50 file artifacts × 20 MCP servers.

    Uses a single mcp_format and a single agent to keep the fixture tractable
    while exercising the loops that dominate runtime (artifact + server iteration).
    """
    monkeypatch.setenv("GITHUB_TOKEN", "test")

    # Real source dirs for 50 skills
    skills_root = tmp_path / "src" / "skills"
    skills_root.mkdir(parents=True)
    skill_lines: list[str] = []
    skill_refs: list[str] = []
    for i in range(50):
        name = f"skill_{i:02d}"
        sd = skills_root / name
        sd.mkdir()
        (sd / "SKILL.md").write_text(f"# {name}\n")
        skill_lines.append(f'[skills.{name}]\nsource = "{sd}"\n')
        skill_refs.append(f'"{name}"')

    # 20 MCP servers
    server_lines: list[str] = []
    server_refs: list[str] = []
    for i in range(20):
        name = f"server_{i:02d}"
        server_lines.append(
            f'[servers.{name}]\ntype = "stdio"\ncommand = "noop-{i}"\n'
            f'env = {{ GITHUB_TOKEN = "${{GITHUB_TOKEN}}" }}\n'
        )
        server_refs.append(f'"{name}"')

    target_root = tmp_path / "claude"
    target_root.mkdir()

    config_text = f"""\
schema_version = 3
[common]
[common.vars]
user_name = "Tom"
[agents.c]
capabilities = ["skills", "mcp"]
mcp_format = "claude-code"
global_profile = "p"
[agents.c.paths.global]
skills = ["{target_root}/skills"]
mcp = ["{target_root}/.claude.json"]
[agents.c.paths.project]
skills = [".s"]
mcp = [".m"]
[agents.c.vars]
{"".join(skill_lines)}
{"".join(server_lines)}
[profiles.p]
skills = [{", ".join(skill_refs)}]
servers = [{", ".join(server_refs)}]
"""
    config_path = tmp_path / "config.toml"
    config_path.write_text(config_text)
    return config_path


def test_apply_under_budget(standard_world):
    config = load(standard_world)
    start = time.perf_counter()
    result = apply_global(config)
    elapsed = time.perf_counter() - start
    assert not result.has_errors, result.errors
    assert elapsed < BUDGET_SECONDS, (
        f"apply took {elapsed:.3f}s, budget {BUDGET_SECONDS}s "
        f"(50 skills + 20 servers, 1 agent, 1 scope)"
    )

"""US6: extract CLI verb — stdout-only, read-only."""

import json

from typer.testing import CliRunner

from twagent.cli import app

runner = CliRunner()


def test_extract_round_trip(tmp_path):
    src = tmp_path / "mcp.json"
    src.write_text(json.dumps({"mcpServers": {"github": {"command": "npx"}}}))
    result = runner.invoke(app, ["extract", str(src)])
    assert result.exit_code == 0
    assert "[servers.github]" in result.output
    assert 'command = "npx"' in result.output


def test_extract_does_not_touch_disk(tmp_path):
    src = tmp_path / "mcp.json"
    payload = json.dumps({"mcpServers": {"x": {"command": "y"}}})
    src.write_text(payload)
    before = sorted(p.name for p in tmp_path.iterdir())
    runner.invoke(app, ["extract", str(src)])
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after
    assert src.read_text() == payload


def test_extract_missing_file_exits_two(tmp_path):
    result = runner.invoke(app, ["extract", str(tmp_path / "ghost.json")])
    assert result.exit_code == 2


def test_extract_invalid_json_exits_two(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    result = runner.invoke(app, ["extract", str(bad)])
    assert result.exit_code == 2

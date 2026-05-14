from pathlib import Path

import pytest
from typer.testing import CliRunner

from twagent import __version__
from twagent.cli import _STUB_CONFIG, app
from twagent.config import load

runner = CliRunner()


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_init_stub_config_is_valid_v3(tmp_path: Path):
    """`twagent edit --init` writes _STUB_CONFIG to disk. A user who saves it
    unedited must be able to run any twagent command without a ConfigError.
    Regression: the stub used to ship an uncommented [[scopes]] block that
    config.py rejects in v3."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(_STUB_CONFIG)
    # `source` warnings are fine here (placeholders point at real-looking
    # paths that don't exist in a tmpdir). What we care about is that
    # parsing + validation succeeds.
    with pytest.warns(UserWarning):
        cfg = load(config_path)
    assert cfg.schema_version == 3
    assert "claude-code" in cfg.agents
    assert "minimal" in cfg.profiles

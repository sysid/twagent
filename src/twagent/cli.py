import logging

import typer

from twagent import __version__

logger = logging.getLogger(__name__)

app = typer.Typer(add_completion=True, no_args_is_help=True)


@app.callback()
def _main() -> None:
    """twagent CLI."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()

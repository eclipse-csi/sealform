"""Command-line interface."""

from __future__ import annotations

import click

from signpath_manager import __version__


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="signpath-manager")
def cli() -> None:
    """Manage SignPath.io organizations as code."""


def main() -> None:
    cli()

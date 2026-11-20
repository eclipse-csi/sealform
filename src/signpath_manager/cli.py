"""Command-line interface."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console

from signpath_manager import __version__
from signpath_manager.config.loader import load_config
from signpath_manager.config.schema import SCHEMAS, json_schema
from signpath_manager.diagnostics import Diagnostics, Severity


@dataclass
class Settings:
    config_dir: Path
    verbose: int


def _stderr() -> Console:
    return Console(stderr=True, highlight=False, soft_wrap=True)


def print_diagnostics(diags: Diagnostics, console: Console | None = None) -> None:
    console = console or _stderr()
    for d in diags.items:
        style = "red" if d.severity is Severity.ERROR else "yellow"
        console.print(f"[{style}]{d}[/{style}]", markup=True, highlight=False)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="signpath-manager")
@click.option(
    "-c",
    "--config",
    "config_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Configuration root (the directory containing organization.yaml).",
)
@click.option("-v", "--verbose", count=True, help="Increase verbosity.")
@click.pass_context
def cli(ctx: click.Context, config_dir: Path, verbose: int) -> None:
    """Manage SignPath.io organizations as code."""
    ctx.obj = Settings(config_dir=config_dir, verbose=verbose)


@cli.command()
@click.option("-p", "--project", "projects", multiple=True, help="Only validate this project (repeatable).")
@click.pass_obj
def validate(settings: Settings, projects: tuple[str, ...]) -> None:
    """Validate the configuration offline (no API access)."""
    state, diags = load_config(settings.config_dir, projects=projects or None)
    print_diagnostics(diags)
    if state is None or diags.has_errors:
        _stderr().print(f"[red]{len(diags.errors)} error(s), {len(diags.warnings)} warning(s)[/red]")
        sys.exit(1)
    _stderr().print(
        f"[green]Configuration is valid[/green]: {len(state.projects)} project(s), "
        f"{len(state.pipeline_policies)} pipeline policy(ies), {len(diags.warnings)} warning(s)."
    )


@cli.command()
@click.argument("kind", type=click.Choice(sorted(SCHEMAS)), required=False)
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write <kind>.schema.json files for all kinds into this directory.",
)
def schema(kind: str | None, output_dir: Path | None) -> None:
    """Print the JSON Schema of a configuration file kind (organization, profile, project)."""
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for k in sorted(SCHEMAS):
            (output_dir / f"{k}.schema.json").write_text(json.dumps(json_schema(k), indent=2) + "\n")
        return
    if kind is None:
        raise click.UsageError("give a KIND or --output-dir")
    click.echo(json.dumps(json_schema(kind), indent=2))


def main() -> None:
    cli()

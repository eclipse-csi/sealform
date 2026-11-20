"""Command-line interface."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import click
import httpx
from rich.console import Console
from rich.markup import escape

from signpath_manager import __version__
from signpath_manager.api.client import SignPathClient
from signpath_manager.api.errors import ApiError, TransportError
from signpath_manager.api.http import HttpSession
from signpath_manager.apply.executor import Executor, Outcome
from signpath_manager.auth import TokenError, resolve_token
from signpath_manager.config.desired import DesiredState
from signpath_manager.config.loader import load_config
from signpath_manager.config.model import DEFAULT_API_URL
from signpath_manager.config.schema import SCHEMAS, json_schema
from signpath_manager.diagnostics import Diagnostics, Severity
from signpath_manager.importer.export import Importer
from signpath_manager.live.fetch import Fetcher, FetchRequest
from signpath_manager.live.state import Scope
from signpath_manager.plan.actions import Plan
from signpath_manager.plan.diff import build_plan
from signpath_manager.plan.render import render_plan
from signpath_manager.tokens.sinks import DiscardSink, FileSink, TokenSink, TokenSinkError, TtySink

# Tests replace this to talk to an in-memory fake of the SignPath API.
TransportFactory = Callable[[], httpx.BaseTransport | None]
transport_factory: TransportFactory = lambda: None  # noqa: E731


@dataclass
class Settings:
    config_dir: Path
    verbose: int
    token_file: Path | None = None
    token_stdin: bool = False
    organization_id: str | None = None
    api_url: str | None = None
    parallelism: int = 8
    warnings: list[str] = field(default_factory=list)


def _stderr() -> Console:
    return Console(stderr=True, highlight=False, soft_wrap=True)


def _stdout() -> Console:
    return Console(highlight=False, soft_wrap=True)


def fail(message: str, code: int = 1) -> NoReturn:
    _stderr().print(f"[red]error:[/red] {message}", highlight=False)
    sys.exit(code)


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
@click.option(
    "--token-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Read the API token from this file (otherwise --token-stdin or $SIGNPATH_API_TOKEN).",
)
@click.option("--token-stdin", is_flag=True, help="Read the API token from the first line of stdin.")
@click.option(
    "--organization-id",
    envvar="SIGNPATH_ORGANIZATION_ID",
    help="Override organization_id from organization.yaml (e.g. to target a sandbox).",
)
@click.option("--api-url", envvar="SIGNPATH_API_URL", help="Override the API base URL.")
@click.option(
    "--parallelism",
    type=click.IntRange(1, 32),
    default=8,
    show_default=True,
    help="Maximum concurrent API requests while reading.",
)
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-vv logs every API request).")
@click.pass_context
def cli(
    ctx: click.Context,
    config_dir: Path,
    token_file: Path | None,
    token_stdin: bool,
    organization_id: str | None,
    api_url: str | None,
    parallelism: int,
    verbose: int,
) -> None:
    """Manage SignPath.io organizations as code."""
    if verbose:
        logging.basicConfig(
            level=logging.DEBUG if verbose > 1 else logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)  # never log request headers
        logging.getLogger("httpcore").setLevel(logging.WARNING)
    ctx.obj = Settings(
        config_dir=config_dir,
        verbose=verbose,
        token_file=token_file,
        token_stdin=token_stdin,
        organization_id=organization_id,
        api_url=api_url,
        parallelism=parallelism,
    )


# ---------------------------------------------------------------------------- shared helpers


def load_desired(settings: Settings, projects: tuple[str, ...] | None) -> DesiredState:
    state, diags = load_config(settings.config_dir, projects=projects or None)
    print_diagnostics(diags)
    if state is None or diags.has_errors:
        fail(f"invalid configuration: {len(diags.errors)} error(s)")
    if settings.organization_id:
        state.organization_id = settings.organization_id
    if settings.api_url:
        state.api_url = settings.api_url.rstrip("/")
    return state


def make_client(settings: Settings, api_url: str, organization_id: str) -> SignPathClient:
    try:
        token = resolve_token(settings.token_file, settings.token_stdin, warn=settings.warnings)
    except TokenError as e:
        fail(str(e))
    for w in settings.warnings:
        _stderr().print(f"[yellow]warning:[/yellow] {w}")
    return SignPathClient(HttpSession(api_url, organization_id, token, transport=transport_factory()))


def scope_for(projects: tuple[str, ...], org_only: bool) -> Scope:
    if org_only and projects:
        raise click.UsageError("--org-only and --project are mutually exclusive")
    if org_only:
        return "org"
    return "projects" if projects else "full"


def compute_plan(
    settings: Settings,
    desired: DesiredState,
    client: SignPathClient,
    scope: Scope,
    *,
    prune: bool,
    max_deactivations: int,
) -> Plan:
    fetcher = Fetcher(client, parallelism=settings.parallelism)
    live = fetcher.fetch(FetchRequest.for_desired(desired, scope))
    return build_plan(desired, live, scope=scope, prune=prune, max_deactivations=max_deactivations)


def api_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn API and transport errors into clean CLI failures."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ApiError as e:
            if e.status == 401:
                fail(f"authentication failed: {e.reason}")
            fail(str(e))
        except TransportError as e:
            fail(f"cannot reach the SignPath API: {e}")

    return wrapper


def scope_options(fn: Callable[..., Any]) -> Callable[..., Any]:
    fn = click.option(
        "--max-deactivations",
        type=click.IntRange(0),
        default=10,
        show_default=True,
        help="Refuse --prune when it would deactivate more objects than this.",
    )(fn)
    fn = click.option("--prune", is_flag=True, help="Deactivate objects that are not declared.")(fn)
    fn = click.option(
        "--org-only", is_flag=True, help="Only organization-level objects (pipeline policies, prune)."
    )(fn)
    return click.option(
        "-p", "--project", "projects", multiple=True, help="Limit to this project (repeatable)."
    )(fn)


# ---------------------------------------------------------------------------- commands


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
@scope_options
@click.option(
    "-o",
    "--output",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--detailed-exitcode",
    is_flag=True,
    help="Exit with 0 when there is nothing to do, 2 when there are changes, 1 on errors.",
)
@click.pass_obj
@api_errors
def plan(
    settings: Settings,
    projects: tuple[str, ...],
    org_only: bool,
    prune: bool,
    max_deactivations: int,
    output: str,
    detailed_exitcode: bool,
) -> None:
    """Show what apply would change (read-only; a Global Reader token is enough)."""
    scope = scope_for(projects, org_only)
    desired = load_desired(settings, projects if scope == "projects" else None)
    client = make_client(settings, desired.api_url, desired.organization_id)
    try:
        result = compute_plan(
            settings, desired, client, scope, prune=prune, max_deactivations=max_deactivations
        )
    finally:
        client.close()
    if output == "json":
        data = result.to_dict()
        data["organization"]["id"] = desired.organization_id
        click.echo(json.dumps(data, indent=2))
    else:
        render_plan(result, _stdout())
    if result.diagnostics.has_errors:
        sys.exit(1)
    if detailed_exitcode and result.has_changes:
        sys.exit(2)


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def choose_sink(ci_token_file: Path | None, discard: bool, *, needed: bool) -> TokenSink | None:
    if ci_token_file is not None and discard:
        raise click.UsageError("--ci-token-file and --discard-new-ci-tokens are mutually exclusive")
    if not needed:
        return None
    try:
        if ci_token_file is not None:
            return FileSink(ci_token_file)
        if discard:
            return DiscardSink()
        if is_interactive():
            return TtySink()
    except TokenSinkError as e:
        fail(str(e))
    fail(
        "the plan issues new CI user API tokens but there is no terminal to show them: "
        "pass --ci-token-file PATH, or --discard-new-ci-tokens and rotate them later"
    )


def render_outcome(outcome: Outcome, console: Console) -> None:
    a = outcome.action
    where = f"project {a.project}: " if a.project else ""
    label = escape(f"{where}{a.op.value} {a.target}")
    if outcome.ok:
        console.print(f"  [green]done[/green]    {label}")
    elif outcome.skipped:
        console.print(f"  [yellow]skipped[/yellow] {label}: {escape(outcome.error or '')}")
    else:
        console.print(f"  [red]failed[/red]  {label}: {escape(outcome.error or '')}")


@cli.command()
@scope_options
@click.option(
    "-y", "--yes", is_flag=True, help="Do not ask for confirmation (required when not interactive)."
)
@click.option(
    "--ci-token-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Append new CI user API tokens to this file (created with mode 0600).",
)
@click.option(
    "--discard-new-ci-tokens",
    is_flag=True,
    help="Do not keep new CI user tokens; recover them later with 'ci-user rotate-token'.",
)
@click.pass_obj
@api_errors
def apply(
    settings: Settings,
    projects: tuple[str, ...],
    org_only: bool,
    prune: bool,
    max_deactivations: int,
    yes: bool,
    ci_token_file: Path | None,
    discard_new_ci_tokens: bool,
) -> None:
    """Apply the configuration to SignPath."""
    scope = scope_for(projects, org_only)
    desired = load_desired(settings, projects if scope == "projects" else None)
    client = make_client(settings, desired.api_url, desired.organization_id)
    out = _stdout()
    try:
        result = compute_plan(
            settings, desired, client, scope, prune=prune, max_deactivations=max_deactivations
        )
        render_plan(result, out)
        if result.diagnostics.has_errors:
            sys.exit(1)
        if not result.has_changes:
            return
        sink = choose_sink(
            ci_token_file,
            discard_new_ci_tokens,
            needed=any(a.issues_token for a in result.actions),
        )
        if not yes:
            if not is_interactive():
                fail("refusing to apply without confirmation: pass --yes")
            if not click.confirm("\nApply these changes?", default=False):
                fail("aborted", code=1)
        if sink is not None:
            out.print(f"New CI user API tokens go to: {sink.description}")
        out.print()
        executor = Executor(
            client,
            result,
            sink,
            on_outcome=lambda o: render_outcome(o, out),
            parallelism=settings.parallelism,
        )
        outcome = executor.run()
    finally:
        client.close()
    out.print()
    out.print(
        f"Applied {len(outcome.applied)} change(s), {len(outcome.failed)} failed, "
        f"{len(outcome.skipped)} skipped."
    )
    if isinstance(sink, DiscardSink) and sink.discarded:
        out.print(
            "[yellow]Discarded the API tokens of new CI users: "
            + ", ".join(sink.discarded)
            + ". Use 'signpath-manager ci-user rotate-token NAME' to obtain usable tokens.[/yellow]"
        )
    if not outcome.success:
        sys.exit(1)


@cli.group("ci-user")
def ci_user() -> None:
    """Operations on CI users."""


@ci_user.command("rotate-token")
@click.argument("name")
@click.option(
    "--ci-token-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Append the new token to this file (created with mode 0600) instead of showing it.",
)
@click.option("-y", "--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_obj
@api_errors
def rotate_token(settings: Settings, name: str, ci_token_file: Path | None, yes: bool) -> None:
    """Regenerate the API token of CI user NAME (the previous token stops working)."""
    desired = load_desired(settings, None)
    sink = choose_sink(ci_token_file, False, needed=True)
    assert sink is not None
    client = make_client(settings, desired.api_url, desired.organization_id)
    try:
        matches = [u for u in client.ci_users() if u.name.casefold() == name.casefold()]
        users = [client.ci_user(u.id) for u in matches]
        active = [u for u in users if u.status != "Inactive"]
        if not active:
            fail(f"no active CI user named '{name}'")
        user = active[0]
        if user.user_id == client.me().user_id:
            fail("refusing to rotate the token used by this session")
        owner = desired.ci_user_owner(user.name)
        if not yes:
            if not is_interactive():
                fail("refusing to rotate without confirmation: pass --yes")
            if not click.confirm(
                f"Regenerate the API token of '{user.name}'? The current token stops working."
            ):
                fail("aborted")
        token = client.regenerate_ci_user_token(user.user_id).api_token
        sink.deliver(owner, user.name, token)
    finally:
        client.close()
    _stderr().print(f"Rotated the API token of '{user.name}'; the new token went to: {sink.description}.")


@cli.command("import")
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Configuration root to write (created if missing).",
)
@click.option("-p", "--project", "projects", multiple=True, help="Only import this project (repeatable).")
@click.option(
    "--profile",
    help="Write projects relative to this profile (profiles/<name>.yaml must exist in the output).",
)
@click.option("--include-inactive", is_flag=True, help="Also import inactive objects (as active: false).")
@click.option("--force", is_flag=True, help="Overwrite existing project and XML files.")
@click.pass_obj
@api_errors
def import_(
    settings: Settings,
    output: Path,
    projects: tuple[str, ...],
    profile: str | None,
    include_inactive: bool,
    force: bool,
) -> None:
    """Write the configuration of a live organization (round trip: import, then plan shows no change)."""
    org_file = output / "organization.yaml"
    organization_id = settings.organization_id
    api_url = settings.api_url
    if org_file.is_file():
        from signpath_manager.config.yamlio import YamlLoadError, load_yaml_file

        try:
            data = load_yaml_file(org_file).data or {}
        except YamlLoadError as e:
            fail(f"{e.location}: {e.message}")
        organization_id = organization_id or data.get("organization_id")
        api_url = api_url or data.get("api_url")
    if not organization_id:
        fail("no organization ID: pass --organization-id (or $SIGNPATH_ORGANIZATION_ID)")
    output.mkdir(parents=True, exist_ok=True)
    client = make_client(settings, api_url or DEFAULT_API_URL, str(organization_id))
    scope: Scope = "projects" if projects else "full"
    request = FetchRequest(
        scope=scope,
        project_slugs={p.casefold() for p in projects},
        all_projects=not projects,
        all_ci_users=True,
        all_pipeline_policies=not projects,
        interactive_users=True,
    )
    try:
        live = Fetcher(client, parallelism=settings.parallelism).fetch(request)
    finally:
        client.close()
    missing = {p.casefold() for p in projects} - set(live.projects)
    if missing:
        fail(f"no such project(s) in SignPath: {', '.join(sorted(missing))}")
    try:
        report = Importer(
            live,
            output,
            organization_id=str(organization_id),
            profile=profile,
            include_inactive=include_inactive,
            force=force,
            full_scope=not projects,
        ).run()
    except FileNotFoundError as e:
        fail(str(e))
    err = _stderr()
    err.print(f"Wrote {len(report.written)} file(s) to {output} (see import-report.md).")
    for problem in report.problems:
        err.print(f"[yellow]attention:[/yellow] {escape(problem)}")
    if report.skipped_files:
        kept = len(report.skipped_files)
        err.print(f"[yellow]{kept} existing file(s) differ and were kept (use --force).[/yellow]")
    _, diags = load_config(output)
    print_diagnostics(diags, err)
    if diags.has_errors:
        fail("the imported configuration does not validate; see the errors above")
    err.print("Next: run 'signpath-manager -c <dir> plan' and expect no changes.")


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

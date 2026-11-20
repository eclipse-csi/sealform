"""Human-readable rendering of a plan."""

from __future__ import annotations

import difflib
import json
from typing import Any

from rich.console import Console
from rich.markup import escape

from sealform.diagnostics import Severity
from sealform.plan.actions import Action, Op, Plan

SYMBOLS = {
    Op.CREATE: ("+", "green"),
    Op.UPDATE: ("~", "yellow"),
    Op.ACTIVATE: ("^", "cyan"),
    Op.DEACTIVATE: ("-", "red"),
    Op.LINK: ("+", "green"),
    Op.SET_DEFAULT: ("~", "yellow"),
}


def _fmt(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) if "\n" not in value else "(multi-line text)"
    if isinstance(value, list | dict | tuple):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _visible_lines(text: str) -> list[str]:
    """Split on "\n" only, making carriage returns visible, so line-ending changes show up."""
    return text.replace("\r", "\\r").split("\n")


def _text_diff(old: str, new: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            _visible_lines(old), _visible_lines(new), fromfile="signpath", tofile="config", lineterm=""
        )
    )


def _render_action(console: Console, action: Action) -> None:
    symbol, color = SYMBOLS[action.op]
    label = f"{action.op.value} {action.target}"
    extra = f"  [dim]({escape(action.reason)})[/dim]" if action.reason else ""
    console.print(f"  [{color}]{symbol} {escape(label)}[/{color}]{extra}")
    for change in action.changes:
        if action.op is Op.CREATE or (change.old is None and change.diff is None):
            console.print(f"      {escape(change.field)}: {escape(_fmt(change.new))}")
        elif isinstance(change.old, list) and isinstance(change.new, list):
            removed = [x for x in change.old if x not in change.new]
            added = [x for x in change.new if x not in change.old]
            parts = [f"+{x}" for x in added] + [f"-{x}" for x in removed]
            console.print(
                f"      {escape(change.field)}: {escape(', '.join(map(str, parts)) or _fmt(change.new))}"
            )
        else:
            console.print(
                f"      {escape(change.field)}: {escape(_fmt(change.old))} -> {escape(_fmt(change.new))}"
            )
        diff = change.diff
        multiline = (
            isinstance(change.old, str)
            and isinstance(change.new, str)
            and ("\n" in change.old or "\n" in change.new)
        )
        if diff is None and multiline:
            diff = _text_diff(change.old, change.new)
        if diff:
            for line in diff.splitlines():
                style = "green" if line.startswith("+") else "red" if line.startswith("-") else "dim"
                console.print(f"        [{style}]{escape(line)}[/{style}]")


def render_plan(plan: Plan, console: Console) -> None:
    org = plan.organization
    me = plan.me
    console.print(
        f"[bold]Organization:[/bold] {escape(org.name)} ({escape(org.subscription_type)})  "
        f"[bold]as[/bold] {escape(me.name)} ({escape(me.role)})  [bold]scope:[/bold] {plan.scope}"
    )
    console.print()
    current: object = object()
    for action in plan.ordered():
        if action.project != current:
            current = action.project
            heading = f"project {action.project}" if action.project else "organization"
            console.print(f"[bold]{escape(heading)}[/bold]")
        _render_action(console, action)
    if plan.drift:
        console.print()
        console.print("[bold]Not managed by the configuration:[/bold]")
        for d in plan.drift:
            where = f"project {d.project}: " if d.project else ""
            hint = " (deactivated with --prune)" if d.prunable else ""
            console.print(
                f"  [magenta]? {escape(where)}{escape(d.kind.value)} {escape(d.name)}[/magenta]"
                f" [dim]{escape(d.message)}{hint}[/dim]"
            )
    if plan.diagnostics.items:
        console.print()
        for diag in plan.diagnostics.items:
            color = "red" if diag.severity is Severity.ERROR else "yellow"
            console.print(f"[{color}]{escape(str(diag))}[/{color}]")
    console.print()
    counts = plan.counts()
    if not counts:
        console.print("[green]No changes.[/green] SignPath matches the configuration.")
    else:
        summary = ", ".join(f"{n} to {op}" for op, n in sorted(counts.items()))
        console.print(f"[bold]Plan:[/bold] {summary}.")
    if plan.drift:
        console.print(f"{len(plan.drift)} unmanaged object(s).")
    if plan.diagnostics.has_errors:
        console.print(f"[red]{len(plan.diagnostics.errors)} error(s): the plan cannot be applied.[/red]")

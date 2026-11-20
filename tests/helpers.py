from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from sealform.api.client import SignPathClient
from sealform.api.http import HttpSession
from sealform.config.desired import DesiredState
from sealform.config.loader import load_config
from sealform.live.fetch import Fetcher, FetchRequest
from sealform.live.state import Scope
from sealform.plan.actions import Plan
from sealform.plan.diff import build_plan

from .fake_signpath import BASE, FakeSignPath


def client_for(fake: FakeSignPath, organization_id: str | None = None) -> SignPathClient:
    from .fake_signpath import ORG_ID

    return SignPathClient(
        HttpSession(BASE, organization_id or ORG_ID, SecretStr(fake.admin.token), transport=fake.transport())
    )


def load(root: Path, projects: list[str] | None = None) -> DesiredState:
    state, diags = load_config(root, projects=projects)
    assert state is not None, [str(d) for d in diags.items]
    assert not diags.has_errors, [str(d) for d in diags.errors]
    return state


def plan_for(
    fake: FakeSignPath,
    root: Path,
    *,
    scope: Scope = "full",
    projects: list[str] | None = None,
    prune: bool = False,
    max_deactivations: int = 10,
) -> Plan:
    desired = load(root, projects)
    client = client_for(fake)
    live = Fetcher(client, parallelism=2).fetch(FetchRequest.for_desired(desired, scope))
    return build_plan(desired, live, scope=scope, prune=prune, max_deactivations=max_deactivations)


def standard_fake() -> FakeSignPath:
    """A fake organization with what the default test profile references."""
    fake = FakeSignPath()
    fake.add_certificate("test-cert", "TestSigning")
    fake.add_certificate("release-cert", "ReleaseSigning")
    fake.add_tbs("GitHub.com")
    return fake


def summary(plan: Plan) -> list[str]:
    return [f"{a.op.value} {a.kind.value} {a.name}" for a in plan.ordered()]


def apply_plan(fake: FakeSignPath, plan: Plan, sink: object | None = None):  # type: ignore[no-untyped-def]
    from sealform.apply.executor import Executor
    from sealform.tokens.sinks import DiscardSink

    executor = Executor(client_for(fake), plan, sink or DiscardSink(), parallelism=2)  # type: ignore[arg-type]
    return executor.run()


def converge(fake: FakeSignPath, root: Path, **kwargs: object) -> Plan:
    """Plan, apply, and return the follow-up plan (which should be empty)."""
    plan = plan_for(fake, root, **kwargs)  # type: ignore[arg-type]
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.errors]
    result = apply_plan(fake, plan)
    assert result.success, [(o.action.target, o.error) for o in result.outcomes if not o.ok]
    return plan_for(fake, root, **kwargs)  # type: ignore[arg-type]

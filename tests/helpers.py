from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr

from signpath_manager.api.client import SignPathClient
from signpath_manager.api.http import HttpSession
from signpath_manager.config.desired import DesiredState
from signpath_manager.config.loader import load_config
from signpath_manager.live.fetch import Fetcher, FetchRequest
from signpath_manager.live.state import Scope
from signpath_manager.plan.actions import Plan
from signpath_manager.plan.diff import build_plan

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

"""Plan data structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any

from sealform.diagnostics import Diagnostics

if TYPE_CHECKING:
    from sealform.api.client import SignPathClient
    from sealform.api.models import Me, Organization
    from sealform.plan.resolve import Resolver


class Op(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"
    LINK = "link"
    SET_DEFAULT = "set-default"


class Kind(StrEnum):
    PIPELINE_POLICY = "pipeline policy"
    PROJECT = "project"
    ARTIFACT_CONFIGURATION = "artifact configuration"
    TRUSTED_BUILD_SYSTEM_LINK = "trusted build system link"
    CI_USER = "CI user"
    SIGNING_POLICY = "signing policy"


class Phase(IntEnum):
    """Global execution order: dependencies of a phase are created by earlier phases."""

    PIPELINE_POLICIES = 1
    PROJECTS = 2
    CI_USERS = 3
    SIGNING_POLICIES = 4
    DEACTIVATIONS = 5


@dataclass
class Change:
    field: str
    old: Any
    new: Any
    diff: str | None = None  # unified diff for long text (XML)


@dataclass
class Precondition:
    """Emulated If-Match: the resource must still look like it did when the plan was computed."""

    key: str
    fetch: Callable[[SignPathClient], Any]
    expected: Any


@dataclass
class Action:
    op: Op
    kind: Kind
    name: str
    project: str | None
    phase: Phase
    changes: list[Change] = field(default_factory=list)
    reason: str | None = None
    desired: Any = None
    live: Any = None
    precondition: Precondition | None = None
    issues_token: bool = False
    # principal IDs resolved at plan time, e.g. {"submitters": [...]}; may contain pending CI user IDs
    resolved: dict[str, list[str]] = field(default_factory=dict)

    @property
    def target(self) -> str:
        return f"{self.kind.value} {self.name}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "op": self.op.value,
            "kind": self.kind.value,
            "name": self.name,
            "project": self.project,
            "changes": [
                {"field": c.field, "old": _jsonable(c.old), "new": _jsonable(c.new)} for c in self.changes
            ],
        }
        if self.reason:
            out["reason"] = self.reason
        if self.issues_token:
            out["issues_token"] = True
        return out


@dataclass
class Drift:
    """Something in SignPath that the configuration does not describe."""

    kind: Kind
    name: str
    project: str | None
    message: str
    prunable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "project": self.project,
            "message": self.message,
            "prunable": self.prunable,
        }


@dataclass
class Plan:
    organization: Organization
    me: Me
    scope: str
    actions: list[Action]
    drift: list[Drift]
    diagnostics: Diagnostics
    resolver: Resolver
    prune: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.actions)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.actions:
            out[a.op.value] = out.get(a.op.value, 0) + 1
        return out

    def ordered(self) -> list[Action]:
        projects_order: dict[str | None, int] = {}
        for a in self.actions:
            projects_order.setdefault(a.project, len(projects_order))
        indexed = list(enumerate(self.actions))
        indexed.sort(key=lambda ia: (ia[1].phase, projects_order[ia[1].project], ia[0]))
        return [a for _, a in indexed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization": {
                "id": None,
                "name": self.organization.name,
                "subscription": self.organization.subscription_type,
            },
            "identity": {"name": self.me.name, "role": self.me.role, "type": self.me.user_type},
            "scope": self.scope,
            "prune": self.prune,
            "summary": self.counts(),
            "actions": [a.to_dict() for a in self.ordered()],
            "drift": [d.to_dict() for d in self.drift],
            "diagnostics": [
                {
                    "severity": d.severity.value,
                    "message": d.message,
                    "location": str(d.location) if d.location else None,
                }
                for d in self.diagnostics.items
            ],
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, set | frozenset):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    return value

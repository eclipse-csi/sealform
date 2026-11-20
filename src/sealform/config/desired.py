"""Fully resolved desired state: profiles merged, files loaded, defaults applied."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PrincipalKind = Literal["user", "group", "ci"]


@dataclass(frozen=True, order=True)
class Principal:
    kind: PrincipalKind
    value: str

    @classmethod
    def parse(cls, ref: str) -> Principal:
        if ref.startswith("group:"):
            return cls("group", ref[len("group:") :])
        if ref.startswith("ci:"):
            return cls("ci", ref[len("ci:") :])
        return cls("user", ref)

    def __str__(self) -> str:
        return self.value if self.kind == "user" else f"{self.kind}:{self.value}"

    @property
    def key(self) -> tuple[str, str]:
        """Case-insensitive identity."""
        return (self.kind, self.value.casefold())


@dataclass
class DesiredArtifactConfiguration:
    slug: str
    name: str
    description: str | None
    xml: str
    source: str  # human readable: "file:<path>" or "inline"
    active: bool = True


@dataclass
class DesiredApproval:
    approvers: list[Principal]
    required: int


@dataclass
class DesiredOriginVerification:
    branches: list[str] | None
    build_definitions: list[str] | None


@dataclass
class DesiredSigningPolicy:
    slug: str
    name: str
    description: str | None
    purpose: Literal["test", "release"] | None
    certificate: str
    submitters: list[Principal]  # includes the project's own CI users as Principal("ci", name)
    approval: DesiredApproval | None
    trusted_build_system_verification: bool
    origin_verification: DesiredOriginVerification | None
    pipeline_policies: dict[str, Literal["log", "enforce"]]
    disable_malware_scanning: bool
    active: bool = True


@dataclass
class DesiredCIUser:
    name: str
    project: str
    description: str | None
    notification_email: str | None
    signing_policies: list[str]
    active: bool = True


@dataclass
class DesiredProject:
    slug: str
    name: str
    description: str | None
    repositories: list[str]
    readers: list[Principal]
    configurators: list[Principal]
    trusted_build_systems: list[str]
    artifact_configurations: dict[str, DesiredArtifactConfiguration]  # key: casefolded slug
    default_artifact_configuration: str
    ci_users: dict[str, DesiredCIUser]  # key: casefolded name
    signing_policies: dict[str, DesiredSigningPolicy]  # key: casefolded slug
    active: bool
    source: Path
    profile: str | None = None


@dataclass
class DesiredPipelinePolicy:
    slug: str
    name: str
    description: str | None
    definition_text: str | None  # internal definition as sent to SignPath
    definition_data: Any  # parsed internal definition, used for comparison
    repository: str | None = None
    branch: str | None = None
    path: str | None = None
    active: bool = True

    @property
    def external(self) -> bool:
        return self.repository is not None


@dataclass
class Unmanaged:
    projects: set[str] = field(default_factory=set)  # casefolded
    ci_users: set[str] = field(default_factory=set)
    pipeline_policies: set[str] = field(default_factory=set)


@dataclass
class DesiredState:
    root: Path
    organization_id: str
    api_url: str
    default_profile: str | None
    projects: dict[str, DesiredProject]  # key: casefolded slug
    pipeline_policies: dict[str, DesiredPipelinePolicy]  # key: casefolded slug
    unmanaged: Unmanaged
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)  # raw profile data

    def ci_user_owner(self, name: str) -> str | None:
        key = name.casefold()
        for project in self.projects.values():
            if key in project.ci_users:
                return project.slug
        return None

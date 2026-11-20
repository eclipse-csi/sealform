"""Snapshot of the live SignPath organization, as needed to compute a plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from signpath_manager.api.models import (
    Certificate,
    CIUserDetail,
    InteractiveUserDetail,
    Me,
    Organization,
    Participant,
    PipelinePolicyDetail,
    PipelinePolicyListItem,
    ProjectDetail,
    ProjectListItem,
    SigningPolicyDetail,
    TrustedBuildSystem,
    UserGroupDetail,
    is_inactive,
)


@dataclass
class LiveArtifactConfiguration:
    slug: str
    name: str
    description: str | None
    active: bool
    is_default: bool
    xml: str | None  # None when not fetched


@dataclass
class LiveSigningPolicy:
    detail: SigningPolicyDetail
    raw: Any
    active: bool

    @property
    def slug(self) -> str:
        return self.detail.slug


@dataclass
class LiveProject:
    detail: ProjectDetail
    raw: Any
    artifact_configurations: dict[str, LiveArtifactConfiguration]  # key: casefolded slug
    signing_policies: dict[str, LiveSigningPolicy]  # key: casefolded slug

    @property
    def slug(self) -> str:
        return self.detail.slug

    @property
    def active(self) -> bool:
        return not is_inactive(self.detail.status)

    @property
    def default_artifact_configuration(self) -> LiveArtifactConfiguration | None:
        return next((a for a in self.artifact_configurations.values() if a.is_default), None)


@dataclass
class LiveCIUser:
    detail: CIUserDetail
    raw: Any

    @property
    def id(self) -> str:
        return self.detail.user_id

    @property
    def name(self) -> str:
        return self.detail.name

    @property
    def active(self) -> bool:
        return not is_inactive(self.detail.status)


@dataclass
class LivePipelinePolicy:
    detail: PipelinePolicyDetail
    raw: Any

    @property
    def active(self) -> bool:
        return not is_inactive(self.detail.status)


Scope = Literal["full", "projects", "org"]


@dataclass
class LiveState:
    me: Me
    organization: Organization
    scope: Scope
    project_list: dict[str, ProjectListItem]  # key: casefolded slug, every project of the organization
    projects: dict[str, LiveProject]  # key: casefolded slug, only fetched (in scope) projects
    ci_user_list: list[Participant]
    ci_users: dict[str, LiveCIUser]  # key: id, only fetched details
    interactive_users: dict[str, InteractiveUserDetail]  # key: id (details fetched when needed)
    interactive_user_list: list[Participant]
    group_list: list[Participant]
    groups: dict[str, UserGroupDetail]  # key: id (details fetched when needed)
    certificates: dict[str, Certificate]  # key: casefolded slug
    trusted_build_systems: dict[str, TrustedBuildSystem]  # key: casefolded slug
    pipeline_policy_list: dict[str, PipelinePolicyListItem]  # key: casefolded slug
    pipeline_policies: dict[str, LivePipelinePolicy] = field(default_factory=dict)  # fetched details

    def ci_users_named(self, name: str) -> list[LiveCIUser]:
        key = name.casefold()
        return [u for u in self.ci_users.values() if u.name.casefold() == key]

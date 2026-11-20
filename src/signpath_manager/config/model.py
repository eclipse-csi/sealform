"""Schema of the configuration files (organization.yaml, profiles/*.yaml, projects/*.yaml).

These models describe the files as written. Profiles and projects are merged before the
project model is validated a second time; required-after-merge checks live in the loader.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
EMAIL_RE = re.compile(r"^[^@\s:]+@[^@\s]+\.[^@\s]+$")
DEFAULT_API_URL = "https://app.signpath.io/Api"

Slug = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, pattern=SLUG_RE.pattern),
    Field(description="Identifier: ASCII letters, digits, '.', '-' and '_' (case-insensitive)."),
]
Name = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(max_length=5000)]
Email = Annotated[str, StringConstraints(max_length=320, pattern=EMAIL_RE.pattern)]


def _check_principal(value: str, allowed: tuple[str, ...]) -> str:
    if value.startswith("group:"):
        kind, rest = "group", value[len("group:") :]
    elif value.startswith("ci:"):
        kind, rest = "ci", value[len("ci:") :]
    elif EMAIL_RE.match(value):
        kind, rest = "user", value
    else:
        raise ValueError(
            "expected a user email (alice@example.org), 'group:<name>'"
            + (" or 'ci:<name>'" if "ci" in allowed else "")
        )
    if kind not in allowed:
        raise ValueError(f"'{kind}:' references are not allowed here")
    if not rest.strip():
        raise ValueError(f"empty {kind} name")
    return value


def _person_or_group(value: str) -> str:
    return _check_principal(value, ("user", "group"))


def _submitter(value: str) -> str:
    return _check_principal(value, ("user", "group", "ci"))


PersonOrGroupRef = Annotated[
    str,
    AfterValidator(_person_or_group),
    Field(description="A user email (alice@example.org) or a group reference (group:<name>)."),
]
SubmitterRef = Annotated[
    str,
    AfterValidator(_submitter),
    Field(
        description="A user email, 'group:<name>', or 'ci:<name>' for a CI user not owned by this project."
    ),
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- organization.yaml


class ExternalDefinitionSpec(_Strict):
    repository: Annotated[str, StringConstraints(min_length=1, max_length=5000)]
    branch: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None
    path: Annotated[str, StringConstraints(min_length=1, max_length=5000, pattern=r"^\.signpath/policies/.+")]


class PipelinePolicySpec(_Strict):
    name: Name
    description: Description | None = None
    definition: dict[str, Any] | None = Field(
        default=None, description="Inline pipeline policy definition (YAML mapping)."
    )
    definition_file: str | None = Field(
        default=None, description="Path (relative to the configuration root) of a YAML definition."
    )
    external: ExternalDefinitionSpec | None = Field(
        default=None, description="Definition stored in a source repository under .signpath/policies/."
    )
    active: bool = True

    @model_validator(mode="after")
    def _one_source(self) -> PipelinePolicySpec:
        sources = [s for s in (self.definition, self.definition_file, self.external) if s is not None]
        if len(sources) != 1:
            raise ValueError("exactly one of 'definition', 'definition_file' or 'external' is required")
        return self


class UnmanagedSpec(_Strict):
    projects: list[str] = Field(default_factory=list)
    ci_users: list[str] = Field(default_factory=list)
    pipeline_policies: list[str] = Field(default_factory=list)


class OrganizationSpec(_Strict):
    organization_id: UUID
    api_url: str = DEFAULT_API_URL
    default_profile: str | None = None
    pipeline_policies: dict[Slug, PipelinePolicySpec] = Field(default_factory=dict)
    unmanaged: UnmanagedSpec = Field(default_factory=UnmanagedSpec)


# --------------------------------------------------------------------------- profiles and projects


class ArtifactConfigurationSpec(_Strict):
    name: Name | None = Field(default=None, description="Display name; defaults to the slug.")
    description: Description | None = None
    shared: Annotated[str, StringConstraints(pattern=SLUG_RE.pattern)] | None = Field(
        default=None, description="Name of a file in artifact-configurations/ (without .xml)."
    )
    file: str | None = Field(default=None, description="XML file path relative to the configuration root.")
    xml: str | None = Field(default=None, description="Inline artifact configuration XML.")
    active: bool = True


class ApprovalSpec(_Strict):
    approvers: list[PersonOrGroupRef] = Field(min_length=1)
    required: Annotated[int, Field(ge=1)] = 1


class OriginVerificationSpec(_Strict):
    branches: list[Annotated[str, StringConstraints(min_length=1)]] | None = Field(
        default=None, description="Allowed branch names or patterns, e.g. main, release/*."
    )
    build_definitions: list[Annotated[str, StringConstraints(min_length=1)]] | None = Field(
        default=None, description="Allowed build definitions, e.g. .github/workflows/release.yml."
    )


class SigningPolicySpec(_Strict):
    name: Name | None = Field(default=None, description="Display name; defaults to the slug.")
    description: Description | None = None
    purpose: Literal["test", "release"] | None = None
    certificate: Slug | None = Field(default=None, description="Slug of the certificate (not managed).")
    submitters: list[SubmitterRef] = Field(
        default_factory=list,
        description="Additional submitters; the project's own CI users attach via ci_users.",
    )
    approval: ApprovalSpec | None = None
    trusted_build_system_verification: bool = False
    origin_verification: OriginVerificationSpec | None = None
    pipeline_policies: dict[Slug, Literal["log", "enforce"]] = Field(default_factory=dict)
    disable_malware_scanning: bool = False
    active: bool = True


class CIUserSpec(_Strict):
    description: Description | None = None
    notification_email: Email | None = None
    signing_policies: list[Slug] = Field(
        default_factory=list, description="Signing policies of this project this CI user submits to."
    )
    active: bool = True


class ProfileSpec(_Strict):
    readers: list[PersonOrGroupRef] | None = None
    configurators: list[PersonOrGroupRef] | None = None
    trusted_build_systems: list[Slug] | None = None
    artifact_configurations: dict[Slug, ArtifactConfigurationSpec | None] | None = None
    default_artifact_configuration: Slug | None = None
    signing_policies: dict[Slug, SigningPolicySpec | None] | None = None


class ProjectSpec(ProfileSpec):
    name: Name | None = None
    description: Description | None = None
    profile: str | None = Field(
        default=None,
        description="Profile to inherit from; defaults to organization.yaml default_profile, null for none.",
    )
    repositories: list[Annotated[str, StringConstraints(min_length=1)]] | None = None
    ci_users: dict[Name, CIUserSpec] | None = None
    active: bool = True

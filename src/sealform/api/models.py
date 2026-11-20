"""Lenient models of the SignPath API responses used by the tool (see ADR-0010).

Unknown fields are ignored and enum-like fields accept any value: real responses deviate
from the OpenAPI document (for example numeric ``status`` values on invalid policies).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.alias_generators import to_camel

Status = str | int


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


def is_inactive(status: Status | None) -> bool:
    return status == "Inactive"


class Participant(ApiModel):
    id: str
    name: str
    identity: str | None = None
    type: str
    is_directory_synchronized: bool = False


class NotificationEmailAddress(ApiModel):
    confirmed_email: str | None = None
    unconfirmed_email: str | None = None

    @property
    def any(self) -> str | None:
        return self.confirmed_email or self.unconfirmed_email


class Me(ApiModel):
    user_type: str
    user_id: str
    name: str
    role: str
    status: Status | None = None


class QuotaValues(ApiModel):
    currently_used: int | None = None
    quota: int | None = None


class Organization(ApiModel):
    name: str
    subscription_type: str
    quotas: dict[str, Any] = Field(default_factory=dict)

    @property
    def active_projects_quota(self) -> QuotaValues | None:
        raw = self.quotas.get("maxNumberOfActiveProjects")
        return QuotaValues.model_validate(raw) if isinstance(raw, dict) else None


class ProjectListSigningPolicy(ApiModel):
    name: str
    slug: str
    description: str | None = None
    status: Status | None = None


class ProjectListItem(ApiModel):
    name: str
    slug: str
    description: str | None = None
    status: Status | None = None
    signing_policies: list[ProjectListSigningPolicy] = Field(default_factory=list)


class ProjectArtifactConfiguration(ApiModel):
    name: str
    slug: str
    description: str | None = None
    status: Status | None = None
    is_default: bool = False


class ProjectSigningPolicy(ApiModel):
    name: str
    slug: str
    description: str | None = None
    status: Status | None = None


class TrustedBuildSystemLink(ApiModel):
    name: str
    slug: str
    type: str | None = None
    status: Status | None = None


class ProjectDetail(ApiModel):
    name: str
    slug: str
    description: str | None = None
    status: Status | None = None
    repository_urls: list[str] | None = None
    artifact_configurations: list[ProjectArtifactConfiguration] = Field(default_factory=list)
    signing_policies: list[ProjectSigningPolicy] = Field(default_factory=list)
    trusted_build_system_links: list[TrustedBuildSystemLink] = Field(default_factory=list)
    readers: list[Participant] = Field(default_factory=list)
    configurators: list[Participant] = Field(default_factory=list)


class OriginPolicyVerification(ApiModel):
    verify_origin_policy: bool = False
    allowed_branch_names: list[str] | None = None
    allowed_build_definitions: list[str] | None = None


class SigningPolicyPipelinePolicy(ApiModel):
    slug: str
    name: str | None = None
    level: str
    status: Status | None = None
    type: str | None = None


class PipelinePolicyVerification(ApiModel):
    pipeline_policies: list[SigningPolicyPipelinePolicy] = Field(default_factory=list)


class TrustedBuildSystemVerification(ApiModel):
    require_trusted_build_system: bool = False
    origin_policy_verification: OriginPolicyVerification | None = None
    pipeline_policy_verification: PipelinePolicyVerification | None = None


class SigningPolicyDetail(ApiModel):
    name: str
    slug: str
    description: str | None = None
    status: Status | None = None
    submitters: list[Participant] = Field(default_factory=list)
    approvers: list[Participant] = Field(default_factory=list)
    number_of_required_approvals: int = 0
    purpose: str | None = None
    certificate_link: str | None = None
    disable_malware_scanning: bool = False
    verifies_trusted_build_system: bool = False
    verifies_origin_policy: bool = False
    trusted_build_system_verification: TrustedBuildSystemVerification | None = None
    pipeline_policies: list[SigningPolicyPipelinePolicy] = Field(default_factory=list)

    @property
    def certificate_slug(self) -> str | None:
        if not self.certificate_link:
            return None
        from urllib.parse import unquote

        return unquote(self.certificate_link.rstrip("/").rsplit("/", 1)[-1])


class CIUserDetail(ApiModel):
    user_id: str
    name: str
    role: str | None = None
    description: str | None = None
    notification_email_address: NotificationEmailAddress = Field(default_factory=NotificationEmailAddress)
    status: Status | None = None
    is_directory_synchronized: bool = False


class CreatedCIUser(ApiModel):
    user_id: str
    api_token: SecretStr


class RegeneratedToken(ApiModel):
    api_token: SecretStr


class InteractiveUserDetail(ApiModel):
    user_id: str
    name: str
    identity: str | None = None
    account_email_address: str | None = None
    notification_email_address: NotificationEmailAddress = Field(default_factory=NotificationEmailAddress)
    role: str | None = None
    status: Status | None = None
    is_invitation_pending: bool | None = None

    @property
    def emails(self) -> list[str]:
        """Every email this user may be referenced by, in matching priority order."""
        out: list[str] = []
        if self.account_email_address:
            out.append(self.account_email_address)
        if self.identity and ":" in self.identity and "@" in self.identity:
            out.append(self.identity.split(":", 1)[1])
        n = self.notification_email_address
        out.extend(e for e in (n.confirmed_email, n.unconfirmed_email) if e)
        seen: set[str] = set()
        result = []
        for e in out:
            if e.casefold() not in seen:
                seen.add(e.casefold())
                result.append(e)
        return result


class UserGroupDetail(ApiModel):
    user_group_id: str
    name: str
    description: str | None = None
    status: Status | None = None
    members: list[Participant] = Field(default_factory=list)


class Certificate(ApiModel):
    slug: str
    name: str
    status: Status | None = None
    purpose: str | None = None
    type: str | None = None


class TrustedBuildSystem(ApiModel):
    slug: str
    name: str
    status: Status | None = None
    type: str | None = None


class PipelinePolicyListItem(ApiModel):
    slug: str
    name: str
    description: str | None = None
    status: Status | None = None
    type: str | None = None


class PipelinePolicyInternal(ApiModel):
    definition: str
    revision: str | None = None


class PipelinePolicyExternal(ApiModel):
    repository: str
    branch: str | None = None
    path: str


class PipelinePolicyDetail(ApiModel):
    slug: str
    name: str
    description: str | None = None
    status: Status | None = None
    type: str | None = None
    internal: PipelinePolicyInternal | None = None
    external: PipelinePolicyExternal | None = None

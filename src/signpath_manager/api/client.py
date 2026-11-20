"""Typed client for the SignPath API operations used by signpath-manager."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from pydantic import TypeAdapter

from signpath_manager.api.http import HttpSession, Response
from signpath_manager.api.models import (
    Certificate,
    CIUserDetail,
    CreatedCIUser,
    InteractiveUserDetail,
    Me,
    Organization,
    Participant,
    PipelinePolicyDetail,
    PipelinePolicyListItem,
    ProjectDetail,
    ProjectListItem,
    RegeneratedToken,
    SigningPolicyDetail,
    TrustedBuildSystem,
    UserGroupDetail,
)

_participants = TypeAdapter(list[Participant])
_projects = TypeAdapter(list[ProjectListItem])
_certificates = TypeAdapter(list[Certificate])
_tbs = TypeAdapter(list[TrustedBuildSystem])
_pipeline_policies = TypeAdapter(list[PipelinePolicyListItem])


def _seg(value: str) -> str:
    return quote(value, safe="")


class SignPathClient:
    def __init__(self, http: HttpSession) -> None:
        self.http = http

    @property
    def organization_id(self) -> str:
        return self.http.organization_id

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------------ organization

    def me(self) -> Me:
        return Me.model_validate(self.http.get("/Users/Me").json())

    def organization(self) -> Organization:
        return Organization.model_validate(self.http.get("/Organization").json())

    def certificates(self) -> list[Certificate]:
        return _certificates.validate_python(self.http.get("/Certificates").json())

    def trusted_build_systems(self) -> list[TrustedBuildSystem]:
        return _tbs.validate_python(self.http.get("/TrustedBuildSystems").json())

    # ------------------------------------------------------------------ users and groups

    def ci_users(self) -> list[Participant]:
        return _participants.validate_python(self.http.get("/CIUsers").json())

    def ci_user(self, user_id: str) -> CIUserDetail:
        return CIUserDetail.model_validate(self.http.get(f"/CIUsers/{_seg(user_id)}").json())

    def ci_user_raw(self, user_id: str) -> Any:
        return self.http.get(f"/CIUsers/{_seg(user_id)}").json()

    def create_ci_user(
        self, name: str, description: str | None, notification_email: str | None
    ) -> CreatedCIUser:
        body = {"name": name, "description": description, "notificationEmailAddress": notification_email}
        return CreatedCIUser.model_validate(self.http.post("/CIUsers", body).json())

    def edit_ci_user(self, user_id: str, name: str, description: str | None) -> None:
        self.http.post(f"/CIUsers/{_seg(user_id)}/Edit", {"name": name, "description": description})

    def change_ci_user_notification_email(self, user_id: str, email: str | None) -> None:
        self.http.post(
            f"/CIUsers/{_seg(user_id)}/ChangeNotificationEmailAddress", {"notificationEmailAddress": email}
        )

    def activate_ci_user(self, user_id: str) -> None:
        self.http.post(f"/CIUsers/{_seg(user_id)}/Activate")

    def deactivate_ci_user(self, user_id: str) -> None:
        self.http.post(f"/CIUsers/{_seg(user_id)}/Deactivate")

    def regenerate_ci_user_token(self, user_id: str) -> RegeneratedToken:
        return RegeneratedToken.model_validate(
            self.http.post(f"/CIUsers/{_seg(user_id)}/RegenerateApiToken").json()
        )

    def interactive_users(self) -> list[Participant]:
        return _participants.validate_python(self.http.get("/InteractiveUsers").json())

    def interactive_user(self, user_id: str) -> InteractiveUserDetail:
        return InteractiveUserDetail.model_validate(
            self.http.get(f"/InteractiveUsers/{_seg(user_id)}").json()
        )

    def user_groups(self) -> list[Participant]:
        return _participants.validate_python(self.http.get("/UserGroups").json())

    def user_group(self, group_id: str) -> UserGroupDetail:
        return UserGroupDetail.model_validate(self.http.get(f"/UserGroups/{_seg(group_id)}").json())

    # ------------------------------------------------------------------ projects

    def projects(self) -> list[ProjectListItem]:
        return _projects.validate_python(self.http.get("/Projects").json())

    def project_raw(self, slug: str) -> Any:
        return self.http.get(f"/Projects/{_seg(slug)}").json()

    def project(self, slug: str) -> ProjectDetail:
        return ProjectDetail.model_validate(self.project_raw(slug))

    def create_project(
        self,
        *,
        name: str,
        slug: str,
        description: str | None,
        repository_urls: list[str],
        reader_ids: list[str],
        configurator_ids: list[str],
        artifact_configuration_xml: str,
    ) -> None:
        self.http.post(
            "/Projects",
            {
                "name": name,
                "slug": slug,
                "description": description,
                "repositoryUrls": repository_urls,
                "readerIds": reader_ids,
                "configuratorIds": configurator_ids,
                "artifactConfigurationXml": artifact_configuration_xml,
            },
        )

    def edit_project(
        self,
        current_slug: str,
        *,
        name: str,
        slug: str,
        description: str | None,
        repository_urls: list[str],
        reader_ids: list[str],
        configurator_ids: list[str],
    ) -> None:
        self.http.post(
            f"/Projects/{_seg(current_slug)}/Edit",
            {
                "name": name,
                "slug": slug,
                "description": description,
                "repositoryUrls": repository_urls,
                "readerIds": reader_ids,
                "configuratorIds": configurator_ids,
            },
        )

    def activate_project(self, slug: str) -> None:
        self.http.post(f"/Projects/{_seg(slug)}/Activate")

    def deactivate_project(self, slug: str) -> None:
        self.http.post(f"/Projects/{_seg(slug)}/Deactivate")

    def set_default_artifact_configuration(self, project: str, ac_slug: str) -> None:
        self.http.post(
            f"/Projects/{_seg(project)}/SetDefaultArtifactConfiguration",
            {"artifactConfigurationSlug": ac_slug},
        )

    def link_trusted_build_system(self, project: str, tbs_slug: str) -> None:
        self.http.post(
            f"/Projects/{_seg(project)}/TrustedBuildSystemLinks", {"trustedBuildSystemSlug": tbs_slug}
        )

    # ------------------------------------------------------------------ artifact configurations

    def artifact_configuration_xml(self, project: str, ac_slug: str) -> str:
        path = f"/Projects/{_seg(project)}/ArtifactConfigurations/{_seg(ac_slug)}/Xml"
        return self.http.get(path, accept="application/xml").text

    def add_artifact_configuration(
        self, project: str, *, name: str, slug: str, description: str | None, xml: str
    ) -> None:
        self.http.post(
            f"/Projects/{_seg(project)}/ArtifactConfigurations",
            {"name": name, "slug": slug, "description": description, "artifactConfigurationXml": xml},
        )

    def edit_artifact_configuration(
        self, project: str, current_slug: str, *, name: str, slug: str, description: str | None
    ) -> None:
        self.http.post(
            f"/Projects/{_seg(project)}/ArtifactConfigurations/{_seg(current_slug)}/Edit",
            {"name": name, "slug": slug, "description": description},
        )

    def edit_artifact_configuration_xml(self, project: str, ac_slug: str, xml: str) -> None:
        self.http.post(
            f"/Projects/{_seg(project)}/ArtifactConfigurations/{_seg(ac_slug)}/EditXml",
            {"artifactConfigurationXml": xml},
        )

    def activate_artifact_configuration(self, project: str, ac_slug: str) -> None:
        self.http.post(f"/Projects/{_seg(project)}/ArtifactConfigurations/{_seg(ac_slug)}/Activate")

    def deactivate_artifact_configuration(self, project: str, ac_slug: str) -> None:
        self.http.post(f"/Projects/{_seg(project)}/ArtifactConfigurations/{_seg(ac_slug)}/Deactivate")

    # ------------------------------------------------------------------ signing policies

    def signing_policy_raw(self, project: str, sp_slug: str) -> Any:
        return self.http.get(
            f"/Projects/{_seg(project)}/SigningPolicies/{_seg(sp_slug)}",
            params={"includePipelinePolicyContent": "false"},
        ).json()

    def signing_policy(self, project: str, sp_slug: str) -> SigningPolicyDetail:
        return SigningPolicyDetail.model_validate(self.signing_policy_raw(project, sp_slug))

    def add_signing_policy(self, project: str, body: dict[str, Any]) -> None:
        self.http.post(f"/Projects/{_seg(project)}/SigningPolicies", body)

    def edit_signing_policy(self, project: str, current_slug: str, body: dict[str, Any]) -> None:
        self.http.post(f"/Projects/{_seg(project)}/SigningPolicies/{_seg(current_slug)}/Edit", body)

    def activate_signing_policy(self, project: str, sp_slug: str) -> None:
        self.http.post(f"/Projects/{_seg(project)}/SigningPolicies/{_seg(sp_slug)}/Activate")

    def deactivate_signing_policy(self, project: str, sp_slug: str) -> None:
        self.http.post(f"/Projects/{_seg(project)}/SigningPolicies/{_seg(sp_slug)}/Deactivate")

    # ------------------------------------------------------------------ pipeline policies

    def pipeline_policies(self) -> list[PipelinePolicyListItem]:
        return _pipeline_policies.validate_python(self.http.get("/PipelinePolicies").json())

    def pipeline_policy_raw(self, slug: str) -> Any:
        return self.http.get(f"/PipelinePolicies/{_seg(slug)}").json()

    def pipeline_policy(self, slug: str) -> PipelinePolicyDetail:
        return PipelinePolicyDetail.model_validate(self.pipeline_policy_raw(slug))

    def create_pipeline_policy(self, body: dict[str, Any]) -> None:
        self.http.post("/PipelinePolicies", body)

    def edit_pipeline_policy(self, current_slug: str, body: dict[str, Any]) -> None:
        self.http.post(f"/PipelinePolicies/{_seg(current_slug)}/Edit", body)

    def activate_pipeline_policy(self, slug: str) -> None:
        self.http.post(f"/PipelinePolicies/{_seg(slug)}/Activate")

    def deactivate_pipeline_policy(self, slug: str) -> None:
        self.http.post(f"/PipelinePolicies/{_seg(slug)}/Deactivate")


def response_json(response: Response) -> Any:
    return response.json()

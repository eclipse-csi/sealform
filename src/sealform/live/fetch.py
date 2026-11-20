"""Fetch the live state needed for a plan, with bounded concurrency."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TypeVar

from sealform.api.client import SignPathClient
from sealform.api.errors import NotFoundError
from sealform.api.models import (
    CIUserDetail,
    InteractiveUserDetail,
    PipelinePolicyDetail,
    ProjectDetail,
    SigningPolicyDetail,
    UserGroupDetail,
    is_inactive,
)
from sealform.config.desired import DesiredState
from sealform.live.state import (
    LiveArtifactConfiguration,
    LiveCIUser,
    LivePipelinePolicy,
    LiveProject,
    LiveSigningPolicy,
    LiveState,
    Scope,
)

log = logging.getLogger("sealform.fetch")
T = TypeVar("T")
R = TypeVar("R")


@dataclass
class FetchRequest:
    """What to fetch beyond the organization-wide lists."""

    scope: Scope
    project_slugs: set[str] = field(default_factory=set)  # casefolded; projects to fetch in detail
    all_projects: bool = False  # fetch every project in detail (import)
    ci_user_names: set[str] = field(default_factory=set)  # casefolded; CI users to fetch in detail
    all_ci_users: bool = False
    pipeline_policy_slugs: set[str] = field(default_factory=set)  # casefolded
    all_pipeline_policies: bool = False
    interactive_users: bool = False  # fetch every interactive user's detail (for email resolution)
    group_names: set[str] = field(default_factory=set)  # casefolded; groups to fetch in detail
    all_groups: bool = False

    @classmethod
    def for_desired(cls, desired: DesiredState, scope: Scope) -> FetchRequest:
        req = cls(scope=scope)
        if scope != "org":
            req.project_slugs = set(desired.projects)
        for project in desired.projects.values():
            req.ci_user_names.update(project.ci_users)
            principals = [*project.readers, *project.configurators]
            for sp in project.signing_policies.values():
                principals.extend(sp.submitters)
                if sp.approval:
                    principals.extend(sp.approval.approvers)
            for p in principals:
                if p.kind == "user":
                    req.interactive_users = True
                elif p.kind == "group":
                    req.group_names.add(p.value.casefold())
                else:
                    req.ci_user_names.add(p.value.casefold())
        if scope != "projects":
            req.all_ci_users = True
            req.all_pipeline_policies = True
        else:
            for project in desired.projects.values():
                for sp in project.signing_policies.values():
                    req.pipeline_policy_slugs.update(s.casefold() for s in sp.pipeline_policies)
        return req


class Fetcher:
    def __init__(self, client: SignPathClient, parallelism: int = 8) -> None:
        self.client = client
        self.parallelism = max(1, parallelism)

    def _map(self, fn: Callable[[T], R], items: Iterable[T]) -> list[R]:
        items = list(items)
        if len(items) <= 1 or self.parallelism == 1:
            return [fn(i) for i in items]
        with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
            return list(pool.map(fn, items))

    def _project(self, slug: str) -> LiveProject | None:
        try:
            raw = self.client.project_raw(slug)
        except NotFoundError:
            return None
        detail = ProjectDetail.model_validate(raw)

        def policy(slug_: str) -> LiveSigningPolicy:
            praw = self.client.signing_policy_raw(detail.slug, slug_)
            pdetail = SigningPolicyDetail.model_validate(praw)
            listed = next((p for p in detail.signing_policies if p.slug == slug_), None)
            status = listed.status if listed is not None else pdetail.status
            return LiveSigningPolicy(detail=pdetail, raw=praw, active=not is_inactive(status))

        def ac_xml(slug_: str) -> str:
            return self.client.artifact_configuration_xml(detail.slug, slug_)

        policies = [policy(p.slug) for p in detail.signing_policies]
        xmls = [ac_xml(a.slug) for a in detail.artifact_configurations]
        acs = {
            a.slug.casefold(): LiveArtifactConfiguration(
                slug=a.slug,
                name=a.name,
                description=a.description,
                active=not is_inactive(a.status),
                is_default=a.is_default,
                xml=xml,
            )
            for a, xml in zip(detail.artifact_configurations, xmls, strict=True)
        }
        return LiveProject(
            detail=detail,
            raw=raw,
            artifact_configurations=acs,
            signing_policies={p.slug.casefold(): p for p in policies},
        )

    def fetch(self, req: FetchRequest) -> LiveState:
        c = self.client
        with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
            f_me = pool.submit(c.me)
            f_org = pool.submit(c.organization)
            f_projects = pool.submit(c.projects)
            f_ci = pool.submit(c.ci_users)
            f_iu = pool.submit(c.interactive_users)
            f_groups = pool.submit(c.user_groups)
            f_certs = pool.submit(c.certificates)
            f_tbs = pool.submit(c.trusted_build_systems)
            f_pp = pool.submit(c.pipeline_policies)
            me, org = f_me.result(), f_org.result()
            project_list = {p.slug.casefold(): p for p in f_projects.result()}
            ci_list, iu_list, group_list = f_ci.result(), f_iu.result(), f_groups.result()
            certificates = {x.slug.casefold(): x for x in f_certs.result()}
            tbs = {x.slug.casefold(): x for x in f_tbs.result()}
            pp_list = {x.slug.casefold(): x for x in f_pp.result()}

        # projects: one worker per project, each fetching its policies and XML sequentially
        if req.all_projects:
            wanted = [p.slug for p in project_list.values()]
        else:
            wanted = [project_list[s].slug for s in sorted(req.project_slugs) if s in project_list]
        fetched = self._map(self._project, wanted)
        projects = {p.slug.casefold(): p for p in fetched if p is not None}

        ci_ids = [u.id for u in ci_list if req.all_ci_users or u.name.casefold() in req.ci_user_names]

        def ci_user(user_id: str) -> LiveCIUser:
            raw = c.ci_user_raw(user_id)
            return LiveCIUser(detail=CIUserDetail.model_validate(raw), raw=raw)

        ci_users = {u.id: u for u in self._map(ci_user, ci_ids)}

        interactive: dict[str, InteractiveUserDetail] = {}
        if req.interactive_users:
            interactive = {u.user_id: u for u in self._map(lambda p: c.interactive_user(p.id), iu_list)}

        group_ids = [g.id for g in group_list if req.all_groups or g.name.casefold() in req.group_names]
        groups: dict[str, UserGroupDetail] = {g.user_group_id: g for g in self._map(c.user_group, group_ids)}

        pp_wanted = [
            pp_list[s].slug for s in pp_list if req.all_pipeline_policies or s in req.pipeline_policy_slugs
        ]

        def pipeline_policy(slug: str) -> LivePipelinePolicy:
            raw = c.pipeline_policy_raw(slug)
            return LivePipelinePolicy(detail=PipelinePolicyDetail.model_validate(raw), raw=raw)

        pipeline_policies = {p.detail.slug.casefold(): p for p in self._map(pipeline_policy, pp_wanted)}

        return LiveState(
            me=me,
            organization=org,
            scope=req.scope,
            project_list=project_list,
            projects=projects,
            ci_user_list=ci_list,
            ci_users=ci_users,
            interactive_users=interactive,
            interactive_user_list=iu_list,
            group_list=group_list,
            groups=groups,
            certificates=certificates,
            trusted_build_systems=tbs,
            pipeline_policy_list=pp_list,
            pipeline_policies=pipeline_policies,
        )

"""Compare the desired state with the live state and produce a plan."""

from __future__ import annotations

import difflib
from typing import Any

from signpath_manager.api.client import SignPathClient
from signpath_manager.api.models import SigningPolicyDetail, is_inactive
from signpath_manager.config.desired import (
    DesiredArtifactConfiguration,
    DesiredCIUser,
    DesiredPipelinePolicy,
    DesiredProject,
    DesiredSigningPolicy,
    DesiredState,
    Principal,
)
from signpath_manager.config.xml import stored_form, xml_equal
from signpath_manager.config.yamlio import safe_load
from signpath_manager.diagnostics import Diagnostics, Location
from signpath_manager.live.state import (
    LiveArtifactConfiguration,
    LiveCIUser,
    LivePipelinePolicy,
    LiveProject,
    LiveSigningPolicy,
    LiveState,
    Scope,
)
from signpath_manager.plan.actions import Action, Change, Drift, Kind, Op, Phase, Plan, Precondition
from signpath_manager.plan.resolve import Resolver, is_pending

INITIAL_AC_SLUG = "initial"
PURPOSES = {"TestSigning": "test", "ReleaseSigning": "release"}


def _norm_text(value: str | None) -> str:
    return value or ""


def _xml_diff(old: str, new: str) -> str:
    lines = difflib.unified_diff(
        stored_form(old).splitlines(),
        stored_form(new).splitlines(),
        fromfile="signpath",
        tofile="config",
        lineterm="",
    )
    return "\n".join(lines)


def _project_precondition(lp: LiveProject) -> Precondition:
    slug = lp.slug
    return Precondition(f"project:{slug.casefold()}", lambda c: c.project_raw(slug), lp.raw)


def _policy_precondition(project: str, lsp: LiveSigningPolicy) -> Precondition:
    slug = lsp.slug
    return Precondition(
        f"policy:{project.casefold()}/{slug.casefold()}",
        lambda c: c.signing_policy_raw(project, slug),
        lsp.raw,
    )


def _xml_precondition(project: str, lac: LiveArtifactConfiguration) -> Precondition:
    slug = lac.slug
    return Precondition(
        f"xml:{project.casefold()}/{slug.casefold()}",
        lambda c: c.artifact_configuration_xml(project, slug),
        lac.xml,
    )


def _ci_precondition(lci: LiveCIUser) -> Precondition:
    uid = lci.id
    return Precondition(f"ci:{uid}", lambda c: c.ci_user_raw(uid), lci.raw)


def _pp_precondition(lpp: LivePipelinePolicy) -> Precondition:
    slug = lpp.detail.slug
    return Precondition(f"pp:{slug.casefold()}", lambda c: c.pipeline_policy_raw(slug), lpp.raw)


def comparable_policy(detail: SigningPolicyDetail) -> dict[str, Any]:
    """Live signing policy in the same shape as ``Planner._desired_policy``."""
    tbs = detail.trusted_build_system_verification
    require_tbs = bool(tbs and tbs.require_trusted_build_system) or (
        tbs is None and detail.verifies_trusted_build_system
    )
    opv = tbs.origin_policy_verification if tbs else None
    origin = None
    if opv is not None and opv.verify_origin_policy:
        origin = (sorted(opv.allowed_branch_names or []), sorted(opv.allowed_build_definitions or []))
    approvers = frozenset(p.id for p in detail.approvers)
    approval = (
        None
        if not approvers and detail.number_of_required_approvals == 0
        else (
            approvers,
            detail.number_of_required_approvals,
        )
    )
    return {
        "name": detail.name,
        "slug": detail.slug,
        "description": _norm_text(detail.description),
        "purpose": PURPOSES.get(detail.purpose or "", detail.purpose),
        "certificate": (detail.certificate_slug or "").casefold(),
        "submitters": frozenset(p.id for p in detail.submitters),
        "approval": approval,
        "trusted_build_system_verification": require_tbs,
        "origin_verification": origin,
        "pipeline_policies": {p.slug.casefold(): p.level.casefold() for p in detail.pipeline_policies},
        "disable_malware_scanning": detail.disable_malware_scanning,
    }


class Planner:
    def __init__(
        self,
        desired: DesiredState,
        live: LiveState,
        *,
        scope: Scope,
        prune: bool = False,
        max_deactivations: int = 10,
    ) -> None:
        self.desired = desired
        self.live = live
        self.scope = scope
        self.prune = prune
        self.max_deactivations = max_deactivations
        self.diags = Diagnostics()
        self.resolver = Resolver(live)
        self.actions: list[Action] = []
        self.drift: list[Drift] = []

    # ------------------------------------------------------------------ helpers

    def _add(self, action: Action) -> Action:
        self.actions.append(action)
        return action

    def _resolve_ids(
        self, principals: list[Principal], where: str, source: Location | None
    ) -> list[str] | None:
        ids: list[str] = []
        ok = True
        for p in principals:
            res = self.resolver.resolve(p)
            if res.error:
                self.diags.error(f"{where}: {res.error}", source)
                ok = False
            else:
                if res.warning:
                    self.diags.warning(f"{where}: {res.warning}", source)
                assert res.id is not None
                if res.id not in ids:
                    ids.append(res.id)
        return ids if ok else None

    def _set_change(
        self, field: str, old: frozenset[str] | set[str], new: frozenset[str] | set[str]
    ) -> Change:
        return Change(field, self.resolver.labels(set(old)), self.resolver.labels(set(new)))

    # ------------------------------------------------------------------ entry point

    def plan(self) -> Plan:
        if self.scope != "projects":
            self._plan_pipeline_policies()
        # CI users that will be created must be known before resolving submitters anywhere
        if self.scope != "org":
            for dp in self.desired.projects.values():
                for dci in dp.ci_users.values():
                    lci, _ = self.resolver.match_ci_user(dci.name)
                    if lci is None and dci.active:
                        self.resolver.plan_ci_creation(dci.name)
            for dp in self.desired.projects.values():
                self._plan_project(dp)
        if self.scope != "projects":
            self._plan_org_drift()
        self._apply_prune()
        self._check_quota()
        return Plan(
            organization=self.live.organization,
            me=self.live.me,
            scope=self.scope,
            actions=self.actions,
            drift=self.drift,
            diagnostics=self.diags,
            resolver=self.resolver,
            prune=self.prune,
        )

    # ------------------------------------------------------------------ pipeline policies

    def _plan_pipeline_policies(self) -> None:
        for key, dpp in self.desired.pipeline_policies.items():
            lpp = self.live.pipeline_policies.get(key)
            if lpp is None:
                if dpp.active:
                    self._add(
                        Action(
                            Op.CREATE,
                            Kind.PIPELINE_POLICY,
                            dpp.slug,
                            None,
                            Phase.PIPELINE_POLICIES,
                            changes=self._pp_create_changes(dpp),
                            desired=dpp,
                        )
                    )
                continue
            changes = self._pp_changes(dpp, lpp)
            pre = _pp_precondition(lpp)
            if dpp.active and not lpp.active:
                self._add(
                    Action(
                        Op.ACTIVATE,
                        Kind.PIPELINE_POLICY,
                        dpp.slug,
                        None,
                        Phase.PIPELINE_POLICIES,
                        live=lpp,
                        precondition=pre,
                    )
                )
            if changes:
                self._add(
                    Action(
                        Op.UPDATE,
                        Kind.PIPELINE_POLICY,
                        dpp.slug,
                        None,
                        Phase.PIPELINE_POLICIES,
                        changes=changes,
                        desired=dpp,
                        live=lpp,
                        precondition=pre,
                    )
                )
            if not dpp.active and lpp.active:
                self._add(
                    Action(
                        Op.DEACTIVATE,
                        Kind.PIPELINE_POLICY,
                        dpp.slug,
                        None,
                        Phase.DEACTIVATIONS,
                        live=lpp,
                        precondition=pre,
                        reason="declared inactive",
                    )
                )
        for key, lpp in self.live.pipeline_policies.items():
            if key in self.desired.pipeline_policies or key in self.desired.unmanaged.pipeline_policies:
                continue
            if lpp.active:
                self.drift.append(
                    Drift(
                        Kind.PIPELINE_POLICY, lpp.detail.slug, None, "not declared in organization.yaml", True
                    )
                )

    @staticmethod
    def _pp_create_changes(dpp: DesiredPipelinePolicy) -> list[Change]:
        changes = [Change("name", None, dpp.name)]
        if dpp.external:
            changes.append(Change("external", None, f"{dpp.repository}@{dpp.branch or 'default'}:{dpp.path}"))
        else:
            changes.append(Change("definition", None, dpp.definition_text))
        return changes

    def _pp_changes(self, dpp: DesiredPipelinePolicy, lpp: LivePipelinePolicy) -> list[Change]:
        d = lpp.detail
        changes: list[Change] = []
        if d.slug != dpp.slug:
            changes.append(Change("slug", d.slug, dpp.slug))
        if d.name != dpp.name:
            changes.append(Change("name", d.name, dpp.name))
        if _norm_text(d.description) != _norm_text(dpp.description):
            changes.append(Change("description", d.description, dpp.description))
        if dpp.external:
            ext = d.external
            current = (ext.repository, ext.branch, ext.path) if ext else None
            wanted = (dpp.repository, dpp.branch, dpp.path)
            if current != wanted:
                changes.append(Change("external", current, wanted))
        else:
            live_text = d.internal.definition if d.internal else None
            try:
                live_data = safe_load(live_text) if live_text is not None else None
            except Exception:  # unparsable live definition always differs
                live_data = object()
            if d.external is not None or live_data != dpp.definition_data:
                changes.append(
                    Change(
                        "definition",
                        live_text,
                        dpp.definition_text,
                        diff="\n".join(
                            difflib.unified_diff(
                                (live_text or "").splitlines(),
                                (dpp.definition_text or "").splitlines(),
                                fromfile="signpath",
                                tofile="config",
                                lineterm="",
                            )
                        ),
                    )
                )
        return changes

    # ------------------------------------------------------------------ projects

    def _plan_project(self, dp: DesiredProject) -> None:
        key = dp.slug.casefold()
        lp = self.live.projects.get(key)
        source = Location(dp.source)
        self._check_references(dp, source)

        readers = self._resolve_ids(dp.readers, f"project {dp.slug} readers", source)
        configurators = self._resolve_ids(dp.configurators, f"project {dp.slug} configurators", source)
        resolved = {"readers": readers or [], "configurators": configurators or []}

        if lp is None:
            if not dp.active:
                self.diags.warning(
                    f"project {dp.slug} is declared inactive and does not exist: nothing to do", source
                )
                return
            default = dp.artifact_configurations[dp.default_artifact_configuration.casefold()]
            self._add(
                Action(
                    Op.CREATE,
                    Kind.PROJECT,
                    dp.slug,
                    dp.slug,
                    Phase.PROJECTS,
                    changes=[
                        Change("name", None, dp.name),
                        *([Change("description", None, dp.description)] if dp.description else []),
                        *([Change("repositories", None, dp.repositories)] if dp.repositories else []),
                        *([Change("readers", None, self.resolver.labels(readers or []))] if readers else []),
                        *(
                            [Change("configurators", None, self.resolver.labels(configurators or []))]
                            if configurators
                            else []
                        ),
                        Change("default artifact configuration", None, f"{default.slug} ({default.source})"),
                    ],
                    desired=dp,
                    resolved=resolved,
                )
            )
            for dac in dp.artifact_configurations.values():
                if dac is default:
                    continue
                self._add(self._ac_create(dp, dac))
                if not dac.active:
                    self._add(
                        Action(
                            Op.DEACTIVATE,
                            Kind.ARTIFACT_CONFIGURATION,
                            dac.slug,
                            dp.slug,
                            Phase.DEACTIVATIONS,
                            desired=dac,
                            reason="declared inactive",
                        )
                    )
            for tbs in dp.trusted_build_systems:
                self._add(Action(Op.LINK, Kind.TRUSTED_BUILD_SYSTEM_LINK, tbs, dp.slug, Phase.PROJECTS))
            self._plan_ci_users(dp, None)
            for dsp in dp.signing_policies.values():
                self._plan_policy(dp, None, dsp, source)
            return

        pre = _project_precondition(lp)
        if dp.active and not lp.active:
            self._add(
                Action(Op.ACTIVATE, Kind.PROJECT, dp.slug, dp.slug, Phase.PROJECTS, live=lp, precondition=pre)
            )

        d = lp.detail
        changes: list[Change] = []
        if d.slug != dp.slug:
            changes.append(Change("slug", d.slug, dp.slug))
        if d.name != dp.name:
            changes.append(Change("name", d.name, dp.name))
        if _norm_text(d.description) != _norm_text(dp.description):
            changes.append(Change("description", d.description, dp.description))
        if set(d.repository_urls or []) != set(dp.repositories):
            changes.append(Change("repositories", d.repository_urls or [], dp.repositories))
        if readers is not None and {p.id for p in d.readers} != set(readers):
            changes.append(self._set_change("readers", {p.id for p in d.readers}, set(readers)))
        if configurators is not None and {p.id for p in d.configurators} != set(configurators):
            changes.append(
                self._set_change("configurators", {p.id for p in d.configurators}, set(configurators))
            )
        if changes:
            self._add(
                Action(
                    Op.UPDATE,
                    Kind.PROJECT,
                    dp.slug,
                    dp.slug,
                    Phase.PROJECTS,
                    changes=changes,
                    desired=dp,
                    live=lp,
                    precondition=pre,
                    resolved=resolved,
                )
            )

        self._plan_artifact_configurations(dp, lp, pre)

        live_links = {link.slug.casefold() for link in d.trusted_build_system_links}
        for tbs in dp.trusted_build_systems:
            if tbs.casefold() not in live_links:
                self._add(
                    Action(
                        Op.LINK,
                        Kind.TRUSTED_BUILD_SYSTEM_LINK,
                        tbs,
                        dp.slug,
                        Phase.PROJECTS,
                        precondition=pre,
                    )
                )
        desired_links = {t.casefold() for t in dp.trusted_build_systems}
        for link in d.trusted_build_system_links:
            if link.slug.casefold() not in desired_links:
                self.drift.append(
                    Drift(
                        Kind.TRUSTED_BUILD_SYSTEM_LINK,
                        link.slug,
                        dp.slug,
                        "linked in SignPath but not declared; the API cannot unlink it, "
                        "remove it in the web UI",
                        prunable=False,
                    )
                )

        self._plan_ci_users(dp, lp)
        for key_, dsp in dp.signing_policies.items():
            self._plan_policy(dp, lp, dsp, source, lp.signing_policies.get(key_))
        for key_, lsp in lp.signing_policies.items():
            if key_ not in dp.signing_policies and lsp.active:
                self.drift.append(
                    Drift(Kind.SIGNING_POLICY, lsp.slug, dp.slug, "not declared", prunable=True)
                )

        if not dp.active and lp.active:
            self._add(
                Action(
                    Op.DEACTIVATE,
                    Kind.PROJECT,
                    dp.slug,
                    dp.slug,
                    Phase.DEACTIVATIONS,
                    live=lp,
                    precondition=pre,
                    reason="declared inactive",
                )
            )

    def _check_references(self, dp: DesiredProject, source: Location) -> None:
        for tbs in dp.trusted_build_systems:
            live_tbs = self.live.trusted_build_systems.get(tbs.casefold())
            if live_tbs is None:
                self.diags.error(f"project {dp.slug}: no trusted build system '{tbs}' in SignPath", source)
            elif is_inactive(live_tbs.status):
                self.diags.warning(f"project {dp.slug}: trusted build system '{tbs}' is inactive", source)

    # ------------------------------------------------------------------ artifact configurations

    def _ac_create(self, dp: DesiredProject, dac: DesiredArtifactConfiguration) -> Action:
        changes = [Change("name", None, dac.name), Change("xml", None, dac.source)]
        if dac.description:
            changes.append(Change("description", None, dac.description))
        return Action(
            Op.CREATE,
            Kind.ARTIFACT_CONFIGURATION,
            dac.slug,
            dp.slug,
            Phase.PROJECTS,
            changes=changes,
            desired=dac,
        )

    def _plan_artifact_configurations(self, dp: DesiredProject, lp: LiveProject, pre: Precondition) -> None:
        for key, dac in dp.artifact_configurations.items():
            lac = lp.artifact_configurations.get(key)
            if lac is None:
                self._add(self._ac_create(dp, dac))
                if not dac.active:
                    self._add(
                        Action(
                            Op.DEACTIVATE,
                            Kind.ARTIFACT_CONFIGURATION,
                            dac.slug,
                            dp.slug,
                            Phase.DEACTIVATIONS,
                            desired=dac,
                            reason="declared inactive",
                        )
                    )
                continue
            if dac.active and not lac.active:
                self._add(
                    Action(
                        Op.ACTIVATE,
                        Kind.ARTIFACT_CONFIGURATION,
                        dac.slug,
                        dp.slug,
                        Phase.PROJECTS,
                        live=lac,
                        precondition=pre,
                    )
                )
            meta: list[Change] = []
            if lac.slug != dac.slug:
                meta.append(Change("slug", lac.slug, dac.slug))
            if lac.name != dac.name:
                meta.append(Change("name", lac.name, dac.name))
            if _norm_text(lac.description) != _norm_text(dac.description):
                meta.append(Change("description", lac.description, dac.description))
            if meta:
                self._add(
                    Action(
                        Op.UPDATE,
                        Kind.ARTIFACT_CONFIGURATION,
                        dac.slug,
                        dp.slug,
                        Phase.PROJECTS,
                        changes=meta,
                        desired=dac,
                        live=lac,
                        precondition=pre,
                    )
                )
            if lac.xml is not None and not xml_equal(lac.xml, dac.xml):
                self._add(
                    Action(
                        Op.UPDATE,
                        Kind.ARTIFACT_CONFIGURATION,
                        dac.slug,
                        dp.slug,
                        Phase.PROJECTS,
                        changes=[Change("xml", "(current)", dac.source, diff=_xml_diff(lac.xml, dac.xml))],
                        desired=dac,
                        live=lac,
                        precondition=_xml_precondition(dp.slug, lac),
                    )
                )
            if not dac.active and lac.active:
                self._add(
                    Action(
                        Op.DEACTIVATE,
                        Kind.ARTIFACT_CONFIGURATION,
                        dac.slug,
                        dp.slug,
                        Phase.DEACTIVATIONS,
                        live=lac,
                        precondition=pre,
                        reason="declared inactive",
                    )
                )

        live_default = lp.default_artifact_configuration
        if (
            live_default is None
            or live_default.slug.casefold() != dp.default_artifact_configuration.casefold()
        ):
            self._add(
                Action(
                    Op.SET_DEFAULT,
                    Kind.ARTIFACT_CONFIGURATION,
                    dp.default_artifact_configuration,
                    dp.slug,
                    Phase.PROJECTS,
                    changes=[
                        Change(
                            "default",
                            live_default.slug if live_default else None,
                            dp.default_artifact_configuration,
                        )
                    ],
                    precondition=pre,
                )
            )
        for key, lac in lp.artifact_configurations.items():
            if key not in dp.artifact_configurations and lac.active:
                self.drift.append(
                    Drift(Kind.ARTIFACT_CONFIGURATION, lac.slug, dp.slug, "not declared", prunable=True)
                )

    # ------------------------------------------------------------------ CI users

    def _plan_ci_users(self, dp: DesiredProject, lp: LiveProject | None) -> None:
        source = Location(dp.source)
        for dci in dp.ci_users.values():
            lci, error = self.resolver.match_ci_user(dci.name)
            if error:
                self.diags.error(f"project {dp.slug}: {error}", source)
                continue
            if lci is None:
                if not dci.active:
                    self.diags.warning(f"CI user {dci.name} is declared inactive and does not exist", source)
                    continue
                changes = [Change("name", None, dci.name)]
                if dci.description:
                    changes.append(Change("description", None, dci.description))
                if dci.notification_email:
                    changes.append(Change("notification email", None, dci.notification_email))
                self._add(
                    Action(
                        Op.CREATE,
                        Kind.CI_USER,
                        dci.name,
                        dp.slug,
                        Phase.CI_USERS,
                        changes=changes,
                        desired=dci,
                        issues_token=True,
                        reason="a new API token will be issued",
                    )
                )
                continue
            self._plan_ci_user_update(dp, dci, lci, source)

    def _plan_ci_user_update(
        self, dp: DesiredProject, dci: DesiredCIUser, lci: LiveCIUser, source: Location
    ) -> None:
        pre = _ci_precondition(lci)
        d = lci.detail
        if d.role and d.role != "RegularUser":
            self.diags.warning(
                f"CI user {dci.name} has global role {d.role} (only settable in the web UI)", source
            )
        if dci.active and not lci.active:
            self._add(
                Action(
                    Op.ACTIVATE,
                    Kind.CI_USER,
                    dci.name,
                    dp.slug,
                    Phase.CI_USERS,
                    live=lci,
                    precondition=pre,
                    reason="reactivates the existing user and its current token",
                )
            )
        changes: list[Change] = []
        if d.name != dci.name:
            changes.append(Change("name", d.name, dci.name))
        if _norm_text(d.description) != _norm_text(dci.description):
            changes.append(Change("description", d.description, dci.description))
        n = d.notification_email_address
        live_emails = {e.casefold() for e in (n.confirmed_email, n.unconfirmed_email) if e}
        wanted = dci.notification_email
        if (wanted is None and live_emails) or (wanted is not None and wanted.casefold() not in live_emails):
            changes.append(Change("notification email", n.any, wanted))
        if changes:
            self._add(
                Action(
                    Op.UPDATE,
                    Kind.CI_USER,
                    dci.name,
                    dp.slug,
                    Phase.CI_USERS,
                    changes=changes,
                    desired=dci,
                    live=lci,
                    precondition=pre,
                )
            )
        if not dci.active and lci.active:
            self._add(
                Action(
                    Op.DEACTIVATE,
                    Kind.CI_USER,
                    dci.name,
                    dp.slug,
                    Phase.DEACTIVATIONS,
                    live=lci,
                    precondition=pre,
                    reason="declared inactive",
                )
            )

    # ------------------------------------------------------------------ signing policies

    def _desired_policy(
        self, dp: DesiredProject, dsp: DesiredSigningPolicy, source: Location
    ) -> tuple[dict[str, Any], dict[str, list[str]]] | None:
        where = f"project {dp.slug} signing policy {dsp.slug}"
        submitters = self._resolve_ids(dsp.submitters, f"{where} submitters", source)
        approvers: list[str] | None = []
        if dsp.approval:
            approvers = self._resolve_ids(dsp.approval.approvers, f"{where} approvers", source)
        if submitters is None or approvers is None:
            return None
        ov = dsp.origin_verification
        comparable = {
            "name": dsp.name,
            "slug": dsp.slug,
            "description": _norm_text(dsp.description),
            "purpose": dsp.purpose,
            "certificate": dsp.certificate.casefold(),
            "submitters": frozenset(submitters),
            "approval": (frozenset(approvers), dsp.approval.required) if dsp.approval else None,
            "trusted_build_system_verification": dsp.trusted_build_system_verification,
            "origin_verification": (
                (sorted(ov.branches or []), sorted(ov.build_definitions or [])) if ov is not None else None
            ),
            "pipeline_policies": {k.casefold(): v for k, v in dsp.pipeline_policies.items()},
            "disable_malware_scanning": dsp.disable_malware_scanning,
        }
        return comparable, {"submitters": submitters, "approvers": approvers}

    def _check_policy_references(
        self, dp: DesiredProject, dsp: DesiredSigningPolicy, source: Location, writes: bool
    ) -> None:
        where = f"project {dp.slug} signing policy {dsp.slug}"
        cert = self.live.certificates.get(dsp.certificate.casefold())
        if cert is None:
            self.diags.error(f"{where}: no certificate '{dsp.certificate}' in SignPath", source)
        else:
            if cert.status != "Valid":
                message = f"{where}: certificate '{dsp.certificate}' is {cert.status}"
                if writes:
                    self.diags.error(message + " and SignPath rejects policies using it", source)
                else:
                    self.diags.warning(message, source)
            cert_purpose = PURPOSES.get(cert.purpose or "")
            if dsp.purpose and cert_purpose and cert_purpose != dsp.purpose:
                self.diags.error(
                    f"{where}: purpose '{dsp.purpose}' does not match the {cert_purpose} certificate "
                    f"'{dsp.certificate}' (SignPath marks such policies invalid)",
                    source,
                )
        for pp in dsp.pipeline_policies:
            key = pp.casefold()
            declared = self.desired.pipeline_policies.get(key)
            listed = self.live.pipeline_policy_list.get(key)
            if listed is None and (declared is None or self.scope == "projects"):
                self.diags.error(
                    f"{where}: no pipeline policy '{pp}' in SignPath"
                    + (" (apply organization-level changes first)" if declared else ""),
                    source,
                )
        if dsp.approval:
            users_only = all(p.kind == "user" for p in dsp.approval.approvers)
            if users_only and dsp.approval.required > len(dsp.approval.approvers):
                self.diags.error(f"{where}: more approvals required than approvers", source)

    def _plan_policy(
        self,
        dp: DesiredProject,
        lp: LiveProject | None,
        dsp: DesiredSigningPolicy,
        source: Location,
        lsp: LiveSigningPolicy | None = None,
    ) -> None:
        computed = self._desired_policy(dp, dsp, source)
        if computed is None:
            return
        wanted, resolved = computed
        if lsp is None:
            self._check_policy_references(dp, dsp, source, writes=True)
            changes = [
                Change(k, None, self._display(k, v)) for k, v in wanted.items() if v not in (None, "", {})
            ]
            self._add(
                Action(
                    Op.CREATE,
                    Kind.SIGNING_POLICY,
                    dsp.slug,
                    dp.slug,
                    Phase.SIGNING_POLICIES,
                    changes=changes,
                    desired=dsp,
                    resolved=resolved,
                )
            )
            if not dsp.active:
                self._add(
                    Action(
                        Op.DEACTIVATE,
                        Kind.SIGNING_POLICY,
                        dsp.slug,
                        dp.slug,
                        Phase.DEACTIVATIONS,
                        desired=dsp,
                        reason="declared inactive",
                    )
                )
            return
        current = comparable_policy(lsp.detail)
        changes = [
            Change(k, self._display(k, current[k]), self._display(k, v))
            for k, v in wanted.items()
            if current[k] != v
        ]
        self._check_policy_references(dp, dsp, source, writes=bool(changes))
        pre = _policy_precondition(dp.slug, lsp)
        if dsp.active and not lsp.active:
            self._add(
                Action(
                    Op.ACTIVATE,
                    Kind.SIGNING_POLICY,
                    dsp.slug,
                    dp.slug,
                    Phase.SIGNING_POLICIES,
                    live=lsp,
                    precondition=pre,
                )
            )
        if changes:
            self._add(
                Action(
                    Op.UPDATE,
                    Kind.SIGNING_POLICY,
                    dsp.slug,
                    dp.slug,
                    Phase.SIGNING_POLICIES,
                    changes=changes,
                    desired=dsp,
                    live=lsp,
                    precondition=pre,
                    resolved=resolved,
                )
            )
        if not dsp.active and lsp.active:
            self._add(
                Action(
                    Op.DEACTIVATE,
                    Kind.SIGNING_POLICY,
                    dsp.slug,
                    dp.slug,
                    Phase.DEACTIVATIONS,
                    live=lsp,
                    precondition=pre,
                    reason="declared inactive",
                )
            )

    def _display(self, key: str, value: Any) -> Any:
        if key == "submitters" and isinstance(value, frozenset):
            return self.resolver.labels(set(value))
        if key == "approval" and isinstance(value, tuple):
            approvers, required = value
            return {"approvers": self.resolver.labels(set(approvers)), "required": required}
        if key == "origin_verification" and isinstance(value, tuple):
            return {"branches": value[0], "build_definitions": value[1]}
        return value

    # ------------------------------------------------------------------ organization-level drift

    def _plan_org_drift(self) -> None:
        unmanaged = self.desired.unmanaged
        for key, item in self.live.project_list.items():
            if key in self.desired.projects or key in unmanaged.projects or is_inactive(item.status):
                continue
            self.drift.append(Drift(Kind.PROJECT, item.slug, None, "not declared", prunable=True))
        me_id = self.live.me.user_id
        for lci in self.live.ci_users.values():
            if not lci.active or lci.id == me_id or lci.name.casefold() in unmanaged.ci_users:
                continue
            if self.desired.ci_user_owner(lci.name) is None:
                self.drift.append(
                    Drift(
                        Kind.CI_USER,
                        lci.name,
                        None,
                        "not declared by any project nor listed in unmanaged.ci_users",
                        prunable=True,
                    )
                )

    # ------------------------------------------------------------------ prune and quotas

    def _apply_prune(self) -> None:
        prunable = [d for d in self.drift if d.prunable]
        if not self.prune or not prunable:
            return
        if len(prunable) > self.max_deactivations:
            self.diags.error(
                f"--prune would deactivate {len(prunable)} objects, more than --max-deactivations "
                f"({self.max_deactivations}); review the drift and raise the limit if intended"
            )
            return
        for d in prunable:
            live: Any = None
            pre: Precondition | None = None
            if d.kind is Kind.PROJECT:
                pass
            elif d.kind is Kind.CI_USER:
                live = next(u for u in self.live.ci_users.values() if u.name == d.name and u.active)
                pre = _ci_precondition(live)
            elif d.kind is Kind.PIPELINE_POLICY:
                live = self.live.pipeline_policies[d.name.casefold()]
                pre = _pp_precondition(live)
            elif d.kind is Kind.SIGNING_POLICY and d.project:
                live = self.live.projects[d.project.casefold()].signing_policies[d.name.casefold()]
                pre = _policy_precondition(d.project, live)
            elif d.kind is Kind.ARTIFACT_CONFIGURATION and d.project:
                live = self.live.projects[d.project.casefold()].artifact_configurations[d.name.casefold()]
            self._add(
                Action(
                    Op.DEACTIVATE,
                    d.kind,
                    d.name,
                    d.project,
                    Phase.DEACTIVATIONS,
                    live=live,
                    precondition=pre,
                    reason="pruned: not declared",
                )
            )
        self.drift = [d for d in self.drift if not d.prunable]

    def _check_quota(self) -> None:
        quota = self.live.organization.active_projects_quota
        if quota is None or quota.quota is None or quota.currently_used is None:
            return
        delta = 0
        for a in self.actions:
            if a.kind is Kind.PROJECT:
                if a.op in (Op.CREATE, Op.ACTIVATE):
                    delta += 1
                elif a.op is Op.DEACTIVATE:
                    delta -= 1
        if quota.currently_used + delta > quota.quota:
            self.diags.error(
                f"the plan needs {quota.currently_used + delta} active projects but the subscription allows "
                f"{quota.quota}"
            )


def build_plan(
    desired: DesiredState,
    live: LiveState,
    *,
    scope: Scope,
    prune: bool = False,
    max_deactivations: int = 10,
) -> Plan:
    return Planner(desired, live, scope=scope, prune=prune, max_deactivations=max_deactivations).plan()


def unresolved_pending(ids: list[str]) -> list[str]:
    return [i for i in ids if is_pending(i)]


__all__ = ["SignPathClient", "build_plan", "comparable_policy"]

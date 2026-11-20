"""Execute a plan against the SignPath API."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from signpath_manager.api.client import SignPathClient
from signpath_manager.api.errors import ApiError, StalePlanError, TransportError
from signpath_manager.config.desired import (
    DesiredArtifactConfiguration,
    DesiredCIUser,
    DesiredPipelinePolicy,
    DesiredProject,
    DesiredSigningPolicy,
)
from signpath_manager.config.xml import stored_form
from signpath_manager.live.state import LiveArtifactConfiguration, LiveCIUser, LivePipelinePolicy, LiveProject
from signpath_manager.plan.actions import Action, Kind, Op, Plan, Precondition
from signpath_manager.plan.diff import INITIAL_AC_SLUG
from signpath_manager.plan.resolve import is_pending
from signpath_manager.tokens.sinks import TokenSink

PURPOSE_API = {"test": "TestSigning", "release": "ReleaseSigning"}
LEVEL_API = {"log": "Log", "enforce": "Enforce"}


@dataclass
class Outcome:
    action: Action
    ok: bool
    error: str | None = None
    skipped: bool = False


@dataclass
class ApplyResult:
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def failed(self) -> list[Outcome]:
        return [o for o in self.outcomes if not o.ok and not o.skipped]

    @property
    def skipped(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.skipped]

    @property
    def applied(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def success(self) -> bool:
        return not self.failed and not self.skipped


def _same(pre: Precondition, current: Any) -> bool:
    if isinstance(pre.expected, str) and isinstance(current, str):
        return stored_form(pre.expected) == stored_form(current)
    return json.dumps(current, sort_keys=True) == json.dumps(pre.expected, sort_keys=True)


class Executor:
    def __init__(
        self,
        client: SignPathClient,
        plan: Plan,
        sink: TokenSink | None,
        *,
        on_outcome: Callable[[Outcome], None] | None = None,
        parallelism: int = 8,
    ) -> None:
        self.client = client
        self.plan = plan
        self.sink = sink
        self.on_outcome = on_outcome or (lambda _o: None)
        self.parallelism = parallelism
        self.ids: dict[str, str] = {}  # pending CI user id -> real id
        self._blocked: dict[str | None, str] = {}  # project (None = organization object) -> reason

    # ------------------------------------------------------------------ preconditions

    def verify_preconditions(self) -> list[tuple[Action, str]]:
        """Re-read every resource the plan will modify; report those that changed since planning.

        This emulates If-Match (SignPath ignores it, see ADR-0011). It runs once, before the
        first write, so the tool's own writes cannot trip it.
        """
        by_key: dict[str, tuple[Precondition, Action]] = {}
        for action in self.plan.actions:
            if action.precondition is not None:
                by_key.setdefault(action.precondition.key, (action.precondition, action))

        def check(item: tuple[Precondition, Action]) -> tuple[Action, str] | None:
            pre, action = item
            try:
                current = pre.fetch(self.client)
            except (ApiError, TransportError) as e:
                return action, f"cannot re-read {action.target}: {e}"
            if not _same(pre, current):
                return action, f"{action.target} changed in SignPath since the plan was computed"
            return None

        items = list(by_key.values())
        if len(items) > 1 and self.parallelism > 1:
            with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
                results = list(pool.map(check, items))
        else:
            results = [check(i) for i in items]
        stale = [r for r in results if r is not None]
        for action, reason in stale:
            self._block(action, reason)
        return stale

    def _block(self, action: Action, reason: str) -> None:
        key = action.project if action.project is not None else f"{action.kind.value}:{action.name}"
        self._blocked.setdefault(key, reason)

    def _blocked_reason(self, action: Action) -> str | None:
        if action.project is not None:
            return self._blocked.get(action.project)
        return self._blocked.get(f"{action.kind.value}:{action.name}")

    # ------------------------------------------------------------------ execution

    def run(self) -> ApplyResult:
        result = ApplyResult()
        self.verify_preconditions()
        for action in self.plan.ordered():
            reason = self._blocked_reason(action)
            if reason is not None:
                outcome = Outcome(action, ok=False, error=f"skipped: {reason}", skipped=True)
            else:
                try:
                    self._execute(action)
                    outcome = Outcome(action, ok=True)
                except (ApiError, TransportError, StalePlanError, ValueError) as e:
                    outcome = Outcome(action, ok=False, error=str(e))
                    self._block(action, f"an earlier action failed ({action.op.value} {action.target})")
            result.outcomes.append(outcome)
            self.on_outcome(outcome)
        return result

    def _id(self, id_: str) -> str:
        if is_pending(id_):
            real = self.ids.get(id_)
            if real is None:
                raise ValueError(f"{id_.removeprefix('new:ci:')} was not created in this run")
            return real
        return id_

    def _ids(self, action: Action, key: str) -> list[str]:
        return [self._id(i) for i in action.resolved.get(key, [])]

    def _execute(self, action: Action) -> None:
        c = self.client
        match action.kind, action.op:
            # -------------------------------------------------------- pipeline policies
            case Kind.PIPELINE_POLICY, Op.CREATE:
                c.create_pipeline_policy(self._pp_body(action.desired))
            case Kind.PIPELINE_POLICY, Op.UPDATE:
                live: LivePipelinePolicy = action.live
                c.edit_pipeline_policy(live.detail.slug, self._pp_body(action.desired))
            case Kind.PIPELINE_POLICY, Op.ACTIVATE:
                c.activate_pipeline_policy(action.name)
            case Kind.PIPELINE_POLICY, Op.DEACTIVATE:
                c.deactivate_pipeline_policy(action.name)
            # -------------------------------------------------------- projects
            case Kind.PROJECT, Op.CREATE:
                dp: DesiredProject = action.desired
                default = dp.artifact_configurations[dp.default_artifact_configuration.casefold()]
                c.create_project(
                    name=dp.name,
                    slug=dp.slug,
                    description=dp.description,
                    repository_urls=dp.repositories,
                    reader_ids=self._ids(action, "readers"),
                    configurator_ids=self._ids(action, "configurators"),
                    artifact_configuration_xml=default.xml,
                )
                if (default.slug, default.name, default.description) != (
                    INITIAL_AC_SLUG,
                    "Initial version",
                    None,
                ):
                    c.edit_artifact_configuration(
                        dp.slug,
                        INITIAL_AC_SLUG,
                        name=default.name,
                        slug=default.slug,
                        description=default.description,
                    )
            case Kind.PROJECT, Op.UPDATE:
                dp = action.desired
                lp: LiveProject = action.live
                c.edit_project(
                    lp.slug,
                    name=dp.name,
                    slug=dp.slug,
                    description=dp.description,
                    repository_urls=dp.repositories,
                    reader_ids=self._ids(action, "readers"),
                    configurator_ids=self._ids(action, "configurators"),
                )
            case Kind.PROJECT, Op.ACTIVATE:
                c.activate_project(action.name)
            case Kind.PROJECT, Op.DEACTIVATE:
                c.deactivate_project(action.name)
            # -------------------------------------------------------- artifact configurations
            case Kind.ARTIFACT_CONFIGURATION, Op.CREATE:
                dac: DesiredArtifactConfiguration = action.desired
                c.add_artifact_configuration(
                    self._project(action),
                    name=dac.name,
                    slug=dac.slug,
                    description=dac.description,
                    xml=dac.xml,
                )
            case Kind.ARTIFACT_CONFIGURATION, Op.UPDATE:
                dac = action.desired
                lac: LiveArtifactConfiguration = action.live
                if any(ch.field == "xml" for ch in action.changes):
                    c.edit_artifact_configuration_xml(self._project(action), lac.slug, dac.xml)
                else:
                    c.edit_artifact_configuration(
                        self._project(action),
                        lac.slug,
                        name=dac.name,
                        slug=dac.slug,
                        description=dac.description,
                    )
            case Kind.ARTIFACT_CONFIGURATION, Op.ACTIVATE:
                c.activate_artifact_configuration(self._project(action), action.name)
            case Kind.ARTIFACT_CONFIGURATION, Op.DEACTIVATE:
                c.deactivate_artifact_configuration(self._project(action), action.name)
            case Kind.ARTIFACT_CONFIGURATION, Op.SET_DEFAULT:
                c.set_default_artifact_configuration(self._project(action), action.name)
            # -------------------------------------------------------- trusted build system links
            case Kind.TRUSTED_BUILD_SYSTEM_LINK, Op.LINK:
                c.link_trusted_build_system(self._project(action), action.name)
            # -------------------------------------------------------- CI users
            case Kind.CI_USER, Op.CREATE:
                dci: DesiredCIUser = action.desired
                if self.sink is None:
                    raise ValueError("no destination for the new API token")
                created = c.create_ci_user(dci.name, dci.description, dci.notification_email)
                self.ids[f"new:ci:{dci.name}"] = created.user_id
                self.sink.deliver(action.project, dci.name, created.api_token)
            case Kind.CI_USER, Op.UPDATE:
                dci = action.desired
                lci: LiveCIUser = action.live
                fields = {ch.field for ch in action.changes}
                if fields & {"name", "description"}:
                    c.edit_ci_user(lci.id, dci.name, dci.description)
                if "notification email" in fields:
                    c.change_ci_user_notification_email(lci.id, dci.notification_email)
            case Kind.CI_USER, Op.ACTIVATE:
                c.activate_ci_user(self._ci_id(action))
            case Kind.CI_USER, Op.DEACTIVATE:
                c.deactivate_ci_user(self._ci_id(action))
            # -------------------------------------------------------- signing policies
            case Kind.SIGNING_POLICY, Op.CREATE:
                c.add_signing_policy(self._project(action), self._policy_body(action))
            case Kind.SIGNING_POLICY, Op.UPDATE:
                c.edit_signing_policy(self._project(action), action.live.slug, self._policy_body(action))
            case Kind.SIGNING_POLICY, Op.ACTIVATE:
                c.activate_signing_policy(self._project(action), action.name)
            case Kind.SIGNING_POLICY, Op.DEACTIVATE:
                c.deactivate_signing_policy(self._project(action), action.name)
            case _:
                raise ValueError(f"unsupported action {action.op.value} {action.kind.value}")

    @staticmethod
    def _project(action: Action) -> str:
        assert action.project is not None
        return action.project

    def _ci_id(self, action: Action) -> str:
        if isinstance(action.live, LiveCIUser):
            return action.live.id
        pending = self.ids.get(f"new:ci:{action.name}")
        if pending is None:
            raise ValueError(f"CI user {action.name} was not created in this run")
        return pending

    @staticmethod
    def _pp_body(dpp: DesiredPipelinePolicy) -> dict[str, Any]:
        body: dict[str, Any] = {"name": dpp.name, "slug": dpp.slug, "description": dpp.description}
        if dpp.external:
            body["external"] = {"repository": dpp.repository, "branch": dpp.branch, "path": dpp.path}
        else:
            body["internal"] = {"definition": dpp.definition_text}
        return body

    def _policy_body(self, action: Action) -> dict[str, Any]:
        dsp: DesiredSigningPolicy = action.desired
        body: dict[str, Any] = {
            "name": dsp.name,
            "slug": dsp.slug,
            "description": dsp.description,
            "certificateSlug": dsp.certificate,
            "purpose": PURPOSE_API[dsp.purpose] if dsp.purpose else None,
            "submitterIds": self._ids(action, "submitters"),
            "disableMalwareScanning": dsp.disable_malware_scanning,
            "approval": None,
            "trustedBuildSystemVerification": None,
        }
        if dsp.approval is not None:
            body["approval"] = {
                "approverIds": self._ids(action, "approvers"),
                "numberOfRequiredApprovals": dsp.approval.required,
            }
        if dsp.trusted_build_system_verification:
            tbsv: dict[str, Any] = {"requireTrustedBuildSystem": True}
            ov = dsp.origin_verification
            if ov is not None:
                tbsv["originPolicyVerification"] = {
                    "verifyOriginPolicy": True,
                    "allowedBranchNames": ov.branches,
                    "allowedBuildDefinitions": ov.build_definitions,
                }
            if dsp.pipeline_policies:
                tbsv["pipelinePolicyVerification"] = {
                    "pipelinePolicies": [
                        {"pipelinePolicySlug": slug, "level": LEVEL_API[level]}
                        for slug, level in dsp.pipeline_policies.items()
                    ]
                }
            body["trustedBuildSystemVerification"] = tbsv
        return body

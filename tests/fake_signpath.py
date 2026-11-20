"""In-memory fake of the SignPath API, served through httpx.MockTransport.

It mirrors the behavior verified against a real sandbox organization (see PLAN.md §11):
case-insensitive slugs that cannot be reused once deactivated, the ``initial`` artifact
configuration of new projects, the XML declaration SignPath prepends, unique names among
active CI users, numeric statuses of invalid policies, feature-gated pipeline policies, ...
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx
from lxml import etree

ORG_ID = "11111111-2222-3333-4444-555555555555"
BASE = "https://app.signpath.io/Api"
DECL = '<?xml version="1.0" encoding="utf-8"?>'


class Problem(Exception):
    def __init__(
        self, status: int, detail: str | None = None, errors: dict[str, list[str]] | None = None
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.errors = errors


def _bad(detail: str) -> Problem:
    return Problem(400, detail)


@dataclass
class AC:
    name: str
    slug: str
    description: str | None
    xml: str
    active: bool = True
    is_default: bool = False


@dataclass
class Policy:
    name: str
    slug: str
    description: str | None
    purpose: str | None
    certificate: str
    submitter_ids: list[str]
    approver_ids: list[str]
    required: int
    disable_malware_scanning: bool
    tbsv: dict[str, Any] | None
    active: bool = True


@dataclass
class Project:
    name: str
    slug: str
    description: str | None
    repository_urls: list[str]
    reader_ids: list[str]
    configurator_ids: list[str]
    acs: list[AC] = field(default_factory=list)
    policies: list[Policy] = field(default_factory=list)
    tbs_links: list[str] = field(default_factory=list)
    active: bool = True


@dataclass
class CIUser:
    id: str
    name: str
    description: str | None
    email: str | None
    token: str
    active: bool = True
    role: str = "RegularUser"


@dataclass
class PipelinePolicy:
    name: str
    slug: str
    description: str | None
    definition: str | None
    external: dict[str, Any] | None
    active: bool = True


class FakeSignPath:
    def __init__(self, *, pipeline_policies_enabled: bool = True, project_quota: int = 25) -> None:
        self.org_name = "Fake Org"
        self.subscription = "EnterpriseSandbox"
        self.project_quota = project_quota
        self.pipeline_policies_enabled = pipeline_policies_enabled
        self.projects: list[Project] = []
        self.ci_users: list[CIUser] = []
        self.interactive_users: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.certificates: list[dict[str, Any]] = []
        self.tbs: list[dict[str, Any]] = []
        self.pipeline_policies: list[PipelinePolicy] = []
        self.requests: list[tuple[str, str, Any]] = []
        self.admin = CIUser(
            str(uuid.uuid4()), "admin-bot", None, None, "admin-token", role="GlobalAdministrator"
        )
        self.ci_users.append(self.admin)
        self.fail_next: list[tuple[str, str, int]] = []  # (method, path regex, status) injected failures

    # ------------------------------------------------------------------ seeding helpers

    def add_user(self, name: str, email: str, *, active: bool = True) -> str:
        uid = str(uuid.uuid4())
        self.interactive_users.append(
            {
                "userId": uid,
                "name": name,
                "identity": f"idp:{email}",
                "accountEmailAddress": email,
                "notificationEmailAddress": {"confirmedEmail": email},
                "role": "RegularUser",
                "status": "Active" if active else "Inactive",
                "isInvitationPending": False,
                "isDirectorySynchronized": False,
            }
        )
        return uid

    def add_group(self, name: str, *, active: bool = True) -> str:
        gid = str(uuid.uuid4())
        self.groups.append(
            {
                "userGroupId": gid,
                "name": name,
                "status": "Active" if active else "Inactive",
                "members": [],
                "isDirectorySynchronized": False,
            }
        )
        return gid

    def add_certificate(self, slug: str, purpose: str = "TestSigning", status: str = "Valid") -> None:
        self.certificates.append(
            {"slug": slug, "name": slug, "status": status, "purpose": purpose, "type": "X509"}
        )

    def add_tbs(self, slug: str, status: str = "Active") -> None:
        self.tbs.append({"slug": slug, "name": slug, "status": status, "type": "Predefined"})

    def add_ci_user(self, name: str, *, active: bool = True, description: str | None = None) -> CIUser:
        user = CIUser(str(uuid.uuid4()), name, description, None, f"tok-{uuid.uuid4().hex}", active)
        self.ci_users.append(user)
        return user

    def writes(self) -> list[tuple[str, str, Any]]:
        return [r for r in self.requests if r[0] != "GET"]

    # ------------------------------------------------------------------ lookups

    def project(self, slug: str) -> Project:
        for p in self.projects:
            if p.slug.casefold() == slug.casefold():
                return p
        raise Problem(404, "No matching entity was found")

    @staticmethod
    def _find[T](items: list[T], slug: str, attr: str = "slug") -> T:
        for i in items:
            if str(getattr(i, attr) if not isinstance(i, dict) else i[attr]).casefold() == slug.casefold():
                return i
        raise Problem(404, "No matching entity was found")

    def ci_user(self, uid: str) -> CIUser:
        for u in self.ci_users:
            if u.id == uid:
                return u
        raise Problem(404, "No matching entity was found")

    def participant(self, pid: str) -> dict[str, Any]:
        for u in self.ci_users:
            if u.id == pid:
                return {
                    "id": u.id,
                    "name": u.name,
                    "type": "CIUser",
                    "isDirectorySynchronized": False,
                    "link": f"{BASE}/v1/{ORG_ID}/CIUsers/{u.id}",
                }
        for iu in self.interactive_users:
            if iu["userId"] == pid:
                return {
                    "id": pid,
                    "name": iu["name"],
                    "identity": iu["identity"],
                    "type": "InteractiveUser",
                    "isDirectorySynchronized": False,
                    "link": f"{BASE}/v1/{ORG_ID}/InteractiveUsers/{pid}",
                }
        for g in self.groups:
            if g["userGroupId"] == pid:
                return {
                    "id": pid,
                    "name": g["name"],
                    "type": "Group",
                    "isDirectorySynchronized": False,
                    "link": f"{BASE}/v1/{ORG_ID}/UserGroups/{pid}",
                }
        raise Problem(400, f"Unknown participant '{pid}'.")

    def _participant_kind(self, pid: str) -> str:
        return str(self.participant(pid)["type"])

    # ------------------------------------------------------------------ rendering

    def _policy_status(self, project: Project, p: Policy) -> str | int:
        if not p.active:
            return "Inactive"
        cert = self._find(self.certificates, p.certificate)
        if cert["status"] != "Valid":
            return 8
        if p.purpose and p.purpose != cert["purpose"]:
            return 64
        return "Valid"

    def _policy_json(self, project: Project, p: Policy, *, detail: bool) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": p.name,
            "slug": p.slug,
            "status": self._policy_status(project, p)
            if detail
            else (
                "Inactive"
                if not p.active
                else ("Valid" if self._policy_status(project, p) == "Valid" else "Invalid")
            ),
            "submitters": [self.participant(i) for i in p.submitter_ids],
            "approvers": [self.participant(i) for i in p.approver_ids],
            "numberOfRequiredApprovals": p.required,
            "certificateLink": f"{BASE}/v1/{ORG_ID}/Certificates/{p.certificate}",
            "disableMalwareScanning": p.disable_malware_scanning,
            "verifiesTrustedBuildSystem": bool(p.tbsv and p.tbsv.get("requireTrustedBuildSystem")),
            "verifiesOriginPolicy": bool(
                p.tbsv and (p.tbsv.get("originPolicyVerification") or {}).get("verifyOriginPolicy")
            ),
            "pipelinePolicies": [
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, e["pipelinePolicySlug"])),
                    "slug": e["pipelinePolicySlug"],
                    "name": e["pipelinePolicySlug"],
                    "type": "Internal",
                    "status": "Active",
                    "level": e["level"],
                    "link": "x",
                }
                for e in ((p.tbsv or {}).get("pipelinePolicyVerification") or {}).get("pipelinePolicies", [])
            ],
        }
        if p.description is not None:
            out["description"] = p.description
        if p.purpose is not None:
            out["purpose"] = p.purpose
        if detail and p.tbsv and p.tbsv.get("requireTrustedBuildSystem"):
            tbsv: dict[str, Any] = {"requireTrustedBuildSystem": True}
            opv = p.tbsv.get("originPolicyVerification")
            if opv and opv.get("verifyOriginPolicy"):
                o: dict[str, Any] = {"verifyOriginPolicy": True}
                if opv.get("allowedBranchNames") is not None:
                    o["allowedBranchNames"] = opv["allowedBranchNames"]
                if opv.get("allowedBuildDefinitions"):
                    o["allowedBuildDefinitions"] = opv["allowedBuildDefinitions"]
                tbsv["originPolicyVerification"] = o
            out["trustedBuildSystemVerification"] = tbsv
        return out

    def _project_json(self, p: Project) -> dict[str, Any]:
        has_valid_policy = any(self._policy_status(p, sp) == "Valid" for sp in p.policies)
        out: dict[str, Any] = {
            "repositoryUrls": p.repository_urls,
            "artifactConfigurations": [
                {
                    "name": a.name,
                    "slug": a.slug,
                    "status": "Valid" if a.active else "Inactive",
                    "isDefault": a.is_default,
                    "artifactConfigurationXmlLink": "x",
                    **({"description": a.description} if a.description is not None else {}),
                }
                for a in p.acs
            ],
            "signingPolicies": [self._policy_json(p, sp, detail=False) for sp in p.policies],
            "trustedBuildSystemLinks": [
                {
                    **{k: v for k, v in self._find(self.tbs, t).items() if k != "type"},
                    "type": "Predefined",
                    "link": "x",
                }
                for t in p.tbs_links
            ],
            "readers": [self.participant(i) for i in p.reader_ids],
            "configurators": [self.participant(i) for i in p.configurator_ids],
            "name": p.name,
            "slug": p.slug,
            "status": "Inactive" if not p.active else ("Valid" if has_valid_policy else "Invalid"),
        }
        if p.description is not None:
            out["description"] = p.description
        return out

    # ------------------------------------------------------------------ validation helpers

    @staticmethod
    def _stored_xml(xml: str) -> str:
        if xml[:1].isspace() and "<?xml" in xml:
            raise _bad(
                "Unexpected XML declaration. The XML declaration must be the first node in the document."
            )
        try:
            etree.fromstring(xml.encode())
        except etree.XMLSyntaxError as e:
            raise _bad(f"{e.msg}") from e
        return xml if xml.startswith("<?xml") else f"{DECL}\n{xml}"

    def _check_slug(self, slug: str) -> None:
        if not slug or not re.fullmatch(r"[A-Za-z0-9.\-_]+", slug) or len(slug) > 100:
            raise Problem(400, None, {"slug": ["The input was not valid."]})

    def _active_projects(self) -> int:
        return sum(1 for p in self.projects if p.active)

    def _validate_policy(self, project: Project, body: dict[str, Any]) -> Policy:
        if not body.get("submitterIds"):
            raise Problem(400, None, {"SubmitterIds": ["The field 'SubmitterIds' cannot be empty."]})
        for pid in body["submitterIds"]:
            self.participant(pid)
        try:
            cert = self._find(self.certificates, body["certificateSlug"])
        except Problem:
            raise _bad("The given certificate does not exist.") from None
        if cert["status"] != "Valid":
            raise _bad("The given certificate is not available for signing.")
        if body.get("disableMalwareScanning"):
            raise Problem(403, "The DisableMalwareScanning feature is disabled for this subscription.")
        tbsv = body.get("trustedBuildSystemVerification")
        if tbsv:
            opv = tbsv.get("originPolicyVerification") or {}
            if opv.get("verifyOriginPolicy") and not tbsv.get("requireTrustedBuildSystem"):
                raise Problem(
                    400,
                    None,
                    {
                        "TrustedBuildSystemVerification.RequireTrustedBuildSystem": [
                            "Origin policy verification can only be enabled "
                            "when a trusted build system is required."
                        ]
                    },
                )
            for e in (tbsv.get("pipelinePolicyVerification") or {}).get("pipelinePolicies", []):
                try:
                    self._find(self.pipeline_policies, e["pipelinePolicySlug"])
                except Problem:
                    raise Problem(
                        400,
                        None,
                        {
                            "PipelinePolicySlug": [
                                f"There is no pipeline policy with slug '{e['pipelinePolicySlug']}'."
                            ]
                        },
                    ) from None
        approval = body.get("approval")
        approvers = approval["approverIds"] if approval else []
        required = approval["numberOfRequiredApprovals"] if approval else 0
        active_approvers = sum(
            1
            for a in approvers
            if self._participant_kind(a) != "InteractiveUser"
            or next(u for u in self.interactive_users if u["userId"] == a)["status"] == "Active"
        )
        if required > active_approvers:
            raise _bad("There is an insufficient amount of active approvers.")
        self._check_slug(body["slug"])
        return Policy(
            name=body["name"],
            slug=body["slug"],
            description=body.get("description"),
            purpose=body.get("purpose"),
            certificate=cert["slug"],
            submitter_ids=list(body["submitterIds"]),
            approver_ids=list(approvers),
            required=required,
            disable_malware_scanning=bool(body.get("disableMalwareScanning")),
            tbsv=tbsv if tbsv and tbsv.get("requireTrustedBuildSystem") else None,
        )

    def _validate_principals(self, body: dict[str, Any]) -> tuple[list[str], list[str]]:
        readers = list(body.get("readerIds") or [])
        configurators = list(body.get("configuratorIds") or [])
        for pid in readers + configurators:
            self.participant(pid)
        if any(self._participant_kind(c) == "CIUser" for c in configurators):
            raise _bad("CI users cannot be configurators.")
        return readers, configurators

    # ------------------------------------------------------------------ routing

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        prefix = f"/Api/v1/{ORG_ID}"
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path.removeprefix(prefix), body))
        if request.headers.get("Authorization") != f"Bearer {self.admin.token}":
            return httpx.Response(
                401, json={"title": "Unauthorized", "status": 401, "detail": "Invalid token"}
            )
        for i, (m, rx, status) in enumerate(self.fail_next):
            if m == request.method and re.search(rx, path):
                del self.fail_next[i]
                return httpx.Response(
                    status, json={"title": "injected", "status": status}, headers={"Retry-After": "0"}
                )
        if not path.startswith(prefix):
            return httpx.Response(404, json={"title": "Not Found", "status": 404})
        route = path[len(prefix) :]
        try:
            result = self._route(request.method, route, body)
        except Problem as p:
            payload: dict[str, Any] = {
                "type": "about:blank",
                "title": "Bad Request" if p.status == 400 else "Error",
                "status": p.status,
                "traceId": "00-fake",
            }
            if p.detail:
                payload["detail"] = p.detail
            if p.errors:
                payload["errors"] = p.errors
            return httpx.Response(p.status, json=payload)
        if isinstance(result, httpx.Response):
            return result
        if result is None:
            return httpx.Response(204)
        return httpx.Response(200, json=result)

    def _route(self, method: str, route: str, body: Any) -> Any:
        parts = [p for p in route.split("/") if p]
        get = method == "GET"
        match parts:
            case ["Users", "Me"] if get:
                return {
                    "userType": "CIUser",
                    "userId": self.admin.id,
                    "name": self.admin.name,
                    "role": "GlobalAdministrator",
                    "status": "Active",
                    "isDirectorySynchronized": False,
                    "notificationEmailAddress": {},
                }
            case ["Organization"] if get:
                return {
                    "name": self.org_name,
                    "subscriptionType": self.subscription,
                    "quotas": {
                        "maxNumberOfActiveProjects": {
                            "currentlyUsed": self._active_projects(),
                            "quota": self.project_quota,
                        }
                    },
                }
            case ["Certificates"] if get:
                return self.certificates
            case ["TrustedBuildSystems"] if get:
                return self.tbs
            case ["CIUsers"] if get:
                return [self.participant(u.id) for u in self.ci_users]
            case ["CIUsers"] if method == "POST":
                if any(u.active and u.name.casefold() == body["name"].casefold() for u in self.ci_users):
                    raise _bad(
                        "The given user name is already in use by another active user and cannot be reused."
                    )
                user = CIUser(
                    str(uuid.uuid4()),
                    body["name"],
                    body.get("description"),
                    body.get("notificationEmailAddress"),
                    f"tok-{uuid.uuid4().hex}",
                )
                self.ci_users.append(user)
                return httpx.Response(
                    201,
                    json={"userId": user.id, "apiToken": user.token},
                    headers={"Location": f"{BASE}/v1/{ORG_ID}/CIUsers/{user.id}"},
                )
            case ["CIUsers", uid] if get:
                u = self.ci_user(uid)
                out: dict[str, Any] = {
                    "userId": u.id,
                    "name": u.name,
                    "role": u.role,
                    "notificationEmailAddress": {"unconfirmedEmail": u.email} if u.email else {},
                    "status": "Active" if u.active else "Inactive",
                    "isDirectorySynchronized": False,
                }
                if u.description is not None:
                    out["description"] = u.description
                return out
            case ["CIUsers", uid, "Edit"]:
                u = self.ci_user(uid)
                if u.active and any(
                    o is not u and o.active and o.name.casefold() == body["name"].casefold()
                    for o in self.ci_users
                ):
                    raise _bad(
                        "The given user name is already in use by another active user and cannot be reused."
                    )
                u.name, u.description = body["name"], body.get("description")
                return None
            case ["CIUsers", uid, "ChangeNotificationEmailAddress"]:
                self.ci_user(uid).email = body.get("notificationEmailAddress")
                return None
            case ["CIUsers", uid, "Activate"]:
                u = self.ci_user(uid)
                if any(
                    o is not u and o.active and o.name.casefold() == u.name.casefold() for o in self.ci_users
                ):
                    raise _bad("The given user name is already in use by another active user.")
                u.active = True
                return None
            case ["CIUsers", uid, "Deactivate"]:
                self.ci_user(uid).active = False
                return None
            case ["CIUsers", uid, "RegenerateApiToken"]:
                u = self.ci_user(uid)
                u.token = f"tok-{uuid.uuid4().hex}"
                return {"apiToken": u.token}
            case ["InteractiveUsers"] if get:
                return [self.participant(u["userId"]) for u in self.interactive_users]
            case ["InteractiveUsers", uid] if get:
                return self._find(self.interactive_users, uid, "userId")
            case ["UserGroups"] if get:
                return [self.participant(g["userGroupId"]) for g in self.groups]
            case ["UserGroups", gid] if get:
                return self._find(self.groups, gid, "userGroupId")
            case ["PipelinePolicies"] if get:
                return [
                    {
                        "slug": p.slug,
                        "name": p.name,
                        "description": p.description,
                        "status": "Active" if p.active else "Inactive",
                        "type": "External" if p.external else "Internal",
                    }
                    for p in self.pipeline_policies
                ]
            case ["PipelinePolicies", *_] if not get and not self.pipeline_policies_enabled:
                raise Problem(403, "The PipelinePolicies feature is disabled for this subscription.")
            case ["PipelinePolicies"] if method == "POST":
                self._check_slug(body["slug"])
                if any(p.slug.casefold() == body["slug"].casefold() for p in self.pipeline_policies):
                    raise _bad("The given slug is already in use and cannot be reused.")
                pp = PipelinePolicy(
                    body["name"],
                    body["slug"],
                    body.get("description"),
                    (body.get("internal") or {}).get("definition"),
                    body.get("external"),
                )
                self.pipeline_policies.append(pp)
                return httpx.Response(201, json={"slug": pp.slug, "name": pp.name, "status": "Active"})
            case ["PipelinePolicies", slug] if get:
                pp = self._find(self.pipeline_policies, slug)
                out = {
                    "slug": pp.slug,
                    "name": pp.name,
                    "status": "Active" if pp.active else "Inactive",
                    "type": "External" if pp.external else "Internal",
                }
                if pp.description is not None:
                    out["description"] = pp.description
                if pp.external:
                    out["external"] = pp.external
                else:
                    out["internal"] = {"definition": pp.definition, "revision": "A" * 44}
                return out
            case ["PipelinePolicies", slug, "Edit"]:
                pp = self._find(self.pipeline_policies, slug)
                pp.name, pp.slug, pp.description = body["name"], body["slug"], body.get("description")
                pp.definition = (body.get("internal") or {}).get("definition")
                pp.external = body.get("external")
                return None
            case ["PipelinePolicies", slug, op] if op in ("Activate", "Deactivate"):
                self._find(self.pipeline_policies, slug).active = op == "Activate"
                return None
            case ["Projects"] if get:
                return [
                    {
                        "signingPolicies": [
                            {
                                "name": sp.name,
                                "slug": sp.slug,
                                "status": self._project_json(p)["signingPolicies"][i]["status"],
                            }
                            for i, sp in enumerate(p.policies)
                        ],
                        "name": p.name,
                        "slug": p.slug,
                        "status": self._project_json(p)["status"],
                        **({"description": p.description} if p.description is not None else {}),
                    }
                    for p in self.projects
                ]
            case ["Projects"] if method == "POST":
                if not body.get("artifactConfigurationXml"):
                    raise Problem(400, None, {"artifactConfigurationXml": ["The input was not valid."]})
                self._check_slug(body["slug"])
                if any(p.slug.casefold() == body["slug"].casefold() for p in self.projects):
                    raise _bad("The given slug is already in use and cannot be reused.")
                if self._active_projects() >= self.project_quota:
                    raise _bad("The quota of active projects is exceeded.")
                readers, configurators = self._validate_principals(body)
                xml = self._stored_xml(body["artifactConfigurationXml"])
                project = Project(
                    body["name"],
                    body["slug"],
                    body.get("description"),
                    list(body.get("repositoryUrls") or []),
                    readers,
                    configurators,
                )
                project.acs.append(AC("Initial version", "initial", None, xml, is_default=True))
                self.projects.append(project)
                return httpx.Response(
                    201, headers={"Location": f"{BASE}/v1/{ORG_ID}/Projects/{project.slug}"}
                )
            case ["Projects", slug] if get:
                return self._project_json(self.project(slug))
            case ["Projects", slug, "Edit"]:
                p = self.project(slug)
                self._check_slug(body["slug"])
                readers, configurators = self._validate_principals(body)
                p.name, p.slug, p.description = body["name"], body["slug"], body.get("description")
                p.repository_urls = list(body.get("repositoryUrls") or [])
                p.reader_ids, p.configurator_ids = readers, configurators
                return None
            case ["Projects", slug, "Activate"]:
                p = self.project(slug)
                if not p.active and self._active_projects() >= self.project_quota:
                    raise _bad("The quota of active projects is exceeded.")
                p.active = True
                return None
            case ["Projects", slug, "Deactivate"]:
                self.project(slug).active = False
                return None
            case ["Projects", slug, "SetDefaultArtifactConfiguration"]:
                p = self.project(slug)
                target = self._find(p.acs, body["artifactConfigurationSlug"])
                for a in p.acs:
                    a.is_default = a is target
                return None
            case ["Projects", slug, "TrustedBuildSystemLinks"]:
                p = self.project(slug)
                try:
                    tbs = self._find(self.tbs, body["trustedBuildSystemSlug"])
                except Problem:
                    raise _bad("The given trusted build system does not exist.") from None
                if any(t.casefold() == tbs["slug"].casefold() for t in p.tbs_links):
                    raise _bad(
                        "This project already has a trusted build system link for the trusted build system."
                    )
                p.tbs_links.append(tbs["slug"])
                return None
            case ["Projects", slug, "ArtifactConfigurations"] if method == "POST":
                p = self.project(slug)
                self._check_slug(body["slug"])
                if any(a.slug.casefold() == body["slug"].casefold() for a in p.acs):
                    raise _bad("The given slug is already in use and cannot be reused.")
                p.acs.append(
                    AC(
                        body["name"],
                        body["slug"],
                        body.get("description"),
                        self._stored_xml(body["artifactConfigurationXml"]),
                    )
                )
                return httpx.Response(201)
            case ["Projects", slug, "ArtifactConfigurations", ac, "Xml"] if get:
                return httpx.Response(
                    200,
                    text=self._find(self.project(slug).acs, ac).xml,
                    headers={"Content-Type": "application/xml"},
                )
            case ["Projects", slug, "ArtifactConfigurations", ac, "Edit"]:
                p = self.project(slug)
                a = self._find(p.acs, ac)
                self._check_slug(body["slug"])
                if any(o is not a and o.slug.casefold() == body["slug"].casefold() for o in p.acs):
                    raise _bad("The given slug is already in use and cannot be reused.")
                a.name, a.slug, a.description = body["name"], body["slug"], body.get("description")
                return None
            case ["Projects", slug, "ArtifactConfigurations", ac, "EditXml"]:
                a = self._find(self.project(slug).acs, ac)
                a.xml = self._stored_xml(body["artifactConfigurationXml"])
                return None
            case ["Projects", slug, "ArtifactConfigurations", ac, op] if op in ("Activate", "Deactivate"):
                a = self._find(self.project(slug).acs, ac)
                if op == "Deactivate" and a.is_default:
                    raise _bad("You cannot deactivate a project's default artifact configuration.")
                a.active = op == "Activate"
                return None
            case ["Projects", slug, "SigningPolicies"] if method == "POST":
                p = self.project(slug)
                if any(sp.slug.casefold() == body["slug"].casefold() for sp in p.policies):
                    raise _bad("The given slug is already in use and cannot be reused.")
                p.policies.append(self._validate_policy(p, body))
                return httpx.Response(201)
            case ["Projects", slug, "SigningPolicies", sp] if get:
                p = self.project(slug)
                return self._policy_json(p, self._find(p.policies, sp), detail=True)
            case ["Projects", slug, "SigningPolicies", sp, "Edit"]:
                p = self.project(slug)
                current = self._find(p.policies, sp)
                if any(o is not current and o.slug.casefold() == body["slug"].casefold() for o in p.policies):
                    raise _bad("The given slug is already in use and cannot be reused.")
                new = self._validate_policy(p, body)
                new.active = current.active
                p.policies[p.policies.index(current)] = new
                return None
            case ["Projects", slug, "SigningPolicies", sp, op] if op in ("Activate", "Deactivate"):
                self._find(self.project(slug).policies, sp).active = op == "Activate"
                return None
        raise Problem(404, f"no fake route for {method} {route}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

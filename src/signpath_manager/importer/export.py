"""Import a live organization into a configuration root (``signpath-manager import``).

The goal is a faithful round trip: ``import`` followed by ``plan`` reports no change.
"""

from __future__ import annotations

import contextlib
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from signpath_manager.api.models import Participant
from signpath_manager.config.loader import LIBRARY_DIR, ORGANIZATION_FILE, PROFILES_DIR, PROJECTS_DIR
from signpath_manager.config.merge import merge_profile
from signpath_manager.config.model import SLUG_RE, ProjectSpec
from signpath_manager.config.xml import stored_form
from signpath_manager.config.yamlio import dump_yaml, load_yaml_file
from signpath_manager.live.state import LiveCIUser, LiveProject, LiveState
from signpath_manager.plan.diff import PURPOSES, comparable_policy

CUSTOM_DIR = f"{LIBRARY_DIR}/custom"
PIPELINE_DIR = "pipeline-policies"


@dataclass
class ImportReport:
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    written: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = ["# signpath-manager import report", ""]
        if self.problems:
            lines += ["## Needs attention", ""]
            lines += [f"* {p}" for p in self.problems]
            lines.append("")
        if self.notes:
            lines += ["## Notes", ""]
            lines += [f"* {n}" for n in self.notes]
            lines.append("")
        if self.skipped_files:
            lines += ["## Existing files left untouched (use --force to overwrite)", ""]
            lines += [f"* {f}" for f in self.skipped_files]
            lines.append("")
        return "\n".join(lines)


def _xml_key(xml: str) -> str:
    return stored_form(xml)


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _safe_name(*parts: str) -> str:
    name = ".".join(parts)
    return name if SLUG_RE.match(name) else _short_hash(name)


def _effective(project: dict[str, Any]) -> Any:
    data = ProjectSpec.model_validate(project).model_dump(exclude={"profile"})
    acs = data.get("artifact_configurations") or {}
    if data.get("default_artifact_configuration") is None and len(acs) == 1:
        data["default_artifact_configuration"] = next(iter(acs))  # implicit default, as the loader does
    return data


class Importer:
    def __init__(
        self,
        live: LiveState,
        output: Path,
        *,
        organization_id: str,
        profile: str | None = None,
        include_inactive: bool = False,
        force: bool = False,
        full_scope: bool = True,
    ) -> None:
        self.live = live
        self.output = output
        self.organization_id = organization_id
        self.profile_name = profile
        self.include_inactive = include_inactive
        self.force = force
        self.full_scope = full_scope
        self.report = ImportReport()
        self.files: dict[str, str] = {}  # relative path -> content
        self.library: dict[str, str] = {}  # stored-form xml -> library name
        self.profile_data: dict[str, Any] | None = None
        self.default_profile: str | None = None
        self.org_exists = (output / ORGANIZATION_FILE).is_file()

    # ------------------------------------------------------------------ setup

    def _load_existing(self) -> None:
        lib_dir = self.output / LIBRARY_DIR
        if lib_dir.is_dir():
            for path in sorted(lib_dir.glob("*.xml")):
                self.library.setdefault(_xml_key(path.read_text(encoding="utf-8-sig")), path.stem)
        if self.org_exists:
            data = load_yaml_file(self.output / ORGANIZATION_FILE).data or {}
            self.default_profile = data.get("default_profile")
        else:
            # the organization.yaml written by this import makes the profile the default
            self.default_profile = self.profile_name
        if self.profile_name is not None:
            path = self.output / PROFILES_DIR / f"{self.profile_name}.yaml"
            if not path.is_file():
                raise FileNotFoundError(f"profile {path} not found")
            self.profile_data = load_yaml_file(path).data or {}

    # ------------------------------------------------------------------ principals

    def _user_ref(self, p: Participant, where: str) -> str | None:
        if p.type == "Group":
            return f"group:{p.name}"
        if p.type == "InteractiveUser":
            detail = self.live.interactive_users.get(p.id)
            if detail is None or not detail.emails:
                self.report.problems.append(f"{where}: interactive user '{p.name}' has no email; left out")
                return None
            return detail.emails[0]
        return f"ci:{p.name}"

    def _refs(self, participants: list[Participant], where: str, *, allow_ci: bool) -> list[str]:
        refs = []
        for p in participants:
            if p.type == "CIUser" and not allow_ci:
                self.report.problems.append(f"{where}: CI user '{p.name}' cannot be expressed here; left out")
                continue
            ref = self._user_ref(p, where)
            if ref is not None:
                refs.append(ref)
        return refs

    # ------------------------------------------------------------------ main

    def run(self) -> ImportReport:
        self._load_existing()
        projects = [
            p
            for p in sorted(self.live.projects.values(), key=lambda p: p.slug.casefold())
            if self.include_inactive or p.active
        ]
        skipped = len(self.live.projects) - len(projects)
        if skipped:
            self.report.notes.append(f"{skipped} inactive project(s) not imported (use --include-inactive)")

        # XML usage across projects, for deduplication into the shared library
        usage: dict[str, int] = defaultdict(int)
        for lp in projects:
            for ac in lp.artifact_configurations.values():
                if ac.xml is not None and (ac.active or self.include_inactive):
                    usage[_xml_key(ac.xml)] += 1

        owners = self._ci_owners(projects)
        for lp in projects:
            self._project(lp, usage, owners)
        if self.org_exists:
            self.report.notes.append(f"{ORGANIZATION_FILE} exists and was left untouched")
        else:
            self._organization(owners)
        self._write()
        return self.report

    def _ci_owners(self, projects: list[LiveProject]) -> dict[str, str]:
        """CI user id -> owning project slug (first project, alphabetically, using it)."""
        uses: dict[str, list[str]] = defaultdict(list)
        for lp in projects:
            for sp in lp.signing_policies.values():
                if not (sp.active or self.include_inactive):
                    continue
                for s in sp.detail.submitters:
                    if s.type == "CIUser" and lp.slug not in uses[s.id]:
                        uses[s.id].append(lp.slug)
        owners = {}
        for uid, slugs in uses.items():
            owners[uid] = slugs[0]
            if len(slugs) > 1:
                name = next((c.name for c in self.live.ci_user_list if c.id == uid), uid)
                self.report.problems.append(
                    f"CI user '{name}' submits for several projects ({', '.join(slugs)}); it is declared in "
                    f"{slugs[0]} and referenced as 'ci:{name}' elsewhere. Consider one CI user per project."
                )
        return owners

    def _xml_source(self, lp: LiveProject, ac_slug: str, xml: str, usage: dict[str, int]) -> dict[str, str]:
        key = _xml_key(xml)
        name = self.library.get(key)
        if name is None and usage[key] > 1:
            name = f"imported-{_short_hash(key)}"
            self.library[key] = name
            self.files[f"{LIBRARY_DIR}/{name}.xml"] = xml if xml.endswith("\n") else xml + "\n"
        if name is not None:
            return {"shared": name}
        rel = f"{CUSTOM_DIR}/{_safe_name(lp.slug, ac_slug)}.xml"
        self.files[rel] = xml if xml.endswith("\n") else xml + "\n"
        return {"file": rel}

    def _project(self, lp: LiveProject, usage: dict[str, int], owners: dict[str, str]) -> None:
        d = lp.detail
        where = f"project {d.slug}"
        full: dict[str, Any] = {"name": d.name}
        if d.description:
            full["description"] = d.description
        if d.repository_urls:
            full["repositories"] = list(d.repository_urls)
        readers = self._refs(d.readers, f"{where} readers", allow_ci=False)
        if readers:
            full["readers"] = readers
        configurators = self._refs(d.configurators, f"{where} configurators", allow_ci=False)
        if configurators:
            full["configurators"] = configurators
        if d.trusted_build_system_links:
            full["trusted_build_systems"] = [link.slug for link in d.trusted_build_system_links]

        acs: dict[str, Any] = {}
        for ac in lp.artifact_configurations.values():
            if not (ac.active or self.include_inactive) or ac.xml is None:
                continue
            entry: dict[str, Any] = {}
            if ac.name != ac.slug:
                entry["name"] = ac.name
            if ac.description:
                entry["description"] = ac.description
            entry.update(self._xml_source(lp, ac.slug, ac.xml, usage))
            if not ac.active:
                entry["active"] = False
            acs[ac.slug] = entry
        full["artifact_configurations"] = acs
        default = lp.default_artifact_configuration
        if default is not None:
            full["default_artifact_configuration"] = default.slug

        ci_users: dict[str, Any] = {}
        policies: dict[str, Any] = {}
        for sp in lp.signing_policies.values():
            if not (sp.active or self.include_inactive):
                continue
            detail = sp.detail
            view = comparable_policy(detail)
            entry = {}
            if detail.name != detail.slug:
                entry["name"] = detail.name
            if detail.description:
                entry["description"] = detail.description
            purpose = PURPOSES.get(detail.purpose or "")
            if purpose:
                entry["purpose"] = purpose
            if detail.certificate_slug:
                entry["certificate"] = detail.certificate_slug
            foreign: list[str] = []
            for s in detail.submitters:
                if s.type == "CIUser" and owners.get(s.id) == lp.slug:
                    user = self.live.ci_users.get(s.id)
                    ci_entry = ci_users.setdefault(s.name, self._ci_user(user))
                    ci_entry.setdefault("signing_policies", []).append(detail.slug)
                else:
                    ref = self._user_ref(s, f"{where} signing policy {detail.slug} submitters")
                    if ref is not None:
                        foreign.append(ref)
            if foreign:
                entry["submitters"] = foreign
            if view["approval"] is not None:
                entry["approval"] = {
                    "approvers": self._refs(detail.approvers, f"{where} approvers", allow_ci=False),
                    "required": detail.number_of_required_approvals,
                }
            if view["trusted_build_system_verification"]:
                entry["trusted_build_system_verification"] = True
            tbs = detail.trusted_build_system_verification
            opv = tbs.origin_policy_verification if tbs else None
            if opv is not None and opv.verify_origin_policy:
                ov: dict[str, Any] = {}
                if opv.allowed_branch_names is not None:
                    ov["branches"] = list(opv.allowed_branch_names)
                if opv.allowed_build_definitions:
                    ov["build_definitions"] = list(opv.allowed_build_definitions)
                entry["origin_verification"] = ov
            if detail.pipeline_policies:
                entry["pipeline_policies"] = {p.slug: p.level.casefold() for p in detail.pipeline_policies}
            if detail.disable_malware_scanning:
                entry["disable_malware_scanning"] = True
            if not sp.active:
                entry["active"] = False
            policies[detail.slug] = entry
        if ci_users:
            full["ci_users"] = ci_users
        if policies:
            full["signing_policies"] = policies
        if not lp.active:
            full["active"] = False

        document = self._minimize(full, d.slug)
        rel = f"{PROJECTS_DIR}/{d.slug}.yaml"
        self.files[rel] = dump_yaml(document)

    def _ci_user(self, user: LiveCIUser | None) -> dict[str, Any]:
        entry: dict[str, Any] = {}
        if user is None:
            return entry
        if user.detail.description:
            entry["description"] = user.detail.description
        email = user.detail.notification_email_address.any
        if email:
            entry["notification_email"] = email
        if not user.active:
            entry["active"] = False
        return entry

    # ------------------------------------------------------------------ profiles

    def _minimize(self, full: dict[str, Any], slug: str) -> dict[str, Any]:
        """Drop what the profile already provides; fall back to the full document when unsure."""
        profile = self.profile_data
        if profile is None:
            if self.default_profile is not None:
                return {"profile": None, **full}
            return full
        minimized = _diff_against(full, profile)
        if "default_artifact_configuration" in minimized:
            implicit = {k: v for k, v in minimized.items() if k != "default_artifact_configuration"}
            with contextlib.suppress(Exception):  # unusual combination: keep the explicit default
                if _effective(merge_profile(profile, implicit)) == _effective(full):
                    minimized = implicit
        try:
            same = _effective(merge_profile(profile, minimized)) == _effective(full)
        except Exception:  # validation of an unusual combination: keep the full document
            same = False
        header: dict[str, Any] = {}
        if self.profile_name != self.default_profile:
            header["profile"] = self.profile_name
        if not same:
            self.report.notes.append(
                f"project {slug}: could not be expressed relative to the profile; "
                "written in full with 'profile: null'"
            )
            return {"profile": None, **full}
        return {**header, **minimized}

    # ------------------------------------------------------------------ organization

    def _organization(self, owners: dict[str, str]) -> None:
        doc: dict[str, Any] = {"organization_id": self.organization_id}
        if self.profile_name is not None:
            doc["default_profile"] = self.profile_name
            self.default_profile = self.profile_name
        pps: dict[str, Any] = {}
        for pp in sorted(self.live.pipeline_policies.values(), key=lambda p: p.detail.slug.casefold()):
            if not (pp.active or self.include_inactive):
                continue
            d = pp.detail
            entry: dict[str, Any] = {"name": d.name}
            if d.description:
                entry["description"] = d.description
            if d.external is not None:
                entry["external"] = {k: v for k, v in d.external.model_dump().items() if v is not None}
            elif d.internal is not None:
                rel = f"{PIPELINE_DIR}/{_safe_name(d.slug)}.yml"
                self.files[rel] = (
                    d.internal.definition
                    if d.internal.definition.endswith("\n")
                    else (d.internal.definition + "\n")
                )
                entry["definition_file"] = rel
            if not pp.active:
                entry["active"] = False
            pps[d.slug] = entry
        if pps:
            doc["pipeline_policies"] = pps
        if self.full_scope:
            unassigned = sorted(
                u.name
                for u in self.live.ci_users.values()
                if u.active and u.id not in owners and u.id != self.live.me.user_id
            )
            if unassigned:
                doc["unmanaged"] = {"ci_users": unassigned}
                self.report.notes.append(
                    "CI users that submit for no imported project were listed in unmanaged.ci_users: "
                    + ", ".join(unassigned)
                )
        self.files[ORGANIZATION_FILE] = dump_yaml(doc)

    # ------------------------------------------------------------------ output

    def _write(self) -> None:
        for rel, content in sorted(self.files.items()):
            path = self.output / rel
            if path.exists() and not self.force and not rel.startswith(f"{LIBRARY_DIR}/imported-"):
                if path.read_text(encoding="utf-8") != content:
                    self.report.skipped_files.append(rel)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self.report.written.append(rel)
        report_path = self.output / "import-report.md"
        report_path.write_text(self.report.to_markdown(), encoding="utf-8")


def _diff_against(full: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Keys of ``full`` that differ from ``profile``; profile entries absent from ``full`` become null."""
    out: dict[str, Any] = {}
    for key, value in full.items():
        if key not in profile:
            out[key] = value
            continue
        base = profile[key]
        if (
            key in ("artifact_configurations", "signing_policies")
            and isinstance(value, dict)
            and isinstance(base, dict)
        ):
            sub: dict[str, Any] = {}
            for name, entry in value.items():
                inherited = base.get(name)
                if not isinstance(inherited, dict):
                    sub[name] = entry
                    continue
                delta = _entry_delta(entry, inherited, key)
                if delta:
                    sub[name] = delta
            for name in base:
                if name not in value:
                    sub[name] = None
            if sub:
                out[key] = sub
        elif value != base:
            out[key] = value
    for key in profile:
        if key not in full and key not in out:
            out[key] = None
    return out


def _entry_delta(entry: dict[str, Any], inherited: dict[str, Any], kind: str) -> dict[str, Any]:
    from signpath_manager.config.model import ArtifactConfigurationSpec, SigningPolicySpec

    model = SigningPolicySpec if kind == "signing_policies" else ArtifactConfigurationSpec
    mine = model.model_validate(entry).model_dump()
    theirs = model.model_validate(inherited).model_dump()
    delta: dict[str, Any] = {}
    if kind == "artifact_configurations":
        sources = ("shared", "file", "xml")
        if any(mine[s] != theirs[s] for s in sources):
            delta.update({s: mine[s] for s in sources if mine[s] is not None})
        for f in ("name", "description", "active"):
            if mine[f] != theirs[f]:
                delta[f] = mine[f]
        return delta
    for f, v in mine.items():
        if v == theirs[f]:
            continue
        if isinstance(v, dict) and isinstance(theirs[f], dict):
            delta[f] = _dict_delta(v, theirs[f])
        elif isinstance(v, dict):
            delta[f] = {k: x for k, x in v.items() if x is not None}
        else:
            delta[f] = v
    return delta


def _dict_delta(mine: dict[str, Any], theirs: dict[str, Any]) -> dict[str, Any]:
    """Override for a deep-merged mapping: changed keys, plus null for inherited keys to drop."""
    out = {k: v for k, v in mine.items() if v is not None and theirs.get(k) != v}
    for k, v in theirs.items():
        if v is not None and mine.get(k) is None:
            out[k] = None
    return out

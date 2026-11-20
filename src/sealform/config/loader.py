"""Load and validate a configuration root into a DesiredState."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from sealform.config.desired import (
    DesiredApproval,
    DesiredArtifactConfiguration,
    DesiredCIUser,
    DesiredOriginVerification,
    DesiredPipelinePolicy,
    DesiredProject,
    DesiredSigningPolicy,
    DesiredState,
    Principal,
    Unmanaged,
)
from sealform.config.merge import TemplateLoader, drop_nulls, merge_profile
from sealform.config.model import (
    SLUG_RE,
    OrganizationSpec,
    ProfileSpec,
    ProjectSpec,
    SigningPolicySpec,
)
from sealform.config.xml import validate_xml
from sealform.config.yamlio import YamlDocument, YamlLoadError, dump_yaml, load_yaml_file, safe_load
from sealform.diagnostics import Diagnostics, Location

ORGANIZATION_FILE = "organization.yaml"
PROFILES_DIR = "profiles"
PROJECTS_DIR = "projects"
LIBRARY_DIR = "artifact-configurations"
_UNSET = object()


class ConfigError(Exception):
    """Raised when the configuration cannot be loaded at all."""

    def __init__(self, diagnostics: Diagnostics) -> None:
        super().__init__("invalid configuration")
        self.diagnostics = diagnostics


def _dotted(loc: Sequence[str | int]) -> str:
    return ".".join(str(p) for p in loc)


def _validate_model[M: BaseModel](
    model: type[M], doc: YamlDocument, diags: Diagnostics, data: Any = _UNSET
) -> M | None:
    payload = doc.data if data is _UNSET else data
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        diags.error("expected a mapping at the top level", Location(doc.path, 1, 1))
        return None
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        for err in e.errors():
            loc = tuple(p for p in err["loc"] if isinstance(p, str | int))
            # drop pydantic's union/function tags such as 'function-after[...]'
            loc = tuple(p for p in loc if not (isinstance(p, str) and "[" in p))
            msg = err["msg"]
            if err["type"] == "extra_forbidden":
                msg = "unknown key"
            diags.error(msg, doc.locate(loc), _dotted(loc) or None)
        return None


def resolve_file(root: Path, relative: str) -> tuple[Path | None, str | None]:
    """Resolve a file reference (`file:`, `extends:`, `definition_file:`); return (path, problem)."""
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None, f"path is outside the configuration root: {relative}"
    if not candidate.exists():
        return None, f"file does not exist: {relative}"
    if not candidate.is_file():
        return None, f"not a file: {relative}"
    return candidate, None


class XmlFiles:
    """Artifact configuration XML files, each read and validated once however many projects use it."""

    def __init__(self, root: Path, diags: Diagnostics) -> None:
        self.root = root
        self.diags = diags
        self._texts: dict[Path, str | None] = {}

    def get(self, relative: str) -> tuple[str | None, str | None]:
        """Return (text, problem with the reference); text is None when unusable."""
        path, problem = resolve_file(self.root, relative)
        if path is None:
            return None, problem
        if path not in self._texts:
            try:
                text: str | None = path.read_text(encoding="utf-8-sig")
            except OSError as e:
                self.diags.error(f"cannot read file: {e.strerror}", Location(path))
                text = None
            if text is not None:
                for problem in validate_xml(text):
                    self.diags.error(problem, Location(path))
            self._texts[path] = text
        return self._texts[path], None


class PolicyTemplates:
    """Signing policy templates (ADR-0015), each read and validated once."""

    def __init__(self, root: Path, diags: Diagnostics) -> None:
        self.root = root
        self.diags = diags
        self._cache: dict[Path, dict[str, Any] | None] = {}
        self._reported: set[str] = set()

    def get(self, relative: str) -> tuple[dict[str, Any] | None, str | None]:
        """Return (content, problem with the reference)."""
        path, problem = resolve_file(self.root, relative)
        if path is None:
            return None, problem
        if path not in self._cache:
            self._cache[path] = self._load(path)
        return self._cache[path], None

    def _load(self, path: Path) -> dict[str, Any] | None:
        try:
            doc = load_yaml_file(path)
        except YamlLoadError as e:
            self.diags.error(e.message, e.location)
            return None
        if isinstance(doc.data, dict) and "extends" in doc.data:
            self.diags.error(
                "a signing policy template cannot extend another template",
                doc.locate(("extends",)),
                "extends",
            )
            return None
        if isinstance(doc.data, dict) and "renamed_from" in doc.data:
            self.diags.error(
                "a signing policy template cannot rename a policy: put 'renamed_from' in the profile or "
                "project entry",
                doc.locate(("renamed_from",)),
                "renamed_from",
            )
            return None
        if _validate_model(SigningPolicySpec, doc, self.diags) is None:
            return None
        data: dict[str, Any] = drop_nulls(doc.data or {})
        return data

    def loader(
        self, project_doc: YamlDocument, profile_doc: YamlDocument | None, failed: set[str]
    ) -> TemplateLoader:
        """Template loader for one project; policies whose template is unusable go to ``failed``."""

        def load(relative: str, layer: str, policy: str) -> dict[str, Any] | None:
            content, problem = self.get(relative)
            if content is None:
                failed.add(policy.casefold())
            if problem is not None:
                doc = profile_doc if layer == "profile" and profile_doc is not None else project_doc
                path = ("signing_policies", policy, "extends")
                location = doc.locate(path)
                if str(location) not in self._reported:
                    self._reported.add(str(location))
                    self.diags.error(problem, location, _dotted(path))
            return content

        return load


def _load_pipeline_policies(
    root: Path, org: OrganizationSpec, doc: YamlDocument, diags: Diagnostics
) -> dict[str, DesiredPipelinePolicy]:
    result: dict[str, DesiredPipelinePolicy] = {}
    for slug, spec in org.pipeline_policies.items():
        loc = doc.locate(("pipeline_policies", slug))
        key = slug.casefold()
        if key in result:
            diags.error(f"duplicate pipeline policy slug (case-insensitive): {slug}", loc)
            continue
        text: str | None = None
        data: Any = None
        if spec.definition is not None:
            data = spec.definition
            text = dump_yaml(spec.definition)
        elif spec.definition_file is not None:
            path, problem = resolve_file(root, spec.definition_file)
            if path is None:
                diags.error(
                    problem or "unusable file",
                    doc.locate(("pipeline_policies", slug, "definition_file")),
                    f"pipeline_policies.{slug}.definition_file",
                )
                continue
            text = path.read_text(encoding="utf-8-sig")
            try:
                data = safe_load(text)
            except Exception as e:  # ruamel raises many error types
                diags.error(f"definition file is not valid YAML: {e}", Location(path))
                continue
        if text is not None and len(text) > 20480:
            diags.error("pipeline policy definition exceeds 20480 characters", loc)
        ext = spec.external
        result[key] = DesiredPipelinePolicy(
            slug=slug,
            name=spec.name,
            description=spec.description,
            definition_text=text,
            definition_data=data,
            repository=ext.repository if ext else None,
            branch=ext.branch if ext else None,
            path=ext.path if ext else None,
            active=spec.active,
        )
    return result


class _ProjectBuilder:
    def __init__(
        self,
        root: Path,
        doc: YamlDocument,
        profile_doc: YamlDocument | None,
        xml_files: XmlFiles,
        diags: Diagnostics,
        failed_templates: set[str] | None = None,
    ) -> None:
        self.root = root
        self.doc = doc
        self.profile_doc = profile_doc
        self.xml_files = xml_files
        self.diags = diags
        # signing policies (casefolded) whose template could not be used: already reported
        self.failed_templates = failed_templates or set()

    def loc(self, *path: str | int) -> Location:
        """Locate a path in the project file, falling back to the profile file."""
        if self.profile_doc is not None and not self._present(self.doc.tree, path):
            return self.profile_doc.locate(path)
        return self.doc.locate(path)

    @staticmethod
    def _present(tree: Any, path: Sequence[str | int]) -> bool:
        node = tree
        for p in path:
            try:
                node = node[p]
            except (KeyError, IndexError, TypeError):
                return False
        return True

    def error(self, message: str, *path: str | int) -> None:
        self.diags.error(message, self.loc(*path), _dotted(path) or None)

    def warning(self, message: str, *path: str | int) -> None:
        self.diags.warning(message, self.loc(*path), _dotted(path) or None)

    def build(self, slug: str, spec: ProjectSpec, profile_name: str | None) -> DesiredProject | None:
        errors_before = len(self.diags.errors)
        if not spec.name:
            self.error("'name' is required")

        # artifact configurations
        acs: dict[str, DesiredArtifactConfiguration] = {}
        for ac_slug, ac in (spec.artifact_configurations or {}).items():
            if ac is None:
                continue
            key = ac_slug.casefold()
            if key in acs:
                self.error(
                    f"duplicate artifact configuration slug (case-insensitive): {ac_slug}",
                    "artifact_configurations",
                    ac_slug,
                )
                continue
            sources = [s for s in ("file", "xml") if getattr(ac, s) is not None]
            if len(sources) != 1:
                self.error("exactly one of 'file' or 'xml' is required", "artifact_configurations", ac_slug)
                continue
            xml_text: str | None = None
            source = ""
            if ac.file is not None:
                source = f"file:{ac.file}"
                xml_text, problem = self.xml_files.get(ac.file)
                if problem is not None:
                    self.error(problem, "artifact_configurations", ac_slug, "file")
                    continue
                if xml_text is None:  # unreadable or invalid: reported at the file itself
                    continue
            else:
                source = "inline"
                xml_text = ac.xml or ""
                for problem in validate_xml(xml_text):
                    self.error(problem, "artifact_configurations", ac_slug, "xml")
            acs[key] = DesiredArtifactConfiguration(
                slug=ac_slug,
                name=ac.name or ac_slug,
                description=ac.description,
                xml=xml_text,
                source=source,
                active=ac.active,
                renamed_from=ac.renamed_from,
            )
        self._check_renames("artifact_configurations", "artifact configuration", acs)

        active_acs = [a for a in acs.values() if a.active]
        default_ac = spec.default_artifact_configuration
        if not active_acs:
            self.error("at least one active artifact configuration is required", "artifact_configurations")
        elif default_ac is None:
            if len(acs) == 1:
                default_ac = next(iter(acs.values())).slug
            else:
                self.error(
                    "'default_artifact_configuration' is required when there are several "
                    "artifact configurations"
                )
        if default_ac is not None:
            target = acs.get(default_ac.casefold())
            if target is None:
                self.error(f"unknown artifact configuration '{default_ac}'", "default_artifact_configuration")
            elif not target.active:
                self.error(
                    "the default artifact configuration must be active", "default_artifact_configuration"
                )
            else:
                default_ac = target.slug

        # CI users
        ci_users: dict[str, DesiredCIUser] = {}
        for ci_name, ci in (spec.ci_users or {}).items():
            key = ci_name.casefold()
            if key in ci_users:
                self.error(f"duplicate CI user name (case-insensitive): {ci_name}", "ci_users", ci_name)
                continue
            ci_users[key] = DesiredCIUser(
                name=ci_name,
                project=slug,
                description=ci.description,
                notification_email=ci.notification_email,
                signing_policies=list(ci.signing_policies),
                active=ci.active,
                renamed_from=ci.renamed_from,
            )

        # signing policies
        tbs = list(spec.trusted_build_systems or [])
        policies: dict[str, DesiredSigningPolicy] = {}
        declared_policies = {k.casefold() for k, v in (spec.signing_policies or {}).items() if v is not None}
        for sp_slug, sp in (spec.signing_policies or {}).items():
            if sp is None or sp_slug.casefold() in self.failed_templates:
                continue
            key = sp_slug.casefold()
            sp_path: tuple[str, ...] = ("signing_policies", sp_slug)
            if key in policies:
                self.error(f"duplicate signing policy slug (case-insensitive): {sp_slug}", *sp_path)
                continue
            if sp.certificate is None:
                self.error("'certificate' is required", *sp_path)
                continue
            if sp.origin_verification is not None and not sp.trusted_build_system_verification:
                self.error(
                    "origin verification requires 'trusted_build_system_verification: true'",
                    *sp_path,
                    "origin_verification",
                )
            if sp.pipeline_policies and not sp.trusted_build_system_verification:
                self.error(
                    "pipeline policies require 'trusted_build_system_verification: true'",
                    *sp_path,
                    "pipeline_policies",
                )
            if sp.trusted_build_system_verification and not tbs:
                self.error(
                    "trusted build system verification requires at least one entry in "
                    "'trusted_build_systems'",
                    *sp_path,
                )
            approval = None
            if sp.approval is not None:
                if sp.approval.required > len(sp.approval.approvers):
                    self.error(
                        f"{sp.approval.required} approvals required but only "
                        f"{len(sp.approval.approvers)} approvers declared",
                        *sp_path,
                        "approval",
                    )
                approval = DesiredApproval(
                    approvers=[Principal.parse(a) for a in sp.approval.approvers],
                    required=sp.approval.required,
                )
            submitters = [Principal.parse(s) for s in sp.submitters]
            for s in submitters:
                if s.kind == "ci" and s.value.casefold() in ci_users:
                    self.error(
                        f"CI user '{s.value}' belongs to this project: list the policy in "
                        f"ci_users.{s.value}.signing_policies instead",
                        *sp_path,
                        "submitters",
                    )
            for dci in ci_users.values():
                if any(p.casefold() == key for p in dci.signing_policies):
                    submitters.append(Principal("ci", dci.name))
            if sp.trusted_build_system_verification:
                for s in submitters:
                    if s.kind != "ci":
                        self.warning(
                            f"'{s}' is not a CI user: SignPath documents that only CI users "
                            "may submit when a trusted build system is required",
                            *sp_path,
                            "submitters",
                        )
            if sp.active and not submitters:
                self.error(
                    "a signing policy needs at least one submitter (list it in a CI user's "
                    "'signing_policies' or in 'submitters')",
                    *sp_path,
                )
            ov = sp.origin_verification
            policies[key] = DesiredSigningPolicy(
                slug=sp_slug,
                name=sp.name or sp_slug,
                description=sp.description,
                purpose=sp.purpose,
                certificate=sp.certificate,
                submitters=submitters,
                approval=approval,
                trusted_build_system_verification=sp.trusted_build_system_verification,
                origin_verification=(
                    DesiredOriginVerification(branches=ov.branches, build_definitions=ov.build_definitions)
                    if ov is not None
                    else None
                ),
                pipeline_policies=dict(sp.pipeline_policies),
                disable_malware_scanning=sp.disable_malware_scanning,
                active=sp.active,
                renamed_from=sp.renamed_from,
            )
        self._check_renames("signing_policies", "signing policy", policies)

        for dci in ci_users.values():
            for i, p in enumerate(dci.signing_policies):
                if p.casefold() not in declared_policies:
                    self.error(f"unknown signing policy '{p}'", "ci_users", dci.name, "signing_policies", i)

        if len(self.diags.errors) > errors_before:
            return None
        assert spec.name is not None
        assert default_ac is not None
        return DesiredProject(
            slug=slug,
            name=spec.name,
            description=spec.description,
            repositories=list(spec.repositories or []),
            readers=[Principal.parse(r) for r in spec.readers or []],
            configurators=[Principal.parse(r) for r in spec.configurators or []],
            trusted_build_systems=tbs,
            artifact_configurations=acs,
            default_artifact_configuration=default_ac,
            ci_users=ci_users,
            signing_policies=policies,
            active=spec.active,
            source=self.doc.path,
            profile=profile_name,
        )

    def _check_renames(
        self,
        section: str,
        what: str,
        entries: Mapping[str, DesiredArtifactConfiguration] | Mapping[str, DesiredSigningPolicy],
    ) -> None:
        """Each previous slug names one live object, which only one entry may take over."""
        claimed: dict[str, str] = {}  # casefolded previous slug -> slug of the entry renaming it
        for entry in entries.values():
            if entry.renamed_from is None:
                continue
            old = entry.renamed_from.casefold()
            path = (section, entry.slug, "renamed_from")
            if old == entry.slug.casefold():
                self.error("'renamed_from' must differ from the slug (case is not significant)", *path)
            elif old in entries:
                declared = entries[old].slug
                self.error(
                    f"{what} '{declared}' is still declared; remove it ('{declared}: null' if it is "
                    f"inherited) to rename it to '{entry.slug}'",
                    *path,
                )
            elif old in claimed:
                self.error(f"'{entry.renamed_from}' is already renamed to '{claimed[old]}'", *path)
            else:
                claimed[old] = entry.slug


def _check_ci_user_renames(
    renames: list[tuple[str, str, Location]],
    ci_owner: dict[str, tuple[str, Path]],
    unmanaged: Unmanaged,
    diags: Diagnostics,
) -> None:
    """Each previous CI user name, across all project files, names one user that only one entry takes over."""
    claimed: dict[str, str] = {}  # casefolded previous name -> new name
    for new, old, where in renames:
        key = old.casefold()
        if key == new.casefold():
            diags.error("'renamed_from' must differ from the name (case is not significant)", where)
        elif key in ci_owner:
            diags.error(
                f"CI user '{old}' is still declared by project {ci_owner[key][0]}; remove it to rename "
                f"it to '{new}'",
                where,
            )
        elif key in unmanaged.ci_users:
            diags.error(
                f"CI user '{old}' is listed in unmanaged.ci_users; remove it from there to rename it "
                f"to '{new}'",
                where,
            )
        elif key in claimed:
            diags.error(f"CI user '{old}' is already renamed to '{claimed[key]}'", where)
        else:
            claimed[key] = new


def load_config(
    root: Path, *, projects: Sequence[str] | None = None
) -> tuple[DesiredState | None, Diagnostics]:
    """Load a configuration root.

    ``projects`` restricts loading of project files to the given slugs (case-insensitive);
    every project file is still checked for CI user name collisions.
    """
    diags = Diagnostics()
    root = root.resolve()
    org_path = root / ORGANIZATION_FILE
    if not org_path.is_file():
        diags.error(f"{ORGANIZATION_FILE} not found in {root}", Location(org_path))
        return None, diags
    try:
        org_doc = load_yaml_file(org_path)
    except YamlLoadError as e:
        diags.error(e.message, e.location)
        return None, diags
    org = _validate_model(OrganizationSpec, org_doc, diags)
    if org is None:
        return None, diags

    # profiles
    profile_docs: dict[str, YamlDocument] = {}
    profile_dir = root / PROFILES_DIR
    if profile_dir.is_dir():
        for path in sorted(profile_dir.glob("*.yaml")):
            try:
                pdoc = load_yaml_file(path)
            except YamlLoadError as e:
                diags.error(e.message, e.location)
                continue
            if _validate_model(ProfileSpec, pdoc, diags) is not None:
                profile_docs[path.stem] = pdoc
    if org.default_profile is not None and org.default_profile not in profile_docs:
        diags.error(
            f"default profile '{org.default_profile}' not found in {PROFILES_DIR}/",
            org_doc.locate(("default_profile",)),
        )

    xml_files = XmlFiles(root, diags)
    templates = PolicyTemplates(root, diags)
    pipeline_policies = _load_pipeline_policies(root, org, org_doc, diags)

    # projects
    selected = {p.casefold() for p in projects} if projects else None
    project_dir = root / PROJECTS_DIR
    desired_projects: dict[str, DesiredProject] = {}
    seen_files: dict[str, Path] = {}
    ci_owner: dict[str, tuple[str, Path]] = {}
    ci_renames: list[tuple[str, str, Location]] = []  # (new name, previous name, where)
    project_paths = sorted(project_dir.glob("*.yaml")) if project_dir.is_dir() else []
    if project_dir.is_dir():
        for extra in sorted(project_dir.glob("*.yml")):
            diags.error("project files must use the .yaml extension", Location(extra))
    for path in project_paths:
        slug = path.stem
        if not SLUG_RE.match(slug) or len(slug) > 100:
            diags.error("file name must be a valid project slug ([A-Za-z0-9._-], max 100)", Location(path))
            continue
        key = slug.casefold()
        if key in seen_files:
            diags.error(
                f"project slug collides (case-insensitive) with {seen_files[key].name}", Location(path)
            )
            continue
        seen_files[key] = path
        try:
            doc = load_yaml_file(path)
        except YamlLoadError as e:
            diags.error(e.message, e.location)
            continue
        # nulls are merge instructions (remove the inherited value): ignore them for the structural check
        raw = _validate_model(ProjectSpec, doc, diags, data=drop_nulls(doc.data))
        if raw is None:
            continue

        # CI user names must be unique across all project files, even unselected ones
        for ci_name, ci_spec in (raw.ci_users or {}).items():
            ck = ci_name.casefold()
            if ck in ci_owner and ci_owner[ck][0] != slug:
                diags.error(
                    f"CI user '{ci_name}' is also declared by project {ci_owner[ck][0]}",
                    doc.locate(("ci_users", ci_name)),
                )
            else:
                ci_owner[ck] = (slug, path)
            if ci_spec.renamed_from is not None:
                ci_renames.append(
                    (ci_name, ci_spec.renamed_from, doc.locate(("ci_users", ci_name, "renamed_from")))
                )

        if selected is not None and key not in selected:
            continue

        data = doc.data if isinstance(doc.data, dict) else {}
        profile_name = data.get("profile", org.default_profile)
        profile_doc = None
        if profile_name is not None:
            profile_doc = profile_docs.get(profile_name)
            if profile_doc is None:
                diags.error(f"unknown profile '{profile_name}'", doc.locate(("profile",)))
                continue
        failed_templates: set[str] = set()
        merged = merge_profile(
            profile_doc.data if profile_doc else None,
            data,
            templates=templates.loader(doc, profile_doc, failed_templates),
        )
        spec = _validate_model(ProjectSpec, doc, diags, data=merged)
        if spec is None:
            continue
        builder = _ProjectBuilder(root, doc, profile_doc, xml_files, diags, failed_templates)
        project = builder.build(slug, spec, profile_name)
        if project is None:
            continue
        for sp in project.signing_policies.values():
            for pp_slug in sp.pipeline_policies:
                if pp_slug.casefold() not in pipeline_policies:
                    builder.warning(
                        f"pipeline policy '{pp_slug}' is not declared in {ORGANIZATION_FILE}; "
                        "it must already exist in SignPath",
                        "signing_policies",
                        sp.slug,
                        "pipeline_policies",
                        pp_slug,
                    )
        desired_projects[key] = project

    if selected is not None:
        missing = selected - set(seen_files)
        for m in sorted(missing):
            diags.error(f"no project file for '{m}' in {PROJECTS_DIR}/")

    unmanaged = Unmanaged(
        projects={p.casefold() for p in org.unmanaged.projects},
        ci_users={c.casefold() for c in org.unmanaged.ci_users},
        pipeline_policies={p.casefold() for p in org.unmanaged.pipeline_policies},
    )
    for key, project in desired_projects.items():
        if key in unmanaged.projects:
            diags.error(
                f"project '{project.slug}' is both declared and listed in unmanaged.projects",
                Location(project.source),
            )
    for ck, (owner, path) in ci_owner.items():
        if ck in unmanaged.ci_users:
            diags.error(
                f"CI user declared by project {owner} is also listed in unmanaged.ci_users", Location(path)
            )
    _check_ci_user_renames(ci_renames, ci_owner, unmanaged, diags)
    for project in desired_projects.values():
        for sp in project.signing_policies.values():
            for s in sp.submitters:
                if s.kind == "ci" and s.value.casefold() not in project.ci_users:
                    owner_entry = ci_owner.get(s.value.casefold())
                    if owner_entry is not None:
                        diags.warning(
                            f"signing policy {sp.slug} uses CI user '{s.value}' owned by project "
                            f"{owner_entry[0]}; prefer one CI user per project",
                            Location(project.source),
                        )
                    elif s.value.casefold() not in unmanaged.ci_users:
                        # otherwise --prune would deactivate a CI user that a policy still uses
                        diags.error(
                            f"signing policy {sp.slug} uses CI user '{s.value}', which no project declares; "
                            "declare it in its owning project or list it in unmanaged.ci_users",
                            Location(project.source),
                        )

    state = DesiredState(
        root=root,
        organization_id=str(org.organization_id),
        api_url=org.api_url.rstrip("/"),
        default_profile=org.default_profile,
        projects=desired_projects,
        pipeline_policies=pipeline_policies,
        unmanaged=unmanaged,
        profiles={name: (d.data or {}) for name, d in profile_docs.items()},
    )
    return state, diags

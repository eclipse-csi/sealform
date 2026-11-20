from __future__ import annotations

from sealform.plan.actions import Kind, Op

from .conftest import DEFAULT_PROFILE, JAR_XML, MSI_XML, ConfigFactory, project_yaml
from .fake_signpath import AC, Policy, Project
from .helpers import converge, plan_for, standard_fake, summary


def seed_alpha(fake, *, xml: str = JAR_XML) -> None:  # type: ignore[no-untyped-def]
    """Seed the fake with exactly what project_yaml() + the default profile describe."""
    ci = fake.add_ci_user("alpha-ci")
    stored = xml if xml.startswith("<?xml") else '<?xml version="1.0" encoding="utf-8"?>\n' + xml
    project = Project("Alpha", "tech.alpha", None, ["https://github.com/example/alpha"], [], [])
    project.acs.append(AC("Default", "default", None, stored, is_default=True))
    project.tbs_links.append("GitHub.com")
    project.policies.append(
        Policy(
            "Test signing",
            "test-signing",
            None,
            "TestSigning",
            "test-cert",
            [ci.id],
            [],
            0,
            False,
            {
                "requireTrustedBuildSystem": True,
                "originPolicyVerification": {"verifyOriginPolicy": True, "allowedBranchNames": ["*"]},
            },
        )
    )
    project.policies.append(
        Policy(
            "Release signing",
            "release-signing",
            None,
            "ReleaseSigning",
            "release-cert",
            [ci.id],
            [],
            0,
            False,
            {
                "requireTrustedBuildSystem": True,
                "originPolicyVerification": {"verifyOriginPolicy": True, "allowedBranchNames": ["main"]},
            },
        )
    )
    fake.projects.append(project)


def test_new_project_plan(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(standard_fake(), root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == [
        "create project tech.alpha",
        "link trusted build system link GitHub.com",
        "create CI user alpha-ci",
        "create signing policy test-signing",
        "create signing policy release-signing",
    ]
    ci = next(a for a in plan.actions if a.kind is Kind.CI_USER)
    assert ci.issues_token
    policy = next(a for a in plan.actions if a.name == "release-signing")
    assert policy.resolved["submitters"] == ["new:ci:alpha-ci"]


def test_in_sync_project_has_no_changes(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.items]
    assert plan.actions == []
    assert plan.drift == []
    assert not [r for r in fake.requests if r[0] != "GET"]


def test_field_changes(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.add_user("Alice", "alice@example.org")
    extra = """
    description: New description
    signing_policies:
      release-signing:
        origin_verification:
          branches: [main, 'release/*']
        approval:
          approvers: [Alice@Example.org]
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    plan = plan_for(fake, root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == ["update project tech.alpha", "update signing policy release-signing"]
    policy = plan.actions[1]
    fields = {c.field: c for c in policy.changes}
    assert set(fields) == {"approval", "origin_verification"}
    assert fields["approval"].new == {"approvers": ["alice@example.org"], "required": 1}


def test_xml_change_shows_diff(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake, xml=JAR_XML.replace("<jar-sign />", "<jar-sign/>"))
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root)
    assert summary(plan) == ["update artifact configuration default"]
    diff = plan.actions[0].changes[0].diff
    assert diff is not None
    assert "-    <jar-sign/>" in diff
    assert "+    <jar-sign />" in diff


def test_missing_declaration_is_not_a_change(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake, xml=JAR_XML.split("\n", 1)[1])  # stored with the default declaration prepended
    library_xml = JAR_XML.split("\n", 1)[1]
    root = make_config({"projects/tech.alpha.yaml": project_yaml()}, library={"jar": library_xml})
    assert plan_for(fake, root).actions == []


def test_drift_and_prune(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects.append(
        Project("Other", "other", None, [], [], [], acs=[AC("x", "x", None, JAR_XML, is_default=True)])
    )
    fake.add_ci_user("stray-ci")
    fake.add_ci_user("old-ci", active=False)
    project = fake.projects[0]
    project.acs.append(AC("Legacy", "legacy", None, JAR_XML))
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})

    plan = plan_for(fake, root)
    assert plan.actions == []
    drift = {(d.kind, d.name) for d in plan.drift}
    assert drift == {
        (Kind.PROJECT, "other"),
        (Kind.CI_USER, "stray-ci"),
        (Kind.ARTIFACT_CONFIGURATION, "legacy"),
    }

    pruned = plan_for(fake, root, prune=True)
    assert sorted(summary(pruned)) == [
        "deactivate CI user stray-ci",
        "deactivate artifact configuration legacy",
        "deactivate project other",
    ]
    assert all(a.op is Op.DEACTIVATE and a.reason == "pruned: not declared" for a in pruned.actions)

    guarded = plan_for(fake, root, prune=True, max_deactivations=2)
    assert any("--max-deactivations" in str(d) for d in guarded.diagnostics.errors)


def test_unmanaged_objects_are_ignored(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects.append(Project("Sandbox", "sandbox", None, [], [], []))
    fake.add_ci_user("automation")
    org = """
    organization_id: 11111111-2222-3333-4444-555555555555
    default_profile: standard
    unmanaged:
      projects: [SANDBOX]
      ci_users: [automation]
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml()}, org=org)
    assert plan_for(fake, root).drift == []


def test_project_scope_ignores_other_projects(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects.append(Project("Other", "other", None, [], [], []))
    fake.add_ci_user("stray-ci")
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(),
            "projects/tech.beta.yaml": project_yaml("Beta", "beta-ci"),
        }
    )
    plan = plan_for(fake, root, scope="projects", projects=["tech.alpha"])
    assert plan.actions == []
    assert plan.drift == []
    fetched = {r[1] for r in fake.requests if r[0] == "GET"}
    assert not any("tech.beta" in p for p in fetched)


def test_foreign_tbs_link_is_unfixable_drift(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    fake.add_tbs("Jenkins")
    seed_alpha(fake)
    fake.projects[0].tbs_links.append("Jenkins")
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root, prune=True)
    assert plan.actions == []
    assert [(d.kind, d.prunable) for d in plan.drift] == [(Kind.TRUSTED_BUILD_SYSTEM_LINK, False)]


def test_reference_errors(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    fake.certificates[0]["status"] = "Expired"
    extra = """
    readers: [nobody@example.org, group:Nope]
    trusted_build_systems: [GitHub.com, Missing]
    signing_policies:
      release-signing:
        certificate: test-cert
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    plan = plan_for(fake, root)
    errors = "\n".join(str(d) for d in plan.diagnostics.errors)
    assert "no interactive user with email 'nobody@example.org'" in errors
    assert "no user group named 'Nope'" in errors
    assert "no trusted build system 'Missing'" in errors
    assert "certificate 'test-cert' is Expired" in errors
    assert "purpose 'release' does not match the test certificate" in errors


def test_inactive_objects_are_reactivated(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects[0].active = False
    fake.projects[0].policies[0].active = False
    fake.ci_users[-1].active = False
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root)
    assert summary(plan) == [
        "activate project tech.alpha",
        "activate CI user alpha-ci",
        "activate signing policy test-signing",
    ]


def test_ambiguous_inactive_ci_users(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    fake.add_ci_user("alpha-ci", active=False)
    fake.add_ci_user("alpha-ci", active=False)
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root)
    assert any("several inactive CI users" in str(d) for d in plan.diagnostics.errors)


def test_quota(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    fake.project_quota = 0
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root)
    assert any("active projects" in str(d) for d in plan.diagnostics.errors)


def test_pipeline_policies_plan(make_config: ConfigFactory) -> None:
    org = """
    organization_id: 11111111-2222-3333-4444-555555555555
    default_profile: standard
    pipeline_policies:
      gh:
        name: GitHub hosted
        definition: {github-build-policies: {version: '1.0'}}
    """
    extra = "signing_policies:\n  release-signing:\n    pipeline_policies: {gh: enforce}\n"
    fake = standard_fake()
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)}, org=org)
    plan = plan_for(fake, root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.items]
    assert summary(plan)[0] == "create pipeline policy gh"
    # project scope cannot rely on a pipeline policy that does not exist yet
    scoped = plan_for(fake, root, scope="projects", projects=["tech.alpha"])
    assert any("apply organization-level changes first" in str(d) for d in scoped.diagnostics.errors)


def test_multi_line_text_changes_render_as_a_diff(make_config: ConfigFactory) -> None:
    from rich.console import Console

    from sealform.plan.render import render_plan

    fake = standard_fake()
    seed_alpha(fake)
    fake.projects[0].description = "line one\r\nline two"
    extra = "description: |-\n  line one\n  line 2\n"
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    plan = plan_for(fake, root)
    console = Console(record=True, width=200)
    render_plan(plan, console)
    text = console.export_text()
    assert "-line two" in text
    assert "+line 2" in text
    assert "-line one\\r" in text  # carriage returns are made visible


def test_description_line_endings_are_not_a_change(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects[0].description = "line one\r\nline two"
    fake.projects[0].acs[0].description = "a\r\nb\r\n"
    extra = (
        "description: |-\n  line one\n  line two\n"
        "artifact_configurations:\n  default:\n    description: |\n      a\n      b\n"
    )
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    assert plan_for(fake, root).actions == []


def test_renamed_default_artifact_configuration_keeps_its_identity(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    extra = """
    artifact_configurations:
      default: null
      jar:
        name: Default
        renamed_from: default
        file: artifact-configurations/jar.xml
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    plan = plan_for(fake, root)
    assert plan.diagnostics.items == [], [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == ["update artifact configuration jar"]
    assert [(c.field, c.old, c.new) for c in plan.actions[0].changes] == [("slug", "default", "jar")]
    assert plan.drift == []

    follow_up = converge(fake, root)
    assert follow_up.actions == []
    assert follow_up.diagnostics.items == []
    assert [(a.slug, a.is_default, a.active) for a in fake.projects[0].acs] == [("jar", True, True)]
    assert not [r for r in fake.writes() if r[1].endswith("/ArtifactConfigurations")]


def test_rename_reactivates_and_updates_in_order(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects[0].acs.append(AC("Legacy", "legacy", None, JAR_XML, active=False))
    extra = """
    artifact_configurations:
      installer:
        name: Installer
        renamed_from: Legacy
        file: artifact-configurations/msi.xml
    default_artifact_configuration: default
    """
    root = make_config(
        {"projects/tech.alpha.yaml": project_yaml(extra=extra)}, library={"jar": JAR_XML, "msi": MSI_XML}
    )
    plan = plan_for(fake, root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == [
        "activate artifact configuration legacy",
        "update artifact configuration installer",
        "update artifact configuration installer",
    ]
    assert {c.field for c in plan.actions[1].changes} == {"slug", "name"}
    assert [c.field for c in plan.actions[2].changes] == ["xml"]

    assert converge(fake, root).actions == []
    installer = fake.projects[0].acs[1]
    assert (installer.slug, installer.name, installer.active) == ("installer", "Installer", True)
    assert "<msi-file>" in installer.xml


def test_renamed_from_is_ignored_when_the_new_slug_exists(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects[0].acs.append(AC("Legacy", "legacy", None, JAR_XML))
    extra = "artifact_configurations:\n  default:\n    renamed_from: legacy\n"
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    plan = plan_for(fake, root)
    assert plan.actions == []
    assert [(d.kind, d.name) for d in plan.drift] == [(Kind.ARTIFACT_CONFIGURATION, "legacy")]
    assert any("'renamed_from: legacy' is ignored" in str(d) for d in plan.diagnostics.warnings)


def test_renamed_from_an_unknown_slug_creates(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    extra = """
    artifact_configurations:
      installer:
        renamed_from: never-existed
        file: artifact-configurations/jar.xml
    default_artifact_configuration: default
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    plan = plan_for(fake, root)
    assert plan.diagnostics.items == [], [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == ["create artifact configuration installer"]


def test_renamed_signing_policy_is_reactivated_then_renamed(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    fake.projects[0].policies[1].active = False
    profile = DEFAULT_PROFILE.replace(
        "  release-signing:\n", "  release:\n    renamed_from: release-signing\n"
    )
    alpha = project_yaml().replace("[test-signing, release-signing]", "[test-signing, release]")
    root = make_config({"projects/tech.alpha.yaml": alpha}, profile=profile)
    plan = plan_for(fake, root)
    assert plan.diagnostics.items == [], [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == ["activate signing policy release-signing", "update signing policy release"]
    assert [(c.field, c.old, c.new) for c in plan.actions[1].changes] == [
        ("slug", "release-signing", "release")
    ]
    assert plan.drift == []

    assert converge(fake, root).actions == []
    assert [(p.slug, p.active) for p in fake.projects[0].policies] == [
        ("test-signing", True),
        ("release", True),
    ]
    assert not [w for w in fake.writes() if w[1].endswith("/SigningPolicies")]


def _renamed_ci_user(new: str, old: str, name: str = "Alpha", extra: str = "") -> str:
    return project_yaml(name, new, extra).replace(f"  {new}:\n", f"  {new}:\n    renamed_from: {old}\n")


def test_renamed_ci_user_keeps_its_identity_and_token(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    user = fake.ci_users[-1]
    root = make_config({"projects/tech.alpha.yaml": _renamed_ci_user("alpha-builder", "alpha-ci")})
    plan = plan_for(fake, root)
    assert plan.diagnostics.items == [], [str(d) for d in plan.diagnostics.items]
    assert summary(plan) == ["update CI user alpha-builder"]
    assert [(c.field, c.old, c.new) for c in plan.actions[0].changes] == [
        ("name", "alpha-ci", "alpha-builder")
    ]
    assert not plan.actions[0].issues_token
    assert plan.drift == []
    # a project-scoped run fetches the user under its previous name; an org run does not report it as drift
    assert summary(plan_for(fake, root, scope="projects", projects=["tech.alpha"])) == [
        "update CI user alpha-builder"
    ]
    assert plan_for(fake, root, scope="org").drift == []

    assert converge(fake, root).actions == []
    assert [u.name for u in fake.ci_users if u.id == user.id] == ["alpha-builder"]
    assert not any(u.name == "alpha-ci" for u in fake.ci_users)
    assert [w[1] for w in fake.writes() if w[1].startswith("/CIUsers")] == [f"/CIUsers/{user.id}/Edit"]


def test_other_projects_reference_a_renamed_ci_user_by_its_new_name(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    seed_alpha(fake)
    user = fake.ci_users[-1]
    beta = project_yaml(
        "Beta",
        "beta-ci",
        extra="signing_policies:\n  release-signing:\n    submitters: ['ci:alpha-builder']\n",
    )
    root = make_config(
        {
            "projects/tech.alpha.yaml": _renamed_ci_user("alpha-builder", "alpha-ci"),
            "projects/tech.beta.yaml": beta,
        }
    )
    plan = plan_for(fake, root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.errors]
    policy = next(a for a in plan.actions if a.project == "tech.beta" and a.name == "release-signing")
    assert user.id in policy.resolved["submitters"]
    assert "ci:alpha-builder" in next(c.new for c in policy.changes if c.field == "submitters")

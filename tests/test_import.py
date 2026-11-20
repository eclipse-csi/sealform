from __future__ import annotations

import shutil
from pathlib import Path

from sealform.config.yamlio import load_yaml_file
from sealform.importer.export import Importer, ImportReport
from sealform.live.fetch import Fetcher, FetchRequest

from .conftest import DEFAULT_PROFILE, JAR_XML, MSI_XML, ConfigFactory, project_yaml
from .fake_signpath import ORG_ID, FakeSignPath
from .helpers import client_for, converge, plan_for, standard_fake, summary
from .test_apply import RICH_PROJECT, rich_fake


def run_import(
    fake: FakeSignPath,
    output: Path,
    *,
    profile: str | None = None,
    projects: list[str] | None = None,
    force: bool = False,
) -> ImportReport:
    request = FetchRequest(
        scope="projects" if projects else "full",
        project_slugs={p.casefold() for p in projects or []},
        all_projects=not projects,
        all_ci_users=True,
        all_pipeline_policies=not projects,
        interactive_users=True,
    )
    live = Fetcher(client_for(fake), parallelism=2).fetch(request)
    output.mkdir(parents=True, exist_ok=True)
    return Importer(
        live, output, organization_id=ORG_ID, profile=profile, force=force, full_scope=not projects
    ).run()


def assert_round_trip(fake: FakeSignPath, root: Path) -> None:
    plan = plan_for(fake, root)
    assert not plan.diagnostics.has_errors, [str(d) for d in plan.diagnostics.errors]
    assert plan.actions == [], summary(plan)
    assert plan.drift == [], [(d.kind, d.name) for d in plan.drift]


def test_round_trip_without_profile(make_config: ConfigFactory, tmp_path: Path) -> None:
    fake = rich_fake()
    source = make_config(
        {"projects/tech.alpha.yaml": project_yaml(), "projects/tech.beta.yaml": RICH_PROJECT}
    )
    converge(fake, source)
    fake.add_ci_user("automation")  # submits for nothing: becomes unmanaged
    out = tmp_path / "imported"
    report = run_import(fake, out)
    assert report.problems == []
    org = load_yaml_file(out / "organization.yaml").data
    assert org["unmanaged"] == {"ci_users": ["automation"]}
    beta = load_yaml_file(out / "projects/tech.beta.yaml").data
    assert beta["ci_users"]["beta-ci"]["signing_policies"] == ["release-signing", "manual"]
    assert beta["signing_policies"]["manual"]["submitters"] == ["bob@example.org"]
    assert beta["readers"] == ["alice@example.org", "group:Readers"]
    assert_round_trip(fake, out)


def test_shared_xml_goes_to_the_library(make_config: ConfigFactory, tmp_path: Path) -> None:
    fake = standard_fake()
    source = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(),
            "projects/tech.beta.yaml": project_yaml("Beta", "beta-ci"),
        }
    )
    converge(fake, source)
    out = tmp_path / "imported"
    run_import(fake, out)
    library = list((out / "artifact-configurations").glob("imported-*.xml"))
    assert len(library) == 1
    assert library[0].read_text() == JAR_XML
    alpha = load_yaml_file(out / "projects/tech.alpha.yaml").data
    assert alpha["artifact_configurations"]["default"] == {"name": "Default", "shared": library[0].stem}
    assert_round_trip(fake, out)


def test_existing_library_is_reused_and_profile_minimizes(make_config: ConfigFactory, tmp_path: Path) -> None:
    fake = rich_fake()
    source = make_config(
        {"projects/tech.alpha.yaml": project_yaml(), "projects/tech.beta.yaml": RICH_PROJECT}
    )
    converge(fake, source)
    out = tmp_path / "imported"
    shutil.copytree(source / "profiles", out / "profiles")
    shutil.copytree(source / "artifact-configurations", out / "artifact-configurations")
    run_import(fake, out, profile="standard")
    org = load_yaml_file(out / "organization.yaml").data
    assert org["default_profile"] == "standard"
    alpha = load_yaml_file(out / "projects/tech.alpha.yaml").data
    assert alpha == load_yaml_file(source / "projects/tech.alpha.yaml").data
    beta = load_yaml_file(out / "projects/tech.beta.yaml").data
    assert beta["signing_policies"]["test-signing"] is None
    assert "trusted_build_systems" not in beta
    assert beta["artifact_configurations"]["default"] == {"name": "Default jar"}
    assert_round_trip(fake, out)


def test_profile_removals_are_explicit(make_config: ConfigFactory, tmp_path: Path) -> None:
    # the project drops origin verification and pipeline policies the profile sets
    profile = DEFAULT_PROFILE + "    pipeline_policies: {gh: enforce}\n"
    org = f"""
    organization_id: {ORG_ID}
    default_profile: standard
    pipeline_policies:
      gh: {{name: GH, definition: {{github-build-policies: {{version: '1.0'}}}}}}
    """
    extra = """
    signing_policies:
      release-signing:
        origin_verification: null
        pipeline_policies: {gh: null}
        trusted_build_system_verification: false
    """
    fake = standard_fake()
    source = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)}, org=org, profile=profile)
    converge(fake, source)
    out = tmp_path / "imported"
    shutil.copytree(source / "profiles", out / "profiles")
    shutil.copytree(source / "artifact-configurations", out / "artifact-configurations")
    shutil.copy(source / "organization.yaml", out / "organization.yaml")
    run_import(fake, out, profile="standard")
    release = load_yaml_file(out / "projects/tech.alpha.yaml").data["signing_policies"]["release-signing"]
    assert release["origin_verification"] is None
    assert release["trusted_build_system_verification"] is False
    assert release["pipeline_policies"] == {"gh": None}
    assert_round_trip(fake, out)


def test_shared_ci_user_is_reported_and_referenced(make_config: ConfigFactory, tmp_path: Path) -> None:
    fake = standard_fake()
    source = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(ci="shared-ci"),
            "projects/tech.beta.yaml": project_yaml(
                "Beta",
                "beta-ci",
                extra="signing_policies:\n  release-signing:\n    submitters: ['ci:shared-ci']\n",
            ),
        }
    )
    converge(fake, source)
    out = tmp_path / "imported"
    report = run_import(fake, out)
    assert any("shared-ci" in p and "several projects" in p for p in report.problems)
    beta = load_yaml_file(out / "projects/tech.beta.yaml").data
    assert beta["signing_policies"]["release-signing"]["submitters"] == ["ci:shared-ci"]
    assert_round_trip(fake, out)


def test_existing_files_are_kept_without_force(make_config: ConfigFactory, tmp_path: Path) -> None:
    fake = standard_fake()
    source = make_config({"projects/tech.alpha.yaml": project_yaml()})
    converge(fake, source)
    out = tmp_path / "imported"
    run_import(fake, out)
    path = out / "projects/tech.alpha.yaml"
    path.write_text("# edited by hand\n" + path.read_text())
    report = run_import(fake, out)
    assert report.skipped_files == ["projects/tech.alpha.yaml"]
    assert path.read_text().startswith("# edited by hand")
    report = run_import(fake, out, force=True)
    assert "projects/tech.alpha.yaml" in report.written
    assert not path.read_text().startswith("# edited by hand")


def test_pipeline_policies_and_custom_xml(make_config: ConfigFactory, tmp_path: Path) -> None:
    org = f"""
    organization_id: {ORG_ID}
    default_profile: standard
    pipeline_policies:
      gh:
        name: GitHub hosted
        definition: {{github-build-policies: {{version: '1.0', disallow_reruns: true}}}}
    """
    inline = "".join(f"      {line}\n" for line in MSI_XML.splitlines())
    extra = (
        "artifact_configurations:\n  msi:\n    xml: |\n"
        + inline
        + "default_artifact_configuration: default\n"
        "signing_policies:\n  release-signing:\n    pipeline_policies: {gh: log}\n"
    )
    fake = standard_fake()
    source = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)}, org=org)
    converge(fake, source)
    out = tmp_path / "imported"
    run_import(fake, out)
    org_data = load_yaml_file(out / "organization.yaml").data
    assert org_data["pipeline_policies"]["gh"] == {
        "name": "GitHub hosted",
        "definition_file": "pipeline-policies/gh.yml",
    }
    assert (out / "artifact-configurations/custom/tech.alpha.msi.xml").is_file()
    assert_round_trip(fake, out)

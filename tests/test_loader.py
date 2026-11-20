from __future__ import annotations

from pathlib import Path

import pytest

from signpath_manager.config.desired import Principal
from signpath_manager.config.loader import load_config
from signpath_manager.diagnostics import Diagnostics

from .conftest import MSI_XML, ConfigFactory, project_yaml


def _messages(diags: Diagnostics) -> list[str]:
    return [str(d) for d in diags.errors]


def test_minimal_project_inherits_profile(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    state, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert state is not None
    project = state.projects["tech.alpha"]
    assert project.slug == "tech.alpha"
    assert project.trusted_build_systems == ["GitHub.com"]
    assert project.default_artifact_configuration == "default"
    assert project.artifact_configurations["default"].source == "shared:jar"
    release = project.signing_policies["release-signing"]
    assert release.certificate == "release-cert"
    assert release.submitters == [Principal("ci", "alpha-ci")]
    assert release.origin_verification is not None
    assert release.origin_verification.branches == ["main"]
    assert state.organization_id == "11111111-2222-3333-4444-555555555555"


def test_project_overrides_and_removes(make_config: ConfigFactory) -> None:
    extra = """
    signing_policies:
      test-signing: null
      release-signing:
        approval:
          approvers: [alice@example.org, group:Leads]
          required: 2
    """
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(extra=extra).replace(
                "[test-signing, release-signing]", "[release-signing]"
            )
        }
    )
    state, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert state is not None
    project = state.projects["tech.alpha"]
    assert list(project.signing_policies) == ["release-signing"]
    approval = project.signing_policies["release-signing"].approval
    assert approval is not None
    assert approval.required == 2
    assert approval.approvers == [Principal("user", "alice@example.org"), Principal("group", "Leads")]


def test_custom_artifact_configurations(make_config: ConfigFactory) -> None:
    inline = "".join(f"      {line}\n" for line in MSI_XML.splitlines())
    extra = (
        "artifact_configurations:\n"
        "  msi:\n"
        "    file: custom/msi.xml\n"
        "  inline:\n"
        "    xml: |\n"
        f"{inline}"
        "default_artifact_configuration: msi\n"
    )
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra), "custom/msi.xml": MSI_XML})
    state, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert state is not None
    acs = state.projects["tech.alpha"].artifact_configurations
    assert set(acs) == {"default", "msi", "inline"}
    assert acs["msi"].source == "file:custom/msi.xml"
    assert "<msi-file>" in acs["inline"].xml
    assert state.projects["tech.alpha"].default_artifact_configuration == "msi"


def test_errors_are_located(make_config: ConfigFactory) -> None:
    extra = """
    signing_policies:
      release-signing:
        certificate: "not a slug!"
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    _, diags = load_config(root)
    errors = diags.errors
    assert len(errors) == 1
    assert errors[0].location is not None
    assert errors[0].location.file.name == "tech.alpha.yaml"
    assert errors[0].location.line == 9  # blank line 6 comes from the dedented extra block
    assert errors[0].path == "signing_policies.release-signing.certificate"


def test_unknown_key(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra="colour: blue\n")})
    _, diags = load_config(root)
    assert any("unknown key" in m and "colour" in m for m in _messages(diags))


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ("trusted_build_systems: []\n", "requires at least one entry in 'trusted_build_systems'"),
        (
            "signing_policies:\n  release-signing:\n    trusted_build_system_verification: false\n",
            "origin verification requires",
        ),
        (
            "signing_policies:\n  release-signing:\n"
            "    approval: {approvers: [a@example.org], required: 2}\n",
            "2 approvals required but only 1 approvers",
        ),
        (
            "artifact_configurations:\n  other: {shared: jar}\n",
            "'default_artifact_configuration' is required",
        ),
        ("artifact_configurations:\n  default: {shared: nope}\n", "no shared artifact configuration 'nope'"),
        ("artifact_configurations:\n  default: {file: ../escape.xml}\n", "file not found inside"),
        (
            "signing_policies:\n  release-signing:\n    submitters: ['ci:alpha-ci']\n",
            "belongs to this project",
        ),
        ("readers: ['ci:someone']\n", "'ci:' references are not allowed here"),
        ("readers: ['not-an-email']\n", "expected a user email"),
    ],
)
def test_semantic_errors(make_config: ConfigFactory, extra: str, expected: str) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    _, diags = load_config(root)
    assert any(expected in m for m in _messages(diags)), _messages(diags)


def test_policy_without_submitter(make_config: ConfigFactory) -> None:
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml().replace(
                "[test-signing, release-signing]", "[test-signing]"
            )
        }
    )
    _, diags = load_config(root)
    assert any("at least one submitter" in m for m in _messages(diags))


def test_ci_user_unknown_policy(make_config: ConfigFactory) -> None:
    root = make_config(
        {"projects/tech.alpha.yaml": project_yaml().replace("[test-signing, release-signing]", "[nope]")}
    )
    _, diags = load_config(root)
    assert any("unknown signing policy 'nope'" in m for m in _messages(diags))


def test_duplicate_ci_user_across_projects(make_config: ConfigFactory) -> None:
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(ci="shared-ci"),
            "projects/tech.beta.yaml": project_yaml(name="Beta", ci="Shared-CI"),
        }
    )
    _, diags = load_config(root)
    assert any("also declared by project tech.alpha" in m for m in _messages(diags))


def test_duplicate_ci_user_detected_even_when_scoped(make_config: ConfigFactory) -> None:
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(ci="shared-ci"),
            "projects/tech.beta.yaml": project_yaml(name="Beta", ci="shared-ci"),
        }
    )
    state, diags = load_config(root, projects=["tech.alpha"])
    assert diags.has_errors
    assert state is not None
    assert set(state.projects) == {"tech.alpha"}


def test_project_slug_collision(make_config: ConfigFactory, tmp_path: Path) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    (root / "projects" / "Tech.Alpha.yaml").write_text(project_yaml(ci="other"))
    _, diags = load_config(root)
    # case-insensitive file systems cannot hold both files; only assert when they do
    if len(list((root / "projects").glob("*.yaml"))) == 2:
        assert any("collides" in m for m in _messages(diags))


def test_invalid_file_name(make_config: ConfigFactory) -> None:
    root = make_config({"projects/bad name.yaml": project_yaml()})
    _, diags = load_config(root)
    assert any("valid project slug" in m for m in _messages(diags))


def test_unknown_profile(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra="profile: nope\n")})
    _, diags = load_config(root)
    assert any("unknown profile 'nope'" in m for m in _messages(diags))


def test_explicit_no_profile(make_config: ConfigFactory) -> None:
    yaml = """
    name: Alpha
    profile: null
    artifact_configurations:
      only: {shared: jar}
    ci_users:
      alpha-ci: {signing_policies: [s]}
    signing_policies:
      s: {certificate: cert}
    """
    root = make_config({"projects/tech.alpha.yaml": yaml})
    state, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert state is not None
    project = state.projects["tech.alpha"]
    assert project.trusted_build_systems == []
    assert project.default_artifact_configuration == "only"
    assert project.profile is None


def test_missing_organization(tmp_path: Path) -> None:
    state, diags = load_config(tmp_path)
    assert state is None
    assert "organization.yaml not found" in _messages(diags)[0]


def test_invalid_yaml_is_located(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": "name: [unclosed\n"})
    _, diags = load_config(root)
    assert diags.errors[0].location is not None
    assert "invalid YAML" in diags.errors[0].message


def test_duplicate_keys_rejected(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml() + "name: Again\n"})
    _, diags = load_config(root)
    assert diags.has_errors


def test_pipeline_policies(make_config: ConfigFactory) -> None:
    org = """
    organization_id: 11111111-2222-3333-4444-555555555555
    default_profile: standard
    pipeline_policies:
      inline:
        name: Inline
        definition:
          github-build-policies: {version: '1.0', disallow_reruns: true}
      from-file:
        name: From file
        definition_file: pp/policy.yml
      ext:
        name: External
        external: {repository: https://github.com/x/y, path: .signpath/policies/p.yml}
      bad:
        name: Bad
    """
    root = make_config({"pp/policy.yml": "github-build-policies:\n  version: '1.0'\n"}, org=org)
    state, diags = load_config(root)
    assert any("exactly one of 'definition'" in m for m in _messages(diags))
    org_ok = org.replace("      bad:\n        name: Bad\n", "")
    root = make_config({"pp/policy.yml": "github-build-policies:\n  version: '1.0'\n"}, org=org_ok)
    state, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert state is not None
    pps = state.pipeline_policies
    assert pps["inline"].definition_data == {
        "github-build-policies": {"version": "1.0", "disallow_reruns": True}
    }
    assert pps["inline"].definition_text is not None
    assert "disallow_reruns: true" in pps["inline"].definition_text
    assert pps["from-file"].definition_text == "github-build-policies:\n  version: '1.0'\n"
    assert pps["ext"].external
    assert pps["ext"].path == ".signpath/policies/p.yml"


def test_undeclared_pipeline_policy_is_a_warning(make_config: ConfigFactory) -> None:
    extra = "signing_policies:\n  release-signing:\n    pipeline_policies: {gh: enforce}\n"
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    _, diags = load_config(root)
    assert not diags.has_errors
    assert any("pipeline policy 'gh' is not declared" in str(w) for w in diags.warnings)


def test_unmanaged_conflicts(make_config: ConfigFactory) -> None:
    org = """
    organization_id: 11111111-2222-3333-4444-555555555555
    default_profile: standard
    unmanaged:
      projects: [Tech.Alpha]
      ci_users: [alpha-ci]
    """
    root = make_config({"projects/tech.alpha.yaml": project_yaml()}, org=org)
    _, diags = load_config(root)
    messages = _messages(diags)
    assert any("listed in unmanaged.projects" in m for m in messages)
    assert any("listed in unmanaged.ci_users" in m for m in messages)


def test_foreign_ci_user_reference_warns(make_config: ConfigFactory) -> None:
    beta = project_yaml(
        name="Beta",
        ci="beta-ci",
        extra="signing_policies:\n  release-signing:\n    submitters: ['ci:alpha-ci']\n",
    )
    root = make_config({"projects/tech.alpha.yaml": project_yaml(), "projects/tech.beta.yaml": beta})
    _, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert any("owned by project tech.alpha" in str(w) for w in diags.warnings)


def test_interactive_submitter_with_tbs_verification_warns(make_config: ConfigFactory) -> None:
    extra = "signing_policies:\n  release-signing:\n    submitters: [bob@example.org]\n"
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)})
    _, diags = load_config(root)
    assert not diags.has_errors
    assert any("is not a CI user" in str(w) for w in diags.warnings)


def test_scoped_loading(make_config: ConfigFactory) -> None:
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(),
            "projects/tech.beta.yaml": project_yaml(name="Beta", ci="beta-ci"),
        }
    )
    state, diags = load_config(root, projects=["TECH.BETA"])
    assert not diags.has_errors
    assert state is not None
    assert list(state.projects) == ["tech.beta"]
    _, diags = load_config(root, projects=["tech.gamma"])
    assert any("no project file for 'tech.gamma'" in m for m in _messages(diags))


def test_example_configuration_is_valid() -> None:
    root = Path(__file__).parent.parent / "examples" / "basic"
    state, diags = load_config(root)
    assert not diags.has_errors, _messages(diags)
    assert state is not None
    assert set(state.projects) == {"example.alpha", "example.beta"}

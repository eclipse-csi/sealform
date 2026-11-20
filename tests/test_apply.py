from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import SecretStr

from signpath_manager import cli as cli_module
from signpath_manager.apply.executor import Executor
from signpath_manager.cli import cli
from signpath_manager.plan.actions import Kind, Op
from signpath_manager.tokens.sinks import FileSink, TokenSinkError

from .conftest import MSI_XML, ConfigFactory, project_yaml
from .fake_signpath import AC, FakeSignPath, Project
from .helpers import apply_plan, client_for, converge, plan_for, standard_fake, summary

RICH_PROJECT = f"""
name: Beta
description: A richer project
repositories: [https://github.com/example/beta, https://github.com/example/beta-site]
readers: [alice@example.org, group:Readers]
configurators: [bob@example.org]
artifact_configurations:
  installer:
    name: Installer
    description: Windows installer
    xml: |
{"".join("      " + line + chr(10) for line in MSI_XML.splitlines())}
  default:
    name: Default jar
default_artifact_configuration: installer
ci_users:
  beta-ci:
    description: Beta CI
    notification_email: beta@example.org
    signing_policies: [release-signing, manual]
signing_policies:
  test-signing: null
  release-signing:
    approval:
      approvers: [alice@example.org, group:Leads]
      required: 2
    origin_verification:
      branches: [main, 'release/*']
      build_definitions: [.github/workflows/release.yml]
  manual:
    name: Manual
    purpose: test
    certificate: test-cert
    submitters: [bob@example.org]
"""


def rich_fake() -> FakeSignPath:
    fake = standard_fake()
    fake.add_user("Alice", "alice@example.org")
    fake.add_user("Bob", "bob@example.org")
    fake.add_group("Readers")
    fake.add_group("Leads")
    return fake


def test_apply_converges(make_config: ConfigFactory) -> None:
    fake = rich_fake()
    root = make_config({"projects/tech.alpha.yaml": project_yaml(), "projects/tech.beta.yaml": RICH_PROJECT})
    follow_up = converge(fake, root)
    assert follow_up.actions == [], summary(follow_up)
    assert follow_up.drift == []

    beta = fake.project("tech.beta")
    assert [(a.slug, a.is_default) for a in beta.acs] == [("installer", True), ("default", False)]
    assert beta.acs[0].name == "Installer"
    assert beta.acs[0].description == "Windows installer"
    assert beta.tbs_links == ["GitHub.com"]
    release = next(p for p in beta.policies if p.slug == "release-signing")
    assert release.required == 2
    assert release.tbsv is not None
    assert release.tbsv["originPolicyVerification"]["allowedBuildDefinitions"] == [
        ".github/workflows/release.yml"
    ]
    beta_ci = next(u for u in fake.ci_users if u.name == "beta-ci")
    assert release.submitter_ids == [beta_ci.id]
    assert beta_ci.email == "beta@example.org"

    # idempotency: nothing is written on a second run
    writes = len(fake.writes())
    plan = plan_for(fake, root)
    assert apply_plan(fake, plan).outcomes == []
    assert len(fake.writes()) == writes


def test_initial_artifact_configuration_is_renamed(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    converge(fake, root)
    alpha = fake.project("tech.alpha")
    assert [(a.slug, a.name) for a in alpha.acs] == [("default", "Default")]


def test_updates_converge(make_config: ConfigFactory) -> None:
    fake = rich_fake()
    root = make_config({"projects/tech.beta.yaml": RICH_PROJECT})
    converge(fake, root)
    changed = (
        RICH_PROJECT.replace("A richer project", "Changed")
        .replace("required: 2", "required: 1")
        .replace("Beta CI", "CI of beta")
        .replace("default_artifact_configuration: installer", "default_artifact_configuration: default")
        .replace("<authenticode-sign />", "<authenticode-sign/>")
    )
    (root / "projects/tech.beta.yaml").write_text(changed.lstrip("\n"))
    plan = plan_for(fake, root)
    assert sorted(summary(plan)) == [
        "set-default artifact configuration default",
        "update CI user beta-ci",
        "update artifact configuration installer",
        "update project tech.beta",
        "update signing policy release-signing",
    ]
    result = apply_plan(fake, plan)
    assert result.success
    assert plan_for(fake, root).actions == []


def test_declared_inactive_objects(make_config: ConfigFactory) -> None:
    fake = rich_fake()
    root = make_config({"projects/tech.beta.yaml": RICH_PROJECT})
    converge(fake, root)
    inactive = RICH_PROJECT.replace(
        "    submitters: [bob@example.org]", "    submitters: [bob@example.org]\n    active: false"
    )
    inactive = inactive.replace(
        "  default:\n    name: Default jar", "  default:\n    name: Default jar\n    active: false"
    )
    (root / "projects/tech.beta.yaml").write_text(inactive.lstrip("\n"))
    plan = plan_for(fake, root)
    assert sorted(summary(plan)) == [
        "deactivate artifact configuration default",
        "deactivate signing policy manual",
    ]
    assert apply_plan(fake, plan).success
    assert plan_for(fake, root).actions == []


def test_prune_converges(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    fake.projects.append(
        Project("Old", "old", None, [], [], [], acs=[AC("x", "x", None, MSI_XML, is_default=True)])
    )
    fake.add_ci_user("stray")
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    follow_up = converge(fake, root, prune=True)
    assert follow_up.actions == []
    assert follow_up.drift == []
    assert not fake.project("old").active
    assert not next(u for u in fake.ci_users if u.name == "stray").active
    assert fake.admin.active


def test_stale_plan_is_detected_and_isolated(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(),
            "projects/tech.beta.yaml": project_yaml("Beta", "beta-ci"),
        }
    )
    converge(fake, root)
    for name in ("alpha", "beta"):
        (root / f"projects/tech.{name}.yaml").write_text(
            project_yaml(name.title(), f"{name}-ci", extra="description: new\n").lstrip("\n")
        )
    plan = plan_for(fake, root)
    assert sorted(summary(plan)) == ["update project tech.alpha", "update project tech.beta"]
    fake.project("tech.alpha").repository_urls.append(
        "https://example.org/changed-in-ui"
    )  # concurrent change
    result = apply_plan(fake, plan)
    assert [o.action.project for o in result.skipped] == ["tech.alpha"]
    assert "changed in SignPath since the plan was computed" in (result.skipped[0].error or "")
    assert [o.action.project for o in result.applied] == ["tech.beta"]
    assert fake.project("tech.alpha").description is None


def test_failure_skips_rest_of_project(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    root = make_config(
        {
            "projects/tech.alpha.yaml": project_yaml(),
            "projects/tech.beta.yaml": project_yaml("Beta", "beta-ci"),
        }
    )
    plan = plan_for(fake, root)
    fake.fail_next.append(("POST", r"/Projects/tech\.alpha/SigningPolicies$", 500))
    result = apply_plan(fake, plan)
    failed = [(o.action.project, o.action.name) for o in result.failed]
    assert failed == [("tech.alpha", "test-signing")]
    assert [(o.action.project, o.action.name) for o in result.skipped] == [("tech.alpha", "release-signing")]
    assert all(o.ok for o in result.outcomes if o.action.project == "tech.beta")
    # re-running converges
    assert converge(fake, root).actions == []


def test_new_ci_user_token_goes_to_sink(make_config: ConfigFactory, tmp_path: Path) -> None:
    fake = standard_fake()
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    plan = plan_for(fake, root)
    sink = FileSink(tmp_path / "tokens.jsonl")
    result = Executor(client_for(fake), plan, sink).run()
    assert result.success
    path = tmp_path / "tokens.jsonl"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    record = json.loads(path.read_text())
    ci = next(u for u in fake.ci_users if u.name == "alpha-ci")
    assert record["token"] == ci.token
    assert record["project"] == "tech.alpha"


def test_file_sink_refuses_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "tokens.jsonl"
    path.write_text("")
    path.chmod(0o644)
    with pytest.raises(TokenSinkError):
        FileSink(path)
    path.chmod(0o600)
    FileSink(path).deliver("p", "ci", SecretStr("t"))


def test_pipeline_policies_apply(make_config: ConfigFactory) -> None:
    org = """
    organization_id: 11111111-2222-3333-4444-555555555555
    default_profile: standard
    pipeline_policies:
      gh:
        name: GitHub hosted
        definition:
          github-build-policies: {version: '1.0', runners: {require_github_hosted: true}}
    """
    extra = "signing_policies:\n  release-signing:\n    pipeline_policies: {gh: enforce}\n"
    fake = standard_fake()
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra=extra)}, org=org)
    assert converge(fake, root).actions == []
    release = next(p for p in fake.project("tech.alpha").policies if p.slug == "release-signing")
    assert release.tbsv is not None
    assert release.tbsv["pipelinePolicyVerification"]["pipelinePolicies"] == [
        {"pipelinePolicySlug": "gh", "level": "Enforce"}
    ]


def test_pipeline_policies_feature_disabled(make_config: ConfigFactory) -> None:
    org = """
    organization_id: 11111111-2222-3333-4444-555555555555
    default_profile: standard
    pipeline_policies:
      gh: {name: GitHub hosted, definition: {github-build-policies: {version: '1.0'}}}
    """
    fake = standard_fake()
    fake.pipeline_policies_enabled = False
    root = make_config({"projects/tech.alpha.yaml": project_yaml()}, org=org)
    result = apply_plan(fake, plan_for(fake, root))
    assert [o.action.kind for o in result.failed] == [Kind.PIPELINE_POLICY]
    assert "feature is disabled" in (result.failed[0].error or "")
    assert all(o.ok for o in result.outcomes if o.action.kind is not Kind.PIPELINE_POLICY)


def test_reactivates_instead_of_recreating(make_config: ConfigFactory) -> None:
    fake = standard_fake()
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    converge(fake, root)
    fake.project("tech.alpha").active = False
    next(u for u in fake.ci_users if u.name == "alpha-ci").active = False
    plan = plan_for(fake, root)
    assert {(a.op, a.kind) for a in plan.actions} == {
        (Op.ACTIVATE, Kind.PROJECT),
        (Op.ACTIVATE, Kind.CI_USER),
    }
    assert converge(fake, root).actions == []
    assert len([u for u in fake.ci_users if u.name == "alpha-ci"]) == 1


# ---------------------------------------------------------------------------- CLI


@pytest.fixture
def cli_fake(monkeypatch: pytest.MonkeyPatch) -> FakeSignPath:
    fake = standard_fake()
    monkeypatch.setattr(cli_module, "transport_factory", fake.transport)
    monkeypatch.setenv("SIGNPATH_API_TOKEN", fake.admin.token)
    return fake


def test_cli_apply_requires_yes_when_not_interactive(
    cli_fake: FakeSignPath, make_config: ConfigFactory
) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    result = CliRunner().invoke(cli, ["-c", str(root), "apply", "--discard-new-ci-tokens"])
    assert result.exit_code == 1
    assert "pass --yes" in result.output
    assert cli_fake.writes() == []


def test_cli_apply_requires_token_destination(cli_fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    result = CliRunner().invoke(cli, ["-c", str(root), "apply", "--yes"])
    assert result.exit_code == 1
    assert "--ci-token-file" in result.output
    assert cli_fake.writes() == []


def test_cli_apply_with_token_file(
    cli_fake: FakeSignPath, make_config: ConfigFactory, tmp_path: Path
) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    tokens = tmp_path / "new.tokens.jsonl"
    result = CliRunner().invoke(cli, ["-c", str(root), "apply", "--yes", "--ci-token-file", str(tokens)])
    assert result.exit_code == 0, result.output
    assert "Applied 5 change(s), 0 failed, 0 skipped." in result.output
    ci = next(u for u in cli_fake.ci_users if u.name == "alpha-ci")
    assert ci.token not in result.output
    assert json.loads(tokens.read_text())["token"] == ci.token
    # second run: nothing to do
    result = CliRunner().invoke(cli, ["-c", str(root), "apply", "--yes"])
    assert result.exit_code == 0
    assert "No changes" in result.output


def test_cli_apply_discard(cli_fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    result = CliRunner().invoke(cli, ["-c", str(root), "apply", "--yes", "--discard-new-ci-tokens"])
    assert result.exit_code == 0, result.output
    assert "Discarded the API tokens of new CI users: alpha-ci" in result.output


def test_cli_apply_refuses_on_plan_errors(cli_fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra="readers: [ghost@example.org]\n")})
    result = CliRunner().invoke(cli, ["-c", str(root), "apply", "--yes", "--discard-new-ci-tokens"])
    assert result.exit_code == 1
    assert cli_fake.writes() == []


def test_cli_rotate_token(cli_fake: FakeSignPath, make_config: ConfigFactory, tmp_path: Path) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    CliRunner().invoke(cli, ["-c", str(root), "apply", "--yes", "--discard-new-ci-tokens"])
    ci = next(u for u in cli_fake.ci_users if u.name == "alpha-ci")
    old = ci.token
    tokens = tmp_path / "rotated.jsonl"
    result = CliRunner().invoke(
        cli, ["-c", str(root), "ci-user", "rotate-token", "ALPHA-CI", "--yes", "--ci-token-file", str(tokens)]
    )
    assert result.exit_code == 0, result.output
    assert ci.token != old
    assert ci.token not in result.output
    record = json.loads(tokens.read_text())
    assert record == {**record, "ci_user": "alpha-ci", "project": "tech.alpha", "token": ci.token}


def test_cli_rotate_token_refuses_own_token(
    cli_fake: FakeSignPath, make_config: ConfigFactory, tmp_path: Path
) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    result = CliRunner().invoke(
        cli,
        [
            "-c",
            str(root),
            "ci-user",
            "rotate-token",
            cli_fake.admin.name,
            "--yes",
            "--ci-token-file",
            str(tmp_path / "t"),
        ],
    )
    assert result.exit_code == 1
    assert "refusing to rotate the token used by this session" in result.output

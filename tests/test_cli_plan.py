from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from signpath_manager import cli as cli_module
from signpath_manager.cli import cli

from .conftest import ConfigFactory, project_yaml
from .fake_signpath import FakeSignPath
from .helpers import standard_fake


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSignPath:
    fake = standard_fake()
    monkeypatch.setattr(cli_module, "transport_factory", fake.transport)
    monkeypatch.setenv("SIGNPATH_API_TOKEN", fake.admin.token)
    return fake


def run(*args: str, input: str | None = None) -> tuple[int, str]:
    result = CliRunner().invoke(cli, list(args), input=input, catch_exceptions=False)
    return result.exit_code, result.output


def test_plan_text_and_detailed_exitcode(fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    code, out = run("-c", str(root), "plan", "--detailed-exitcode")
    assert code == 2, out
    assert "+ create project tech.alpha" in out
    assert "a new API token will be issued" in out
    assert fake.admin.token not in out
    assert not fake.writes()


def test_plan_json(fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    code, out = run("-c", str(root), "plan", "-o", "json")
    assert code == 0, out
    data = json.loads(out)
    assert data["organization"]["id"] == "11111111-2222-3333-4444-555555555555"
    assert data["summary"] == {"create": 4, "link": 1}
    assert data["actions"][0]["kind"] == "project"


def test_plan_errors_exit_1(fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra="readers: [nobody@example.org]\n")})
    code, out = run("-c", str(root), "plan")
    assert code == 1
    assert "no interactive user with email" in out


def test_missing_token(
    fake: FakeSignPath, make_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SIGNPATH_API_TOKEN")
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    code, out = run("-c", str(root), "plan")
    assert code == 1
    assert "no API token" in out


def test_token_from_file_and_stdin(
    fake: FakeSignPath, make_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SIGNPATH_API_TOKEN")
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    token_file = tmp_path / "token"
    token_file.write_text(fake.admin.token + "\n")
    token_file.chmod(0o600)
    code, out = run("-c", str(root), "--token-file", str(token_file), "plan")
    assert code == 0, out
    assert "accessible by group" not in out
    code, out = run("-c", str(root), "--token-stdin", "plan", input=fake.admin.token + "\n")
    assert code == 0, out


def test_bad_token(fake: FakeSignPath, make_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNPATH_API_TOKEN", "wrong")
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    code, out = run("-c", str(root), "plan")
    assert code == 1
    assert "authentication failed" in out
    assert "wrong" not in out.replace("wrong token", "")


def test_org_only_and_project_are_exclusive(fake: FakeSignPath, make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    result = CliRunner().invoke(cli, ["-c", str(root), "plan", "--org-only", "-p", "tech.alpha"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output

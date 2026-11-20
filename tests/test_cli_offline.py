from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from sealform.cli import cli
from sealform.config.schema import SCHEMAS, json_schema

from .conftest import ConfigFactory, project_yaml

REPO = Path(__file__).parent.parent


def test_validate_ok(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml()})
    result = CliRunner().invoke(cli, ["-c", str(root), "validate"])
    assert result.exit_code == 0, result.output
    assert "Configuration is valid" in result.output


def test_validate_reports_errors_with_location(make_config: ConfigFactory) -> None:
    root = make_config({"projects/tech.alpha.yaml": project_yaml(extra="colour: blue\n")})
    result = CliRunner().invoke(cli, ["-c", str(root), "validate"])
    assert result.exit_code == 1
    assert "tech.alpha.yaml:6:1: error: colour: unknown key" in result.output


def test_committed_json_schemas_are_up_to_date() -> None:
    for kind in SCHEMAS:
        committed = json.loads((REPO / "schemas" / f"{kind}.schema.json").read_text())
        assert committed == json_schema(kind), f"run: sealform schema -o schemas ({kind})"

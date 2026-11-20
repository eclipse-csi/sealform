from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

ORG_ID = "11111111-2222-3333-4444-555555555555"

JAR_XML = """<?xml version="1.0" encoding="utf-8"?>
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <jar-file>
    <jar-sign />
  </jar-file>
</artifact-configuration>
"""

MSI_XML = """<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <msi-file>
    <authenticode-sign />
  </msi-file>
</artifact-configuration>
"""

DEFAULT_ORG = f"""
organization_id: {ORG_ID}
default_profile: standard
"""

DEFAULT_PROFILE = """
trusted_build_systems: [GitHub.com]
artifact_configurations:
  default:
    name: Default
    shared: jar
signing_policies:
  test-signing:
    name: Test signing
    purpose: test
    certificate: test-cert
    trusted_build_system_verification: true
    origin_verification:
      branches: ['*']
  release-signing:
    name: Release signing
    purpose: release
    certificate: release-cert
    trusted_build_system_verification: true
    origin_verification:
      branches: [main]
"""

ConfigFactory = Callable[..., Path]


@pytest.fixture
def make_config(tmp_path: Path) -> ConfigFactory:
    """Create a configuration root. ``files`` maps relative paths to (dedented) contents."""

    def factory(
        files: dict[str, str] | None = None,
        *,
        org: str | None = DEFAULT_ORG,
        profile: str | None = DEFAULT_PROFILE,
        library: dict[str, str] | None = None,
    ) -> Path:
        root = tmp_path / "config"
        root.mkdir(exist_ok=True)
        all_files: dict[str, str] = {}
        if org is not None:
            all_files["organization.yaml"] = org
        if profile is not None:
            all_files["profiles/standard.yaml"] = profile
        for name, xml in (library if library is not None else {"jar": JAR_XML}).items():
            all_files[f"artifact-configurations/{name}.xml"] = xml
        all_files.update(files or {})
        for rel, content in all_files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(content).lstrip("\n"))
        return root

    return factory


def project_yaml(name: str = "Alpha", ci: str = "alpha-ci", extra: str = "") -> str:
    return textwrap.dedent(
        f"""
            name: {name}
            repositories: [https://github.com/example/alpha]
            ci_users:
              {ci}:
                signing_policies: [test-signing, release-signing]
            """
    ) + textwrap.dedent(extra)

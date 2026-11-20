from sealform.config.merge import merge_profile


def test_maps_merge_deeply() -> None:
    profile = {"signing_policies": {"release": {"certificate": "c", "purpose": "release"}}}
    project = {"signing_policies": {"release": {"approval": {"approvers": ["a@x.org"]}}}}
    merged = merge_profile(profile, project)
    assert merged["signing_policies"]["release"] == {
        "certificate": "c",
        "purpose": "release",
        "approval": {"approvers": ["a@x.org"]},
    }


def test_lists_and_scalars_replace() -> None:
    profile = {"trusted_build_systems": ["a", "b"], "default_artifact_configuration": "x"}
    project = {"trusted_build_systems": ["c"], "default_artifact_configuration": "y"}
    merged = merge_profile(profile, project)
    assert merged["trusted_build_systems"] == ["c"]
    assert merged["default_artifact_configuration"] == "y"


def test_null_removes_inherited_entry() -> None:
    profile = {"signing_policies": {"test": {"certificate": "t"}, "release": {"certificate": "r"}}}
    project = {"signing_policies": {"test": None}}
    merged = merge_profile(profile, project)
    assert merged["signing_policies"] == {"release": {"certificate": "r"}}


def test_null_removes_nested_field() -> None:
    profile = {"signing_policies": {"r": {"certificate": "r", "origin_verification": {"branches": ["main"]}}}}
    project = {"signing_policies": {"r": {"origin_verification": None}}}
    assert merge_profile(profile, project)["signing_policies"]["r"] == {"certificate": "r"}


def test_artifact_configuration_sources_replace_as_group() -> None:
    profile = {"artifact_configurations": {"default": {"name": "Default", "file": "jar.xml"}}}
    project = {"artifact_configurations": {"default": {"xml": "<x/>"}}}
    merged = merge_profile(profile, project)
    assert merged["artifact_configurations"]["default"] == {"name": "Default", "xml": "<x/>"}


def test_artifact_configuration_override_without_source_keeps_inherited_source() -> None:
    profile = {"artifact_configurations": {"default": {"name": "Default", "file": "jar.xml"}}}
    project = {"artifact_configurations": {"default": {"name": "Renamed"}}}
    merged = merge_profile(profile, project)
    assert merged["artifact_configurations"]["default"] == {"name": "Renamed", "file": "jar.xml"}


def test_profile_key_is_dropped_and_inputs_untouched() -> None:
    profile = {"readers": ["group:x"]}
    project = {"profile": "standard", "name": "A"}
    merged = merge_profile(profile, project)
    assert merged == {"readers": ["group:x"], "name": "A"}
    assert profile == {"readers": ["group:x"]}
    assert project == {"profile": "standard", "name": "A"}


def test_no_profile() -> None:
    assert merge_profile(None, {"name": "A", "description": None}) == {"name": "A"}


TEMPLATES = {
    "t/release.yaml": {
        "purpose": "release",
        "certificate": "rel",
        "trusted_build_system_verification": True,
        "origin_verification": {"branches": ["**"], "build_definitions": ["release.yml"]},
    },
    "t/test.yaml": {"purpose": "test", "certificate": "tst"},
}


def load(path: str, _layer: str, _policy: str) -> dict[str, object] | None:
    return TEMPLATES.get(path)


def test_template_is_the_bottom_layer() -> None:
    project = {
        "signing_policies": {
            "release": {"extends": "t/release.yaml", "origin_verification": {"branches": ["main", "v*"]}}
        }
    }
    policy = merge_profile(None, project, templates=load)["signing_policies"]["release"]
    assert policy == {
        "purpose": "release",
        "certificate": "rel",
        "trusted_build_system_verification": True,
        "origin_verification": {"branches": ["main", "v*"], "build_definitions": ["release.yml"]},
    }


def test_template_from_profile_then_profile_then_project() -> None:
    profile = {"signing_policies": {"release": {"extends": "t/release.yaml", "description": "from profile"}}}
    project = {"signing_policies": {"release": {"origin_verification": {"branches": ["main"]}}}}
    policy = merge_profile(profile, project, templates=load)["signing_policies"]["release"]
    assert policy["description"] == "from profile"
    assert policy["certificate"] == "rel"
    assert policy["origin_verification"]["branches"] == ["main"]


def test_project_can_switch_template_and_keep_profile_overrides() -> None:
    profile = {"signing_policies": {"release": {"extends": "t/release.yaml", "description": "from profile"}}}
    project = {"signing_policies": {"release": {"extends": "t/test.yaml"}}}
    policy = merge_profile(profile, project, templates=load)["signing_policies"]["release"]
    assert policy == {"purpose": "test", "certificate": "tst", "description": "from profile"}


def test_nulls_remove_template_values_and_extends_null_drops_the_template() -> None:
    profile = {"signing_policies": {"release": {"extends": "t/release.yaml"}}}
    project = {"signing_policies": {"release": {"origin_verification": None}}}
    policy = merge_profile(profile, project, templates=load)["signing_policies"]["release"]
    assert "origin_verification" not in policy
    assert policy["certificate"] == "rel"
    project = {"signing_policies": {"release": {"extends": None, "certificate": "own"}}}
    assert merge_profile(profile, project, templates=load)["signing_policies"]["release"] == {
        "certificate": "own"
    }


def test_policies_without_project_entries_are_resolved() -> None:
    profile = {"signing_policies": {"release": {"extends": "t/release.yaml"}, "plain": {"certificate": "c"}}}
    merged = merge_profile(profile, {"name": "A"}, templates=load)
    assert merged["signing_policies"]["release"]["certificate"] == "rel"
    assert merged["signing_policies"]["plain"] == {"certificate": "c"}
    assert merge_profile(profile, {"signing_policies": None}, templates=load).get("signing_policies") is None

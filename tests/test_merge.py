from signpath_manager.config.merge import merge_profile


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
    profile = {"artifact_configurations": {"default": {"name": "Default", "shared": "jar"}}}
    project = {"artifact_configurations": {"default": {"file": "custom.xml"}}}
    merged = merge_profile(profile, project)
    assert merged["artifact_configurations"]["default"] == {"name": "Default", "file": "custom.xml"}


def test_artifact_configuration_override_without_source_keeps_inherited_source() -> None:
    profile = {"artifact_configurations": {"default": {"name": "Default", "shared": "jar"}}}
    project = {"artifact_configurations": {"default": {"name": "Renamed"}}}
    merged = merge_profile(profile, project)
    assert merged["artifact_configurations"]["default"] == {"name": "Renamed", "shared": "jar"}


def test_profile_key_is_dropped_and_inputs_untouched() -> None:
    profile = {"readers": ["group:x"]}
    project = {"profile": "standard", "name": "A"}
    merged = merge_profile(profile, project)
    assert merged == {"readers": ["group:x"], "name": "A"}
    assert profile == {"readers": ["group:x"]}
    assert project == {"profile": "standard", "name": "A"}


def test_no_profile() -> None:
    assert merge_profile(None, {"name": "A", "description": None}) == {"name": "A"}

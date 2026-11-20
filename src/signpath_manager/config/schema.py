"""JSON Schema export of the configuration files, for editor support (yaml-language-server)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from signpath_manager.config.model import OrganizationSpec, ProfileSpec, ProjectSpec

SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "organization": (OrganizationSpec, "signpath-manager organization.yaml"),
    "profile": (ProfileSpec, "signpath-manager profile (profiles/<name>.yaml)"),
    "project": (ProjectSpec, "signpath-manager project (projects/<slug>.yaml)"),
}


def json_schema(kind: str) -> dict[str, Any]:
    model, title = SCHEMAS[kind]
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    return schema

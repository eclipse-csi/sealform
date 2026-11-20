"""JSON Schema export of the configuration files, for editor support (yaml-language-server)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from sealform.config.model import OrganizationSpec, ProfileSpec, ProjectSpec, SigningPolicySpec

SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "organization": (OrganizationSpec, "sealform organization.yaml"),
    "profile": (ProfileSpec, "sealform profile (profiles/<name>.yaml)"),
    "project": (ProjectSpec, "sealform project (projects/<slug>.yaml)"),
    "signing-policy": (SigningPolicySpec, "sealform signing policy template (signing-policies/<name>.yaml)"),
}


def json_schema(kind: str) -> dict[str, Any]:
    model, title = SCHEMAS[kind]
    schema = model.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = title
    return schema

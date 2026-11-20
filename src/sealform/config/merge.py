"""Merging profiles and signing policy templates into projects (see ADR-0003 and ADR-0015).

Rules:
* maps merge deeply by key;
* scalars and lists replace the inherited value;
* ``null`` removes an inherited map entry;
* the source keys of an artifact configuration (file, xml) replace each other as a group;
* a signing policy is layered as template, then profile entry, then project entry; its template
  is the ``extends`` of the project entry when it has one, otherwise that of the profile entry.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

AC_SOURCE_KEYS = ("file", "xml")
EXTENDS = "extends"

# (path, layer, policy name) -> content of the template at ``path`` (relative to the configuration
# root), or None when it cannot be used (the loader reports why). ``layer`` is "project" or
# "profile": the file whose ``extends`` referenced the template.
TemplateLoader = Callable[[str, str, str], dict[str, Any] | None]

_MISSING = object()


def _merge(base: Any, override: Any, path: tuple[str, ...]) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        if (
            len(path) == 2
            and path[0] == "artifact_configurations"
            and any(k in override for k in AC_SOURCE_KEYS)
        ):
            for k in AC_SOURCE_KEYS:
                result.pop(k, None)
        for key, value in override.items():
            if value is None:
                result.pop(key, None)
            elif key in result:
                result[key] = _merge(result[key], value, (*path, key))
            else:
                result[key] = copy.deepcopy(value)
        return result
    return copy.deepcopy(override)


def _without_extends(entry: Any) -> Any:
    if isinstance(entry, dict):
        return {k: v for k, v in entry.items() if k != EXTENDS}
    return entry


def _merge_policies(
    inherited: dict[str, Any] | None, own: Any, templates: TemplateLoader | None
) -> dict[str, Any] | None:
    if own is None:  # `signing_policies: null` removes every inherited policy
        return None
    own = own if isinstance(own, dict) else {}
    inherited = inherited or {}
    result: dict[str, Any] = {}
    for name in [*inherited, *(n for n in own if n not in inherited)]:
        upper = own.get(name, _MISSING)
        if upper is None:  # `<policy>: null` removes the inherited policy
            continue
        lower = inherited.get(name)
        if isinstance(upper, dict) and EXTENDS in upper:
            extends, where = upper[EXTENDS], "project"
        else:
            extends = lower.get(EXTENDS) if isinstance(lower, dict) else None
            where = "profile"
        entry: Any = {}
        if extends is not None and templates is not None:
            entry = templates(extends, where, name) or {}
        if isinstance(lower, dict):
            entry = _merge(entry, _without_extends(lower), ("signing_policies", name))
        if isinstance(upper, dict):
            entry = _merge(entry, _without_extends(upper), ("signing_policies", name))
        result[name] = entry
    return result


def merge_profile(
    profile: dict[str, Any] | None,
    project: dict[str, Any],
    *,
    templates: TemplateLoader | None = None,
) -> dict[str, Any]:
    """Return the effective project dict: ``profile`` overridden by ``project``.

    Signing policies are resolved against their templates (``extends``) with ``templates``.
    ``null`` values remove the inherited entry and are dropped from the result.
    """
    profile = dict(profile or {})
    project = dict(project)
    inherited_policies = profile.pop("signing_policies", None)
    own_policies = project.pop("signing_policies", _MISSING)
    merged: dict[str, Any] = _merge(profile, project, ())
    merged.pop("profile", None)
    if own_policies is _MISSING:
        own_policies = {}
    policies = _merge_policies(inherited_policies, own_policies, templates)
    if policies:
        merged["signing_policies"] = policies
    result: dict[str, Any] = drop_nulls(merged)
    return result


def drop_nulls(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: drop_nulls(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [drop_nulls(v) for v in node]
    return node

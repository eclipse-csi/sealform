"""Merging profiles into projects (see ADR-0003).

Rules:
* maps merge deeply by key;
* scalars and lists replace the inherited value;
* ``null`` removes an inherited map entry;
* the source keys of an artifact configuration (file, xml) replace each other as a group.
"""

from __future__ import annotations

import copy
from typing import Any

AC_SOURCE_KEYS = ("file", "xml")


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


def merge_profile(profile: dict[str, Any] | None, project: dict[str, Any]) -> dict[str, Any]:
    """Return the effective project dict: ``profile`` overridden by ``project``.

    ``null`` values in the project remove the inherited entry and are dropped from the result.
    """
    merged: dict[str, Any] = _merge(profile or {}, project, ())
    merged.pop("profile", None)
    result: dict[str, Any] = drop_nulls(merged)
    return result


def drop_nulls(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: drop_nulls(v) for k, v in node.items() if v is not None}
    if isinstance(node, list):
        return [drop_nulls(v) for v in node]
    return node

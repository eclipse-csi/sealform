"""YAML reading (with source locations) and writing."""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.scalarstring import LiteralScalarString

from signpath_manager.diagnostics import Location


class YamlLoadError(Exception):
    def __init__(self, message: str, location: Location) -> None:
        super().__init__(message)
        self.message = message
        self.location = location


@dataclass
class YamlDocument:
    """A parsed YAML document: plain Python data plus the round-trip tree for locations."""

    path: Path
    data: Any
    tree: Any

    def locate(self, path: Sequence[str | int]) -> Location:
        """Best-effort location of a dotted path inside the document."""
        return locate(self.tree, path, self.path)


def _rt_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.allow_duplicate_keys = False
    return yaml


def to_plain(node: Any) -> Any:
    """Convert ruamel round-trip containers and scalars into plain Python objects."""
    if isinstance(node, Mapping):
        return {str(k): to_plain(v) for k, v in node.items()}
    if isinstance(node, list | tuple):
        return [to_plain(v) for v in node]
    if isinstance(node, bool):
        return bool(node)
    if isinstance(node, int):
        return int(node)
    if isinstance(node, float):
        return float(node)
    if isinstance(node, str):
        return str(node)
    return node


def load_yaml_file(path: Path) -> YamlDocument:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise YamlLoadError(f"cannot read file: {e.strerror}", Location(path)) from e
    return load_yaml_text(text, path)


def load_yaml_text(text: str, path: Path) -> YamlDocument:
    try:
        tree = _rt_yaml().load(text)
    except MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        loc = Location(path, mark.line + 1, mark.column + 1) if mark else Location(path)
        problem = e.problem or str(e)
        raise YamlLoadError(f"invalid YAML: {problem}", loc) from e
    except YAMLError as e:
        raise YamlLoadError(f"invalid YAML: {e}", Location(path)) from e
    return YamlDocument(path=path, data=to_plain(tree), tree=tree)


def safe_load(text: str) -> Any:
    """Parse YAML text into plain data (no locations); raises YAMLError."""
    return to_plain(YAML(typ="safe", pure=True).load(text))


def locate(tree: Any, path: Sequence[str | int], file: Path) -> Location:
    node = tree
    best = Location(file, 1, 1) if tree is not None else Location(file)
    for part in path:
        try:
            if isinstance(node, CommentedMap) and part in node:
                line, col = node.lc.key(part)
                best = Location(file, line + 1, col + 1)
                node = node[part]
            elif isinstance(node, CommentedSeq) and isinstance(part, int) and 0 <= part < len(node):
                line, col = node.lc.item(part)
                best = Location(file, line + 1, col + 1)
                node = node[part]
            else:
                break
        except (KeyError, IndexError, TypeError, AttributeError):
            break
    return best


def _literalize(node: Any) -> Any:
    if isinstance(node, Mapping):
        out = CommentedMap()
        for k, v in node.items():
            out[k] = _literalize(v)
        return out
    if isinstance(node, list | tuple):
        seq = CommentedSeq()
        seq.extend(_literalize(v) for v in node)
        return seq
    if isinstance(node, str) and "\n" in node:
        return LiteralScalarString(node if node.endswith("\n") else node + "\n")
    return node


def dump_yaml(data: Any) -> str:
    """Serialize plain data as block-style YAML; multi-line strings use literal blocks."""
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    buf = io.StringIO()
    yaml.dump(_literalize(data), buf)
    return buf.getvalue()

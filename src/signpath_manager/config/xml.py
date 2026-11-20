"""Artifact configuration XML: normalization and local validation."""

from __future__ import annotations

from functools import cache
from importlib import resources

from lxml import etree

NAMESPACE = "http://signpath.io/artifact-configuration/v1"
ROOT_TAG = f"{{{NAMESPACE}}}artifact-configuration"


DEFAULT_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'


def normalize_xml_text(text: str) -> str:
    """Normalize what SignPath does not distinguish: BOM, line endings, trailing whitespace.

    SignPath stores artifact configuration XML verbatim (ADR-0004), so everything else counts.
    """
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip()


def stored_form(text: str) -> str:
    """The text SignPath returns after storing ``text`` (normalized).

    Verified against the sandbox: a document without an XML declaration is stored with
    ``<?xml version="1.0" encoding="utf-8"?>`` and a newline prepended; an existing
    declaration is kept as is.
    """
    text = normalize_xml_text(text)
    if not text.startswith("<?xml"):
        text = f"{DEFAULT_DECLARATION}\n{text}"
    return text


def xml_equal(live: str, desired: str) -> bool:
    return stored_form(live) == stored_form(desired)


XS = "http://www.w3.org/2001/XMLSchema"
_PATTERN_PROBE = (
    f'<xs:schema xmlns:xs="{XS}"><xs:simpleType name="t"><xs:restriction base="xs:string">'
    '<xs:pattern value=""/></xs:restriction></xs:simpleType></xs:schema>'
)


def _pattern_compiles(value: str) -> bool:
    probe = etree.fromstring(_PATTERN_PROBE.encode())
    probe.find(f".//{{{XS}}}pattern").set("value", value)  # type: ignore[union-attr]
    try:
        etree.XMLSchema(probe)
    except etree.XMLSchemaParseError:
        return False
    return True


def _adapt_patterns(doc: etree._Element) -> None:
    """SignPath's XSD uses .NET regular expressions; adapt them to XSD regex semantics.

    XSD patterns are implicitly anchored, so ``^...$`` anchors are dropped. Facets that
    still do not compile are removed: the server validates them anyway.
    """
    for pattern in doc.iter(f"{{{XS}}}pattern"):
        value = pattern.get("value", "")
        if value.startswith("^") and value.endswith("$") and len(value) >= 2:
            value = value[1:-1]
        if _pattern_compiles(value):
            pattern.set("value", value)
        else:
            parent = pattern.getparent()
            if parent is not None:
                parent.remove(pattern)


@cache
def _schema() -> etree.XMLSchema:
    data = resources.files("signpath_manager.resources").joinpath("artifact-configuration-v1.xsd")
    doc = etree.fromstring(data.read_bytes(), parser=etree.XMLParser(resolve_entities=False, no_network=True))
    _adapt_patterns(doc)
    return etree.XMLSchema(doc)


def validate_xml(text: str) -> list[str]:
    """Return a list of problems (empty when the XML is valid)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(normalize_xml_text(text).encode("utf-8"), parser=parser)
    except etree.XMLSyntaxError as e:
        return [f"XML is not well-formed: {e.msg} (line {e.lineno})"]
    if root.tag != ROOT_TAG:
        return [f'root element must be <artifact-configuration xmlns="{NAMESPACE}">']
    schema = _schema()
    if schema.validate(root):
        return []
    problems = []
    for err in schema.error_log:  # type: ignore[attr-defined]  # lxml-stubs lacks __iter__
        message = err.message.replace(f"{{{NAMESPACE}}}", "")
        problems.append(f"line {err.line}: {message}")
    return problems

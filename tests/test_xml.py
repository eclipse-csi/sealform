from signpath_manager.config.xml import normalize_xml_text, stored_form, validate_xml, xml_equal

from .conftest import JAR_XML


def test_valid_xml() -> None:
    assert validate_xml(JAR_XML) == []


def test_not_well_formed() -> None:
    problems = validate_xml("<artifact-configuration")
    assert len(problems) == 1
    assert "not well-formed" in problems[0]


def test_wrong_root() -> None:
    assert "root element" in validate_xml("<foo/>")[0]


def test_schema_violation() -> None:
    xml = '<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1"><bogus-file/></artifact-configuration>'
    problems = validate_xml(xml)
    assert problems
    assert "bogus-file" in problems[0]


def test_xml_declaration_with_encoding_is_accepted() -> None:
    assert validate_xml('<?xml version="1.0" encoding="utf-8" ?>\n' + JAR_XML.split("\n", 1)[1]) == []


def test_parameters_are_accepted() -> None:
    xml = """<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <parameters>
    <parameter name="version" required="true" />
  </parameters>
  <zip-file>
    <pe-file path="app-${version}.exe"><authenticode-sign /></pe-file>
  </zip-file>
</artifact-configuration>"""
    assert validate_xml(xml) == []


def test_normalization_only_touches_bom_newlines_and_trailing_whitespace() -> None:
    assert normalize_xml_text("﻿<a>\r\n  <b/>\r\n</a>\n\n  ") == "<a>\n  <b/>\n</a>"
    assert xml_equal("<a>\n</a>\n", "<a>\r\n</a>")
    assert not xml_equal("<a> </a>", "<a></a>")
    assert not xml_equal("<a><!-- c --></a>", "<a></a>")


def test_missing_declaration_matches_what_signpath_stores() -> None:
    body = '<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1"/>'
    stored = '<?xml version="1.0" encoding="utf-8"?>\n' + body
    assert stored_form(body) == stored
    assert xml_equal(stored, body)
    # an explicit, different declaration is kept and therefore differs
    assert not xml_equal(stored, '<?xml version="1.0" encoding="utf-8" ?>\n' + body)

from __future__ import annotations

import pytest

from sealform.config.yamlio import dump_yaml, safe_load

TRICKY = [
    "single line",
    "a\r\nb\r\n",  # Windows line endings, e.g. text pasted into the SignPath web UI
    "a\r\nb",
    "a\rb",
    "a\nb",  # no final newline
    "a\nb\n",
    "a\nb\n\n",  # several final newlines
    "a  \nb \n",  # trailing spaces
    "  indented first line\nb\n",
    "a\tb\nc",
    "",
    " leading and trailing spaces ",
    '<?xml version="1.0" ?>\n<a: b>\n# not a comment\n',
]


@pytest.mark.parametrize("text", TRICKY)
def test_dump_yaml_round_trips_text_exactly(text: str) -> None:
    data = {"description": text, "nested": {"list": [text]}}
    assert safe_load(dump_yaml(data)) == data


def test_multi_line_text_uses_literal_blocks_when_exact() -> None:
    assert "description: |-\n  a\n  b\n" in dump_yaml({"description": "a\nb"})
    assert 'description: "a\\r\\nb"' in dump_yaml({"description": "a\r\nb"})

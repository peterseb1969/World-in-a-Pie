"""The CSV formula escape: spreadsheets never run a WIP-written cell, and
WIP's own importers get back exactly what was exported."""

import pytest

from wip_auth.csv_cells import escape_cell, unescape_cell

CORPUS = [
    "=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "=HYPERLINK(\"x\")",
    "'=1", "''", "''=", "'", "'90s", "plain", "", "a=b", "-",
    "x-1", "it's", "'''x",
]


@pytest.mark.parametrize("value", CORPUS)
def test_round_trip_is_exact(value):
    assert unescape_cell(escape_cell(value)) == value


@pytest.mark.parametrize("value", ["=1+1", "+1", "-1", "@x", "\tx", "\rx"])
def test_a_formula_leading_string_is_escaped(value):
    assert escape_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["plain", "'90s", "it's", "", "a=b", "x-1"])
def test_an_ordinary_string_is_left_alone(value):
    assert escape_cell(value) == value
    assert unescape_cell(value) == value


@pytest.mark.parametrize("value", [-42, -1.5, 0, None, True, 3])
def test_non_strings_are_untouched(value):
    assert escape_cell(value) is value
    assert unescape_cell(value) is value


def test_no_escaped_cell_starts_with_a_formula_character():
    for value in CORPUS:
        escaped = escape_cell(value)
        assert not (isinstance(escaped, str) and escaped[:1] in set("=+-@\t\r")), value

"""CSV cells that a spreadsheet cannot run as formulas (CWE-1236).

A spreadsheet opening a CSV runs a cell that starts with '=', '+', '-' or '@'
(and, in some, tab or carriage return) as a formula, so a document value like
``=HYPERLINK(...)`` executes on the analyst's machine. Every CSV WIP writes
escapes such a string cell by prefixing an apostrophe, which spreadsheets
treat as "text follows". Numbers and other non-strings are written as they
are: a negative number is a number, not a formula.

The escape is reversible, and WIP's own CSV importers reverse it, so export
then import gives back the original values:

- ``escape_cell`` prefixes ``'`` to a string that starts with a formula
  character, and to one that already starts with ``'`` followed by a
  formula character or another ``'`` (so that string survives unescaping).
- ``unescape_cell`` removes one leading ``'`` only when the next character is
  a formula character or ``'``. An ordinary ``'90s`` is never touched either
  way.

libs/wip-archive carries an identical copy (it ships as a wheel without
wip-auth); document-store's tests hold the two to the same behaviour.
"""

from typing import Any

FORMULA_LEADERS = frozenset("=+-@\t\r")


def escape_cell(value: Any) -> Any:
    """The value to write to a CSV cell."""
    if not isinstance(value, str) or not value:
        return value
    if value[0] in FORMULA_LEADERS:
        return "'" + value
    if value[0] == "'" and len(value) > 1 and (value[1] in FORMULA_LEADERS or value[1] == "'"):
        return "'" + value
    return value


def unescape_cell(value: Any) -> Any:
    """The original value of a CSV cell that escape_cell wrote."""
    if (
        isinstance(value, str)
        and len(value) > 1
        and value[0] == "'"
        and (value[1] in FORMULA_LEADERS or value[1] == "'")
    ):
        return value[1:]
    return value

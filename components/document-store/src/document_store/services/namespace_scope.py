"""Joining a namespace condition onto a Mongo query."""

from typing import Any


def and_namespace(query: dict[str, Any], condition: dict[str, Any] | None) -> dict[str, Any]:
    """Add a namespace condition to a query without replacing the one it
    already carries.

    Two conditions on the same field hold together, so a query that already
    constrains ``namespace`` — a caller's own filter on the field, or an
    explicit namespace — joins the new condition under ``$and``. A plain
    ``dict.update`` drops the earlier condition instead: the caller's
    ``namespace`` filter disappeared behind the permission clause, and the
    answer was rows from a namespace the query had excluded. The same ANDing
    ``_build_query`` applies to two filters on one field, on the axis the
    permission filter enters.
    """
    for key, value in (condition or {}).items():
        if key in query:
            query.setdefault("$and", []).append({key: value})
        else:
            query[key] = value
    return query

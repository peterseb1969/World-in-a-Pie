"""What a terminology import will write, worked out before it writes.

The import reads a whole file, checks every term item, and only then
creates anything: a malformed item refuses the import with nothing
written. This module holds the pure part of that — turning term items
into create requests, ordering them so a parent exists before its
children, ordering deprecations so a replacement is still active when a
term is pointed at it, and naming why a relation cannot be imported.

A parent and a replacement travel by VALUE (parent_term_value,
replaced_by_term_value): an import mints fresh term ids, so an id from the
exporting terminology names nothing in the imported one. The legacy
parent_term_id is still accepted and must then name a term of the
destination terminology, as on every term write.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models.api_models import CreateTermRequest
from ..models.term import TermName, TermTranslation

TERM_STATUSES = ("active", "deprecated", "inactive")

# The reason recorded for a term the file marks deprecated without one
# (a CSV carries no reason column).
IMPORTED_DEPRECATION_REASON = "Deprecated in the imported source"


@dataclass
class ImportItem:
    """One term item of an import file, checked."""

    index: int
    request: CreateTermRequest
    status: str = "active"
    reason: str | None = None
    parent_value: str | None = None
    replaced_by_value: str | None = None
    replaced_by_id: str | None = None


def _text(term_data: dict[str, Any], key: str, index: int) -> str | None:
    value = term_data.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"terms[{index}].{key} must be a string")
    return value or None


def _create_request(index: int, value: str, term_data: dict[str, Any]) -> CreateTermRequest:
    try:
        return CreateTermRequest(
            value=value,
            names=[TermName(**n) for n in term_data.get("names") or []],
            aliases=term_data.get("aliases") or [],
            label=term_data.get("label"),
            description=term_data.get("description"),
            sort_order=term_data.get("sort_order", index),
            parent_term_id=_text(term_data, "parent_term_id", index),
            translations=[TermTranslation(**t) for t in term_data.get("translations") or []],
            metadata=term_data.get("metadata") or {},
        )
    except (ValidationError, TypeError) as e:
        raise ValueError(f"terms[{index}] ({value!r}) is not a valid term: {e}") from e


def parse_item(index: int, term_data: Any) -> ImportItem:
    """One term item as a create request plus its lifecycle; ValueError
    names the item when it cannot be imported."""
    if not isinstance(term_data, dict):
        raise ValueError(f"terms[{index}] must be an object")
    value = term_data.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"terms[{index}] has no value")
    status = term_data.get("status") or "active"
    if status not in TERM_STATUSES:
        raise ValueError(f"terms[{index}].status must be one of {', '.join(TERM_STATUSES)}, not {status!r}")
    return ImportItem(
        index=index,
        request=_create_request(index, value, term_data),
        status=status,
        reason=_text(term_data, "deprecated_reason", index),
        parent_value=_text(term_data, "parent_term_value", index),
        replaced_by_value=_text(term_data, "replaced_by_term_value", index),
        replaced_by_id=_text(term_data, "replaced_by_term_id", index),
    )


def parse_items(terms_data: Any) -> list[ImportItem]:
    """Every term item checked, before anything is written."""
    if not isinstance(terms_data, list):
        raise ValueError("'terms' must be a list")
    return [parse_item(i, t) for i, t in enumerate(terms_data)]


def parent_levels(items: list[ImportItem]) -> list[list[ImportItem]]:
    """The items grouped so every item comes after the item it names as
    parent in the same file: level 0 names no parent of the file, level 1
    names a level-0 item, and so on. A parent cycle refuses the import."""
    by_value = {item.request.value: item for item in items}
    depth: dict[str, int] = {}

    def depth_of(item: ImportItem, path: tuple[str, ...]) -> int:
        value = item.request.value
        if value in depth:
            return depth[value]
        parent = by_value.get(item.parent_value) if item.parent_value else None
        if parent is None:
            depth[value] = 0
            return 0
        if parent.request.value in path:
            raise ValueError(f"terms form a parent cycle: {' -> '.join((*path, parent.request.value))}")
        depth[value] = depth_of(parent, (*path, value)) + 1
        return depth[value]

    levels: dict[int, list[ImportItem]] = {}
    for item in items:
        levels.setdefault(depth_of(item, (item.request.value,)), []).append(item)
    return [levels[d] for d in sorted(levels)]


def deprecation_order(items: list[ImportItem]) -> list[ImportItem]:
    """Deprecated items ordered so a term is deprecated before the term
    it names as replacement: a replacement must be active when it is
    named, and in the source it was."""
    deprecated = {i.request.value: i for i in items if i.status == "deprecated"}
    ordered: list[ImportItem] = []
    seen: set[str] = set()

    def visit(item: ImportItem) -> None:
        if item.request.value in seen:
            return
        seen.add(item.request.value)
        ordered.append(item)
        replacement = deprecated.get(item.replaced_by_value or "")
        if replacement is not None:
            visit(replacement)

    # A term no other deprecated term names goes first, so every chain
    # starts at its head.
    named = {i.replaced_by_value for i in deprecated.values()}
    for item in sorted(deprecated.values(), key=lambda i: i.request.value in named):
        visit(item)
    return ordered


def relation_drop_reason(
    relation: Any, terminology_id: str, value_to_id: dict[str, str],
) -> str | None:
    """Why a relation of the file cannot be created in the imported
    terminology, or None when both ends resolve in it."""
    if not isinstance(relation, dict):
        return "not an object"
    if not relation.get("relation_type"):
        return "no relation_type"
    for end in ("source", "target"):
        foreign = relation.get(f"{end}_terminology_id")
        if foreign and foreign != terminology_id:
            return f"its {end} is a term of another terminology; relations across terminologies are not imported"
        value = relation.get(f"{end}_term_value")
        if not value or value not in value_to_id:
            return f"its {end} term {value!r} is not a term of the imported terminology"
    return None

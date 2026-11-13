"""A document's synonym may not be its identity values without "@template".

A document's identity is scoped to its template: the platform writes the
synonym of a document's identity values qualified by the template,
``{"@template": <value>, <identity field>: <value>, …}``. A synonym whose
keys are exactly the template's identity fields, without that qualifier,
claims the same spelling for every template in the namespace. A restore
refuses an archive that carries one, so the Registry refuses to write one:
otherwise any caller could add it, and every later backup of that namespace
would be refused on restore. Every door that writes a synonym onto an entry
applies the rule (add, register, activate, and merge, which moves the merged
entry's keys onto the preferred one), for every caller.

The identity fields are read from template-store's own collection, the way
namespace deletion reads it: a template's identity never changes across its
versions, so any version answers. They are the template's effective
identity, its own fields else the nearest ancestor's along ``extends``
(wip_auth.template_identity.effective_identity_fields, the rule the restore
applies too): a template that inherits its identity stores none of its own.
A document whose template cannot be found has no identity fields to collide
with, and is not refused.
"""

from collections.abc import Iterable
from typing import Any

from wip_auth.template_identity import TemplateIdentityRow, effective_identity_fields

from ..models.namespace import Namespace

# How far up an inheritance chain the read goes; chains are shallow, and a
# cycle stops here.
_MAX_DEPTH = 10

DOCUMENTS = "documents"


def _template_id(entity_type: str, primary_key: dict[str, Any] | None) -> str | None:
    if entity_type != DOCUMENTS or not primary_key:
        return None
    template_id = primary_key.get("template_id")
    return template_id if isinstance(template_id, str) else None


async def identity_fields_by_template(
    targets: Iterable[tuple[str, dict[str, Any] | None]],
) -> dict[str, frozenset[str]]:
    """The identity fields of every template the (entity_type, primary key)
    targets name, keyed by template_id. Templates without identity fields,
    and ones not found, are left out."""
    template_ids = {
        tid for entity_type, key in targets if (tid := _template_id(entity_type, key))
    }
    if not template_ids:
        return {}
    rows = await _template_rows(template_ids)
    found: dict[str, frozenset[str]] = {}
    for tid in template_ids:
        if fields := effective_identity_fields(tid, rows):
            found[tid] = frozenset(fields)
    return found


class UnreadableIdentityFields(dict):
    """The identity fields a read could not get: empty, with the reason.
    Every document synonym the rule would have judged is refused with it
    (bare_identity_refusal): an unreadable template decides nothing, and a
    guardrail that opens when its source is down is worse than none."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def __bool__(self) -> bool:
        # Empty, but not "nothing to judge": an `identity_fields or {}`
        # anywhere on the way must not swap it for a plain empty mapping.
        return True


async def identity_fields_or_unreadable(
    targets: Iterable[tuple[str, dict[str, Any] | None]],
) -> dict[str, frozenset[str]]:
    """identity_fields_by_template, or UnreadableIdentityFields when the
    read fails: a bulk door answers that per item, never with a 500."""
    try:
        return await identity_fields_by_template(targets)
    except Exception as e:
        return UnreadableIdentityFields(e)


async def _template_rows(template_ids: set[str]) -> dict[str, TemplateIdentityRow]:
    """(identity_fields, extends) of each template and of the ancestors the
    inheritance walk needs, read level by level."""
    templates = Namespace.get_motor_collection().database.client["wip_template_store"]["templates"]
    rows: dict[str, TemplateIdentityRow] = {}
    wanted = set(template_ids)
    for _ in range(_MAX_DEPTH):
        missing = sorted(wanted - rows.keys())
        if not missing:
            break
        async for row in templates.find(
            {"template_id": {"$in": missing}},
            {"template_id": 1, "identity_fields": 1, "extends": 1},
        ):
            rows.setdefault(row["template_id"], (row.get("identity_fields") or [], row.get("extends")))
        wanted = {parent for fields, parent in rows.values() if not fields and parent}
    return rows


def bare_identity_refusal(
    entity_type: str,
    primary_key: dict[str, Any] | None,
    synonym_key: dict[str, Any] | None,
    identity_fields: dict[str, frozenset[str]],
) -> dict[str, Any] | None:
    """The per-item refusal for a synonym on a document whose keys are
    exactly its template's identity fields, or None. A system-qualified key
    ('@template') is never a field name, so such a synonym is never bare."""
    template_id = _template_id(entity_type, primary_key)
    if not template_id or not synonym_key or any(str(k).startswith("@") for k in synonym_key):
        return None
    if isinstance(identity_fields, UnreadableIdentityFields):
        return _unreadable_refusal(template_id, identity_fields.error)
    fields = identity_fields.get(template_id)
    if not fields or set(synonym_key) != fields:
        return None
    return {
        "error": (
            f"A document's synonym may not be its template's identity fields "
            f"{sorted(fields)} alone: it would claim those values for every "
            "template in the namespace. Qualify it with another key, or leave "
            "the identity synonym to the platform, which writes it under "
            "'@template'."
        ),
        "error_code": "invalid_request",
        "details": {
            "rule": "bare_identity_synonym",
            "template_id": template_id,
            "identity_fields": sorted(fields),
        },
    }


def _unreadable_refusal(template_id: str, error: Exception) -> dict[str, Any]:
    """The per-item answer when the template's identity could not be read."""
    from .errors import error_fields

    return {
        **error_fields(error),
        "details": {"rule": "bare_identity_synonym", "template_id": template_id},
    }

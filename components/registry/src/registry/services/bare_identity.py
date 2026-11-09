"""A document's synonym may not be its identity values without "@template".

A document's identity is scoped to its template: the platform writes the
synonym of a document's identity values qualified by the template,
``{"@template": <value>, <identity field>: <value>, …}``. A synonym whose
keys are exactly the template's identity fields, without that qualifier,
claims the same spelling for every template in the namespace. A restore
refuses an archive that carries one, so the Registry refuses to write one:
otherwise any caller could add it, and every later backup of that namespace
would be refused on restore. Every door that writes a synonym onto an entry
applies the rule (add, register, activate), for every caller.

The identity fields are read from template-store's own collection, the way
namespace deletion reads it: a template's identity_fields never change
across its versions, so any version answers. A document whose template
cannot be found has no identity fields to collide with, and is not refused.
"""

from collections.abc import Iterable
from typing import Any

from ..models.namespace import Namespace

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
    templates = Namespace.get_motor_collection().database.client["wip_template_store"]["templates"]
    found: dict[str, frozenset[str]] = {}
    cursor = templates.find(
        {"template_id": {"$in": sorted(template_ids)}},
        {"template_id": 1, "identity_fields": 1},
    )
    async for row in cursor:
        fields = row.get("identity_fields") or []
        if fields:
            found.setdefault(row["template_id"], frozenset(fields))
    return found


def bare_identity_refusal(
    entity_type: str,
    primary_key: dict[str, Any] | None,
    synonym_key: dict[str, Any] | None,
    identity_fields: dict[str, frozenset[str]],
) -> dict[str, Any] | None:
    """The per-item refusal for a synonym on a document whose keys are
    exactly its template's identity fields, or None."""
    template_id = _template_id(entity_type, primary_key)
    fields = identity_fields.get(template_id) if template_id else None
    if not fields or not synonym_key or set(synonym_key) != fields:
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

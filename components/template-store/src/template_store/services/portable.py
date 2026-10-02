"""Portable reference projection for template reads.

Template create resolves every field-level reference (terminology_ref,
template_ref, array_terminology_ref, array_template_ref, target_templates,
target_terminologies) and the template-level ``extends`` to canonical
UUIDs and stores the resolved form — the submitted form is discarded.
Canonical IDs are instance-specific: a seed file or export that embeds
them fails to bootstrap on any other install, or on the same install
after a namespace wipe. The write door accepts value forms and synonyms,
so the portable projection — each UUID mapped back to its referent's
value, namespace-qualified only when the referent lives outside the
template's own namespace — round-trips through create anywhere.

Served behind ``?refs=portable`` on the single-template read routes.

Reverse mapping asks each entity type's home store, which is authoritative
for its values: template IDs resolve against this service's own Template
collection (one query), terminology IDs through the Def-Store client. A
reference that is not UUID-shaped is already portable and passes through
untouched, as does a UUID whose referent cannot be found (fail-open to
the stored form — a dangling ID should stay visible, not vanish).

Relationship templates' endpoint declarations need no rewriting: their
entries carry the caller's submitted anchor verbatim in ``lookup_value``,
which IS the portable half.
"""

import logging
import re
from typing import Any, TypeVar, cast

from ..models.template import Template
from .def_store_client import DefStoreError, get_def_store_client

logger = logging.getLogger(__name__)

# The projection reads namespace/extends/fields and works on any
# template-shaped model — the stored Template document and the API's
# TemplateResponse alike.
T = TypeVar("T")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Field-level reference properties, by referent entity type.
_TEMPLATE_SCALAR_PROPS = ("template_ref", "array_template_ref")
_TEMPLATE_LIST_PROPS = ("target_templates",)
_TERMINOLOGY_SCALAR_PROPS = ("terminology_ref", "array_terminology_ref")
_TERMINOLOGY_LIST_PROPS = ("target_terminologies",)


def _is_uuid(ref: str) -> bool:
    return bool(_UUID_RE.match(ref))


async def _template_value_map(ids: set[str]) -> dict[str, tuple[str, str]]:
    """template_id -> (namespace, value), from this service's own collection."""
    if not ids:
        return {}
    docs = await Template.find({"template_id": {"$in": list(ids)}}).to_list()
    return {d.template_id: (d.namespace, d.value) for d in docs}


async def _terminology_value_map(ids: set[str]) -> dict[str, tuple[str, str]]:
    """terminology_id -> (namespace, value), from the Def-Store (home store)."""
    out: dict[str, tuple[str, str]] = {}
    if not ids:
        return out
    client = get_def_store_client()
    for tid in ids:
        try:
            data = await client.get_terminology(terminology_id=tid)
        except DefStoreError as e:
            logger.warning(f"Portable projection: terminology {tid} lookup failed: {e}")
            continue
        if data and data.get("namespace") and data.get("value"):
            out[tid] = (data["namespace"], data["value"])
    return out


async def template_with_portable_refs(template: T) -> T:
    """A deep copy of ``template`` with reference UUIDs rewritten to
    portable value forms: bare value for referents in the template's own
    namespace, ``ns:VALUE`` across namespaces (matching the write door's
    addressing rules, so the projection round-trips through create)."""
    portable: Any = template.model_copy(deep=True)  # type: ignore[attr-defined]

    template_ids: set[str] = set()
    terminology_ids: set[str] = set()

    if portable.extends and _is_uuid(portable.extends):
        template_ids.add(portable.extends)
    for field in portable.fields or []:
        for prop in _TEMPLATE_SCALAR_PROPS:
            ref = getattr(field, prop, None)
            if ref and _is_uuid(ref):
                template_ids.add(ref)
        for prop in _TEMPLATE_LIST_PROPS:
            for ref in getattr(field, prop, None) or []:
                if _is_uuid(ref):
                    template_ids.add(ref)
        for prop in _TERMINOLOGY_SCALAR_PROPS:
            ref = getattr(field, prop, None)
            if ref and _is_uuid(ref):
                terminology_ids.add(ref)
        for prop in _TERMINOLOGY_LIST_PROPS:
            for ref in getattr(field, prop, None) or []:
                if _is_uuid(ref):
                    terminology_ids.add(ref)

    if not template_ids and not terminology_ids:
        return cast(T, portable)

    tpl_map = await _template_value_map(template_ids)
    term_map = await _terminology_value_map(terminology_ids)

    def _fmt(mapping: dict[str, tuple[str, str]], ref: str) -> str:
        hit = mapping.get(ref)
        if not hit:
            return ref
        ns, value = hit
        return value if ns == portable.namespace else f"{ns}:{value}"

    if portable.extends:
        portable.extends = _fmt(tpl_map, portable.extends)
    for field in portable.fields or []:
        for prop in _TEMPLATE_SCALAR_PROPS:
            ref = getattr(field, prop, None)
            if ref:
                setattr(field, prop, _fmt(tpl_map, ref))
        for prop in _TEMPLATE_LIST_PROPS:
            refs = getattr(field, prop, None)
            if refs:
                setattr(field, prop, [_fmt(tpl_map, r) for r in refs])
        for prop in _TERMINOLOGY_SCALAR_PROPS:
            ref = getattr(field, prop, None)
            if ref:
                setattr(field, prop, _fmt(term_map, ref))
        for prop in _TERMINOLOGY_LIST_PROPS:
            refs = getattr(field, prop, None)
            if refs:
                setattr(field, prop, [_fmt(term_map, r) for r in refs])

    return cast(T, portable)

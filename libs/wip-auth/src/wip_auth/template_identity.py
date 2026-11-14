"""A template's effective identity: the identity_fields its documents are
hashed by, its own or inherited along ``extends``.

The rule is template-store's (``TemplateService._effective_identity``, what
GET /templates serves). Other services judge documents by it from raw
template rows, without template-store's resolver: the Registry's synonym
doors and the restore's archive pre-check. Both use this one walk, so they
cannot disagree about the same template.
"""

from collections.abc import Mapping, Sequence

# A template row as the inheritance walk needs it: its stored
# identity_fields and the template_id it extends (None for a root).
TemplateIdentityRow = tuple[Sequence[str], str | None]


def effective_identity_fields(
    template_id: str, rows: Mapping[str, TemplateIdentityRow],
) -> list[str]:
    """The identity fields a template's documents are hashed by: its own,
    else the nearest ancestor's along ``extends`` — template-store's rule,
    and what GET /templates serves. A template that extends another stores
    no identity of its own unless it declares one.

    ``rows`` maps template_id to (stored identity_fields, extends). A
    template or ancestor missing from ``rows`` ends the walk with no fields,
    as does a cycle: such a template cannot be judged. The Registry's
    synonym doors and the restore's archive pre-check use this, so they
    cannot disagree about the same template. document-store's restore
    rehash walks the chain by pinned version instead (it also needs each
    version's fields), with the same nearest-declared rule.
    """
    seen: set[str] = set()
    tid: str | None = template_id
    while tid is not None and tid in rows and tid not in seen:
        seen.add(tid)
        fields, parent = rows[tid]
        if fields:
            return list(fields)
        tid = parent
    return []


__all__ = ["TemplateIdentityRow", "effective_identity_fields"]

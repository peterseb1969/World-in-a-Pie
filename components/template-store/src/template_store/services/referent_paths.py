"""Which identity paths of a template version name a term or a reference.

A term- or reference-typed identity field feeds the identity hash the
canonical ID it resolves to, never the submitted spelling. That holds for
dotted paths into object fields too (``address.country``), but whoever
computes the hash then needs the nested template to know what ``country`` is.

The template store answers that once, when a non-draft version is written:
the version's resolved identity_fields and fields, and every nested template
at its pinned ``template_ref_version`` (itself resolved through its pinned
parents). All of these are fixed for that version — identity_fields are
immutable, parents and nested templates are pinned — so the stored answer
never goes stale, and archives carry it with the version.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from wip_auth.document_identity import is_referent_field

from ..models.field import FieldDefinition
from ..models.template import Template
from .inheritance_service import InheritanceError, InheritanceService

logger = logging.getLogger(__name__)

Overrides = dict[tuple[str, int], Template]


async def _resolved(
    template: Template, overrides: Overrides | None
) -> tuple[list[str], list[FieldDefinition]]:
    """The version's identity_fields and fields with inheritance applied."""
    if not template.extends:
        return list(template.identity_fields or []), list(template.fields or [])
    ancestors = await InheritanceService.walk_ancestors(
        template.template_id, template.extends, template.extends_version, overrides
    )
    chain = [*reversed(ancestors), template]
    return (
        InheritanceService._resolve_identity_fields(chain),
        InheritanceService._merge_fields(chain),
    )


async def _nested_fields(
    template_id: str, version: int, overrides: Overrides | None
) -> list[FieldDefinition] | None:
    nested = (overrides or {}).get((template_id, version))
    if nested is None:
        nested = await Template.find_one({"template_id": template_id, "version": version})
    if nested is None:
        return None
    _, fields = await _resolved(nested, overrides)
    return fields


async def _is_referent_path(
    fields: list[FieldDefinition], segments: list[str], overrides: Overrides | None
) -> bool:
    by_name = {f.name: f for f in fields}
    field = by_name.get(segments[0])
    if field is None:
        return False
    if len(segments) == 1:
        return is_referent_field(field.model_dump())
    if field.type != "object" or not field.template_ref:
        return False
    if field.template_ref_version is None:
        # Same rule as an unpinned parent: the path cannot be classified from
        # the top-level field, and following the ref unqualified would read
        # whatever version is latest. Refused, not guessed.
        raise InheritanceError(
            f"Nested template '{field.template_ref}' is not pinned to an "
            "explicit version, so the identity path cannot be classified",
            "unpinned_nested_template",
        )
    nested = await _nested_fields(field.template_ref, field.template_ref_version, overrides)
    if nested is None:
        raise InheritanceError(
            f"Nested template '{field.template_ref}' not found at version "
            f"{field.template_ref_version}",
            "missing_nested_template",
        )
    return await _is_referent_path(nested, segments[1:], overrides)


async def referent_identity_paths(
    template: Template, overrides: Overrides | None = None
) -> list[str]:
    """The template version's identity paths whose value is a referent.

    Raises InheritanceError when a parent or a nested template on an identity
    path cannot be read, or is not pinned to an explicit version: the answer
    would be a guess.
    """
    identity_fields, fields = await _resolved(template, overrides)
    return [
        path for path in identity_fields
        if await _is_referent_path(fields, path.split("."), overrides)
    ]


async def _has_nested_identity_path(template: Template) -> bool:
    try:
        identity_fields, _ = await _resolved(template, None)
    except InheritanceError:
        return True  # cannot tell; re-deriving reports it
    return any("." in path for path in identity_fields)


async def backfill() -> int:
    """Stamp every non-draft version stored without the field, and re-derive
    the stamp of every version with a nested identity path.

    Versions written before the field existed. Only a nested identity path
    needs it (every other path is classified from the top-level field), so
    a version that cannot be resolved is logged and left for the next start
    rather than failing startup.

    A stamp written before a nested template had to be pinned may leave out a
    path into an unpinned one, so the offline hashers would hash that path's
    spelling. Every version with a nested identity path is therefore derived
    again: a stamp that differs is replaced, and one that can no longer be
    derived is removed, so the version's documents are refused rather than
    hashed from a spelling. Returns the number of versions stamped or
    restamped.
    """
    stamped = 0
    query = {
        "status": {"$ne": "draft"},
        "$or": [
            {"referent_identity_paths": None},
            {"identity_fields": {"$elemMatch": {"$regex": r"\."}}},
            {"extends": {"$ne": None}},
        ],
    }
    async for template in Template.find(query):
        stored = template.referent_identity_paths
        if stored is not None and not await _has_nested_identity_path(template):
            continue
        try:
            derived: list[str] | None = await referent_identity_paths(template)
        except InheritanceError as exc:
            logger.warning(
                "referent_identity_paths: %s v%s left unset (%s)",
                template.value, template.version, exc,
            )
            derived = None
        if derived == stored:
            continue
        template.referent_identity_paths = derived
        # The row changed, so the namespace's change stamp
        # (count:max(updated_at)) must move: the document store caches a
        # version until that stamp moves, and would go on hashing by the stamp
        # it read before.
        template.updated_at = datetime.now(UTC)
        await template.save()
        if derived is not None:
            stamped += 1
    return stamped

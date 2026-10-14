"""The unified term-name model: normalization, validation, projections.

A term is a code (`value`) plus a set of names (`TermName`). The API edge
still accepts the legacy `aliases` (plain strings) and `translations`
(language + label) shapes; this module folds them into names on the way in
and derives them back on the way out. Each legacy field maps to a disjoint
partition of the names list:

- legacy aliases      <-> names with type=None and language=None
- legacy translations <-> names with language set and type=None
- typed names are invisible to legacy callers and untouched by legacy writes
"""

import contextlib
import logging

from ..models.term import TermName, TermTranslation

logger = logging.getLogger(__name__)

# Value of the system terminology that defines valid name types.
NAME_TYPES_TERMINOLOGY_VALUE = "_NAME_TYPES"


class TermNameError(ValueError):
    """A names list that violates the naming rules (loud, per-term)."""


def normalize_names(
    names: list[TermName] | None,
    aliases: list[str] | None = None,
    translations: list[TermTranslation] | None = None,
) -> list[TermName]:
    """Fold structured names and legacy shapes into one names list.

    Plain-string aliases become untyped names; translations become
    language-bearing names carrying their description. Order is preserved:
    structured names first, then aliases, then translations.
    """
    result: list[TermName] = list(names or [])
    for alias in aliases or []:
        result.append(TermName(value=alias))
    for tr in translations or []:
        result.append(TermName(
            value=tr.label,
            language=tr.language,
            description=tr.description,
        ))
    return result


def validate_names(
    names: list[TermName],
    term_value: str,
    valid_types: dict[str, str],
) -> None:
    """Enforce the naming rules on a final (normalized) names list.

    Raises TermNameError on: empty name value, a name exactly duplicating
    another name's value within the same language, a name exactly equal to
    the term's own value, an unknown type, or two preferred names in one
    (type, language) group. Loud by design — a silently-dropped or
    silently-deduped name is how vocabularies rot.

    Duplicate detection is exact-match, not case-folded: case-variant
    names ('MR.', 'Mr.') are an established vocabulary idiom, and in a
    case-sensitive terminology a case variant of the value is genuine
    resolution the value alone does not provide.
    """
    seen: set[tuple[str, str | None]] = set()
    preferred_seen: set[tuple[str | None, str | None]] = set()

    for name in names:
        if not name.value or not name.value.strip():
            raise TermNameError("A name's value must be a non-empty string")

        key = (name.value, name.language)
        if key in seen:
            raise TermNameError(
                f"Duplicate name '{name.value}'"
                + (f" (language '{name.language}')" if name.language else "")
                + " — each name may appear once per language"
            )
        seen.add(key)

        if name.value == term_value and name.language is None:
            raise TermNameError(
                f"Name '{name.value}' duplicates the term's own value"
            )

        if name.type is not None and name.type not in valid_types:
            raise TermNameError(
                f"Unknown name type '{name.type}'. Valid types (from "
                f"{NAME_TYPES_TERMINOLOGY_VALUE}): {sorted(valid_types)}"
            )

        if name.preferred:
            group = (name.type, name.language)
            if group in preferred_seen:
                raise TermNameError(
                    f"More than one preferred name in group (type={name.type}, "
                    f"language={name.language}) — at most one allowed"
                )
            preferred_seen.add(group)


def derived_aliases(names: list[TermName]) -> list[str]:
    """Flat resolvable-name projection served to legacy readers.

    Every name resolves, so every name's value appears here — including
    language names, which legacy `translations` never exposed to
    resolution. That widening is deliberate: a translated label validating
    is correct behavior, not a regression.
    """
    return [n.value for n in names]


def derived_translations(names: list[TermName]) -> list[TermTranslation]:
    """Legacy translations projection: language-bearing, untyped names."""
    return [
        TermTranslation(
            language=n.language,
            label=n.value,
            description=n.description,
        )
        for n in names
        if n.language is not None and n.type is None
    ]


def replace_untyped(names: list[TermName], aliases: list[str]) -> list[TermName]:
    """Apply a legacy aliases update: replace the untyped, language-less
    partition with the given strings; typed and language names survive."""
    kept = [n for n in names if n.type is not None or n.language is not None]
    return kept + [TermName(value=a) for a in aliases]


def replace_translations(
    names: list[TermName], translations: list[TermTranslation]
) -> list[TermName]:
    """Apply a legacy translations update: replace the language-bearing,
    untyped partition; typed and plain names survive."""
    kept = [n for n in names if n.language is None or n.type is not None]
    return kept + [
        TermName(value=t.label, language=t.language, description=t.description)
        for t in translations
    ]


async def migrate_legacy_term_names() -> dict[str, int]:
    """One-shot conversion of stored legacy aliases/translations to names.

    Runs at startup, before the service accepts traffic: a term whose
    aliases live only in the legacy field would otherwise be unresolvable
    by alias until converted. Idempotent — a term with a `names` field is
    already migrated and never touched again.

    Legacy data may carry exact duplicates or aliases exactly equal to the
    term's own value; those are deduplicated here (first occurrence wins)
    rather than carried into a shape whose write-time validation would
    reject the term later. Deduplication is exact-match, mirroring
    validate_names: case-variant aliases are kept, because in a
    case-sensitive terminology they are genuine resolution.
    """
    from pymongo import UpdateOne

    from ..models.term import Term

    coll = Term.get_motor_collection()

    converted = 0
    cleaned = 0

    # Pass 1: terms with legacy data to convert (Python-side for dedupe)
    cursor = coll.find(
        {
            "names": {"$exists": False},
            "$or": [
                {"aliases": {"$exists": True, "$ne": []}},
                {"translations": {"$exists": True, "$ne": []}},
            ],
        },
        projection={"_id": 1, "value": 1, "aliases": 1, "translations": 1},
    )

    ops: list[UpdateOne] = []
    async for doc in cursor:
        seen: set[tuple[str, str | None]] = {(doc["value"], None)}
        names: list[dict] = []
        for alias in doc.get("aliases") or []:
            key = (alias, None)
            if not alias or key in seen:
                continue
            seen.add(key)
            names.append(TermName(value=alias).model_dump())
        for tr in doc.get("translations") or []:
            label = tr.get("label")
            language = tr.get("language")
            key = (label or "", language)
            if not label or key in seen:
                continue
            seen.add(key)
            names.append(TermName(
                value=label,
                language=language,
                description=tr.get("description"),
            ).model_dump())

        ops.append(UpdateOne(
            {"_id": doc["_id"]},
            {"$set": {"names": names}, "$unset": {"aliases": "", "translations": ""}},
        ))
        if len(ops) >= 1000:
            result = await coll.bulk_write(ops)
            converted += result.modified_count
            ops = []
    if ops:
        result = await coll.bulk_write(ops)
        converted += result.modified_count

    # Pass 2: terms with no legacy data — initialize names and drop the
    # now-dead legacy fields in one sweep
    sweep = await coll.update_many(
        {"names": {"$exists": False}},
        {"$set": {"names": []}, "$unset": {"aliases": "", "translations": ""}},
    )
    cleaned = sweep.modified_count

    # The legacy alias index no longer matches any field — drop it so it
    # stops costing writes (best-effort: absent on fresh databases)
    with contextlib.suppress(Exception):
        await coll.drop_index("ns_terminology_aliases_idx")

    if converted or cleaned:
        logger.info(
            "Term-name migration: %d terms converted from legacy fields, "
            "%d initialized empty",
            converted, cleaned,
        )
    return {"converted": converted, "initialized": cleaned}


class NameTypeService:
    """Loads and caches the valid name types from _NAME_TYPES.

    Same pattern as OntologyService's relation-type cache: populated on
    first use, invalidated when the system terminology's terms change.
    """

    _name_types: dict[str, str] | None = None

    @classmethod
    async def get_valid_name_types(cls) -> dict[str, str]:
        """Dict mapping term value -> label (e.g. {"INN": "INN"})."""
        if cls._name_types is not None:
            return cls._name_types

        from ..models.term import Term
        from ..models.terminology import Terminology

        terminology = await Terminology.find_one({
            "value": NAME_TYPES_TERMINOLOGY_VALUE
        })
        if not terminology:
            logger.warning("%s terminology not found", NAME_TYPES_TERMINOLOGY_VALUE)
            return {}

        types: dict[str, str] = {}
        async for term in Term.find({
            "terminology_id": terminology.terminology_id,
            "status": "active",
        }):
            types[term.value] = term.label or term.value

        cls._name_types = types
        logger.info("Loaded %d valid name types", len(types))
        return cls._name_types

    @classmethod
    def invalidate_name_type_cache(cls) -> None:
        """Invalidate the cache so the next call reloads from the DB."""
        cls._name_types = None

"""Term model for the Def-Store service."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel
from pymongo.collation import Collation, CollationStrength


class TermTranslation(BaseModel):
    """Legacy translation shape, kept for the API edge only.

    Stored terms no longer carry a translations list — a translation is a
    TermName with a language. Requests may still submit this shape; it is
    normalized into names on write, and term responses derive it back for
    callers that still read it.
    """

    language: str = Field(
        ...,
        description="Language code (ISO 639-1)"
    )
    label: str = Field(
        ...,
        description="Translated display label"
    )
    description: str | None = Field(
        default=None,
        description="Translated description"
    )


class TermName(BaseModel):
    """One name of a term — the unified naming model.

    A term is a code (`value`) plus a set of names. Trade names,
    development codes, registry numbers, translated labels and plain
    synonyms are all names; what distinguishes them is data on the name,
    not which field they live in. Every name resolves in validation
    exactly like the term's value does.
    """

    value: str = Field(
        ...,
        description="The name itself (e.g., 'Avastin', 'bevacizumab', 'NSC-704865')"
    )
    type: str | None = Field(
        default=None,
        description="Name type from the _NAME_TYPES terminology "
                    "(e.g., 'INN', 'TRADE_GLOBAL', 'CODE'). None = untyped synonym."
    )
    language: str | None = Field(
        default=None,
        description="Language code (ISO 639-1) for language-specific names"
    )
    description: str | None = Field(
        default=None,
        description="Free-text note on this name (e.g., a translated description)"
    )
    source: str | None = Field(
        default=None,
        description="Where this name came from (e.g., 'RTiS', 'OBO:hasExactSynonym')"
    )
    preferred: bool = Field(
        default=False,
        description="Preferred name within its (type, language) group — at most one per group"
    )


# Case-insensitive comparison (ICU secondary strength: case ignored, accents
# kept) for value resolution in case-insensitive terminologies. Queries that
# pass it use the collation indexes on Term below; it narrows the candidates
# and Python's comparison makes the final call.
CASE_INSENSITIVE_COLLATION = Collation(locale="en", strength=CollationStrength.SECONDARY)


class Term(Document):
    """
    An individual term within a terminology.

    Terms are the actual values that can be used in documents.
    Each term belongs to exactly one terminology.

    Examples:
    - In "DOC_STATUS" terminology: draft, review, approved, archived
    - In "PRIORITY" terminology: critical, high, medium, low
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        ...,
        description="Namespace for data isolation (e.g., wip, dev, prod)"
    )

    # Identity (from Registry)
    term_id: str = Field(
        ...,
        description="Unique ID from Registry"
    )

    # Parent terminology
    terminology_id: str = Field(
        ...,
        description="ID of the parent terminology"
    )
    terminology_value: str | None = Field(
        default=None,
        description="Value of the parent terminology (e.g., 'GENDER'). Denormalized for efficient lookups."
    )

    # The actual value used in documents (unique within terminology)
    value: str = Field(
        ...,
        description="The value stored in documents (e.g., 'approved'). Unique within terminology."
    )

    # All alternative names of this term (typed, resolvable)
    names: list[TermName] = Field(
        default_factory=list,
        description="Alternative names that resolve to this term. Each carries "
                    "optional type, language, source and preferred flag."
    )

    # Display information
    label: str | None = Field(
        default=None,
        description="Display label for UI (e.g., 'Approved'). Defaults to value if not set."
    )
    description: str | None = Field(
        default=None,
        description="Detailed description of what this term means"
    )

    # Ordering and hierarchy
    sort_order: int = Field(
        default=0,
        description="Sort order within the terminology"
    )
    parent_term_id: str | None = Field(
        default=None,
        description="Parent term ID for hierarchical terminologies"
    )

    # Additional data
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata (e.g., color codes, icons)"
    )

    # Lifecycle
    status: str = Field(
        default="active",
        description="Status: active, deprecated, inactive"
    )
    deprecated_reason: str | None = Field(
        default=None,
        description="Why this term was deprecated"
    )
    replaced_by_term_id: str | None = Field(
        default=None,
        description="ID of the term that replaces this one"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        default=None,
        description="User or system that created this term"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_by: str | None = Field(
        default=None,
        description="User or system that last updated this term"
    )

    class Settings:
        name = "terms"
        indexes: ClassVar[list[IndexModel]] = [
            # Unique ID within namespace
            IndexModel([("namespace", 1), ("term_id", 1)], unique=True, name="ns_term_id_unique_idx"),
            # The unique value index and the exact-case names index are NOT
            # declared here — see TERM_INDEXES_BUILT_AT_STARTUP below.
            # Case-insensitive value and name resolution (see CASE_INSENSITIVE_COLLATION)
            IndexModel(
                [("namespace", 1), ("terminology_id", 1), ("value", 1)],
                name="ns_terminology_value_ci_idx", collation=CASE_INSENSITIVE_COLLATION,
            ),
            IndexModel(
                [("namespace", 1), ("terminology_id", 1), ("names.value", 1)],
                name="ns_terminology_name_values_ci_idx", collation=CASE_INSENSITIVE_COLLATION,
            ),
            # A terminology's terms in listing order. The listing's default
            # sort is sort_order then value, so value belongs in the key:
            # without it every page sorts the whole terminology in memory.
            # Replaces ns_terminology_sort_idx (no value), which startup drops.
            IndexModel(
                [("namespace", 1), ("terminology_id", 1), ("sort_order", 1), ("value", 1)],
                name="ns_terminology_sort_value_idx",
            ),
            # Lookup by term_id alone. Many paths address a term by its id
            # without knowing its namespace (get, update, delete, merge, bulk
            # prefetch, relation lookups); ns_term_id_unique_idx cannot serve
            # them. term_id is Registry-minted and unique across namespaces,
            # but the index stays non-unique: uniqueness is the Registry's
            # guarantee, not one this collection adds.
            IndexModel([("term_id", 1)], name="term_id_idx"),
            # Status filter within terminology
            IndexModel([("namespace", 1), ("terminology_id", 1), ("status", 1)], name="ns_terminology_status_idx"),
            # Parent term lookup
            IndexModel([("parent_term_id", 1)], name="parent_term_idx"),
            # Text search (global)
            IndexModel([("value", "text"), ("label", "text"), ("description", "text")], name="text_search_idx"),
        ]


# Term indexes that def-store builds itself at startup, after init_beanie.
# Beanie merges the declared indexes by their key fields alone, ignoring
# collation and uniqueness. Each of these shares its keys with a
# case-insensitive twin above, so declared in Settings.indexes it was
# silently dropped in favour of the twin and never built on a fresh
# database: no unique constraint on a term's value within its terminology,
# and no index for exact-case name lookups. Built explicitly instead (see
# def_store.main.reconcile_term_indexes).
TERM_INDEXES_BUILT_AT_STARTUP: list[IndexModel] = [
    # Unique value within terminology within namespace
    IndexModel(
        [("namespace", 1), ("terminology_id", 1), ("value", 1)],
        unique=True, name="ns_terminology_value_unique_idx",
    ),
    # Name lookup within terminology (multikey over names.value)
    IndexModel(
        [("namespace", 1), ("terminology_id", 1), ("names.value", 1)],
        name="ns_terminology_name_values_idx",
    ),
]

"""Canonical bulk-write response models for backend services.

Per wip://conventions §Bulk-First: every WIP write endpoint accepts a list
and returns a BulkResponse with per-item BulkResultItem. The base classes
here define the universal contract; per-domain subclasses add fields that
only that domain emits.

CASE-395 consolidates three drifting definitions across def-store,
document-store, and template-store. The `bulk-result-item-canonical` rule
in scripts/api-consistency-check.py forbids new BulkResultItem definitions
outside this module — subclasses inheriting from BulkResultItemBase and
thin re-export aliases are the only allowed shapes.

Universal contract (BulkResultItemBase):
    index, status, id, error, error_code, details

error_code is the machine-readable surface callers branch on. The codes
the services emit are listed in the error_code field's description below.
The four services that return bulk write results — def-store,
document-store, registry, template-store — publish that description for it;
reporting-sync returns none. A service gives its own wording to error_code
on the items it defines outside this contract: registry's own item models
and its lookup, resolve and search answers, def-store's term-validation
answer, template-store's cascade result.
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, model_validator


class BulkResultItemBase(BaseModel):
    """Universal per-item result for bulk write operations."""

    index: int
    # created, updated, unchanged, deleted, skipped, error; an endpoint adds
    # its own where that is what happened (reactivated, deprecated)
    status: str
    id: str | None = None
    error: str | None = None
    error_code: str | None = Field(
        default=None,
        description=(
            "Machine-readable error code. Documented codes: not_found, "
            "forbidden, identity_field_change, "
            "concurrency_conflict, validation_failed, reference_violation, "
            "internal_error, already_exists, "
            "incompatible_config, force_unsupported, invalid_names, "
            "name_collision, identity_fields_immutable, immutable_property, "
            "cross_namespace_reference, invalid_state, invalid_request, "
            "registry_error, synonym_conflict, registry_entry_not_removed, "
            "cross_namespace_relationship, "
            "ambiguous_reference (an identifier the item names is borne by "
            "several entities; details.field, details.candidates), "
            "ambiguous_terminology (several terminologies bear the value; "
            "def-store's term-validation items), "
            "dependency_unavailable (a service or store the item needed could "
            "not answer; details.dependency), identity_hash_stale (a migrate "
            "dry run found the stored hash is not the one the target version "
            "computes), exists_inactive (a reconcile snapshot repeats a value "
            "the terminology has already retired), duplicate_in_snapshot (the "
            "same value twice in one snapshot), unknown_successor (a "
            "successor the snapshot names does not exist)."
        ),
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured details: the compatibility diff for a create — "
            "template create reports it on the 'updated' item it writes, "
            "whatever on_conflict says (the parameter no longer selects the "
            "behaviour), while a terminology or term re-create reports it as "
            "an 'incompatible_config' error only under on_conflict=validate — "
            "or, on any other error, the rule that failed (details.rule)."
        ),
    )


ItemT = TypeVar("ItemT", bound=BulkResultItemBase)


class BulkResponseBase(BaseModel, Generic[ItemT]):
    """Universal bulk-response envelope.

    Per-domain subclasses parameterise `ItemT` to the matching
    BulkResultItem subclass, and may add envelope-level fields
    (e.g. document-store's `timing`).
    """

    results: list[ItemT]
    total: int
    succeeded: int
    failed: int


# ── Per-domain item subclasses ─────────────────────────────────────────────


class DocumentBulkResultItem(BulkResultItemBase):
    """document-store bulk-result item."""

    document_id: str | None = None
    identity_hash: str | None = None
    version: int | None = None
    template_version: int | None = Field(
        default=None,
        description=(
            "Template version the written (or unchanged) document is pinned "
            "to; on a migrate dry run, the version it would be pinned to."
        ),
    )
    # The token to send as if_match on the next PATCH. Equal to version
    # except on a row overwritten in place (versioned:false), where it
    # counts the writes; filled from version when a writer leaves it out.
    revision: int | None = None
    is_new: bool | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _revision_defaults_to_version(self) -> "DocumentBulkResultItem":
        if self.revision is None and self.version is not None:
            self.revision = self.version
        return self


class TemplateBulkResultItem(BulkResultItemBase):
    """template-store bulk-result item."""

    value: str | None = None
    version: int | None = None
    is_new_version: bool | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "What the write did not do, without refusing it: a template revived "
            "without a synonym another entity took meanwhile, as the Registry "
            "reports it."
        ),
    )


class TerminologyTermBulkResultItem(BulkResultItemBase):
    """def-store bulk-result item (covers terminologies + terms)."""

    value: str | None = None


# ── Per-domain envelope subclasses ─────────────────────────────────────────


class DocumentBulkResponse(BulkResponseBase[DocumentBulkResultItem]):
    """document-store bulk-write envelope (adds server-side timing)."""

    timing: dict[str, float] | None = Field(
        default=None,
        description="Server-side timing breakdown in milliseconds.",
    )


class TemplateBulkResponse(BulkResponseBase[TemplateBulkResultItem]):
    """template-store bulk-write envelope."""


class TerminologyTermBulkResponse(BulkResponseBase[TerminologyTermBulkResultItem]):
    """def-store bulk-write envelope (terminologies + terms)."""


__all__ = [
    "BulkResponseBase",
    "BulkResultItemBase",
    "DocumentBulkResponse",
    "DocumentBulkResultItem",
    "TemplateBulkResponse",
    "TemplateBulkResultItem",
    "TerminologyTermBulkResponse",
    "TerminologyTermBulkResultItem",
]

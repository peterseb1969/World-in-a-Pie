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

error_code is the machine-readable surface callers branch on. The
documented codes from wip://conventions are:
    not_found, forbidden, archived, identity_field_change,
    concurrency_conflict, validation_failed, reference_violation,
    internal_error, incompatible_schema
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


class BulkResultItemBase(BaseModel):
    """Universal per-item result for bulk write operations."""

    index: int
    status: str  # created, updated, unchanged, deleted, skipped, error
    id: str | None = None
    error: str | None = None
    error_code: str | None = Field(
        default=None,
        description=(
            "Machine-readable error code. Documented codes: not_found, "
            "forbidden, archived, identity_field_change, "
            "concurrency_conflict, validation_failed, reference_violation, "
            "internal_error, incompatible_schema."
        ),
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured details for non-error statuses (e.g. compatibility "
            "diff for on_conflict=validate)."
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
    is_new: bool | None = None
    warnings: list[str] = Field(default_factory=list)


class TemplateBulkResultItem(BulkResultItemBase):
    """template-store bulk-result item."""

    value: str | None = None
    version: int | None = None
    is_new_version: bool | None = None


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

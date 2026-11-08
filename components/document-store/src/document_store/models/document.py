"""Document model for the Document Store service."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from beanie import Document as BeanieDocument
from pydantic import BaseModel, Field
from pymongo import DESCENDING, IndexModel


class DocumentStatus(StrEnum):
    """Status values for documents."""
    ACTIVE = "active"
    INACTIVE = "inactive"


class DocumentMetadata(BaseModel):
    """Additional metadata for a document."""

    source_system: str | None = Field(
        default=None,
        description="System that created this document"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Validation warnings (non-blocking issues)"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata fields"
    )


class Document(BeanieDocument):
    """
    A document conforming to a template.

    Documents are validated against templates from the Template Store.
    They support versioning through identity-based upsert logic.

    The identity_hash is computed from the template's identity_fields,
    allowing the system to detect when a document should be updated
    (new version) rather than created as a new entity.
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        ...,
        description="Namespace for data isolation (e.g., wip, dev, seed)"
    )

    # Identity (from Registry - UUID7 for time-ordering)
    document_id: str = Field(
        ...,
        description="Unique ID from Registry (UUID7)"
    )

    # Template reference
    template_id: str = Field(
        ...,
        description="Reference to Template Store template ID"
    )
    template_version: int = Field(
        ...,
        description="Version of template used for validation"
    )
    template_value: str | None = Field(
        default=None,
        description="Template value (e.g., PLANNED_VISIT) for easier identification"
    )

    # Identity for upsert logic (empty string when template has no identity_fields)
    identity_hash: str = Field(
        default="",
        description="SHA-256 hash of identity field values (empty when template has no identity_fields)"
    )

    # Document versioning
    version: int = Field(
        default=1,
        description="Document version (per identity)"
    )
    # The write token if_match compares against. Absent until the row is
    # overwritten in place (a versioned:false template), and then counting
    # those writes; while absent the token is `version`. A versioned row is
    # never overwritten, so its token is its version. See current_revision().
    revision: int | None = Field(
        default=None,
        description=(
            "Write counter of a row overwritten in place (versioned:false); "
            "absent until the first such write"
        ),
    )

    # Document content
    data: dict[str, Any] = Field(
        ...,
        description="Document content conforming to template"
    )

    # Term references - resolved term IDs for term fields (legacy, use references instead)
    # Array format for indexing: [{"field_path": "gender", "term_id": "019abc42-..."}, ...]
    term_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved term IDs for term fields"
    )

    # Unified references - resolved references for all reference type fields
    # Array format: [{"field_path": "supervisor", "reference_type": "document", "resolved": {...}}, ...]
    references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved references for reference type fields"
    )

    # File references - resolved file IDs for file fields
    # Array format: [{"field_path": "scan_image", "file_id": "FILE-000001", "filename": "...", ...}, ...]
    file_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved file references for file fields"
    )

    # Lifecycle
    status: DocumentStatus = Field(
        default=DocumentStatus.ACTIVE,
        description="Document status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        default=None,
        description="User or system that created this document"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_by: str | None = Field(
        default=None,
        description="User or system that last updated this document"
    )

    # Metadata
    metadata: DocumentMetadata = Field(
        default_factory=DocumentMetadata,
        description="Additional document metadata"
    )

    class Settings:
        name = "documents"
        # ClassVar signals to ruff that this is class-level state (Beanie
        # convention — read at metaclass init), not an instance default.
        indexes: ClassVar[list] = [
            # Unique (document_id, version) within namespace — stable ID across versions
            IndexModel([("namespace", 1), ("document_id", 1), ("version", 1)], unique=True, name="ns_document_id_version_unique_idx"),
            # Version lookup by identity within namespace
            IndexModel([("namespace", 1), ("identity_hash", 1), ("version", 1)], name="ns_identity_version_idx"),
            # Active document lookup by identity within namespace
            IndexModel([("namespace", 1), ("identity_hash", 1), ("status", 1)], name="ns_identity_status_idx"),
            # Template queries within namespace
            IndexModel([("namespace", 1), ("template_id", 1), ("status", 1)], name="ns_template_status_idx"),
            # Time-based queries within namespace
            IndexModel([("namespace", 1), ("created_at", DESCENDING)], name="ns_created_at_idx"),
            # One template's documents in the list endpoint's default order:
            # created_at descending, then document_id (the tiebreaker
            # build_sort_clauses always appends). document_id belongs in the
            # key: without it every page reads and sorts the template's whole
            # document set in memory. Replaces ns_template_status_time_idx
            # (no document_id), which startup drops.
            IndexModel(
                [("namespace", 1), ("template_id", 1), ("status", 1),
                 ("created_at", DESCENDING), ("document_id", 1)],
                name="ns_template_status_time_doc_idx"
            ),
            # Cursor paging of one template's documents (the list endpoint's
            # cursor mode, which the reporting batch sync uses): equality on
            # the prefix, then _id order, so each page reads one page of keys
            # instead of sorting every matching document.
            IndexModel(
                [("namespace", 1), ("template_id", 1), ("status", 1), ("_id", 1)],
                name="ns_template_status_id_idx"
            ),
            # The same cursor paging and counts for a caller with no
            # namespace filter (a superadmin key, or a whole-instance
            # reporting sync): every index above leads with namespace and
            # cannot serve them.
            IndexModel(
                [("template_id", 1), ("status", 1), ("_id", 1)],
                name="template_status_id_idx"
            ),
            # document_id lookup (non-unique, for finding all versions)
            IndexModel([("document_id", 1)], name="document_id_idx"),
            # Term reference reverse lookups (find documents referencing a term)
            IndexModel(
                [("term_references.term_id", 1)],
                name="term_references_term_id_idx",
                sparse=True
            ),
            # Reference reverse lookups (find documents referencing another document)
            IndexModel(
                [("references.resolved.document_id", 1)],
                name="references_document_id_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.identity_hash", 1)],
                name="references_identity_hash_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.term_id", 1)],
                name="references_term_id_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.template_id", 1)],
                name="references_template_id_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.terminology_id", 1)],
                name="references_terminology_id_idx",
                sparse=True
            ),
            # File reference reverse lookups (find documents referencing a file)
            IndexModel(
                [("file_references.file_id", 1)],
                name="file_references_file_id_idx",
                sparse=True
            ),
        ]


def current_revision(doc: "Document") -> int:
    """The token a caller passes as if_match to write over this row: its
    in-place write counter once it has one, else its version."""
    return doc.revision if doc.revision is not None else doc.version

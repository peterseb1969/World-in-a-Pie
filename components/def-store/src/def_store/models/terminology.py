"""Terminology model for the Def-Store service."""

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class TerminologyMetadata(BaseModel):
    """Additional metadata for a terminology."""

    source: Optional[str] = Field(
        None,
        description="Source of the terminology (e.g., 'ISO 3166', 'internal')"
    )
    source_url: Optional[str] = Field(
        None,
        description="URL to the source specification"
    )
    version: Optional[str] = Field(
        None,
        description="Version of the terminology (e.g., '2024.1')"
    )
    language: str = Field(
        default="en",
        description="Primary language code (ISO 639-1)"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata fields"
    )


class Terminology(Document):
    """
    A controlled vocabulary or enumeration.

    Terminologies define sets of allowed values that can be referenced
    by templates for field validation.

    Examples:
    - Document statuses: [draft, review, approved, archived]
    - Priority levels: [critical, high, medium, low]
    - Country codes: [imported from ISO 3166]
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        default="wip",
        description="Namespace for data isolation (e.g., wip, dev, prod)"
    )

    # Identity (from Registry)
    terminology_id: str = Field(
        ...,
        description="Unique ID from Registry"
    )

    # Human-friendly identifier (mutable)
    value: str = Field(
        ...,
        description="Human-readable value (e.g., 'DOC_STATUS'). Must be unique within namespace."
    )

    # Display information
    label: str = Field(
        ...,
        description="Display label (e.g., 'Document Status')"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description of the terminology's purpose"
    )

    # Configuration
    case_sensitive: bool = Field(
        default=False,
        description="Whether term values are case-sensitive"
    )
    allow_multiple: bool = Field(
        default=False,
        description="Whether multiple terms can be selected (for UI hints)"
    )
    extensible: bool = Field(
        default=False,
        description="Whether users can add new terms at runtime"
    )

    # Metadata
    metadata: TerminologyMetadata = Field(
        default_factory=TerminologyMetadata,
        description="Additional metadata"
    )

    # Lifecycle
    status: str = Field(
        default="active",
        description="Status: active, inactive"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system that created this terminology"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system that last updated this terminology"
    )

    # Statistics (denormalized for performance)
    term_count: int = Field(
        default=0,
        description="Number of active terms in this terminology"
    )

    class Settings:
        name = "terminologies"
        indexes = [
            # Unique ID within namespace
            IndexModel([("namespace", 1), ("terminology_id", 1)], unique=True, name="ns_terminology_id_unique_idx"),
            # Unique value within namespace
            IndexModel([("namespace", 1), ("value", 1)], unique=True, name="ns_value_unique_idx"),
            # Filter by status within namespace
            IndexModel([("namespace", 1), ("status", 1)], name="ns_status_idx"),
            # Text search (global)
            IndexModel([("label", "text"), ("description", "text")], name="text_search_idx"),
        ]

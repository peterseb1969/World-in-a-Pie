"""Term model for the Def-Store service."""

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class TermTranslation(BaseModel):
    """Translation of a term's display values."""

    language: str = Field(
        ...,
        description="Language code (ISO 639-1)"
    )
    label: str = Field(
        ...,
        description="Translated display label"
    )
    description: Optional[str] = Field(
        None,
        description="Translated description"
    )


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
        default="wip",
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
    terminology_value: Optional[str] = Field(
        None,
        description="Value of the parent terminology (e.g., 'GENDER'). Denormalized for efficient lookups."
    )

    # The actual value used in documents (unique within terminology)
    value: str = Field(
        ...,
        description="The value stored in documents (e.g., 'approved'). Unique within terminology."
    )

    # Alternative values that resolve to this term
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative values that resolve to this term (e.g., ['MR.', 'mr', 'Mr.'])"
    )

    # Display information
    label: Optional[str] = Field(
        None,
        description="Display label for UI (e.g., 'Approved'). Defaults to value if not set."
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description of what this term means"
    )

    # Ordering and hierarchy
    sort_order: int = Field(
        default=0,
        description="Sort order within the terminology"
    )
    parent_term_id: Optional[str] = Field(
        None,
        description="Parent term ID for hierarchical terminologies"
    )

    # Multi-language support
    translations: list[TermTranslation] = Field(
        default_factory=list,
        description="Translations for internationalization"
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
    deprecated_reason: Optional[str] = Field(
        None,
        description="Why this term was deprecated"
    )
    replaced_by_term_id: Optional[str] = Field(
        None,
        description="ID of the term that replaces this one"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system that created this term"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system that last updated this term"
    )

    class Settings:
        name = "terms"
        indexes = [
            # Unique ID within namespace
            IndexModel([("namespace", 1), ("term_id", 1)], unique=True, name="ns_term_id_unique_idx"),
            # Unique value within terminology within namespace
            IndexModel([("namespace", 1), ("terminology_id", 1), ("value", 1)], unique=True, name="ns_terminology_value_unique_idx"),
            # Alias lookup within terminology
            IndexModel([("namespace", 1), ("terminology_id", 1), ("aliases", 1)], name="ns_terminology_aliases_idx"),
            # Sort order within terminology
            IndexModel([("namespace", 1), ("terminology_id", 1), ("sort_order", 1)], name="ns_terminology_sort_idx"),
            # Status filter within terminology
            IndexModel([("namespace", 1), ("terminology_id", 1), ("status", 1)], name="ns_terminology_status_idx"),
            # Global term_id lookup (for cross-namespace refs in open mode)
            IndexModel([("term_id", 1)], unique=True, name="term_id_unique_idx"),
            # Parent term lookup
            IndexModel([("parent_term_id", 1)], name="parent_term_idx"),
            # Text search (global)
            IndexModel([("value", "text"), ("label", "text"), ("description", "text")], name="text_search_idx"),
        ]

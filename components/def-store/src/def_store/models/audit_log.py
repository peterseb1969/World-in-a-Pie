"""Audit log models for tracking changes to terminologies and terms."""

from datetime import UTC, datetime
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import DESCENDING, IndexModel


class TermAuditLog(Document):
    """
    Audit log entry for term changes.

    Records all changes to terms for historical tracking.
    Since terms don't have versioning (term_id is stable),
    this provides the audit trail for what changed and when.
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        ...,
        description="Namespace of the term (e.g., wip, dev, prod)"
    )

    # Reference to the term
    term_id: str = Field(
        ...,
        description="ID of the term that was changed"
    )
    terminology_id: str = Field(
        ...,
        description="ID of the parent terminology"
    )

    # Change metadata
    action: str = Field(
        ...,
        description="Type of change: created, updated, deprecated, deleted"
    )
    changed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the change occurred"
    )
    changed_by: str | None = Field(
        None,
        description="User or system that made the change"
    )

    # Change details
    changed_fields: list[str] = Field(
        default_factory=list,
        description="List of field names that were changed"
    )
    previous_values: dict[str, Any] = Field(
        default_factory=dict,
        description="Previous values of changed fields"
    )
    new_values: dict[str, Any] = Field(
        default_factory=dict,
        description="New values of changed fields"
    )

    # Optional comment
    comment: str | None = Field(
        None,
        description="Optional comment explaining the change"
    )

    class Settings:
        name = "term_audit_log"
        indexes = [
            # Time-based queries within namespace
            IndexModel([("namespace", 1), ("term_id", 1), ("changed_at", DESCENDING)], name="ns_term_time_idx"),
            IndexModel([("namespace", 1), ("terminology_id", 1), ("changed_at", DESCENDING)], name="ns_terminology_time_idx"),
            IndexModel([("namespace", 1), ("changed_at", DESCENDING)], name="ns_time_idx"),
            # Action filter
            IndexModel([("namespace", 1), ("action", 1)], name="ns_action_idx"),
            # Global time index for admin queries
            IndexModel([("changed_at", DESCENDING)], name="time_idx"),
        ]

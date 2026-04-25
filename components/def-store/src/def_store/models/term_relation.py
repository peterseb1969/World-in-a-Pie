"""TermRelation model for ontology support in the Def-Store service."""

from datetime import UTC, datetime
from typing import Any

from beanie import Document
from pydantic import Field
from pymongo import IndexModel


class TermRelation(Document):
    """
    A typed, directed relation between two terms.

    Enables ontology support by representing polyhierarchy (multiple parents),
    typed relations (is_a, part_of, etc.), and cross-terminology links.

    Examples:
    - "Viral pneumonia" is_a "Pneumonia"
    - "Viral pneumonia" is_a "Viral respiratory infection"
    - "Heart" part_of "Circulatory system"
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        ...,
        description="Namespace for data isolation"
    )

    # Relation endpoints
    source_term_id: str = Field(
        ...,
        description="The subject term ID"
    )
    target_term_id: str = Field(
        ...,
        description="The object term ID"
    )

    # Relation type (term ID or value from _ONTOLOGY_RELATIONSHIP_TYPES)
    relation_type: str = Field(
        ...,
        description="Relation type value (e.g., 'is_a', 'part_of')"
    )

    # Denormalized fields for query efficiency
    relation_value: str | None = Field(
        None,
        description="Denormalized relation type display value"
    )
    source_terminology_id: str | None = Field(
        None,
        description="Denormalized source term's terminology ID"
    )
    target_terminology_id: str | None = Field(
        None,
        description="Denormalized target term's terminology ID"
    )

    # Additional data
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance, confidence, source ontology, OWL axioms"
    )

    # Lifecycle
    status: str = Field(
        default="active",
        description="Status: active, inactive"
    )

    # Audit
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        None,
        description="User or system that created this relation"
    )

    class Settings:
        name = "term_relations"
        indexes = [
            # Find all relations FROM a term
            IndexModel(
                [("namespace", 1), ("source_term_id", 1), ("relation_type", 1)],
                name="ns_source_type_idx"
            ),
            # Find all relations TO a term (reverse lookup)
            IndexModel(
                [("namespace", 1), ("target_term_id", 1), ("relation_type", 1)],
                name="ns_target_type_idx"
            ),
            # Uniqueness: one relation of each type between two terms
            IndexModel(
                [("namespace", 1), ("source_term_id", 1), ("target_term_id", 1), ("relation_type", 1)],
                unique=True,
                name="ns_source_target_type_unique_idx"
            ),
            # By terminology (for ontology-wide queries)
            IndexModel(
                [("namespace", 1), ("source_terminology_id", 1), ("relation_type", 1)],
                name="ns_src_terminology_type_idx"
            ),
            # Status filter
            IndexModel(
                [("namespace", 1), ("status", 1)],
                name="ns_status_idx"
            ),
        ]

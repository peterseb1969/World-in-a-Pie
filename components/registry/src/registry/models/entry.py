"""Registry entry model for the Registry service."""

from datetime import datetime, timezone
from typing import Any, Optional

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class SourceInfo(BaseModel):
    """Source system identification and endpoint."""

    system_id: str = Field(
        ...,
        description="Identifier of the source system"
    )
    endpoint_url: Optional[str] = Field(
        None,
        description="API endpoint URL for proxied queries"
    )


class Synonym(BaseModel):
    """A composite key variant that resolves to the parent entry."""

    pool_id: str = Field(
        ...,
        description="ID pool this synonym belongs to"
    )
    composite_key: dict[str, Any] = Field(
        ...,
        description="The actual key-value pairs"
    )
    composite_key_hash: str = Field(
        ...,
        description="SHA-256 hash of the composite key"
    )
    source_info: Optional[SourceInfo] = Field(
        None,
        description="Source system information"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system that created this synonym"
    )


class RegistryEntry(Document):
    """
    A registered identity in the central registry.

    Supports multiple synonyms (composite keys) that all resolve to the same entity.
    One entry is marked as preferred, with additional_ids storing merged duplicates.
    """

    # The primary ID for this entry (generated based on pool config)
    entry_id: str = Field(
        ...,
        description="Primary ID in the primary pool"
    )

    # The ID pool of the primary entry
    primary_pool_id: str = Field(
        default="default",
        description="ID pool of the primary ID"
    )

    # Is this the preferred entry when multiple entries were merged?
    is_preferred: bool = Field(
        default=True,
        description="Whether this is the preferred ID for the entity"
    )

    # The primary composite key
    primary_composite_key: dict[str, Any] = Field(
        ...,
        description="The primary composite key values"
    )

    primary_composite_key_hash: str = Field(
        ...,
        description="Hash of the primary composite key"
    )

    # All synonyms (alternative keys that resolve to this entry)
    synonyms: list[Synonym] = Field(
        default_factory=list,
        description="Alternative composite keys that resolve to this entry"
    )

    # IDs that were merged into this entry (from ID-as-synonym merges)
    additional_ids: list[dict[str, str]] = Field(
        default_factory=list,
        description="Other IDs that are synonyms [{'pool_id': '...', 'id': '...'}]"
    )

    # Source system that owns this entry
    source_info: Optional[SourceInfo] = Field(
        None,
        description="Source system information"
    )

    # Status for soft delete
    status: str = Field(
        default="active",
        description="Status: active or inactive"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system that created this entry"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system that last updated this entry"
    )

    # Additional metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )

    class Settings:
        name = "registry_entries"
        indexes = [
            # Primary lookup by entry_id + pool
            IndexModel(
                [("primary_pool_id", 1), ("entry_id", 1)],
                unique=True,
                name="entry_id_pool_unique_idx"
            ),
            # Lookup by primary composite key hash
            IndexModel(
                [("primary_composite_key_hash", 1)],
                name="primary_key_hash_idx"
            ),
            # Lookup by synonym composite key hash (for cross-pool search)
            IndexModel(
                [("synonyms.composite_key_hash", 1)],
                name="synonyms_key_hash_idx"
            ),
            # Lookup by synonym pool + hash
            IndexModel(
                [("synonyms.pool_id", 1), ("synonyms.composite_key_hash", 1)],
                name="synonyms_pool_hash_idx"
            ),
            # Status index for filtering active entries
            IndexModel(
                [("status", 1)],
                name="status_idx"
            ),
            # Text index for search-by-term across composite key values
            IndexModel(
                [("primary_composite_key", "text"), ("synonyms.composite_key", "text")],
                name="composite_key_text_idx"
            ),
        ]

    def get_all_hashes(self) -> list[str]:
        """Return all composite key hashes (primary + synonyms)."""
        hashes = [self.primary_composite_key_hash]
        hashes.extend(s.composite_key_hash for s in self.synonyms)
        return hashes

    def find_synonym_by_hash(self, key_hash: str) -> Optional[Synonym]:
        """Find a synonym by its composite key hash."""
        for syn in self.synonyms:
            if syn.composite_key_hash == key_hash:
                return syn
        return None

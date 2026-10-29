"""Registry entry model for the Registry service."""

from datetime import UTC, datetime
from typing import Any, ClassVar

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel


class SourceInfo(BaseModel):
    """Source system identification and endpoint."""

    system_id: str = Field(
        ...,
        description="Identifier of the source system"
    )
    endpoint_url: str | None = Field(
        default=None,
        description="API endpoint URL for proxied queries"
    )


class Synonym(BaseModel):
    """A composite key variant that resolves to the parent entry."""

    namespace: str = Field(
        ...,
        description="Namespace this synonym belongs to"
    )
    entity_type: str = Field(
        ...,
        description="Entity type (terminologies, terms, templates, documents, files)"
    )
    composite_key: dict[str, Any] = Field(
        ...,
        description="The actual key-value pairs"
    )
    composite_key_hash: str = Field(
        ...,
        description="SHA-256 hash of the composite key"
    )
    source_info: SourceInfo | None = Field(
        default=None,
        description="Source system information"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        default=None,
        description="User or system that created this synonym"
    )
    status: str = Field(
        default="active",
        description=(
            "active | inactive. An inactive synonym belongs to a retired "
            "entity: it stays recorded on the entry but holds no claim, so it "
            "neither blocks another entity from taking the key nor resolves. "
            "Legacy synonyms carry no status and read as active."
        ),
    )


# Registry entity types are plural; the auto-synonym's "type" part is singular.
_SINGULAR_ENTITY_TYPE = {
    "terminologies": "terminology",
    "terms": "term",
    "templates": "template",
    "documents": "document",
    "files": "file",
}


class RegistryEntry(Document):
    """
    A registered identity in the central registry.

    Supports multiple synonyms (composite keys) that all resolve to the same entity.

    Lifecycle: reserved → active → inactive
    """

    # The primary ID for this entry
    entry_id: str = Field(
        ...,
        description="Primary ID for this entry"
    )

    # Namespace this entry belongs to
    namespace: str = Field(
        ...,
        description="Namespace (e.g., 'wip', 'dev')"
    )

    # Entity type
    entity_type: str = Field(
        ...,
        description="Entity type: terminologies, terms, templates, documents, files"
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

    # Source system that owns this entry
    source_info: SourceInfo | None = Field(
        default=None,
        description="Source system information"
    )

    # Status: reserved (claimed), active (resolvable), inactive (soft-deleted)
    status: str = Field(
        default="active",
        description="Status: reserved, active, or inactive"
    )

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    created_by: str | None = Field(
        default=None,
        description="User or system that created this entry"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    updated_by: str | None = Field(
        default=None,
        description="User or system that last updated this entry"
    )

    # Flat array of all string values from primary + synonym composite keys
    search_values: list[str] = Field(
        default_factory=list,
        description="Flattened string values from all composite keys for search"
    )

    # Additional metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )

    class Settings:
        name = "registry_entries"
        indexes: ClassVar[list[IndexModel]] = [
            # Primary lookup by entry_id (globally unique)
            IndexModel(
                [("entry_id", 1)],
                unique=True,
                name="entry_id_unique_idx"
            ),
            # Lookup by namespace + entity_type
            IndexModel(
                [("namespace", 1), ("entity_type", 1), ("status", 1)],
                name="namespace_entity_status_idx"
            ),
            # Lookup by primary composite key hash
            IndexModel(
                [("primary_composite_key_hash", 1)],
                name="primary_key_hash_idx"
            ),
            # Lookup by synonym composite key hash
            IndexModel(
                [("synonyms.composite_key_hash", 1)],
                name="synonyms_key_hash_idx"
            ),
            # Status index for filtering active entries
            IndexModel(
                [("status", 1)],
                name="status_idx"
            ),
            # Index for value-based lookups
            IndexModel(
                [("search_values", 1), ("namespace", 1), ("status", 1)],
                name="search_values_namespace_status_idx"
            ),
            # Namespace-scoped dedup: same composite key in different namespaces
            # must produce separate registry entries (CASE-18)
            IndexModel(
                [("namespace", 1), ("entity_type", 1), ("primary_composite_key_hash", 1)],
                unique=True,
                partialFilterExpression={"primary_composite_key_hash": {"$gt": ""}},
                name="namespace_entity_keyhash_unique_idx"
            ),
        ]

    def get_all_hashes(self) -> list[str]:
        """Return all composite key hashes (primary + synonyms)."""
        hashes = [self.primary_composite_key_hash]
        hashes.extend(s.composite_key_hash for s in self.synonyms)
        return hashes

    def find_synonym_by_hash(self, key_hash: str) -> Synonym | None:
        """Find a synonym by its composite key hash."""
        for syn in self.synonyms:
            if syn.composite_key_hash == key_hash:
                return syn
        return None

    def active_synonyms(self) -> list[Synonym]:
        """The synonyms that claim their key and resolve."""
        return [s for s in self.synonyms if s.status != "inactive"]

    def is_auto_synonym(self, syn: Synonym) -> bool:
        """True for the synonym the owning service registers as the entity's
        own address: {"ns": <the entry's namespace>, "type": <its singular
        entity type>, …} — a term's {ns, type, terminology, value}, a
        template's {ns, type, value}, a document's {ns, type, template,
        identity_hash}. Every such synonym is minted through one helper
        (wip_auth's RegistryClient._register_auto_synonym), so the shape is
        the marker. Retirement keeps these claimed: they are how a retired
        entity stays addressable by value (to reactivate or restore it), and
        nothing else can rightfully claim them."""
        key = syn.composite_key
        return (
            key.get("ns") == self.namespace
            and key.get("type") == _SINGULAR_ENTITY_TYPE.get(self.entity_type)
        )

    def rebuild_search_values(self):
        """Rebuild the flat search_values array from the primary key and the
        active synonyms — an inactive synonym's values must not resolve."""
        values = set()
        for v in self.primary_composite_key.values():
            if isinstance(v, str):
                values.add(v)
        for syn in self.active_synonyms():
            for v in syn.composite_key.values():
                if isinstance(v, str):
                    values.add(v)
        self.search_values = sorted(values)

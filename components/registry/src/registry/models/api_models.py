"""API request and response models for the Registry service."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .entry import SourceInfo, Synonym


class StrictModel(BaseModel):
    """Base for API request models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


# =============================================================================
# Namespace API Models
# =============================================================================

class NamespaceCreate(StrictModel):
    """Request model for creating a user-facing namespace."""

    prefix: str = Field(..., description="Unique prefix (e.g., 'dev', 'staging', 'prod')")
    description: str = Field(default="", description="Human-readable description")
    isolation_mode: str = Field(
        default="open",
        description="'open' allows cross-namespace refs; 'strict' requires same-namespace only"
    )
    allowed_external_refs: list[str] = Field(
        default_factory=list,
        description="For open mode, optional allowlist of external namespace prefixes"
    )
    id_config: dict[str, Any] | None = Field(
        None,
        description="Per-entity-type ID algorithm config. Defaults to UUID7 for all."
    )
    deletion_mode: str = Field(
        default="retain",
        description="'retain' = soft-delete only; 'full' = allows hard-delete and namespace deletion"
    )
    created_by: str | None = Field(None, description="User creating the namespace")


class NamespaceUpdate(StrictModel):
    """Request model for updating a user-facing namespace."""

    description: str | None = None
    isolation_mode: str | None = None
    allowed_external_refs: list[str] | None = None
    id_config: dict[str, Any] | None = None
    deletion_mode: str | None = Field(
        default=None,
        description="'retain' = soft-delete only; 'full' = allows hard-delete and namespace deletion"
    )
    updated_by: str | None = None


class NamespaceResponse(BaseModel):
    """Response model for a user-facing namespace."""

    prefix: str
    description: str
    isolation_mode: str
    allowed_external_refs: list[str]
    id_config: dict[str, Any]
    deletion_mode: str = "retain"
    status: str
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None


class NamespaceStatsResponse(BaseModel):
    """Response model for namespace with entity counts."""

    prefix: str
    description: str
    isolation_mode: str
    deletion_mode: str = "retain"
    status: str
    entity_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Map of entity_type to entry count"
    )


# =============================================================================
# Browse API Models
# =============================================================================

class BrowseEntryItem(BaseModel):
    """A single entry in the browse response."""

    entry_id: str
    namespace: str
    entity_type: str
    primary_composite_key: dict[str, Any]
    synonyms_count: int
    status: str
    created_at: datetime
    created_by: str | None = None
    updated_at: datetime


class BrowseEntriesResponse(BaseModel):
    """Response model for browsing registry entries."""

    items: list[BrowseEntryItem]
    total: int
    page: int
    page_size: int
    pages: int = 0


# =============================================================================
# Registration API Models
# =============================================================================

class RegisterKeyItem(StrictModel):
    """Request model for registering a composite key (reserve + activate in one step)."""

    namespace: str = Field(default="wip", description="Namespace")
    entity_type: str = Field(default="terms", description="Entity type")
    entry_id: str | None = Field(None, description="Client-provided ID (if not provided, registry generates one)")
    composite_key: dict[str, Any] = Field(default_factory=dict, description="Composite key values (empty = no dedup, always generates new ID)")
    identity_values: dict[str, Any] | None = Field(
        None,
        description="Raw identity field values. Registry computes identity_hash, "
                    "injects it into composite_key, and creates a synonym with the raw values."
    )
    source_info: SourceInfo | None = Field(None, description="Source system info")
    created_by: str | None = Field(None, description="Creator identifier")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterKeyResponse(BaseModel):
    """Response model for a registration operation."""

    input_index: int
    status: str  # created, already_exists, error
    registry_id: str | None = None
    namespace: str | None = None
    entity_type: str | None = None
    identity_hash: str | None = None
    error: str | None = None


class RegisterBulkResponse(BaseModel):
    """Response model for bulk registration."""

    results: list[RegisterKeyResponse]
    total: int
    created: int
    already_exists: int
    errors: int


# =============================================================================
# Provision API Models (Registry generates IDs)
# =============================================================================

class ProvisionRequest(StrictModel):
    """Request model for provisioning IDs (registry generates them)."""

    namespace: str = Field(..., description="Namespace")
    entity_type: str = Field(..., description="Entity type")
    count: int = Field(default=1, description="Number of IDs to provision", ge=1, le=1000)
    composite_keys: list[dict[str, Any]] | None = Field(
        None,
        description="Optional composite keys to associate with provisioned IDs"
    )
    created_by: str | None = Field(None, description="Creator identifier")


class ProvisionedId(BaseModel):
    """A single provisioned (reserved) ID."""

    entry_id: str
    status: str = "reserved"


class ProvisionResponse(BaseModel):
    """Response model for ID provisioning."""

    namespace: str
    entity_type: str
    ids: list[ProvisionedId]
    total: int


# =============================================================================
# Reserve API Models (Client provides IDs)
# =============================================================================

class ReserveItem(StrictModel):
    """Request model for reserving a client-provided ID."""

    entry_id: str = Field(..., description="Client-provided ID")
    namespace: str = Field(..., description="Namespace")
    entity_type: str = Field(..., description="Entity type")
    composite_key: dict[str, Any] | None = Field(None, description="Composite key values")
    created_by: str | None = Field(None, description="Creator identifier")


class ReserveItemResponse(BaseModel):
    """Response for a single reserve operation."""

    input_index: int
    status: str  # reserved, already_exists, invalid_format, error
    entry_id: str | None = None
    error: str | None = None


class ReserveBulkResponse(BaseModel):
    """Response model for bulk reservation."""

    results: list[ReserveItemResponse]
    total: int
    reserved: int
    errors: int


# =============================================================================
# Activate API Models
# =============================================================================

class ActivateItem(StrictModel):
    """Request model for activating a reserved entry."""

    entry_id: str = Field(..., description="ID to activate")


class ActivateItemResponse(BaseModel):
    """Response for a single activate operation."""

    input_index: int
    status: str  # activated, not_found, already_active, error
    entry_id: str | None = None
    error: str | None = None


class ActivateBulkResponse(BaseModel):
    """Response model for bulk activation."""

    results: list[ActivateItemResponse]
    total: int
    activated: int
    errors: int


# =============================================================================
# Synonym API Models
# =============================================================================

class AddSynonymItem(StrictModel):
    """Request model for adding a synonym to an existing entry."""

    target_id: str = Field(..., description="ID of target entry")
    synonym_namespace: str = Field(..., description="Namespace for the synonym")
    synonym_entity_type: str = Field(..., description="Entity type for the synonym")
    synonym_composite_key: dict[str, Any] = Field(..., description="Composite key for the synonym")
    synonym_source_info: SourceInfo | None = Field(None, description="Source info for synonym")
    created_by: str | None = Field(None, description="Creator identifier")


class AddSynonymResponse(BaseModel):
    """Response model for adding a synonym."""

    input_index: int
    status: str  # added, already_exists, target_not_found, error
    registry_id: str | None = None
    error: str | None = None


class RemoveSynonymItem(StrictModel):
    """Request model for removing a synonym from an entry."""

    target_id: str = Field(..., description="ID of target entry")
    synonym_namespace: str = Field(..., description="Namespace of synonym to remove")
    synonym_entity_type: str = Field(..., description="Entity type of synonym to remove")
    synonym_composite_key: dict[str, Any] = Field(..., description="Composite key of synonym to remove")
    updated_by: str | None = Field(None, description="Updater identifier")


class RemoveSynonymResponse(BaseModel):
    """Response model for removing a synonym."""

    input_index: int
    status: str  # removed, not_found, error
    registry_id: str | None = None
    error: str | None = None


# =============================================================================
# Merge API Models
# =============================================================================

class MergeItem(StrictModel):
    """Request model for merging two entries."""

    preferred_id: str
    deprecated_id: str
    updated_by: str | None = Field(None, description="Updater identifier")


class MergeResponse(BaseModel):
    """Response model for a merge operation."""

    input_index: int
    status: str  # merged, preferred_not_found, deprecated_not_found, error
    preferred_id: str | None = None
    deprecated_id: str | None = None
    error: str | None = None


# =============================================================================
# Lookup API Models
# =============================================================================

class LookupByIdItem(StrictModel):
    """Request model for looking up by ID."""

    entry_id: str = Field(..., description="The entry ID to look up")
    namespace: str | None = Field(default=None, description="Namespace filter")
    entity_type: str | None = Field(default=None, description="Entity type filter")
    fetch_source_data: bool = Field(default=False, description="Whether to fetch from source")


class LookupByKeyItem(StrictModel):
    """Request model for looking up by composite key."""

    namespace: str = Field(default="wip", description="Namespace to search in")
    entity_type: str = Field(default="terms", description="Entity type to search in")
    composite_key: dict[str, Any] = Field(..., description="Composite key to look up")
    search_synonyms: bool = Field(default=True, description="Also search in synonyms")
    fetch_source_data: bool = Field(default=False, description="Whether to fetch from source")


class LookupResponse(BaseModel):
    """Response model for lookups."""

    input_index: int
    status: str  # found, not_found, error

    entry_id: str | None = None
    namespace: str | None = None
    entity_type: str | None = None

    matched_namespace: str | None = None
    matched_entity_type: str | None = None
    matched_composite_key: dict[str, Any] | None = None

    synonyms: list[Synonym] = Field(default_factory=list)

    matched_via: str | None = Field(
        None,
        description="How the match was found: entry_id or composite_key_value"
    )

    source_info: SourceInfo | None = None
    source_data: dict[str, Any] | None = None

    error: str | None = None


class LookupBulkResponse(BaseModel):
    """Response model for bulk lookups."""

    results: list[LookupResponse]
    total: int
    found: int
    not_found: int
    errors: int


# =============================================================================
# Resolve API Models (synonym resolution)
# =============================================================================

class ResolveItem(StrictModel):
    """Request model for resolving a synonym composite key to an entry ID."""

    composite_key: dict[str, Any] = Field(..., description="Synonym composite key to resolve")


class ResolveResponse(BaseModel):
    """Response model for a single resolve result."""

    input_index: int
    status: str  # found, not_found, error
    composite_key: dict[str, Any] | None = None
    entry_id: str | None = None
    error: str | None = None


class BulkResolveResponse(BaseModel):
    """Response model for bulk resolve."""

    results: list[ResolveResponse]
    total: int
    found: int
    not_found: int
    errors: int


# =============================================================================
# Search API Models
# =============================================================================

class SearchItem(StrictModel):
    """Request model for structured search."""

    field_criteria: dict[str, Any] = Field(
        ...,
        description="Field-value pairs to search for in composite keys"
    )
    restrict_to_namespaces: list[str] | None = Field(
        None,
        description="Only search in these namespaces (None = all)"
    )
    restrict_to_entity_types: list[str] | None = Field(
        None,
        description="Only search in these entity types (None = all)"
    )
    include_inactive: bool = Field(default=False)


class SearchByTermItem(StrictModel):
    """Request model for free-text term search."""

    term: str = Field(..., description="Term to search for across all composite key values")
    restrict_to_namespaces: list[str] | None = Field(
        None,
        description="Only search in these namespaces (None = all)"
    )
    restrict_to_entity_types: list[str] | None = Field(
        None,
        description="Only search in these entity types (None = all)"
    )
    include_inactive: bool = Field(default=False)


class SearchResult(BaseModel):
    """A single search result."""

    registry_id: str
    namespace: str
    entity_type: str
    matched_in: str  # "primary" or "synonym"
    matched_namespace: str
    matched_entity_type: str
    matched_composite_key: dict[str, Any]
    all_synonyms: list[Synonym]


class SearchResponse(BaseModel):
    """Response model for a search query."""

    input_index: int
    results: list[SearchResult]
    total_matches: int


class SearchBulkResponse(BaseModel):
    """Response model for bulk search."""

    results: list[SearchResponse]


# =============================================================================
# Update API Models
# =============================================================================

class UpdateEntryItem(StrictModel):
    """Request model for updating an entry."""

    entry_id: str
    source_info: SourceInfo | None = None
    metadata: dict[str, Any] | None = None
    updated_by: str | None = None


class UpdateEntryResponse(BaseModel):
    """Response model for an update operation."""

    input_index: int
    status: str  # updated, not_found, error
    registry_id: str | None = None
    error: str | None = None


# =============================================================================
# Delete API Models
# =============================================================================

class DeleteItem(StrictModel):
    """Request model for deleting (deactivating) an entry."""

    entry_id: str
    updated_by: str | None = None


class DeleteResponse(BaseModel):
    """Response model for a delete operation."""

    input_index: int
    status: str  # deactivated, not_found, error
    registry_id: str | None = None
    error: str | None = None


class BulkUpdateResponse(BaseModel):
    """Wrapped response for bulk update operations."""

    results: list[UpdateEntryResponse]
    total: int
    succeeded: int
    failed: int


class BulkDeleteResponse(BaseModel):
    """Wrapped response for bulk delete operations."""

    results: list[DeleteResponse]
    total: int
    succeeded: int
    failed: int


class BulkSynonymAddResponse(BaseModel):
    """Wrapped response for bulk synonym add operations."""

    results: list[AddSynonymResponse]
    total: int
    succeeded: int
    failed: int


class BulkSynonymRemoveResponse(BaseModel):
    """Wrapped response for bulk synonym remove operations."""

    results: list[RemoveSynonymResponse]
    total: int
    succeeded: int
    failed: int


class BulkMergeResponse(BaseModel):
    """Wrapped response for bulk merge operations."""

    results: list[MergeResponse]
    total: int
    succeeded: int
    failed: int


# =============================================================================
# Export/Import API Models
# =============================================================================

# =============================================================================
# Unified Search API Models
# =============================================================================

class UnifiedSearchResultItem(BaseModel):
    """A single result from unified search."""

    entry_id: str
    namespace: str
    entity_type: str
    status: str
    primary_composite_key: dict[str, Any]
    synonyms: list[Synonym] = Field(default_factory=list)
    source_info: SourceInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by: str | None = None
    updated_at: datetime
    updated_by: str | None = None
    matched_via: str = Field(
        ...,
        description="How the match was found: entry_id, composite_key_value, synonym_key_value"
    )
    matched_value: str = Field(
        ...,
        description="The actual value that matched the query"
    )
    resolution_path: str = Field(
        ...,
        description="Human-readable resolution path (e.g., 'V1-001 → synonym → T-000042 (wip-terms)')"
    )


class UnifiedSearchResponse(BaseModel):
    """Response model for unified search."""

    items: list[UnifiedSearchResultItem]
    total: int
    page: int
    page_size: int
    query: str


# =============================================================================
# Entry Detail API Models
# =============================================================================

class EntryDetailResponse(BaseModel):
    """Full detail response for a single registry entry."""

    entry_id: str
    namespace: str
    entity_type: str
    primary_composite_key: dict[str, Any]
    primary_composite_key_hash: str
    synonyms: list[Synonym] = Field(default_factory=list)
    source_info: SourceInfo | None = None
    search_values: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime
    created_by: str | None = None
    updated_at: datetime
    updated_by: str | None = None


# =============================================================================
# Export/Import API Models
# =============================================================================

class ExportResponse(BaseModel):
    """Response model for namespace export."""

    export_id: str
    prefix: str
    download_url: str
    stats: dict[str, int] = Field(
        default_factory=dict,
        description="Count of exported entities by type"
    )


class ImportRequest(StrictModel):
    """Request model for namespace import."""

    target_prefix: str | None = Field(
        None,
        description="Optional new prefix for the imported namespace"
    )
    mode: str = Field(
        default="create",
        description="Import mode: create (fail if exists), merge (add new), replace (overwrite)"
    )
    imported_by: str | None = Field(
        None,
        description="User performing the import"
    )


class ImportResponse(BaseModel):
    """Response model for namespace import."""

    prefix: str
    mode: str
    stats: dict[str, int] = Field(
        default_factory=dict,
        description="Count of imported entities by type"
    )
    source_prefix: str | None = Field(
        None,
        description="Original prefix from the export (if remapped)"
    )

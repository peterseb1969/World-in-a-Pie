"""API request and response models for the Registry service."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .id_pool import IdGeneratorConfig
from .entry import Synonym, SourceInfo


class StrictModel(BaseModel):
    """Base for API request models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


# =============================================================================
# ID Pool API Models (Internal - for ID generation pools)
# =============================================================================

class IdPoolCreate(StrictModel):
    """Request model for creating an ID pool."""

    pool_id: str = Field(..., description="Unique ID pool identifier")
    name: str = Field(..., description="Human-readable name")
    description: Optional[str] = Field(None, description="Purpose of this ID pool")
    id_generator: Optional[IdGeneratorConfig] = Field(
        None, description="ID generation configuration"
    )
    source_endpoint: Optional[str] = Field(None, description="API endpoint for external pools")
    metadata: dict[str, Any] = Field(default_factory=dict)


class IdPoolUpdate(StrictModel):
    """Request model for updating an ID pool."""

    name: Optional[str] = None
    description: Optional[str] = None
    id_generator: Optional[IdGeneratorConfig] = None
    source_endpoint: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class IdPoolResponse(BaseModel):
    """Response model for an ID pool."""

    pool_id: str
    name: str
    description: Optional[str]
    id_generator: IdGeneratorConfig
    source_endpoint: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


class IdPoolBulkResponse(BaseModel):
    """Response model for bulk ID pool operations."""

    input_index: int
    status: str  # created, updated, deleted, error
    pool_id: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Namespace API Models (User-facing - for organizing data)
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
    created_by: Optional[str] = Field(None, description="User creating the namespace")


class NamespaceUpdate(StrictModel):
    """Request model for updating a user-facing namespace."""

    description: Optional[str] = None
    isolation_mode: Optional[str] = None
    allowed_external_refs: Optional[list[str]] = None
    updated_by: Optional[str] = None


class NamespaceResponse(BaseModel):
    """Response model for a user-facing namespace."""

    prefix: str
    description: str
    isolation_mode: str
    allowed_external_refs: list[str]
    status: str
    created_at: datetime
    created_by: Optional[str]
    updated_at: datetime
    updated_by: Optional[str]
    # Derived ID pool names
    terminologies_pool: str
    terms_pool: str
    templates_pool: str
    documents_pool: str
    files_pool: str


class NamespaceStatsResponse(BaseModel):
    """Response model for namespace with entity counts."""

    prefix: str
    description: str
    isolation_mode: str
    status: str
    pools: dict[str, int] = Field(
        default_factory=dict,
        description="Map of pool_id to entry count"
    )


# =============================================================================
# Registration API Models
# =============================================================================

class RegisterKeyItem(StrictModel):
    """Request model for registering a composite key."""

    pool_id: str = Field(default="default", description="ID pool for the key")
    composite_key: dict[str, Any] = Field(..., description="Composite key values")
    source_info: Optional[SourceInfo] = Field(None, description="Source system info")
    created_by: Optional[str] = Field(None, description="Creator identifier")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterKeyResponse(BaseModel):
    """Response model for a registration operation."""

    input_index: int
    status: str  # created, already_exists, error
    registry_id: Optional[str] = None
    pool_id: Optional[str] = None
    error: Optional[str] = None


class RegisterBulkResponse(BaseModel):
    """Response model for bulk registration."""

    results: list[RegisterKeyResponse]
    total: int
    created: int
    already_exists: int
    errors: int


# =============================================================================
# Synonym API Models
# =============================================================================

class AddSynonymItem(StrictModel):
    """Request model for adding a synonym to an existing entry."""

    # Target entry identification (either by ID or by existing key)
    target_pool_id: str = Field(..., description="ID pool of the target entry")
    target_id: Optional[str] = Field(None, description="ID of target entry")
    target_composite_key: Optional[dict[str, Any]] = Field(
        None, description="Composite key to find target entry"
    )

    # The new synonym to add
    synonym_pool_id: str = Field(..., description="ID pool for the new synonym")
    synonym_composite_key: dict[str, Any] = Field(..., description="Composite key for the synonym")
    synonym_source_info: Optional[SourceInfo] = Field(None, description="Source info for synonym")
    created_by: Optional[str] = Field(None, description="Creator identifier")


class AddSynonymResponse(BaseModel):
    """Response model for adding a synonym."""

    input_index: int
    status: str  # added, already_exists, target_not_found, error
    registry_id: Optional[str] = None
    error: Optional[str] = None


class RemoveSynonymItem(StrictModel):
    """Request model for removing a synonym from an entry."""

    target_pool_id: str = Field(..., description="ID pool of the target entry")
    target_id: str = Field(..., description="ID of target entry")
    synonym_pool_id: str = Field(..., description="ID pool of synonym to remove")
    synonym_composite_key: dict[str, Any] = Field(..., description="Composite key of synonym to remove")
    updated_by: Optional[str] = Field(None, description="Updater identifier")


class RemoveSynonymResponse(BaseModel):
    """Response model for removing a synonym."""

    input_index: int
    status: str  # removed, not_found, error
    registry_id: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Merge API Models (ID-as-Synonym)
# =============================================================================

class MergeItem(StrictModel):
    """Request model for merging two entries (making one a synonym of the other)."""

    # The entry that will become the preferred/primary
    preferred_pool_id: str
    preferred_id: str

    # The entry that will be merged into the preferred
    deprecated_pool_id: str
    deprecated_id: str

    updated_by: Optional[str] = Field(None, description="Updater identifier")


class MergeResponse(BaseModel):
    """Response model for a merge operation."""

    input_index: int
    status: str  # merged, preferred_not_found, deprecated_not_found, error
    preferred_id: Optional[str] = None
    deprecated_id: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Lookup API Models
# =============================================================================

class LookupByIdItem(StrictModel):
    """Request model for looking up by ID."""

    pool_id: Optional[str] = Field(default=None, description="ID pool to search in (None = search all pools)")
    entry_id: str = Field(..., description="The entry ID to look up")
    fetch_source_data: bool = Field(default=False, description="Whether to fetch from source")


class LookupByKeyItem(StrictModel):
    """Request model for looking up by composite key."""

    pool_id: str = Field(default="default", description="ID pool to search in")
    composite_key: dict[str, Any] = Field(..., description="Composite key to look up")
    search_synonyms: bool = Field(default=True, description="Also search in synonyms")
    fetch_source_data: bool = Field(default=False, description="Whether to fetch from source")


class LookupResponse(BaseModel):
    """
    Response model for lookups.
    Always returns preferred_id and all additional_ids per spec.
    """

    input_index: int
    status: str  # found, not_found, error

    # Always return all IDs
    preferred_id: Optional[str] = None
    preferred_pool_id: Optional[str] = None
    additional_ids: list[dict[str, str]] = Field(default_factory=list)

    # The matched key info
    matched_pool_id: Optional[str] = None
    matched_composite_key: Optional[dict[str, Any]] = None

    # All synonyms
    synonyms: list[Synonym] = Field(default_factory=list)

    # How the match was found
    matched_via: Optional[str] = Field(
        None,
        description="How the match was found: entry_id, additional_id, or composite_key_value"
    )

    # Source info and optional fetched data
    source_info: Optional[SourceInfo] = None
    source_data: Optional[dict[str, Any]] = None

    error: Optional[str] = None


class LookupBulkResponse(BaseModel):
    """Response model for bulk lookups."""

    results: list[LookupResponse]
    total: int
    found: int
    not_found: int
    errors: int


# =============================================================================
# Search API Models
# =============================================================================

class SearchItem(StrictModel):
    """Request model for structured search."""

    # Field-value search criteria within composite keys
    field_criteria: dict[str, Any] = Field(
        ...,
        description="Field-value pairs to search for in composite keys"
    )
    # Optional ID pool restriction
    restrict_to_pools: Optional[list[str]] = Field(
        None,
        description="Only search in these ID pools (None = all)"
    )
    # Include inactive entries?
    include_inactive: bool = Field(default=False)


class SearchByTermItem(StrictModel):
    """Request model for free-text term search."""

    term: str = Field(..., description="Term to search for across all composite key values")
    restrict_to_pools: Optional[list[str]] = Field(
        None,
        description="Only search in these ID pools (None = all)"
    )
    include_inactive: bool = Field(default=False)


class SearchResult(BaseModel):
    """A single search result."""

    registry_id: str
    pool_id: str
    matched_in: str  # "primary" or "synonym"
    matched_pool_id: str
    matched_composite_key: dict[str, Any]
    all_synonyms: list[Synonym]
    additional_ids: list[dict[str, str]]


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

    pool_id: str
    entry_id: str
    source_info: Optional[SourceInfo] = None
    metadata: Optional[dict[str, Any]] = None
    updated_by: Optional[str] = None


class UpdateEntryResponse(BaseModel):
    """Response model for an update operation."""

    input_index: int
    status: str  # updated, not_found, error
    registry_id: Optional[str] = None
    error: Optional[str] = None


class SetPreferredItem(StrictModel):
    """Request model for setting the preferred ID."""

    # Current entry
    pool_id: str
    entry_id: str

    # The ID to make preferred (must be in additional_ids or same as entry_id)
    new_preferred_pool_id: str
    new_preferred_id: str

    updated_by: Optional[str] = None


class SetPreferredResponse(BaseModel):
    """Response model for setting preferred ID."""

    input_index: int
    status: str  # updated, not_found, id_not_in_entry, error
    new_preferred_id: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Delete API Models
# =============================================================================

class DeleteItem(StrictModel):
    """Request model for deleting (deactivating) an entry."""

    pool_id: str
    entry_id: str
    updated_by: Optional[str] = None


class DeleteResponse(BaseModel):
    """Response model for a delete operation."""

    input_index: int
    status: str  # deactivated, not_found, error
    registry_id: Optional[str] = None
    error: Optional[str] = None


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

    target_prefix: Optional[str] = Field(
        None,
        description="Optional new prefix for the imported namespace"
    )
    mode: str = Field(
        default="create",
        description="Import mode: create (fail if exists), merge (add new), replace (overwrite)"
    )
    imported_by: Optional[str] = Field(
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
    source_prefix: Optional[str] = Field(
        None,
        description="Original prefix from the export (if remapped)"
    )

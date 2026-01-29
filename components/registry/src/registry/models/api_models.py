"""API request and response models for the Registry service."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .namespace import IdGeneratorConfig
from .entry import Synonym, SourceInfo


# =============================================================================
# Namespace API Models
# =============================================================================

class NamespaceCreate(BaseModel):
    """Request model for creating a namespace."""

    namespace_id: str = Field(..., description="Unique namespace identifier")
    name: str = Field(..., description="Human-readable name")
    description: Optional[str] = Field(None, description="Purpose of this namespace")
    id_generator: Optional[IdGeneratorConfig] = Field(
        None, description="ID generation configuration"
    )
    source_endpoint: Optional[str] = Field(None, description="API endpoint for external namespaces")
    metadata: dict[str, Any] = Field(default_factory=dict)


class NamespaceUpdate(BaseModel):
    """Request model for updating a namespace."""

    name: Optional[str] = None
    description: Optional[str] = None
    id_generator: Optional[IdGeneratorConfig] = None
    source_endpoint: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class NamespaceResponse(BaseModel):
    """Response model for a namespace."""

    namespace_id: str
    name: str
    description: Optional[str]
    id_generator: IdGeneratorConfig
    source_endpoint: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


class NamespaceBulkResponse(BaseModel):
    """Response model for bulk namespace operations."""

    input_index: int
    status: str  # created, updated, deleted, error
    namespace_id: Optional[str] = None
    error: Optional[str] = None


# =============================================================================
# Registration API Models
# =============================================================================

class RegisterKeyItem(BaseModel):
    """Request model for registering a composite key."""

    namespace: str = Field(default="default", description="Namespace for the key")
    composite_key: dict[str, Any] = Field(..., description="Composite key values")
    source_info: Optional[SourceInfo] = Field(None, description="Source system info")
    created_by: Optional[str] = Field(None, description="Creator identifier")
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterKeyResponse(BaseModel):
    """Response model for a registration operation."""

    input_index: int
    status: str  # created, already_exists, error
    registry_id: Optional[str] = None
    namespace: Optional[str] = None
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

class AddSynonymItem(BaseModel):
    """Request model for adding a synonym to an existing entry."""

    # Target entry identification (either by ID or by existing key)
    target_namespace: str = Field(..., description="Namespace of the target entry")
    target_id: Optional[str] = Field(None, description="ID of target entry")
    target_composite_key: Optional[dict[str, Any]] = Field(
        None, description="Composite key to find target entry"
    )

    # The new synonym to add
    synonym_namespace: str = Field(..., description="Namespace for the new synonym")
    synonym_composite_key: dict[str, Any] = Field(..., description="Composite key for the synonym")
    synonym_source_info: Optional[SourceInfo] = Field(None, description="Source info for synonym")
    created_by: Optional[str] = Field(None, description="Creator identifier")


class AddSynonymResponse(BaseModel):
    """Response model for adding a synonym."""

    input_index: int
    status: str  # added, already_exists, target_not_found, error
    registry_id: Optional[str] = None
    error: Optional[str] = None


class RemoveSynonymItem(BaseModel):
    """Request model for removing a synonym from an entry."""

    target_namespace: str = Field(..., description="Namespace of the target entry")
    target_id: str = Field(..., description="ID of target entry")
    synonym_namespace: str = Field(..., description="Namespace of synonym to remove")
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

class MergeItem(BaseModel):
    """Request model for merging two entries (making one a synonym of the other)."""

    # The entry that will become the preferred/primary
    preferred_namespace: str
    preferred_id: str

    # The entry that will be merged into the preferred
    deprecated_namespace: str
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

class LookupByIdItem(BaseModel):
    """Request model for looking up by ID."""

    namespace: str = Field(default="default", description="Namespace of the ID")
    entry_id: str = Field(..., description="The entry ID to look up")
    fetch_source_data: bool = Field(default=False, description="Whether to fetch from source")


class LookupByKeyItem(BaseModel):
    """Request model for looking up by composite key."""

    namespace: str = Field(default="default", description="Namespace to search in")
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
    preferred_namespace: Optional[str] = None
    additional_ids: list[dict[str, str]] = Field(default_factory=list)

    # The matched key info
    matched_namespace: Optional[str] = None
    matched_composite_key: Optional[dict[str, Any]] = None

    # All synonyms
    synonyms: list[Synonym] = Field(default_factory=list)

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

class SearchItem(BaseModel):
    """Request model for structured search."""

    # Field-value search criteria within composite keys
    field_criteria: dict[str, Any] = Field(
        ...,
        description="Field-value pairs to search for in composite keys"
    )
    # Optional namespace restriction
    restrict_to_namespaces: Optional[list[str]] = Field(
        None,
        description="Only search in these namespaces (None = all)"
    )
    # Include inactive entries?
    include_inactive: bool = Field(default=False)


class SearchByTermItem(BaseModel):
    """Request model for free-text term search."""

    term: str = Field(..., description="Term to search for across all composite key values")
    restrict_to_namespaces: Optional[list[str]] = Field(
        None,
        description="Only search in these namespaces (None = all)"
    )
    include_inactive: bool = Field(default=False)


class SearchResult(BaseModel):
    """A single search result."""

    registry_id: str
    namespace: str
    matched_in: str  # "primary" or "synonym"
    matched_namespace: str
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

class UpdateEntryItem(BaseModel):
    """Request model for updating an entry."""

    namespace: str
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


class SetPreferredItem(BaseModel):
    """Request model for setting the preferred ID."""

    # Current entry
    namespace: str
    entry_id: str

    # The ID to make preferred (must be in additional_ids or same as entry_id)
    new_preferred_namespace: str
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

class DeleteItem(BaseModel):
    """Request model for deleting (deactivating) an entry."""

    namespace: str
    entry_id: str
    updated_by: Optional[str] = None


class DeleteResponse(BaseModel):
    """Response model for a delete operation."""

    input_index: int
    status: str  # deactivated, not_found, error
    registry_id: Optional[str] = None
    error: Optional[str] = None

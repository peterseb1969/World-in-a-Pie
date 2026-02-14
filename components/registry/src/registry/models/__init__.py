"""Data models for the Registry service."""

# Core models
from .id_algorithm import IdAlgorithmConfig, IdFormatValidator, IdGenerator, DEFAULT_ID_CONFIG, VALID_ENTITY_TYPES
from .namespace import Namespace
from .entry import RegistryEntry, Synonym, SourceInfo
from .id_counter import IdCounter

# API models
from .api_models import (
    # User-facing Namespace API models
    NamespaceCreate,
    NamespaceUpdate,
    NamespaceResponse,
    NamespaceStatsResponse,
    # Registration API models
    RegisterKeyItem,
    RegisterKeyResponse,
    RegisterBulkResponse,
    # Provision API models
    ProvisionRequest,
    ProvisionedId,
    ProvisionResponse,
    # Reserve API models
    ReserveItem,
    ReserveItemResponse,
    ReserveBulkResponse,
    # Activate API models
    ActivateItem,
    ActivateItemResponse,
    ActivateBulkResponse,
    # Synonym API models
    AddSynonymItem,
    AddSynonymResponse,
    RemoveSynonymItem,
    RemoveSynonymResponse,
    # Merge API models
    MergeItem,
    MergeResponse,
    # Lookup API models
    LookupByIdItem,
    LookupByKeyItem,
    LookupResponse,
    LookupBulkResponse,
    # Search API models
    SearchItem,
    SearchByTermItem,
    SearchResult,
    SearchResponse,
    SearchBulkResponse,
    # Update API models
    UpdateEntryItem,
    UpdateEntryResponse,
    SetPreferredItem,
    SetPreferredResponse,
    # Delete API models
    DeleteItem,
    DeleteResponse,
    # Export/Import API models
    ExportResponse,
    ImportRequest,
    ImportResponse,
)

__all__ = [
    # Core models
    "IdAlgorithmConfig",
    "IdFormatValidator",
    "IdGenerator",
    "DEFAULT_ID_CONFIG",
    "VALID_ENTITY_TYPES",
    "IdCounter",
    "Namespace",
    "RegistryEntry",
    "Synonym",
    "SourceInfo",
    # Namespace API models
    "NamespaceCreate",
    "NamespaceUpdate",
    "NamespaceResponse",
    "NamespaceStatsResponse",
    # Registration API models
    "RegisterKeyItem",
    "RegisterKeyResponse",
    "RegisterBulkResponse",
    # Provision API models
    "ProvisionRequest",
    "ProvisionedId",
    "ProvisionResponse",
    # Reserve API models
    "ReserveItem",
    "ReserveItemResponse",
    "ReserveBulkResponse",
    # Activate API models
    "ActivateItem",
    "ActivateItemResponse",
    "ActivateBulkResponse",
    # Other API models
    "AddSynonymItem",
    "AddSynonymResponse",
    "RemoveSynonymItem",
    "RemoveSynonymResponse",
    "MergeItem",
    "MergeResponse",
    "LookupByIdItem",
    "LookupByKeyItem",
    "LookupResponse",
    "LookupBulkResponse",
    "SearchItem",
    "SearchByTermItem",
    "SearchResult",
    "SearchResponse",
    "SearchBulkResponse",
    "UpdateEntryItem",
    "UpdateEntryResponse",
    "SetPreferredItem",
    "SetPreferredResponse",
    "DeleteItem",
    "DeleteResponse",
    "ExportResponse",
    "ImportRequest",
    "ImportResponse",
]

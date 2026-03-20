"""Data models for the Registry service."""

# Core models
# API models
from .api_models import (
    ActivateBulkResponse,
    # Activate API models
    ActivateItem,
    ActivateItemResponse,
    # Synonym API models
    AddSynonymItem,
    AddSynonymResponse,
    # Delete API models
    DeleteItem,
    DeleteResponse,
    # Export/Import API models
    ExportResponse,
    ImportRequest,
    ImportResponse,
    LookupBulkResponse,
    # Lookup API models
    LookupByIdItem,
    LookupByKeyItem,
    LookupResponse,
    # Merge API models
    MergeItem,
    MergeResponse,
    # User-facing Namespace API models
    NamespaceCreate,
    NamespaceResponse,
    NamespaceStatsResponse,
    NamespaceUpdate,
    ProvisionedId,
    # Provision API models
    ProvisionRequest,
    ProvisionResponse,
    RegisterBulkResponse,
    # Registration API models
    RegisterKeyItem,
    RegisterKeyResponse,
    RemoveSynonymItem,
    RemoveSynonymResponse,
    ReserveBulkResponse,
    # Reserve API models
    ReserveItem,
    ReserveItemResponse,
    SearchBulkResponse,
    SearchByTermItem,
    # Search API models
    SearchItem,
    SearchResponse,
    SearchResult,
    # Update API models
    UpdateEntryItem,
    UpdateEntryResponse,
)
from .entry import RegistryEntry, SourceInfo, Synonym
from .grant import GrantCreate, GrantResponse, GrantRevoke, MyNamespaceResponse, NamespaceGrant
from .id_algorithm import DEFAULT_ID_CONFIG, VALID_ENTITY_TYPES, IdAlgorithmConfig, IdFormatValidator, IdGenerator
from .id_counter import IdCounter
from .namespace import Namespace

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
    "DeleteItem",
    "DeleteResponse",
    "ExportResponse",
    "ImportRequest",
    "ImportResponse",
    # Grant models
    "NamespaceGrant",
    "GrantCreate",
    "GrantRevoke",
    "GrantResponse",
    "MyNamespaceResponse",
]

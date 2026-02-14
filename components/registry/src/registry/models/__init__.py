"""Data models for the Registry service."""

# Core models
from .id_pool import IdPool, IdGeneratorConfig, IdGeneratorType, WIP_ID_POOLS
from .namespace import Namespace
from .entry import RegistryEntry, Synonym, SourceInfo
from .id_counter import IdCounter

# API models
from .api_models import (
    # ID Pool API models (internal)
    IdPoolCreate,
    IdPoolUpdate,
    IdPoolResponse,
    IdPoolBulkResponse,
    # User-facing Namespace API models
    NamespaceCreate,
    NamespaceUpdate,
    NamespaceResponse,
    NamespaceStatsResponse,
    # Registration API models
    RegisterKeyItem,
    RegisterKeyResponse,
    RegisterBulkResponse,
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
    "IdPool",
    "IdCounter",
    "Namespace",
    "IdGeneratorConfig",
    "IdGeneratorType",
    "WIP_ID_POOLS",
    "RegistryEntry",
    "Synonym",
    "SourceInfo",
    # ID Pool API models
    "IdPoolCreate",
    "IdPoolUpdate",
    "IdPoolResponse",
    "IdPoolBulkResponse",
    # Namespace API models
    "NamespaceCreate",
    "NamespaceUpdate",
    "NamespaceResponse",
    "NamespaceStatsResponse",
    # Registration API models
    "RegisterKeyItem",
    "RegisterKeyResponse",
    "RegisterBulkResponse",
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

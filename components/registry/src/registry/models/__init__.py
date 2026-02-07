"""Data models for the Registry service."""

from .namespace import Namespace, IdGeneratorConfig, IdGeneratorType
from .namespace_group import NamespaceGroup
from .entry import RegistryEntry, Synonym, SourceInfo
from .api_models import (
    # Namespace API models
    NamespaceCreate,
    NamespaceUpdate,
    NamespaceResponse,
    NamespaceBulkResponse,
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
    # Namespace Group API models
    NamespaceGroupCreate,
    NamespaceGroupUpdate,
    NamespaceGroupResponse,
    NamespaceGroupStatsResponse,
)

__all__ = [
    # Core models
    "Namespace",
    "NamespaceGroup",
    "IdGeneratorConfig",
    "IdGeneratorType",
    "RegistryEntry",
    "Synonym",
    "SourceInfo",
    # API models
    "NamespaceCreate",
    "NamespaceUpdate",
    "NamespaceResponse",
    "NamespaceBulkResponse",
    "RegisterKeyItem",
    "RegisterKeyResponse",
    "RegisterBulkResponse",
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
    "NamespaceGroupCreate",
    "NamespaceGroupUpdate",
    "NamespaceGroupResponse",
    "NamespaceGroupStatsResponse",
]

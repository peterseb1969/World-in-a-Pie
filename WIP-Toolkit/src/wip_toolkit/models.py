"""Pydantic models for export manifests and statistics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class NamespaceConfig(BaseModel):
    """Namespace configuration from the Registry."""
    prefix: str
    description: str = ""
    isolation_mode: str = "open"
    id_config: dict[str, Any] | None = None


class ClosureInfo(BaseModel):
    """Information about referential integrity closure."""
    external_terminologies: list[str] = Field(default_factory=list)
    external_templates: list[str] = Field(default_factory=list)
    iterations: int = 0
    warnings: list[str] = Field(default_factory=list)


class EntityCounts(BaseModel):
    """Counts of each entity type in the archive."""
    terminologies: int = 0
    terms: int = 0
    relationships: int = 0
    templates: int = 0
    documents: int = 0
    files: int = 0

    @property
    def total(self) -> int:
        return self.terminologies + self.terms + self.relationships + self.templates + self.documents + self.files


class Manifest(BaseModel):
    """Archive manifest describing the export."""
    format_version: str = "1.1"
    tool_version: str = "0.2.2"
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_host: str = ""
    namespace: str = ""
    namespace_config: NamespaceConfig | None = None
    include_inactive: bool = False
    include_files: bool = False
    include_all_versions: bool = False
    closure: ClosureInfo = Field(default_factory=ClosureInfo)
    counts: EntityCounts = Field(default_factory=EntityCounts)


class ExportStats(BaseModel):
    """Statistics for an export operation."""
    namespace: str
    counts: EntityCounts = Field(default_factory=EntityCounts)
    closure_iterations: int = 0
    external_terminologies: int = 0
    external_templates: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class ImportStats(BaseModel):
    """Statistics for an import operation."""
    mode: str  # "restore" or "fresh"
    source_namespace: str = ""
    target_namespace: str = ""
    created: EntityCounts = Field(default_factory=EntityCounts)
    skipped: EntityCounts = Field(default_factory=EntityCounts)
    failed: EntityCounts = Field(default_factory=EntityCounts)
    id_mappings: int = 0
    synonyms_registered: int = 0
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0

"""Pydantic models for export manifests and statistics."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class NamespaceConfig(BaseModel):
    """Namespace configuration from the Registry.

    allowed_external_refs and deletion_mode default to None, not to the
    platform defaults: None means "this archive predates the field" and the
    restore upsert must OMIT it (leaving an existing namespace's config
    untouched), while an explicit value — including an empty list — is
    applied. A [] default would make restoring an old archive actively
    clear an existing namespace's allowlist.
    """
    prefix: str
    description: str = ""
    isolation_mode: str = "open"
    id_config: dict[str, Any] | None = None
    allowed_external_refs: list[str] | None = None
    deletion_mode: str | None = None


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
    term_relations: int = 0
    templates: int = 0
    documents: int = 0
    files: int = 0
    registry_entries: int = 0

    @property
    def total(self) -> int:
        return (
            self.terminologies + self.terms + self.term_relations
            + self.templates + self.documents + self.files
            + self.registry_entries
        )


class NamespaceEntry(BaseModel):
    """One namespace's slice of a multi-namespace (v3) archive.

    A v3 archive carries a list of these on the manifest; each maps to a
    ``namespaces/<prefix>/`` subtree of JSONL files. Per-namespace config and
    counts live here (the top-level manifest ``counts`` is the aggregate).
    """
    prefix: str
    namespace_config: NamespaceConfig | None = None
    counts: EntityCounts = Field(default_factory=EntityCounts)


class Manifest(BaseModel):
    """Archive manifest describing the export.

    **Format v3 (CASE-542)** is multi-namespace: one archive can carry N
    namespaces, each under a ``namespaces/<prefix>/`` subtree, listed in
    ``namespaces``. The top-level ``counts`` is the aggregate across all of
    them; ``namespace``/``namespace_config`` are retained only as a
    single-namespace convenience (set to the sole namespace when N==1, else
    left empty). A v2.0 (flat, single-namespace) archive is converted to v3 by
    ``wip_toolkit.convert_archive`` before the engines read it.
    """
    format_version: str = "3.0"
    tool_version: str = "0.5.0"
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_host: str = ""
    # v3: the authoritative list of namespaces in this archive.
    namespaces: list[NamespaceEntry] = Field(default_factory=list)
    # Single-namespace convenience (mirrors namespaces[0] when len==1).
    namespace: str = ""
    namespace_config: NamespaceConfig | None = None
    source_install: dict[str, Any] | None = None
    include_inactive: bool = False
    include_files: bool = False
    include_all_versions: bool = False
    closure: ClosureInfo = Field(default_factory=ClosureInfo)
    counts: EntityCounts = Field(default_factory=EntityCounts)

    def namespace_prefixes(self) -> list[str]:
        """The namespaces carried by this archive.

        Reads the v3 ``namespaces`` list; falls back to the single-namespace
        ``namespace`` field for a converted/legacy-shaped manifest where the
        list wasn't populated.
        """
        if self.namespaces:
            return [n.prefix for n in self.namespaces]
        return [self.namespace] if self.namespace else []


class ExportStats(BaseModel):
    """Statistics for an export operation."""
    namespace: str
    counts: EntityCounts = Field(default_factory=EntityCounts)
    closure_iterations: int = 0
    external_terminologies: int = 0
    external_templates: int = 0
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0


class ProgressEvent(BaseModel):
    """A progress event emitted by export/import orchestrators.

    Used by callers (e.g. a REST endpoint streaming SSE) to observe long-running
    backup/restore operations without parsing console output. The orchestrators
    invoke an optional ``progress_callback`` at meaningful checkpoints; this
    model is the payload.

    Conventions:
    - ``phase`` names are stable (e.g. ``"start"``, ``"phase_1a_entities"``,
      ``"phase_1b_documents"``, ``"phase_3_finalize"``, ``"complete"``).
      Callers may use them for ordering / progress-bar bucketing.
    - ``percent`` is best-effort and may be ``None`` for phases where total
      work is unknown ahead of time. When set, it covers 0-100 across the
      whole operation, not the current phase.
    - ``current`` and ``total`` apply within a phase (e.g. document N of M).
    - ``message`` is a short, human-readable line suitable for a status row.
    - ``details`` carries phase-specific structured fields when useful.
    """
    phase: str
    message: str
    percent: float | None = None
    current: int | None = None
    total: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


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

"""Restore provenance: one record per restore into a namespace.

Operators must be able to tell restored data from data written through the
platform, and say where it came from. A record belongs to the namespace, not
to any document. Stamping documents would break the id-preserving restore's
exact-copy contract, and a re-minted copy's documents are forks with no
identity to share. Records are only ever added. Each restore inserts its own,
and a later backup carries the namespace's records into its manifest, so a
restore of that backup first copies them in (``inherited``) and then appends
its own. Lineage therefore survives any number of backup and restore hops.
Deleting the namespace deletes its records.

The backup engine writes this collection through the raw Mongo client, like
every other collection it restores. This model owns the indexes and the
read surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from beanie import Document as BeanieDocument
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel

COLLECTION_NAME = "restore_provenance"


class RestoreProvenance(BeanieDocument):
    """One restore that wrote into ``namespace``."""

    namespace: str = Field(..., description="The namespace this record belongs to")
    restored_into: str = Field(
        ...,
        description=(
            "The namespace the restore wrote into. Equals `namespace` for the "
            "namespace's own restores. An inherited record keeps the "
            "namespace its restore happened in, which may be an earlier name."
        ),
    )
    restore_job_id: str | None = None
    mode: str = Field(..., description="restore | remap | merge")
    at: datetime
    actor: str | None = None
    archive: dict[str, Any] = Field(
        default_factory=dict,
        description="sha256 of the archive file, format_version, tool_version",
    )
    source: dict[str, Any] = Field(
        default_factory=dict,
        description="Archived namespace, source host, export time, source install",
    )
    derived_from: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "The archive's transform chain, oldest hop first, copied verbatim "
            "from its manifest. Empty for a full engine backup."
        ),
    )
    merge: dict[str, Any] | None = Field(
        None,
        description=(
            "Merge only: on_clash, add_missing, extend_terminologies, and the "
            "merge plan's per-entity-type summary"
        ),
    )
    validation_job_id: str | None = Field(
        None,
        description="The validation job this restore started for this namespace",
    )
    inherited: bool = Field(
        False,
        description="True when the record arrived with an archive rather than being written by a restore here",
    )

    class Settings:
        name = COLLECTION_NAME
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("namespace", ASCENDING), ("at", ASCENDING)],
                name="restore_provenance_ns_at_idx",
            ),
            IndexModel(
                [("restore_job_id", ASCENDING)],
                name="restore_provenance_job_idx",
                sparse=True,
            ),
        ]


class RestoreProvenanceItem(BaseModel):
    """A provenance record as the API serves it."""

    namespace: str
    restored_into: str
    restore_job_id: str | None = None
    mode: str
    at: datetime
    actor: str | None = None
    archive: dict[str, Any] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)
    derived_from: list[dict[str, Any]] = Field(default_factory=list)
    merge: dict[str, Any] | None = None
    validation_job_id: str | None = None
    inherited: bool = False


class RestoreProvenanceListResponse(BaseModel):
    """A namespace's restore history, oldest first."""

    items: list[RestoreProvenanceItem]
    total: int
    page: int
    page_size: int
    pages: int

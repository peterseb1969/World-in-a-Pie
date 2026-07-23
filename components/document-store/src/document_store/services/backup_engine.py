"""Direct MongoDB backup/restore engine (CASE-23 redesign).

Replaces the toolkit-based HTTP-fan-out engine with direct motor cursor
reads for backup and bulk inserts for restore. Runs inside document-store,
which shares the MongoDB instance with all other services.

The engine emits :class:`~wip_archive.models.ProgressEvent` via a callback,
making it compatible with the existing ``start_async_job`` / SSE machinery
in :mod:`backup_service`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from motor.motor_asyncio import AsyncIOMotorClient
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.remap import IDRemapper
from wip_archive.models import (
    EntityCounts,
    Manifest,
    NamespaceConfig,
    NamespaceEntry,
    ProgressEvent,
)

from wip_auth.composite_key import compute_composite_key_hash

from .file_storage_client import FileStorageClient
from .merge_definitions import (
    DEFINITION_TYPES,
    DefinitionsPlan,
    DefinitionsPlanner,
)
from .merge_plan import MERGE_ENTITY_SPECS, EntityPlan, MergePlanner
from .remap_restore import (
    REMAP_ENTITY_ORDER,
    RemapPlan,
    RemapSource,
    plan_multi,
)
from .reporting_client import ReportingSyncClient

logger = logging.getLogger("document_store.backup_engine")

# ---------------------------------------------------------------------------
# Collection map: (archive entity type) → (database env var, database default, collection name)
#
# Archive entity type and MongoDB collection name match: "term_relations".
# ---------------------------------------------------------------------------

_DB_REGISTRY = os.getenv("REGISTRY_DATABASE_NAME", "wip_registry")
_DB_DEF_STORE = os.getenv("DEF_STORE_DATABASE_NAME", "wip_def_store")
_DB_TEMPLATE_STORE = os.getenv("TEMPLATE_STORE_DATABASE_NAME", "wip_template_store")
_DB_DOCUMENT_STORE = os.getenv("DOCUMENT_STORE_DATABASE_NAME", "wip_document_store")

# Composite-key claims are the Registry's uniqueness gate: one row per
# (namespace, entity_type, composite_key_hash) naming the entry that owns it.
# They are NOT backed up — every claim is derivable from the registry entries
# themselves — but they MUST be recreated after a restore. Without them a
# restored namespace has entries whose keys nothing claims, so the next write
# reusing one of those keys passes the gate and mints a duplicate identity.
CLAIMS_COLLECTION: tuple[str, str] = (_DB_REGISTRY, "composite_key_claims")

# (archive_entity_type) → (database_name, collection_name)
COLLECTION_MAP: dict[str, tuple[str, str]] = {
    "terminologies": (_DB_DEF_STORE, "terminologies"),
    "terms": (_DB_DEF_STORE, "terms"),
    "term_relations": (_DB_DEF_STORE, "term_relations"),
    "templates": (_DB_TEMPLATE_STORE, "templates"),
    "documents": (_DB_DOCUMENT_STORE, "documents"),
    "files": (_DB_DOCUMENT_STORE, "files"),
    "registry_entries": (_DB_REGISTRY, "registry_entries"),
}

# Backup reads these entity types in this order.
BACKUP_ENTITY_ORDER = [
    "terminologies",
    "terms",
    "term_relations",
    "templates",
    "documents",
    "files",
    "registry_entries",
]

# Every entity stream is exported in natural-key order, so an archive
# member's bytes are a pure function of corpus content — not of whichever
# index the query planner happened to walk. Unordered exports produced row
# permutations across otherwise-identical backups, which broke byte-level
# parity checks and cost up to ~32% archive size: near-identical versions of
# the same document only deflate well when they sit inside the same 32 KB
# compression window. allow_disk_use on the cursor covers namespaces where
# the sort cannot ride an index.
EXPORT_SORT_ORDER: dict[str, list[tuple[str, int]]] = {
    "terminologies": [("terminology_id", 1)],
    "terms": [("terminology_id", 1), ("value", 1)],
    "term_relations": [("source_term_id", 1), ("relation_type", 1), ("target_term_id", 1)],
    "templates": [("template_id", 1), ("version", 1)],
    "documents": [("document_id", 1), ("version", 1)],
    "files": [("file_id", 1)],
    "registry_entries": [("entry_id", 1)],
}

# Merge writes in the same order for the same reason a restore does: it is
# dependency order. Terms need their terminology, documents need their
# template, and registry entries come last so nothing claims an identity
# before the entity holding it exists.
MERGE_ENTITY_ORDER = list(BACKUP_ENTITY_ORDER)

# The Registry's provision endpoint caps `count` per call (ProvisionRequest
# bounds it at 1000), so a remap of a namespace-sized entity type asks in
# chunks. Found live: 1099 terms in one request came back 422.
PROVISION_BATCH = 1000


class BackupEngineError(Exception):
    """Raised when the backup engine encounters a fatal error."""


class RestoreEngineError(Exception):
    """Raised when the restore engine encounters a fatal error."""


# ---------------------------------------------------------------------------
# DirectBackupEngine
# ---------------------------------------------------------------------------


class DirectBackupEngine:
    """Backup a namespace via direct MongoDB cursor reads.

    Reads from all service databases (registry, def-store, template-store,
    document-store) using the shared motor client. Writes to a ZIP archive
    via :class:`~wip_archive.archive.ArchiveWriter`.
    """

    def __init__(
        self,
        mongo_client: AsyncIOMotorClient,
        storage_client: FileStorageClient | None,
        progress: Callable[[ProgressEvent], None],
    ) -> None:
        self._mongo = mongo_client
        self._storage = storage_client
        self._progress = progress

    async def run_backup(
        self,
        namespaces: str | list[str],
        archive_path: Path,
        *,
        include_files: bool = False,
        skip_documents: bool = False,
        latest_only: bool = False,
        tmp_dir: Path | None = None,
    ) -> None:
        """Run the full backup pipeline over one or more namespaces (CASE-542).

        Each namespace is read independently and written to its own
        ``namespaces/<prefix>/`` subtree of the v3 archive; the manifest carries
        a per-namespace entry plus the aggregate counts. A single-element list
        (or a bare string, accepted for ergonomics) produces a 1-namespace v3
        archive.
        """
        if isinstance(namespaces, str):
            namespaces = [namespaces]
        if not namespaces:
            raise BackupEngineError("run_backup requires at least one namespace")

        self._emit(
            "start",
            f"Starting backup of {len(namespaces)} namespace(s): {', '.join(namespaces)}",
            percent=0,
        )

        # Pre-count entities across all namespaces for percent calculation
        per_ns_counts: dict[str, dict[str, int]] = {}
        for ns in namespaces:
            per_ns_counts[ns] = await self._pre_count(ns, skip_documents)
        total_entities = sum(sum(c.values()) for c in per_ns_counts.values())

        writer = ArchiveWriter(archive_path, tmp_dir=tmp_dir)
        processed = 0

        try:
            for ns in namespaces:
                for entity_type in BACKUP_ENTITY_ORDER:
                    if skip_documents and entity_type == "documents":
                        continue

                    db_name, coll_name = COLLECTION_MAP[entity_type]
                    collection = self._mongo[db_name][coll_name]

                    query = self._build_query(ns)

                    phase_name = f"phase_{entity_type}"
                    expected = per_ns_counts[ns].get(entity_type, 0)
                    self._emit(
                        phase_name,
                        f"[{ns}] Reading {entity_type} ({expected} expected)",
                        percent=self._pct(processed, total_entities),
                    )

                    count = 0
                    cursor = collection.find(query, allow_disk_use=True).sort(
                        EXPORT_SORT_ORDER[entity_type]
                    )
                    async for doc in cursor:
                        doc.pop("_id", None)
                        writer.add_entity(entity_type, doc, namespace=ns)
                        count += 1
                        processed += 1
                        if count % 5000 == 0:
                            self._emit(
                                phase_name,
                                f"[{ns}] {entity_type}: {count}/{expected}",
                                percent=self._pct(processed, total_entities),
                                current=count,
                                total=expected,
                            )

                    logger.info("Backed up %d %s for namespace %s", count, entity_type, ns)

                # Blobs (per namespace; file_ids are globally unique so the
                # archive's flat blobs/ dir never collides across namespaces)
                if include_files and self._storage:
                    await self._backup_blobs(writer, ns, archive_path, processed, total_entities)

            # Build manifest: one entry per namespace + aggregate counts
            ns_entries: list[NamespaceEntry] = []
            agg: dict[str, int] = {et: 0 for et in BACKUP_ENTITY_ORDER}
            for ns in namespaces:
                counts = EntityCounts(
                    **{et: writer.entity_count(et, namespace=ns) for et in BACKUP_ENTITY_ORDER}
                )
                ns_entries.append(
                    NamespaceEntry(
                        prefix=ns,
                        namespace_config=await self._read_namespace_config(ns),
                        counts=counts,
                    )
                )
                for et in BACKUP_ENTITY_ORDER:
                    agg[et] += writer.entity_count(et, namespace=ns)

            single = len(namespaces) == 1
            manifest = Manifest(
                format_version="3.0",
                exported_at=datetime.now(UTC),
                source_host=socket.gethostname(),
                namespaces=ns_entries,
                namespace=namespaces[0] if single else "",
                namespace_config=ns_entries[0].namespace_config if single else None,
                source_install={
                    "schema_version": "1.4",
                    "hash_version": 1,
                },
                include_files=include_files,
                include_all_versions=not latest_only,
                counts=EntityCounts(**agg),
            )

            self._emit("phase_finalize", "Writing archive", percent=95)
            writer.write(manifest)

            self._emit("complete", "Backup complete", percent=100)

        except Exception:
            # ArchiveWriter cleans up temp files in __del__, but let's be explicit
            with contextlib.suppress(Exception):
                writer._cleanup()
            raise

    async def _pre_count(
        self, namespace: str, skip_documents: bool
    ) -> dict[str, int]:
        """Count documents per entity type for progress reporting."""
        query = self._build_query(namespace)
        counts: dict[str, int] = {}
        for entity_type in BACKUP_ENTITY_ORDER:
            if skip_documents and entity_type == "documents":
                counts[entity_type] = 0
                continue
            db_name, coll_name = COLLECTION_MAP[entity_type]
            counts[entity_type] = await self._mongo[db_name][coll_name].count_documents(query)
        return counts

    def _build_query(self, namespace: str) -> dict[str, Any]:
        # A backup is a full copy of the namespace — every entity, every
        # status, deliberately. Live data references inactive and archived
        # entities (documents pin inactive template versions, edges point at
        # archived documents), so an archive missing them would be a restore
        # trap. The retired include_inactive flag's status filter matched a
        # status no persisted entity ever carried; the query is the namespace
        # alone.
        return {"namespace": namespace}

    async def _backup_blobs(
        self,
        writer: ArchiveWriter,
        namespace: str,
        archive_path: Path,
        processed: int,
        total_entities: int,
    ) -> None:
        """Stream file blobs from MinIO into the archive."""
        assert self._storage is not None
        self._emit(
            "phase_blobs",
            "Downloading file blobs",
            percent=self._pct(processed, total_entities),
        )
        # Read file entries from what was already written
        files_db = self._mongo[_DB_DOCUMENT_STORE]["files"]
        query = {"namespace": namespace, "status": {"$ne": "deleted"}}
        count = 0
        async for file_doc in files_db.find(query, {"file_id": 1, "storage_key": 1}):
            file_id = file_doc.get("file_id") or str(file_doc.get("_id"))
            storage_key = file_doc.get("storage_key", file_id)
            try:
                with writer.open_blob(file_id) as fh:
                    async for chunk in self._storage.download_stream(storage_key):
                        fh.write(chunk)
                count += 1
            except Exception:
                logger.warning("Failed to download blob for file %s", file_id, exc_info=True)

        logger.info("Backed up %d blobs for namespace %s", count, namespace)

    async def _read_namespace_config(self, namespace: str) -> NamespaceConfig | None:
        """Read namespace config from the Registry database."""
        ns_doc = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": namespace}
        )
        if ns_doc is None:
            return None
        return NamespaceConfig(
            prefix=ns_doc.get("prefix", namespace),
            description=ns_doc.get("description", ""),
            isolation_mode=ns_doc.get("isolation_mode", "open"),
            id_config=ns_doc.get("id_config"),
            allowed_external_refs=ns_doc.get("allowed_external_refs"),
            deletion_mode=ns_doc.get("deletion_mode"),
        )

    def _emit(
        self,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        current: int | None = None,
        total: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._progress(
            ProgressEvent(
                phase=phase,
                message=message,
                percent=percent,
                current=current,
                total=total,
                details=details or {},
            )
        )

    @staticmethod
    def _pct(done: int, total: int) -> float:
        if total == 0:
            return 0.0
        # Reserve 0-90% for entity reads, 90-95% for blobs, 95-100% for finalize
        return min(round(done / total * 90, 1), 90.0)


# ---------------------------------------------------------------------------
# DirectRestoreEngine
# ---------------------------------------------------------------------------


class DirectRestoreEngine:
    """Restore a namespace from an archive via direct MongoDB bulk inserts.

    ID-preserving restore into an empty namespace. Reads the archive with
    :class:`~wip_archive.archive.ArchiveReader`, bulk-inserts into all
    service databases.
    """

    def __init__(
        self,
        mongo_client: AsyncIOMotorClient,
        storage_client: FileStorageClient | None,
        progress: Callable[[ProgressEvent], None],
        *,
        registry_base_url: str | None = None,
        registry_api_key: str | None = None,
        reporting_client: ReportingSyncClient | None = None,
    ) -> None:
        self._mongo = mongo_client
        self._storage = storage_client
        self._progress = progress
        # Structured outcome for a caller that was not watching the event
        # stream. Progress events overwrite each other on the job record, so a
        # dry run's per-type counts — the whole reason to ask — are gone by the
        # time it completes unless they are collected here.
        self.result: dict[str, Any] = {}
        self._registry_url = registry_base_url or os.getenv(
            "REGISTRY_URL", "http://localhost:8001"
        )
        self._registry_api_key = cast(str, registry_api_key or os.getenv(
            "REGISTRY_API_KEY",
            os.getenv("API_KEY", "dev_master_key_for_testing"),
        ))
        # Reporting verification phases (restore-verification design): when a
        # client is injected AND reporting-sync answers, the restore verifies
        # the reporting layer at phase boundaries. When reporting-sync is
        # unreachable (core preset deploys without it), the phases degrade to
        # a logged warning — a restore never fails because the convenience
        # layer is absent, only on positive verification failures.
        self._reporting = reporting_client

    async def run_restore(
        self,
        archive_path: Path,
        target_namespace: str,
        *,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        drop_stale_reporting: bool = False,
        dry_run: bool = False,
    ) -> None:
        """Run the full restore pipeline over every namespace in the archive.

        Identity-only restore (CASE-542): each namespace restores to itself and
        its target must be empty. A ``target_namespace`` that differs from the
        archive's own namespace is rejected — ID-preserving restore cannot
        re-namespace data (entities, registry entries, and composite keys all
        embed the source namespace); re-namespacing is the planned remap mode.

        With ``dry_run`` the engine runs every precondition (archive format,
        empty targets, reporting schema) and reports what it *would* restore
        from the manifest, then completes without writing anything — no
        namespace upsert, no inserts, no blobs, no reporting sync.
        """
        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            targets = self._resolve_targets(reader, manifest, target_namespace)

            self._emit(
                "start",
                f"Starting restore of {len(targets)} namespace(s)",
                percent=0,
            )

            # Phase 1: validate ALL targets empty before writing anything.
            # The reporting precondition runs alongside: the namespace's
            # reporting schema must be absent/empty too, or stale tables
            # would shadow the restored data (fossil-schema incident class).
            self._emit("phase_validate", "Checking target namespaces are empty", percent=2)
            for _src, tgt in targets:
                await self._check_namespace_empty(tgt)
                await self._check_reporting_precondition(
                    tgt, drop_stale_reporting, dry_run=dry_run
                )

            entry_by_prefix = {e.prefix: e for e in manifest.namespaces}
            restore_order = [
                "terminologies",
                "terms",
                "term_relations",
                "templates",
                "documents",
                "files",
                "registry_entries",
            ]

            if dry_run:
                self.result = {
                    "mode": "restore",
                    "dry_run": True,
                    "namespaces": {
                        tgt: {
                            entity_type: (
                                getattr(entry_by_prefix[src].counts, entity_type, 0)
                                if src in entry_by_prefix
                                else reader.entity_count(entity_type, namespace=src)
                            )
                            for entity_type in restore_order
                        }
                        for src, tgt in targets
                    },
                }
                self._dry_run_report(
                    reader, targets, entry_by_prefix, restore_order,
                    skip_documents=skip_documents, skip_files=skip_files,
                )
                return

            total = sum(getattr(manifest.counts, et, 0) for et in restore_order)
            processed = 0

            for src, tgt in targets:
                entry = entry_by_prefix.get(src)
                ns_config = entry.namespace_config if entry else manifest.namespace_config

                self._emit(
                    "phase_namespace",
                    f"Creating/updating namespace '{tgt}'",
                    percent=self._pct(processed, total),
                )
                await self._upsert_namespace(tgt, ns_config)

                for entity_type in restore_order:
                    if skip_documents and entity_type == "documents":
                        continue
                    if skip_files and entity_type == "files":
                        continue

                    db_name, coll_name = COLLECTION_MAP[entity_type]
                    collection = self._mongo[db_name][coll_name]

                    expected = getattr(entry.counts, entity_type, 0) if entry else 0
                    phase_name = f"phase_{entity_type}"
                    self._emit(
                        phase_name,
                        f"[{tgt}] Restoring {entity_type} ({expected} expected)",
                        percent=self._pct(processed, total),
                    )

                    batch: list[dict[str, Any]] = []
                    count = 0
                    claimed = 0
                    taken_by_other = 0

                    for entity in reader.read_entities(entity_type, namespace=src):
                        entity.pop("_id", None)  # Strip MongoDB internal ID
                        batch.append(entity)

                        if len(batch) >= batch_size:
                            await self._insert_batch(collection, batch, entity_type)
                            if entity_type == "registry_entries":
                                ok, theirs = await self._claim_inserted_entries(
                                    tgt, batch
                                )
                                claimed += ok
                                taken_by_other += theirs
                            count += len(batch)
                            processed += len(batch)
                            batch = []
                            if count % 5000 == 0:
                                self._emit(
                                    phase_name,
                                    f"[{tgt}] {entity_type}: {count}/{expected}",
                                    percent=self._pct(processed, total),
                                    current=count,
                                    total=expected,
                                )

                    if batch:
                        await self._insert_batch(collection, batch, entity_type)
                        if entity_type == "registry_entries":
                            ok, theirs = await self._claim_inserted_entries(tgt, batch)
                            claimed += ok
                            taken_by_other += theirs
                        count += len(batch)
                        processed += len(batch)

                    logger.info("Restored %d %s into namespace %s", count, entity_type, tgt)
                    if entity_type == "registry_entries":
                        self._report_claims(tgt, claimed, taken_by_other)

                    # Structural gate between templates and documents: the
                    # reporting tables the restored templates imply must be
                    # creatable and correctly shaped BEFORE any document
                    # moves. Catches broken bookkeeping / mis-shaped tables
                    # at the first template instead of after a full restore.
                    if entity_type == "templates":
                        await self._reporting_phase_structure(tgt)

                # Count parity after the namespace's data is in: expected vs
                # actual rows, bounded wait. Mismatch completes WITH a
                # warning — never a hard fail (operator ruling).
                await self._reporting_phase_counts(tgt, skip_documents=skip_documents)

            # Blobs are flat (namespace-agnostic, globally-unique file_ids) — restore
            # the whole archive's blob set once after all namespaces are in.
            if not skip_files and self._storage:
                await self._restore_blobs(reader, targets[0][1])

        self._emit("complete", "Restore complete", percent=100)

    # Documents are the only thing a merge resolves by policy. Definitions
    # (terminologies, terms, templates) are a precondition instead: a merge
    # that had to reconcile schemas while writing data would be validating one
    # side's documents against the other's contract.
    MERGE_CLASH_POLICIES = ("skip", "overwrite", "newer")

    async def run_remap(
        self,
        archive_path: Path,
        target_namespace: str = "",
        *,
        namespace_map: dict[str, str] | None = None,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        dry_run: bool = False,
    ) -> None:
        """Restore an archive's content as brand-new entities.

        Nothing is kept: every entity is registered afresh with a
        Registry-minted id, and every reference between them is rewritten. It
        is what lets a namespace be restored beside the one it came from —
        two live copies cannot share a canonical id.

        A single-namespace archive takes ``target_namespace``. A
        multi-namespace archive takes ``namespace_map`` — an explicit
        ``{source: target}`` for EVERY namespace it carries; several sources
        may share one target (Registry-key collisions between them refuse at
        plan time), and cross-namespace references between archived
        namespaces follow their entities to the new names.

        The targets must be empty, as for a plain restore. What differs is
        that the archive's own registry entries are NOT written: provisioning
        creates the entries, so importing the archived ones would duplicate
        every identity under its old id.

        Ordering is deliberate. Entries are provisioned as *reserved*, which
        does not resolve, so a job that dies partway leaves an invisible and
        reconcilable namespace; a single activation at the end makes the whole
        set visible at once.

        No reporting work happens here. A restore's job is to get the data in;
        PostgreSQL is a derived layer that can be rebuilt at any time, and
        syncing mid-restore only couples the write to a service that has
        nothing to say yet. Verify afterwards — `check_reporting_parity` and
        the namespace validation job both exist for that.
        """
        if not target_namespace and not namespace_map:
            raise RestoreEngineError(
                "A remap restore needs a target namespace — it mints new "
                "identities, so it must be told where to put them."
            )

        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            sources = self._archive_namespaces(reader, manifest)
            mapping = self._resolve_remap_mapping(
                sources, target_namespace, namespace_map
            )
            targets = list(dict.fromkeys(mapping.values()))

            self._emit(
                "start",
                "Starting remap restore: "
                + ", ".join(f"'{s}' → '{t}'" for s, t in mapping.items()),
                percent=0,
            )
            self._emit("phase_validate", "Checking the targets are empty", percent=2)
            for target in targets:
                await self._check_namespace_empty(target)
                await self._check_reporting_precondition(
                    target, False, dry_run=dry_run
                )

            entries = {e.prefix: e for e in manifest.namespaces}
            remap_sources: list[RemapSource] = []
            for src, target in mapping.items():
                entities_by_type: dict[str, list[dict[str, Any]]] = {}
                for entity_type in (*REMAP_ENTITY_ORDER, "term_relations"):
                    if skip_documents and entity_type == "documents":
                        continue
                    if skip_files and entity_type == "files":
                        continue
                    rows = []
                    for row in reader.read_entities(entity_type, namespace=src):
                        row.pop("_id", None)
                        rows.append(row)
                    entities_by_type[entity_type] = rows

                # Identity fields come from the ARCHIVE's templates: the
                # target has none yet, and these are the definitions the
                # documents were written against.
                identity_fields = {
                    template.get("value"): list(template.get("identity_fields") or [])
                    for template in entities_by_type.get("templates", [])
                    if template.get("value")
                }
                remap_sources.append(RemapSource(
                    source=src, target=target,
                    entities_by_type=entities_by_type,
                    identity_fields=identity_fields,
                ))

            # The namespaces have to exist before anything is provisioned: the
            # Registry mints ids per the TARGET namespace's id_config, and
            # refuses outright for a namespace it does not know. Creating them
            # is a write, so a dry run skips it — which is exactly why the dry
            # run cannot catch this ordering: its stand-in provisioner never
            # asks the Registry anything. For several sources collapsing into
            # one target, the FIRST source's config wins (archive order);
            # allowed_external_refs naming other archive sources are rewritten
            # through the mapping, names outside the archive are kept.
            if not dry_run:
                for target in targets:
                    first_src = next(s for s, t in mapping.items() if t == target)
                    entry = entries.get(first_src)
                    ns_config = (
                        entry.namespace_config if entry
                        else manifest.namespace_config
                    )
                    ns_config = self._rewrite_ns_config_refs(ns_config, mapping)
                    # Fresh means fresh: a remap re-mints every id, so the target
                    # must not inherit the source's id_config. A prefixed source
                    # scheme would otherwise re-mint the source's exact ids and
                    # collide with the live original on the global entry_id index
                    # (CASE-784). preserve_id_config=False defaults the target to
                    # UUID7 (or keeps a pre-created target's own config).
                    await self._upsert_namespace(
                        target, ns_config, preserve_id_config=False
                    )

            # The remapper gets the namespace mapping so reference snapshots
            # (resolved.namespace) follow their entities — after a fresh
            # restore no data may point at the original namespaces.
            remapper = IDRemapper(namespace_map=dict(mapping))
            provision_for = (
                self._dry_run_provisioner() if dry_run
                else self._registry_provisioner
            )
            plans = await plan_multi(remap_sources, remapper, provision_for)

            phase = "phase_dry_run" if dry_run else "phase_remap"
            for src, target in mapping.items():
                for entity_type, count in plans[src].summary().items():
                    if count:
                        self._emit(phase, f"[{src} → {target}] {entity_type}: {count}")
            self.result = {
                "mode": "fresh",
                "dry_run": dry_run,
                "namespace_map": dict(mapping),
                # Kept for single-namespace callers; absent shape-change risk.
                "source_namespace": next(iter(mapping)),
                "target_namespace": mapping[next(iter(mapping))],
                "planned": {src: plans[src].summary() for src in mapping},
            }

            if dry_run:
                self._emit(
                    "complete",
                    "Remap dry run complete — nothing provisioned, nothing "
                    "written",
                    percent=100,
                    details=self.result,
                )
                return

            for src, target in mapping.items():
                await self._write_remapped(target, plans[src], batch_size)

            if not skip_files and self._storage:
                merged_file_map: dict[str, str] = {}
                for plan in plans.values():
                    merged_file_map.update(plan.id_map["files"])
                await self._remap_blobs(reader, merged_file_map)

            for target in targets:
                await self._activate_entries(
                    target,
                    [
                        new_id
                        for src, t in mapping.items() if t == target
                        for m in plans[src].id_map.values()
                        for new_id in m.values()
                    ],
                )
            for src, target in mapping.items():
                await self._record_provenance(target, src, manifest)

        self._emit("complete", "Remap restore complete", percent=100,
                   details=self.result)

    def _resolve_remap_mapping(
        self,
        sources: list[str],
        target_namespace: str,
        namespace_map: dict[str, str] | None,
    ) -> dict[str, str]:
        """Settle where every archived namespace goes.

        Without a map, only a single-namespace archive is unambiguous and
        target_namespace names its destination. With a map, EVERY archived
        namespace must be mapped — restoring an unmapped namespace to its old
        name by default would silently collide with the live original, which
        is the exact accident this mode exists to avoid. A target equal to
        the source name is legal precisely when that namespace is absent (the
        per-target emptiness check enforces it). Several sources may share
        one target; key collisions refuse at plan time.
        """
        if namespace_map:
            unknown = sorted(set(namespace_map) - set(sources))
            unmapped = sorted(set(sources) - set(namespace_map))
            if unknown or unmapped:
                problems = []
                if unmapped:
                    problems.append(f"unmapped archive namespace(s) {unmapped}")
                if unknown:
                    problems.append(f"mapped but not in the archive: {unknown}")
                raise RestoreEngineError(
                    "namespace_map must cover exactly the archive's "
                    f"namespaces {sorted(sources)} — " + "; ".join(problems)
                )
            invalid = {s: t for s, t in namespace_map.items() if not t}
            if invalid:
                raise RestoreEngineError(
                    f"namespace_map has empty target(s) for {sorted(invalid)}"
                )
            # Preserve archive order, not caller order — provisioning order
            # and the N:1 first-source-config rule both key off it.
            return {s: namespace_map[s] for s in sources}

        if not target_namespace:
            raise RestoreEngineError(
                "A remap restore needs a target namespace — it mints new "
                "identities, so it must be told where to put them."
            )
        if len(sources) > 1:
            raise RestoreEngineError(
                f"This archive carries {len(sources)} namespaces {sources}. "
                "A remap restore needs an explicit source-to-target mapping "
                "for each one: pass namespace_map."
            )
        return {sources[0]: target_namespace}

    @staticmethod
    def _rewrite_ns_config_refs(
        ns_config: Any, mapping: dict[str, str]
    ) -> Any:
        """allowed_external_refs that name other ARCHIVE namespaces must
        follow them to their new names; names outside the archive (e.g.
        'wip') are kept as-is."""
        if not ns_config:
            return ns_config
        config = dict(ns_config) if isinstance(ns_config, dict) else ns_config
        refs = (
            config.get("allowed_external_refs")
            if isinstance(config, dict)
            else getattr(config, "allowed_external_refs", None)
        )
        if not refs:
            return ns_config
        rewritten = [mapping.get(r, r) for r in refs]
        if isinstance(config, dict):
            config["allowed_external_refs"] = rewritten
            return config
        config.allowed_external_refs = rewritten
        return config

    def _registry_provisioner(self, namespace: str) -> Any:
        """Ask the Registry for ids. It is the identity authority; a service
        minting its own UUIDs would be inventing identity for itself."""
        async def provision(
            entity_type: str, keys: list[dict[str, Any]]
        ) -> list[str]:
            import httpx

            if not keys:
                return []
            url = f"{self._registry_url}/api/registry/entries/provision"
            headers = {
                "X-API-Key": self._registry_api_key,
                "Content-Type": "application/json",
            }
            minted: list[str] = []
            async with httpx.AsyncClient(timeout=120.0) as client:
                # The endpoint caps `count` at PROVISION_BATCH per call, so a
                # namespace-sized type has to be asked for in chunks. Ids come
                # back in request order and are appended in that order, which
                # is what lets the caller zip them against its entities.
                for start in range(0, len(keys), PROVISION_BATCH):
                    chunk = keys[start:start + PROVISION_BATCH]
                    resp = await client.post(
                        url,
                        json={
                            "namespace": namespace,
                            "entity_type": entity_type,
                            "count": len(chunk),
                            "composite_keys": chunk,
                        },
                        headers=headers,
                    )
                    if resp.status_code != 200:
                        raise RestoreEngineError(
                            f"Could not provision {len(chunk)} {entity_type} "
                            f"id(s) in '{namespace}' (chunk at offset {start} "
                            f"of {len(keys)}): {resp.status_code} — {resp.text}"
                        )
                    minted.extend(item["entry_id"] for item in resp.json()["ids"])
            return minted

        return provision

    @staticmethod
    def _dry_run_provisioner() -> Any:
        """Factory for stand-in ids, ONE counter per dry run.

        Provisioning writes reserved entries, so a dry run must not call the
        Registry — a preview that leaves rows behind is not a preview.

        Returns a per-target factory (the shape plan_multi expects) whose
        provisioners all share one counter: placeholder ids must be unique
        across every source and entity type in the run, exactly as
        Registry-minted ids are. A counter per source hands two sources'
        terminologies the same placeholder, their terms' Registry keys then
        embed identical parent ids, and an N:1 dry run reports a collision
        the real run would never hit — a preview that refuses what apply
        allows.
        """
        counter = {"n": 0}

        async def provision(
            entity_type: str, keys: list[dict[str, Any]]
        ) -> list[str]:
            ids = []
            for _ in keys:
                counter["n"] += 1
                ids.append(f"would-mint-{counter['n']}")
            return ids

        return lambda _target: provision

    async def _write_remapped(
        self, namespace: str, plan: RemapPlan, batch_size: int
    ) -> None:
        """Insert the rewritten rows, in dependency order.

        Registry entries are absent on purpose: provisioning created them, so
        writing the archive's would duplicate every identity under its old id.
        """
        for entity_type in (*REMAP_ENTITY_ORDER, "term_relations"):
            rows = plan.rows.get(entity_type) or []
            if not rows:
                continue
            db_name, coll_name = COLLECTION_MAP[entity_type]
            collection = self._mongo[db_name][coll_name]
            for start in range(0, len(rows), batch_size):
                await self._insert_batch(
                    collection, rows[start:start + batch_size], entity_type
                )
            self._emit(
                f"phase_{entity_type}",
                f"[{namespace}] wrote {len(rows)} {entity_type} under new ids",
            )

    async def _remap_blobs(
        self, reader: ArchiveReader, file_id_map: dict[str, str]
    ) -> None:
        """Copy each blob under its file's new id.

        The storage key follows the file id, so re-minting the id means the
        bytes need a new home rather than a second reference to the old one —
        which would leave two files sharing one object and either one able to
        delete it.
        """
        assert self._storage is not None
        if not file_id_map:
            return
        self._emit("phase_blobs", f"Copying {len(file_id_map)} blob(s)", percent=92)
        copied = 0
        for old_id, new_id in file_id_map.items():
            data = reader.read_blob(old_id)
            if data is None:
                logger.warning("Blob %s missing from archive; skipped", old_id)
                continue
            await self._storage.upload(
                storage_key=new_id,
                content=data,
                content_type="application/octet-stream",
            )
            copied += 1
        logger.info("Copied %d blob(s) under new ids", copied)

    async def _activate_entries(
        self, namespace: str, entry_ids: list[str]
    ) -> None:
        """Make the provisioned entries resolvable, in one step.

        Until this runs the namespace is invisible: reserved entries do not
        resolve. That is the property that makes a failed remap recoverable
        rather than half-live.
        """
        import httpx

        if not entry_ids:
            return
        self._emit(
            "phase_activate",
            f"[{namespace}] activating {len(entry_ids)} identit(ies)",
        )
        url = f"{self._registry_url}/api/registry/entries/activate"
        headers = {
            "X-API-Key": self._registry_api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            for start in range(0, len(entry_ids), 500):
                batch = entry_ids[start:start + 500]
                resp = await client.post(
                    url,
                    json=[{"entry_id": entry_id} for entry_id in batch],
                    headers=headers,
                )
                if resp.status_code != 200:
                    raise RestoreEngineError(
                        f"Could not activate {len(batch)} restored identit(ies) "
                        f"in '{namespace}': {resp.status_code} — {resp.text}. "
                        "The data is written but invisible; re-run activation "
                        "or delete the namespace and retry."
                    )
                errors = resp.json().get("errors", 0)
                if errors:
                    self._emit(
                        "warning",
                        f"[{namespace}] {errors} identit(ies) did not activate "
                        "— they remain reserved and will not resolve",
                    )

    async def _record_provenance(
        self, namespace: str, source: str, manifest: Any
    ) -> None:
        """Note where a remapped namespace came from, on the namespace itself.

        Per-entity lineage was rejected: a synonym asserts that two ids denote
        the same entity, and a re-minted copy is a fork that diverges on the
        first write. Provenance belongs to the namespace, which is one line
        that cannot drift — and it travels forward, since a later backup
        captures the description into its own manifest.

        Written after the namespace upsert, which sets the description from
        the manifest and would otherwise overwrite it.
        """
        exported = getattr(manifest, "exported_at", None)
        host = getattr(manifest, "source_host", None) or "unknown host"
        note = (
            f"Restored {datetime.now(UTC).date().isoformat()} from an archive "
            f"of '{source}' taken {exported or 'at an unrecorded time'} on {host}."
        )
        collection = self._mongo[_DB_REGISTRY]["namespaces"]
        live = await collection.find_one({"prefix": namespace})
        existing = (live or {}).get("description") or ""
        result = await collection.update_one(
            {"prefix": namespace},
            {"$set": {"description": f"{existing} {note}".strip()}},
        )
        if not getattr(result, "matched_count", 1):
            # The namespace upsert should have created this row. Losing the
            # provenance line is not worth failing a completed restore over,
            # but it should not vanish quietly either.
            self._emit(
                "warning",
                f"[{namespace}] could not record restore provenance — no "
                "namespace record to write it to",
            )
            return
        self._emit("phase_namespace", f"[{namespace}] {note}")

    async def run_merge(
        self,
        archive_path: Path,
        target_namespace: str,
        *,
        on_clash: str = "skip",
        add_missing: bool = False,
        extend_terminologies: bool = False,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        dry_run: bool = False,
    ) -> None:
        """Merge an archive into an existing, possibly non-empty namespace.

        Two passes. The first checks that the two sides' definitions —
        terminologies, terms and templates — are compatible by content, and
        produces the ID mapping that says which of them are the same thing
        under different IDs. Only then do documents move, against a schema both
        sides are known to agree on.

        Definitions are a precondition, not a policy: a mismatch refuses.
        ``add_missing`` and ``extend_terminologies`` are the two opt-ins that
        let the merge extend the target's definitions instead of refusing;
        without them, changing a live namespace's schema is never a side
        effect of restoring data into it.

        Documents get the policy: ``on_clash`` decides what happens where the
        target already holds an identity.

        Both passes are computed before anything is written, so ``dry_run`` is
        exact rather than indicative, and still fails on everything a real run
        would refuse.
        """
        if on_clash not in self.MERGE_CLASH_POLICIES:
            raise RestoreEngineError(
                f"Invalid on_clash '{on_clash}' — must be one of "
                f"{', '.join(self.MERGE_CLASH_POLICIES)}"
            )

        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            targets = self._resolve_targets(
                reader, manifest, target_namespace, allow_redirect=True
            )

            self._emit(
                "start",
                f"Starting merge into {len(targets)} namespace(s)",
                percent=0,
            )

            # The empty-target precondition inverts: merge REQUIRES the
            # namespace to exist (creating it would be a plain restore), and
            # its reporting schema is expected to be there already.
            self._emit("phase_validate", "Checking target namespaces", percent=2)
            for _src, tgt in targets:
                await self._check_namespace_exists(tgt)
                await self._check_merge_reporting_precondition(tgt)

            entry_by_prefix = {e.prefix: e for e in manifest.namespaces}
            inserted_file_ids: set[str] = set()

            for src, tgt in targets:
                entry = entry_by_prefix.get(src)
                ns_config = entry.namespace_config if entry else manifest.namespace_config
                await self._report_namespace_config_drift(tgt, ns_config)

                archive_entities, doc_groups = self._build_merge_plan_inputs(
                    reader, src, skip_documents=skip_documents,
                    skip_files=skip_files,
                )

                if src != tgt:
                    # Redirected merge: the IDs must be free here, and every
                    # entity has to be moved to the target namespace before it
                    # is matched — the keys it matches on embed the namespace.
                    await self._check_ids_are_free(
                        archive_entities.get("registry_entries") or [], src, tgt
                    )
                    for entity_type, entities in archive_entities.items():
                        archive_entities[entity_type] = [
                            self._rewrite_namespace(entity_type, entity, src, tgt)
                            for entity in entities
                        ]
                    doc_groups = {
                        doc_id: [
                            self._rewrite_namespace("documents", row, src, tgt)
                            for row in rows
                        ]
                        for doc_id, rows in doc_groups.items()
                    }
                    self._emit(
                        "phase_merge_plan",
                        f"[{tgt}] merging from namespace '{src}' — entities "
                        "and composite keys re-scoped to the target",
                    )

                # ---- Pass 1: definitions -------------------------------
                definitions = await DefinitionsPlanner(
                    self._mongo, COLLECTION_MAP,
                    add_missing=add_missing,
                    extend_terminologies=extend_terminologies,
                ).plan(archive_entities, tgt)
                self._emit_definitions_plan(tgt, definitions, dry_run=dry_run)
                self._enforce_definitions_gate(tgt, definitions)

                # The mapping pass 1 produced is what lets pass 2 point the
                # incoming data at the definitions that survived.
                remapper = IDRemapper()
                for old_id, new_id in definitions.mapping["terminologies"].items():
                    remapper.add_terminology_mapping(old_id, new_id)
                for old_id, new_id in definitions.mapping["terms"].items():
                    remapper.add_term_mapping(old_id, new_id)
                for old_id, new_id in definitions.mapping["templates"].items():
                    remapper.add_template_mapping(old_id, new_id)

                # ---- Pass 2: everything else ---------------------------
                planner = MergePlanner(self._mongo, COLLECTION_MAP)
                built: dict[str, EntityPlan] = {}
                rewritten = 0
                for entity_type in MERGE_ENTITY_ORDER:
                    if entity_type in DEFINITION_TYPES:
                        continue
                    entities = archive_entities.get(entity_type)
                    if entities is None:
                        continue
                    entities, changed = self._rewrite_entities(
                        entity_type, entities, remapper
                    )
                    rewritten += changed
                    archive_entities[entity_type] = entities
                    if entity_type == "documents":
                        doc_groups = self._regroup_documents(doc_groups, remapper)
                    plan = await planner.plan(entity_type, entities, tgt)
                    built[entity_type] = plan
                    # A document the target holds under another ID is still a
                    # collision for policy, but the ID it survives under has to
                    # reach anything referencing it.
                    for old_id, new_id in plan.mapping.items():
                        if entity_type == "documents":
                            remapper.add_document_mapping(old_id, new_id)
                        elif entity_type == "files":
                            remapper.add_file_mapping(old_id, new_id)

                if rewritten:
                    self._emit(
                        "phase_merge_plan",
                        f"[{tgt}] {rewritten} incoming entit(ies) had references "
                        f"rewritten onto the target's definitions",
                    )
                self._emit_merge_plan(tgt, built, on_clash=on_clash, dry_run=dry_run)
                self.result.setdefault("mode", "merge")
                self.result["dry_run"] = dry_run
                self.result.setdefault("namespaces", {})[tgt] = {
                    "definitions": definitions.summary(),
                    "incompatibilities": len(definitions.incompatibilities),
                    "target_wins": len(definitions.target_wins),
                    "entities": {
                        entity_type: plan.summary()
                        for entity_type, plan in built.items()
                        if any(plan.summary().values())
                    },
                }
                self._enforce_merge_gates(tgt, built)

                # What this merge is about to bring with it — a reference is
                # satisfied by the target OR by something arriving alongside.
                arriving = {
                    "templates": {
                        t["template_id"]
                        for t in definitions.to_add["templates"]
                        if t.get("template_id")
                    },
                    "terms": {
                        t["term_id"]
                        for t in definitions.to_add["terms"]
                        if t.get("term_id")
                    },
                    "documents": {
                        d["document_id"]
                        for d in built.get(
                            "documents", EntityPlan(entity_type="documents")
                        ).to_insert
                        if d.get("document_id")
                    },
                    "files": {
                        f["file_id"]
                        for f in built.get(
                            "files", EntityPlan(entity_type="files")
                        ).to_insert
                        if f.get("file_id")
                    },
                }
                documents_plan = built.get("documents")
                rows_to_write: list[dict[str, Any]] = []
                if documents_plan is not None:
                    for head in documents_plan.to_insert:
                        rows_to_write.extend(
                            doc_groups.get(head.get("document_id", ""), [head])
                        )
                    if on_clash != "skip":
                        rows_to_write.extend(
                            clash.entity for clash in documents_plan.differing_clashes
                        )
                await self._check_merge_references(tgt, rows_to_write, arriving)

                if dry_run:
                    continue

                await self._apply_definitions(tgt, definitions, remapper, batch_size)
                await self._apply_merge(
                    tgt, built, doc_groups,
                    on_clash=on_clash, batch_size=batch_size,
                )
                inserted_file_ids.update(
                    f["file_id"] for f in built.get(
                        "files", EntityPlan(entity_type="files")
                    ).to_insert
                    if f.get("file_id")
                )
                await self._reporting_phase_counts(tgt, skip_documents=skip_documents)

            if dry_run:
                self._emit(
                    "complete",
                    "Merge dry run complete — plan computed, no changes made",
                    percent=100,
                    details=self.result,
                )
                return

            # Only blobs for files the merge actually inserted: a file the
            # target already had keeps its bytes, and re-uploading them would
            # be work at best and an orphan object at worst.
            if not skip_files and self._storage and inserted_file_ids:
                await self._restore_blobs(reader, targets[0][1], only=inserted_file_ids)

        self._emit("complete", "Merge complete", percent=100, details=self.result)

    def _build_merge_plan_inputs(
        self,
        reader: ArchiveReader,
        src: str,
        *,
        skip_documents: bool,
        skip_files: bool,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        """Read the archive's entities for one namespace, ready for planning.

        Documents are planned at the *logical* level: an archived document is
        a chain of version rows, and it is the chain that clashes with the
        target's chain, not each row separately. The head row represents the
        group during planning; ``doc_groups`` keeps the full chains so an
        inserted document brings all its history.
        """
        entities_by_type: dict[str, list[dict[str, Any]]] = {}
        doc_groups: dict[str, list[dict[str, Any]]] = {}

        for entity_type in MERGE_ENTITY_ORDER:
            if skip_documents and entity_type == "documents":
                continue
            if skip_files and entity_type == "files":
                continue

            entities: list[dict[str, Any]] = []
            for entity in reader.read_entities(entity_type, namespace=src):
                entity.pop("_id", None)
                entities.append(entity)

            if entity_type == "documents":
                for row in entities:
                    doc_groups.setdefault(row.get("document_id", ""), []).append(row)
                entities = [
                    max(rows, key=lambda r: r.get("version", 1))
                    for rows in doc_groups.values()
                ]

            entities_by_type[entity_type] = entities

        return entities_by_type, doc_groups

    async def _check_ids_are_free(
        self, entries: list[dict[str, Any]], source: str, target: str
    ) -> None:
        """A redirected merge may only carry IDs this Registry does not know.

        Canonical IDs are preserved by a merge, and a Registry entry belongs to
        exactly one namespace — so an entity that still exists here cannot also
        be placed in another namespace under the same ID. That is not a policy
        question with a sensible default; it is a request the identity model
        cannot represent, and the only honest answer is to refuse.

        In practice this holds whenever the archive comes from somewhere else:
        another instance, or a namespace since deleted here. When it does not
        hold, the caller wants a copy, and a copy needs re-minted IDs.

        Checked before anything is written. Left to the database, the same
        collision surfaces as a duplicate-key error partway through, with some
        of the merge already applied.
        """
        entry_ids = [e["entry_id"] for e in entries if e.get("entry_id")]
        if not entry_ids:
            return

        db_name, coll_name = COLLECTION_MAP["registry_entries"]
        collection = self._mongo[db_name][coll_name]
        taken: list[dict[str, Any]] = []
        for start in range(0, len(entry_ids), 500):
            batch = entry_ids[start:start + 500]
            cursor = collection.find(
                {"entry_id": {"$in": batch}},
                {"entry_id": 1, "namespace": 1, "entity_type": 1},
            )
            async for row in cursor:
                taken.append(row)
                if len(taken) >= 5:
                    break
            if len(taken) >= 5:
                break

        if not taken:
            return

        detail = "; ".join(
            f"{row.get('entry_id')} (already in '{row.get('namespace')}' "
            f"as {row.get('entity_type')})"
            for row in taken[:5]
        )
        raise RestoreEngineError(
            f"Merge of '{source}' into '{target}' refused — the archive's "
            f"entities are still registered on this instance: {detail}. A "
            "merge preserves canonical IDs, and one ID cannot name an entity "
            "in two namespaces. Merge an archive whose namespace no longer "
            "exists here, or from another instance."
        )

    def _rewrite_namespace(
        self,
        entity_type: str,
        entity: dict[str, Any],
        source: str,
        target: str,
    ) -> dict[str, Any]:
        """Move one entity from the source namespace to the target.

        The ``namespace`` field is the easy half. The load-bearing half is the
        Registry composite key, which embeds the namespace as ``ns`` and is
        hashed into the value the uniqueness gate and every claim are built
        on: left alone, the imported entry would claim a key naming the old
        namespace and collide with nothing, silently opting out of the gate.

        Only ``ns`` components equal to the SOURCE namespace are rewritten. A
        synonym legitimately scoped to some third namespace keeps its own.
        """
        result = dict(entity)
        if result.get("namespace") == source:
            result["namespace"] = target
        if entity_type != "registry_entries":
            return result

        key = result.get("primary_composite_key")
        if isinstance(key, dict) and key.get("ns") == source:
            new_key = {**key, "ns": target}
            result["primary_composite_key"] = new_key
            result["primary_composite_key_hash"] = compute_composite_key_hash(new_key)

        synonyms = result.get("synonyms")
        if isinstance(synonyms, list):
            rewritten = []
            for synonym in synonyms:
                if not isinstance(synonym, dict):
                    rewritten.append(synonym)
                    continue
                syn = dict(synonym)
                if syn.get("namespace") == source:
                    syn["namespace"] = target
                syn_key = syn.get("composite_key")
                if isinstance(syn_key, dict) and syn_key.get("ns") == source:
                    new_syn_key = {**syn_key, "ns": target}
                    syn["composite_key"] = new_syn_key
                    syn["composite_key_hash"] = compute_composite_key_hash(new_syn_key)
                rewritten.append(syn)
            result["synonyms"] = rewritten

        return result

    def _rewrite_entities(
        self,
        entity_type: str,
        entities: list[dict[str, Any]],
        remapper: IDRemapper,
    ) -> tuple[list[dict[str, Any]], int]:
        """Point one type's outward references at the surviving IDs.

        Returns the rewritten entities and how many actually changed. Skipping
        an entity because the target already has it is only half the job — the
        rows that referenced it are still carrying the other install's ID, and
        inserting those unrewritten is how a merge imports dangling
        references.
        """
        rewriter = {
            "terms": remapper.remap_term,
            "term_relations": remapper.remap_term_relation,
            "templates": remapper.remap_template,
            "documents": remapper.remap_document,
            "registry_entries": lambda e: self._rewrite_registry_entry(e, remapper),
        }.get(entity_type)
        # Terminologies and files have no outward references of their own.
        if rewriter is None:
            return entities, 0

        rewritten: list[dict[str, Any]] = []
        changed = 0
        for entity in entities:
            new_entity = rewriter(entity)
            if new_entity != entity:
                changed += 1
            rewritten.append(new_entity)
        return rewritten, changed

    def _rewrite_registry_entry(
        self, entry: dict[str, Any], remapper: IDRemapper
    ) -> dict[str, Any]:
        """Rewrite the IDs embedded in an entry's composite keys, and rehash.

        A registry entry's key is what the uniqueness gate and every claim are
        built on, and for most entity types it embeds a parent's canonical ID
        (a term's key carries ``terminology_id``, a document's carries
        ``template_id``). Left alone, the imported entry would claim a key
        naming an entity that does not exist here — and would fail to match
        the target's equivalent entry, which is keyed on the surviving ID.

        The hash must be recomputed from the rewritten key using the Registry's
        own algorithm, which is why it lives in wip_auth rather than being
        re-derived here.
        """
        result = dict(entry)
        key = result.get("primary_composite_key")
        if isinstance(key, dict):
            new_key = self._remap_key_values(key, remapper)
            if new_key != key:
                result["primary_composite_key"] = new_key
                result["primary_composite_key_hash"] = compute_composite_key_hash(
                    new_key
                )

        synonyms = result.get("synonyms")
        if isinstance(synonyms, list):
            new_synonyms = []
            for synonym in synonyms:
                if not isinstance(synonym, dict):
                    new_synonyms.append(synonym)
                    continue
                syn = dict(synonym)
                syn_key = syn.get("composite_key")
                if isinstance(syn_key, dict):
                    new_syn_key = self._remap_key_values(syn_key, remapper)
                    if new_syn_key != syn_key:
                        syn["composite_key"] = new_syn_key
                        syn["composite_key_hash"] = compute_composite_key_hash(
                            new_syn_key
                        )
                new_synonyms.append(syn)
            result["synonyms"] = new_synonyms

        return result

    @staticmethod
    def _remap_key_values(
        key: dict[str, Any], remapper: IDRemapper
    ) -> dict[str, Any]:
        """Replace any ID-valued component of a composite key.

        Composite keys are flat maps of scalars, and only the ID-shaped values
        are remappable — ``ns``, ``value`` and ``label`` components pass
        through because no map contains them.
        """
        maps = (
            remapper.terminology_map,
            remapper.term_map,
            remapper.template_map,
            remapper.document_map,
            remapper.file_map,
        )
        result = dict(key)
        for name, value in key.items():
            if not isinstance(value, str):
                continue
            for id_map in maps:
                if value in id_map:
                    result[name] = id_map[value]
                    break
        return result

    def _regroup_documents(
        self,
        doc_groups: dict[str, list[dict[str, Any]]],
        remapper: IDRemapper,
    ) -> dict[str, list[dict[str, Any]]]:
        """Apply the document rewrite to every version row, not just the head.

        Planning sees one head row per document, but an insert carries the
        whole chain — and every row in it references the same rewritten
        template and terms.
        """
        return {
            doc_id: [remapper.remap_document(row) for row in rows]
            for doc_id, rows in doc_groups.items()
        }

    @staticmethod
    def _record_matches(
        entity_type: str, plan: EntityPlan, remapper: IDRemapper
    ) -> None:
        """Feed a type's matches into the remapper for the types that follow.

        Term relations and registry entries get no map: nothing references
        them by ID, so a match there is simply a skip.
        """
        add = {
            "terminologies": remapper.add_terminology_mapping,
            "terms": remapper.add_term_mapping,
            "templates": remapper.add_template_mapping,
            "documents": remapper.add_document_mapping,
            "files": remapper.add_file_mapping,
        }.get(entity_type)
        if add is None:
            return
        for match in plan.matches:
            add(match.old_id, match.new_id)

    def _emit_definitions_plan(
        self, namespace: str, definitions: DefinitionsPlan, *, dry_run: bool
    ) -> None:
        """Report pass 1: what matched, what would be added, what the target
        kept."""
        phase = "phase_dry_run" if dry_run else "phase_definitions"
        for entity_type, counts in definitions.summary().items():
            if not any(counts.values()):
                continue
            self._emit(
                phase,
                f"[{namespace}] {entity_type}: {counts['unchanged']} already "
                f"identical, {counts['add']} to add, {counts['mapped']} mapped "
                "to the target's IDs",
            )
        # Never silent: an operator who loses a label or an alias list to the
        # target's copy finds out later from a UI that changed under them.
        for note in definitions.target_wins[:20]:
            self._emit(
                phase,
                f"[{namespace}] {note.entity_type} '{note.name}': target's "
                f"{', '.join(note.fields)} kept, the archive's discarded "
                f"({note.detail})",
            )
        if len(definitions.target_wins) > 20:
            self._emit(
                phase,
                f"[{namespace}] …and {len(definitions.target_wins) - 20} more "
                "definition(s) where the target's metadata was kept",
            )

    def _enforce_definitions_gate(
        self, namespace: str, definitions: DefinitionsPlan
    ) -> None:
        """Definitions are the precondition — an incompatibility stops the
        merge before any document moves."""
        if definitions.compatible:
            return
        detail = "; ".join(
            f"{issue.entity_type} '{issue.name}' — {issue.reason}"
            for issue in definitions.incompatibilities[:5]
        )
        raise RestoreEngineError(
            f"Merge into '{namespace}' refused — "
            f"{len(definitions.incompatibilities)} definition(s) are not "
            f"compatible with the target. {detail}. Documents cannot be merged "
            "under definitions the two sides disagree on."
        )

    async def _apply_definitions(
        self,
        namespace: str,
        definitions: DefinitionsPlan,
        remapper: IDRemapper,
        batch_size: int,
    ) -> None:
        """Insert the definitions the opt-in strategies allowed.

        Dependency order again: a term added to the target must point at the
        terminology that survived, not at the archive's copy of it.
        """
        for entity_type in DEFINITION_TYPES:
            rows = definitions.to_add[entity_type]
            if not rows:
                continue
            if entity_type == "terms":
                rows = [remapper.remap_term(row) for row in rows]
            elif entity_type == "templates":
                rows = [remapper.remap_template(row) for row in rows]

            db_name, coll_name = COLLECTION_MAP[entity_type]
            collection = self._mongo[db_name][coll_name]
            for start in range(0, len(rows), batch_size):
                await self._insert_batch(
                    collection, rows[start:start + batch_size], entity_type
                )
            self._emit(
                "phase_definitions",
                f"[{namespace}] added {len(rows)} {entity_type} the target "
                "did not have",
            )

    def _merge_policy_for(self, entity_type: str, *, on_clash: str) -> str:
        if entity_type == "documents":
            return on_clash
        # Term relations dedup by their endpoints, files by checksum, registry
        # entries by composite key — for all three the target's copy IS the
        # entity, so there is nothing an archive copy could add.
        return "skip"

    def _emit_merge_plan(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        *,
        on_clash: str,
        dry_run: bool,
    ) -> None:
        """Report the classification — the dry run's whole output, and a real
        merge's record of what it is about to do."""
        phase = "phase_dry_run" if dry_run else "phase_merge_plan"
        verb = "would merge" if dry_run else "merging"
        for entity_type, plan in plans.items():
            counts = plan.summary()
            if not any(counts.values()):
                continue
            policy = self._merge_policy_for(entity_type, on_clash=on_clash)
            self._emit(
                phase,
                f"[{namespace}] {verb} {entity_type}: "
                f"insert={counts['insert']}, unchanged={counts['unchanged']}, "
                f"clash={counts['clash']} (on clash: {policy}), "
                f"conflict={counts['conflict']}",
            )
            for clash in plan.differing_clashes[:5]:
                self._emit(
                    phase,
                    f"[{namespace}] {entity_type} clash: "
                    f"{self._describe_entity(entity_type, clash.entity)} — "
                    + (
                        f"differs in {', '.join(sorted(clash.diff))}"
                        if clash.diff
                        else "target holds this identity already"
                    ),
                )

    @staticmethod
    def _describe_entity(entity_type: str, entity: dict[str, Any]) -> str:
        """Name an entity the way an operator reading a merge report would."""
        spec = MERGE_ENTITY_SPECS[entity_type]
        parts = [
            f"{name}={entity.get(name)!r}"
            for name in (spec.logical_fields or spec.id_fields)
        ]
        return " ".join(parts) or "<unidentified>"

    @staticmethod
    def _referenced_ids(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
        """Every entity id the given document rows point at, by kind."""
        wanted: dict[str, set[str]] = {
            "templates": set(), "terms": set(),
            "documents": set(), "files": set(),
        }
        for row in rows:
            if row.get("template_id"):
                wanted["templates"].add(row["template_id"])
            for ref in row.get("term_references") or []:
                if ref.get("term_id"):
                    wanted["terms"].add(ref["term_id"])
            for ref in row.get("references") or []:
                resolved = ref.get("resolved") or {}
                if resolved.get("document_id"):
                    wanted["documents"].add(resolved["document_id"])
            for ref in row.get("file_references") or []:
                if ref.get("file_id"):
                    wanted["files"].add(ref["file_id"])
        return wanted

    async def _resolvable_ids(
        self, entity_type: str, wanted: set[str]
    ) -> set[str]:
        """Which of these ids the target instance already holds.

        Not namespace-scoped: a document referencing one in another namespace
        is legitimate, and shared vocabularies are the normal case.
        """
        if not wanted:
            return set()
        id_field = {
            "templates": "template_id", "terms": "term_id",
            "documents": "document_id", "files": "file_id",
        }[entity_type]
        db_name, coll_name = COLLECTION_MAP[entity_type]
        collection = self._mongo[db_name][coll_name]
        found: set[str] = set()
        ids = list(wanted)
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            found.update(
                await collection.distinct(id_field, {id_field: {"$in": batch}})
            )
        return found

    async def _check_merge_references(
        self,
        namespace: str,
        rows_to_write: list[dict[str, Any]],
        arriving: dict[str, set[str]],
    ) -> None:
        """Refuse a merge that would import references resolving to nothing.

        Checked BEFORE writing, unlike a restore's post-hoc verification, and
        for one reason: a merge lands in a live namespace that cannot be
        deleted to recover. A restore into a fresh namespace can be thrown
        away and retried; a merge that pollutes production data cannot.

        A reference is satisfied if the target already holds its target, or if
        this merge is about to insert it. ``arriving`` carries the second set.

        This is the same question ``integrity_service`` answers after the fact,
        asked on a different substrate: that one walks Beanie documents through
        the service clients, this walks raw archive dicts through direct Mongo
        batches. The post-merge validation job remains the authoritative check.
        """
        if not rows_to_write:
            return

        wanted = self._referenced_ids(rows_to_write)
        problems: list[str] = []
        for entity_type, ids in wanted.items():
            unresolved = ids - arriving.get(entity_type, set())
            if not unresolved:
                continue
            unresolved -= await self._resolvable_ids(entity_type, unresolved)
            for missing in sorted(unresolved)[:5]:
                problems.append(f"{entity_type[:-1]} '{missing}'")

        if problems:
            raise RestoreEngineError(
                f"Merge into '{namespace}' refused — incoming documents "
                f"reference {len(problems)} entit(ies) that neither the target "
                f"holds nor this merge supplies: {', '.join(problems[:5])}. "
                "Merging them would import references that resolve to nothing. "
                "Include the missing entities in the archive, or merge the "
                "namespace that holds them first."
            )

    def _enforce_merge_gates(
        self, namespace: str, plans: dict[str, EntityPlan]
    ) -> None:
        """Refuse on the one thing no policy covers: an ID naming two things."""
        conflicts = [
            (entity_type, conflict)
            for entity_type, plan in plans.items()
            for conflict in plan.conflicts
        ]
        if not conflicts:
            return
        detail = "; ".join(
            f"{entity_type}: {conflict.reason}"
            for entity_type, conflict in conflicts[:5]
        )
        raise RestoreEngineError(
            f"Merge into '{namespace}' refused — {len(conflicts)} identity "
            f"conflict(s) between archive and target. {detail}. One ID names "
            "two different entities across the two sides; no automatic "
            "resolution is defensible, since picking either would destroy the "
            "other identity."
        )

    async def _apply_merge(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        doc_groups: dict[str, list[dict[str, Any]]],
        *,
        on_clash: str,
        batch_size: int,
    ) -> None:
        """Write the plan. Inserts first, then policy-driven clash handling."""
        for entity_type in MERGE_ENTITY_ORDER:
            plan = plans.get(entity_type)
            if plan is None:
                continue

            db_name, coll_name = COLLECTION_MAP[entity_type]
            collection = self._mongo[db_name][coll_name]

            rows = plan.to_insert
            if entity_type == "documents":
                # An inserted document brings its whole version chain.
                rows = [
                    row
                    for head in plan.to_insert
                    for row in doc_groups.get(head.get("document_id", ""), [head])
                ]

            for start in range(0, len(rows), batch_size):
                await self._insert_batch(
                    collection, rows[start:start + batch_size], entity_type
                )
            if rows:
                self._emit(
                    f"phase_{entity_type}",
                    f"[{namespace}] inserted {len(rows)} {entity_type}",
                )

            # Same structural gate a plain restore runs: the reporting tables
            # the new templates imply must exist and be correctly shaped
            # before any document moves.
            if entity_type == "templates":
                await self._reporting_phase_structure(namespace)

            if entity_type == "documents" and on_clash in ("overwrite", "newer"):
                await self._overwrite_documents(
                    namespace, plan, doc_groups, on_clash=on_clash
                )

        entries_plan = plans.get("registry_entries")
        if entries_plan and entries_plan.to_insert:
            claimed, taken_by_other = await self._claim_inserted_entries(
                namespace, entries_plan.to_insert
            )
            self._report_claims(namespace, claimed, taken_by_other)

    @staticmethod
    def _as_timestamp(value: Any) -> datetime | None:
        """Normalize a stored timestamp for comparison, or None if unusable.

        The two sides arrive in different shapes: the target's rows come from
        MongoDB as naive datetimes that are UTC by convention, the archive's
        come from JSONL as ISO-8601 strings. Comparing them raw raises rather
        than answering, so both are coerced to aware UTC and anything
        unparseable becomes None — which the caller treats as "cannot tell".
        """
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None

    def _archive_is_newer(
        self, archive_row: dict[str, Any], target_row: dict[str, Any]
    ) -> bool | None:
        """Is the archive's copy more recently updated? None = cannot tell.

        Compares ``updated_at`` — when the content last changed — rather than
        the document_id's embedded UUID7 time, which records when the document
        was first CREATED. A document created in January and edited yesterday
        would otherwise lose to one created in June and never touched.

        Across two installs this is only as good as the two machines' clock
        discipline: UTC removes timezone error, not skew.
        """
        archive_at = self._as_timestamp(archive_row.get("updated_at"))
        target_at = self._as_timestamp(target_row.get("updated_at"))
        if archive_at is None or target_at is None:
            return None
        return archive_at > target_at

    async def _overwrite_documents(
        self,
        namespace: str,
        plan: EntityPlan,
        doc_groups: dict[str, list[dict[str, Any]]],
        *,
        on_clash: str = "overwrite",
    ) -> None:
        """Let the archive win, going forward.

        The archive's LATEST version of a clashing identity is appended as one
        new version on top of the target's head, adopting the target's
        document_id. The target's history is preserved and the archive's is not
        spliced in: interleaving two independent version chains has no defined
        order and would corrupt the (document_id, version) contract.

        A ``versioned: false`` template has no such contract to protect — its
        lifecycle IS overwrite-in-place — so there the single row is replaced.

        Under ``newer`` the same write happens, but only where the archive's
        copy is STRICTLY more recently updated. A tie keeps the target: equal
        timestamps carry no information about which side to prefer, and
        writing on no information is worse than leaving a live namespace
        alone. An unusable timestamp on either side is reported and also keeps
        the target.
        """
        clashes = plan.differing_clashes
        if not clashes:
            return

        db_name, coll_name = COLLECTION_MAP["documents"]
        collection = self._mongo[db_name][coll_name]
        versioned = await self._template_versioned_flags(
            namespace, [c.entity.get("template_id") for c in clashes]
        )

        appended = 0
        replaced = 0
        kept = 0
        undecidable = 0
        new_rows: list[dict[str, Any]] = []
        for clash in clashes:
            source_rows = doc_groups.get(
                clash.entity.get("document_id", ""), [clash.entity]
            )
            latest = max(source_rows, key=lambda r: r.get("version", 1))
            target_head = max(clash.targets, key=lambda r: r.get("version", 1))

            if on_clash == "newer":
                verdict = self._archive_is_newer(latest, target_head)
                if verdict is None:
                    undecidable += 1
                    kept += 1
                    continue
                if not verdict:
                    kept += 1
                    continue

            row = dict(latest)
            row["document_id"] = target_head.get("document_id")

            if versioned.get(clash.entity.get("template_id"), True) is False:
                row["version"] = target_head.get("version", 1)
                await collection.replace_one(
                    {
                        "namespace": namespace,
                        "document_id": row["document_id"],
                        "version": row["version"],
                    },
                    row,
                )
                replaced += 1
            else:
                row["version"] = target_head.get("version", 1) + 1
                new_rows.append(row)
                appended += 1

        if new_rows:
            await self._insert_batch(collection, new_rows, "documents")

        summary = (
            f"[{namespace}] {appended + replaced} clashing document(s) taken "
            f"from the archive ({appended} appended as a new version, "
            f"{replaced} replaced in place on versioned:false templates)"
        )
        if on_clash == "newer":
            summary += f", {kept} kept because the target's copy is not older"
        self._emit("phase_documents", summary)

        if undecidable:
            self._emit(
                "warning",
                f"[{namespace}] {undecidable} document(s) could not be compared "
                "by update time (a missing or unparseable updated_at on one "
                "side) — the target's copy was kept for those.",
            )

    async def _template_versioned_flags(
        self, namespace: str, template_ids: list[str | None]
    ) -> dict[str, bool]:
        """``versioned`` per template — immutable after create, so any row of
        a template answers for all its versions."""
        wanted = [t for t in template_ids if t]
        flags: dict[str, bool] = {}
        if not wanted:
            return flags
        db_name, coll_name = COLLECTION_MAP["templates"]
        cursor = self._mongo[db_name][coll_name].find(
            {"namespace": namespace, "template_id": {"$in": wanted}},
            {"template_id": 1, "versioned": 1},
        )
        async for row in cursor:
            flags[row.get("template_id")] = row.get("versioned", True)
        return flags

    async def _check_namespace_exists(self, namespace: str) -> None:
        """Merge inverts the empty-target precondition: the namespace must be
        there. Creating it would mean there is nothing to merge into, which is
        a plain restore."""
        ns_doc = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": namespace}
        )
        if ns_doc is None:
            raise RestoreEngineError(
                f"Namespace '{namespace}' does not exist — merge restores into "
                "an existing namespace. Use a plain restore to create it from "
                "this archive."
            )

    async def _check_merge_reporting_precondition(self, namespace: str) -> None:
        """Reporting must be usable, but a populated schema is expected here.

        The plain restore refuses a non-empty reporting schema because stale
        tables would shadow restored data. A merge is *supposed* to land in a
        live namespace, so the same finding is normal; only unusable
        bookkeeping — which would make the merge complete with a silently
        broken reporting layer — still refuses.
        """
        if not self._reporting:
            return
        parity = await self._reporting.parity(namespace, include_counts=False)
        if parity is None:
            self._reporting_unavailable(namespace, "precondition check")
            return
        if not parity.get("bookkeeping_tables_ok", True):
            raise RestoreEngineError(
                f"Reporting bookkeeping tables are unusable: "
                f"{parity.get('bookkeeping_error')} — the merge would complete "
                "with a silently broken reporting layer. Remediate first."
            )
        if not parity.get("schema_present"):
            self._emit(
                "warning",
                f"[{namespace}] has no reporting schema yet — the merge will "
                "create it, but a live namespace without one means reporting "
                "was never synced here. Verify after the merge.",
            )

    async def _report_namespace_config_drift(
        self, namespace: str, ns_config: NamespaceConfig | None
    ) -> None:
        """Compare the archive's namespace config against the live one.

        Merge never applies it: the target namespace is live and its
        configuration belongs to whoever is running it, not to an archive
        taken at some earlier point. Drift is still worth naming — an
        id_config or isolation_mode that has moved changes how the merged
        data will behave.
        """
        if ns_config is None:
            return
        live = await self._mongo[_DB_REGISTRY]["namespaces"].find_one(
            {"prefix": namespace}
        )
        if live is None:
            return

        drift: list[str] = []
        for field_name, archived in (
            ("isolation_mode", ns_config.isolation_mode),
            ("id_config", ns_config.id_config),
            ("allowed_external_refs", ns_config.allowed_external_refs),
            ("deletion_mode", ns_config.deletion_mode),
        ):
            if archived is None:
                continue
            current = live.get(field_name)
            if current != archived:
                drift.append(f"{field_name}: target={current!r} archive={archived!r}")

        if drift:
            self._emit(
                "warning",
                f"[{namespace}] namespace config differs from the archive's "
                f"({'; '.join(drift)}) — the target's config is kept; a merge "
                "never applies the archive's.",
            )

    def _archive_namespaces(
        self, reader: ArchiveReader, manifest: Any
    ) -> list[str]:
        """The namespaces an archive actually carries, or a loud failure.

        Belt behind the endpoint's synchronous 400: a pre-v3 archive is flat,
        so every namespaces/<ns>/<entity>.jsonl read would find nothing and the
        job would complete "successfully" having written zero entities.

        A manifest may also *claim* 3.x yet carry no namespaces/ subtree
        (hand-assembled or truncated zip). ``namespace_prefixes()`` can be
        non-empty from the manifest alone, so the actual layout is checked too
        — otherwise the same silent zero-entity run happens.
        """
        if not manifest.format_version.startswith("3"):
            raise RestoreEngineError(
                f"Archive is format v{manifest.format_version} — the restore "
                "engine reads the v3 layout. Convert it first: "
                "python -m wip_toolkit convert-archive <src> <dst>"
            )
        if not reader.list_namespaces():
            raise RestoreEngineError(
                "Archive manifest claims v3 but the zip has no namespaces/ "
                "tree — malformed archive, nothing to restore"
            )
        namespaces = manifest.namespace_prefixes() or reader.list_namespaces()
        if not namespaces:
            raise RestoreEngineError("Archive contains no namespaces to restore")
        return namespaces

    def _resolve_targets(
        self,
        reader: ArchiveReader,
        manifest: Any,
        target_namespace: str,
        *,
        allow_redirect: bool = False,
    ) -> list[tuple[str, str]]:
        """Validate the archive's shape and pair each source namespace with
        its target. Shared by every mode that reads an archive.

        A plain restore writes each namespace to itself and rejects a
        different target: it preserves everything verbatim, and the archived
        entities and composite keys embed the source namespace, so writing
        them elsewhere would produce records still carrying it.

        A merge may redirect (``allow_redirect``). It rewrites the namespace
        on every entity and rehashes the composite keys that embed it, which
        is what makes "fold NS2's archive into NS1" work. The IDs still have
        to be free — see :meth:`_check_ids_are_free`.
        """
        source_namespaces = self._archive_namespaces(reader, manifest)

        if target_namespace:
            if len(source_namespaces) > 1:
                raise RestoreEngineError(
                    "target_namespace override is not supported for a "
                    "multi-namespace archive; each namespace restores "
                    "to itself."
                )
            if target_namespace != source_namespaces[0]:
                if allow_redirect:
                    return [(source_namespaces[0], target_namespace)]
                raise RestoreEngineError(
                    f"target_namespace '{target_namespace}' differs from "
                    f"the archive's namespace '{source_namespaces[0]}' — "
                    "an ID-preserving restore cannot re-namespace data. "
                    "Restore to the archive's own namespace, or merge it into "
                    "the namespace you want it in."
                )
        return [(ns, ns) for ns in source_namespaces]

    @staticmethod
    def _claim_rows_for(
        entries: list[dict[str, Any]], now: datetime
    ) -> list[dict[str, Any]]:
        """Derive the claim rows a batch of registry entries implies.

        One per entry primary key, one per embedded synonym. Synonyms carry
        their own namespace/entity_type — a synonym may live in a different
        namespace than the entry it points at — so claims are keyed from the
        synonym's fields, not the owner's.

        An empty hash means "this entity opts out of dedup" (legacy template
        entries, identity-less documents). The claims unique index exempts it
        and so does this.
        """
        rows: list[dict[str, Any]] = []
        for entry in entries:
            pairs = [(
                entry.get("namespace"),
                entry.get("entity_type"),
                entry.get("primary_composite_key_hash"),
                "primary",
            )]
            pairs += [
                (
                    syn.get("namespace"),
                    syn.get("entity_type"),
                    syn.get("composite_key_hash"),
                    "synonym",
                )
                for syn in entry.get("synonyms", [])
                if isinstance(syn, dict)
            ]
            for ns, entity_type, key_hash, kind in pairs:
                if not key_hash or not ns or not entity_type:
                    continue
                rows.append({
                    "namespace": ns,
                    "entity_type": entity_type,
                    "composite_key_hash": key_hash,
                    "owner_entry_id": entry.get("entry_id"),
                    "kind": kind,
                    "state": "confirmed",
                    "created_at": now,
                })
        return rows

    async def _insert_claims(
        self, namespace: str, claim_rows: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Insert derived claims. Returns (claimed, taken_by_other).

        A duplicate is one of two things and they are told apart before
        anything is said: the key is already claimed by *this* entry (the
        rebuild is simply idempotent, and a merge into a namespace that
        already has claims hits this on every row) or by a *different* one,
        which means the incumbent keeps the key and the entry sharing it is
        not gate-protected. Only the second is worth a warning, and neither
        fails a restore whose data is already committed.
        """
        from pymongo.errors import BulkWriteError

        if not claim_rows:
            return (0, 0)

        claims_db, claims_coll_name = CLAIMS_COLLECTION
        claims = self._mongo[claims_db][claims_coll_name]
        try:
            result = await claims.insert_many(claim_rows, ordered=False)
            return (len(result.inserted_ids), 0)
        except BulkWriteError as exc:
            write_errors = exc.details.get("writeErrors", [])
            duplicates = [e for e in write_errors if e.get("code") == 11000]
            if len(duplicates) != len(write_errors):
                raise RestoreEngineError(
                    f"Claim rebuild failed for namespace '{namespace}': "
                    f"{write_errors[0].get('errmsg', 'unknown error')}"
                ) from exc
            mine = 0
            for write_error in duplicates:
                rejected = write_error.get("op") or claim_rows[write_error["index"]]
                incumbent = await claims.find_one({
                    "namespace": rejected["namespace"],
                    "entity_type": rejected["entity_type"],
                    "composite_key_hash": rejected["composite_key_hash"],
                })
                if incumbent and incumbent.get("owner_entry_id") == rejected["owner_entry_id"]:
                    mine += 1
            return (len(claim_rows) - len(duplicates), len(duplicates) - mine)

    async def _claim_inserted_entries(
        self, namespace: str, entries: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """Claim the keys of registry entries as they are written.

        Derived inline rather than by re-reading the collection afterwards:
        each entry is already in hand at this point, and a namespace-wide
        re-scan is pure waste on top of the inserts that are genuinely
        required.
        """
        rows = self._claim_rows_for(entries, datetime.now(UTC))
        return await self._insert_claims(namespace, rows)

    def _report_claims(
        self, namespace: str, claimed: int, taken_by_other: int
    ) -> None:
        logger.info(
            "Claimed %d composite key(s) for namespace %s (%d owned by other "
            "entries)", claimed, namespace, taken_by_other,
        )
        if taken_by_other:
            self._emit(
                "warning",
                f"[{namespace}] {taken_by_other} composite key(s) were already "
                "claimed by other entries and were left with their existing "
                "owner — the entries sharing those keys are not gate-protected. "
                "Review with the Registry's claim reconcile.",
            )
        elif claimed:
            self._emit(
                "phase_claims",
                f"[{namespace}] claimed {claimed} composite key(s)",
            )

    async def _check_namespace_empty(self, namespace: str) -> None:
        """Verify no data exists for this namespace across all collections."""
        non_empty: list[str] = []
        for _entity_type, (db_name, coll_name) in COLLECTION_MAP.items():
            count = await self._mongo[db_name][coll_name].count_documents(
                {"namespace": namespace}, limit=1
            )
            if count > 0:
                non_empty.append(f"{db_name}.{coll_name}")

        if non_empty:
            raise RestoreEngineError(
                f"Namespace '{namespace}' is not empty. "
                f"Found data in: {', '.join(non_empty)}. "
                "Restore requires an empty namespace."
            )

    def _dry_run_report(
        self,
        reader: ArchiveReader,
        targets: list[tuple[str, str]],
        entry_by_prefix: dict[str, Any],
        restore_order: list[str],
        *,
        skip_documents: bool,
        skip_files: bool,
    ) -> None:
        """Emit what a real run would restore, then complete without writing.

        Counts come from the manifest's per-namespace entry when present and
        fall back to counting the archive's JSONL lines. All preconditions
        have already run at this point — a dry run that reaches this method
        would have started writing if it were a real run.
        """
        for src, tgt in targets:
            entry = entry_by_prefix.get(src)
            parts: list[str] = []
            for entity_type in restore_order:
                if skip_documents and entity_type == "documents":
                    parts.append("documents=skipped")
                    continue
                if skip_files and entity_type == "files":
                    parts.append("files=skipped")
                    continue
                count = (
                    getattr(entry.counts, entity_type, 0)
                    if entry
                    else reader.entity_count(entity_type, namespace=src)
                )
                parts.append(f"{entity_type}={count}")
            self._emit(
                "phase_dry_run",
                f"[{tgt}] dry run — would restore: {', '.join(parts)} "
                "(plus rebuilt composite-key claims)",
            )

        if not skip_files and self._storage:
            self._emit(
                "phase_dry_run",
                f"dry run — would upload {len(reader.list_blobs())} file blob(s)",
            )

        self._emit(
            "complete",
            "Dry run complete — preconditions passed, no changes made",
            percent=100,
            details=self.result,
        )

    # -- Reporting verification phases (restore-verification design) --------
    #
    # Poll pacing: structure materializes within a couple of batch-sync
    # seconds; counts follow the batch sync of the full document set. Both
    # bounds are generous for dev-class data volumes and merely delay the
    # warning, not the restore, when exceeded.
    _REPORTING_STRUCTURE_TIMEOUT_S = 30
    _REPORTING_COUNTS_TIMEOUT_S = 90
    _REPORTING_POLL_INTERVAL_S = 3

    def _reporting_unavailable(self, namespace: str, what: str) -> None:
        """Disable further reporting phases and surface why, loudly once."""
        self._emit(
            "warning",
            f"[{namespace}] reporting-sync unreachable during {what} — "
            "reporting verification skipped for this restore",
        )
        self._reporting = None

    async def _check_reporting_precondition(
        self, namespace: str, drop_stale: bool, *, dry_run: bool = False
    ) -> None:
        """The namespace's reporting schema must be absent/empty before restore.

        Stale tables would shadow the restored data (a complete-looking but
        dead copy). With ``drop_stale`` the stale schema is dropped after the
        operator's explicit opt-in; without it, the restore refuses and names
        the flag. A bookkeeping-table shape problem also fails here — loudly,
        before anything is written — instead of surfacing as per-type sync
        failures afterwards.

        A dry run still *fails* on the same conditions a real run would refuse
        (that is the information a dry run exists to surface), but never
        mutates: with ``drop_stale`` set it reports the schema that would be
        dropped instead of dropping it.
        """
        if not self._reporting:
            return
        parity = await self._reporting.parity(namespace, include_counts=False)
        if parity is None:
            self._reporting_unavailable(namespace, "precondition check")
            return
        if not parity.get("bookkeeping_tables_ok", True):
            raise RestoreEngineError(
                f"Reporting bookkeeping tables are unusable: "
                f"{parity.get('bookkeeping_error')} — restore would complete "
                "with a silently empty reporting layer. Remediate first."
            )
        if parity.get("schema_present") and parity.get("table_count", 0) > 0:
            if not drop_stale:
                raise RestoreEngineError(
                    f"Reporting schema '{parity.get('schema_name')}' already "
                    f"holds {parity.get('table_count')} table(s) for namespace "
                    f"'{namespace}' — stale reporting data would shadow the "
                    "restore. Re-run with drop_stale_reporting=true to drop "
                    "it, or clear it manually."
                )
            if dry_run:
                self._emit(
                    "phase_dry_run",
                    f"[{namespace}] dry run — stale reporting schema "
                    f"({parity.get('table_count')} table(s)) would be dropped "
                    "(drop_stale_reporting is set)",
                )
                return
            if not await self._reporting.drop_namespace_schema(namespace):
                raise RestoreEngineError(
                    f"Could not drop stale reporting schema for '{namespace}' "
                    "(drop_stale_reporting was set) — refusing to restore "
                    "over shadowed reporting data."
                )
            self._emit(
                "phase_reporting_drop",
                f"[{namespace}] dropped stale reporting schema "
                f"({parity.get('table_count')} table(s))",
            )

    async def _reporting_phase_structure(self, namespace: str) -> None:
        """Post-templates gate: reporting tables must materialize correctly.

        Triggers a namespace batch sync (no documents are restored yet, so
        tables materialize empty) and polls the structure-only parity until
        green. A persistent structural failure HALTS the restore before any
        document moves.
        """
        if not self._reporting:
            return
        self._emit(
            "phase_reporting_structure",
            f"[{namespace}] verifying reporting tables for restored templates",
        )
        await self._reporting.trigger_batch_sync(namespace)
        deadline = asyncio.get_event_loop().time() + self._REPORTING_STRUCTURE_TIMEOUT_S
        parity: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            parity = await self._reporting.parity(namespace, include_counts=False)
            if parity is None:
                self._reporting_unavailable(namespace, "structure verification")
                return
            if parity.get("bookkeeping_tables_ok", True) and parity.get("structural_issues", 0) == 0:
                return
            await asyncio.sleep(self._REPORTING_POLL_INTERVAL_S)
        issues = [
            f"{t.get('template_value')}: "
            + (t.get("error") or (
                "legacy pre-split table shadows the entity view"
                if t.get("legacy_table")
                else "table missing" if not t.get("table_present")
                else f"missing columns {t.get('missing_columns')}"
            ))
            for t in (parity or {}).get("templates", [])
            if not (
                t.get("table_present")
                and not t.get("missing_columns")
                and not t.get("legacy_table")
                and not t.get("error")
            )
        ]
        raise RestoreEngineError(
            f"Reporting tables for namespace '{namespace}' did not verify "
            f"within {self._REPORTING_STRUCTURE_TIMEOUT_S}s — halting before "
            f"document restore. Issues: {'; '.join(issues[:5]) or 'unknown'}. "
            f"NOTE: namespace '{namespace}' is now PARTIAL — definitions were "
            "restored but documents and registry identities were NOT, so "
            "namespaced lookups against it will fail to resolve. Delete the "
            "namespace and re-restore after remediating."
        )

    async def _reporting_phase_counts(
        self, namespace: str, *, skip_documents: bool
    ) -> None:
        """Post-data count parity: bounded wait, then complete WITH warning.

        Never a hard fail — data is safely in MongoDB at this point and the
        reporting layer can catch up or be replayed; the warning makes the
        gap visible instead of silent.
        """
        if not self._reporting or skip_documents:
            return
        self._emit(
            "phase_reporting_parity",
            f"[{namespace}] verifying reporting row-count parity",
        )
        await self._reporting.trigger_batch_sync(namespace)
        deadline = asyncio.get_event_loop().time() + self._REPORTING_COUNTS_TIMEOUT_S
        parity: dict[str, Any] | None = None
        while asyncio.get_event_loop().time() < deadline:
            parity = await self._reporting.parity(namespace, include_counts=True)
            if parity is None:
                self._reporting_unavailable(namespace, "count parity")
                return
            if parity.get("ok"):
                self._emit(
                    "phase_reporting_parity",
                    f"[{namespace}] reporting parity verified "
                    f"({len(parity.get('templates', []))} template(s))",
                )
                return
            await asyncio.sleep(self._REPORTING_POLL_INTERVAL_S)
        mismatches = [
            f"{t.get('template_value')} expected={t.get('expected_documents')} "
            f"actual={t.get('actual_rows')}"
            for t in (parity or {}).get("templates", [])
            if t.get("counts_match") is False
        ]
        self._emit(
            "warning",
            f"[{namespace}] reporting count parity incomplete after "
            f"{self._REPORTING_COUNTS_TIMEOUT_S}s: "
            f"{'; '.join(mismatches[:5]) or 'no per-template detail'} — "
            "restore data is complete in MongoDB; re-run the batch sync or "
            "check the parity endpoint",
        )

    async def _upsert_namespace(
        self,
        namespace: str,
        ns_config: NamespaceConfig | None,
        *,
        preserve_id_config: bool = True,
    ) -> None:
        """Upsert the namespace via Registry HTTP PUT.

        ``preserve_id_config`` carries the source's id_config onto the target.
        That is correct for an id-preserving restore (the DR case): the archived
        ids are re-inserted unchanged and the copy should continue the source's
        minting sequence. A FRESH restore passes False, because it re-mints every
        id — inheriting a prefixed source scheme would re-mint the source's exact
        prefixed ids and collide with the live original on the global entry_id
        index, and stamp the copy's ids with the source's prefix. With it False
        the fresh target defaults to UUID7, or keeps its own config if the
        operator pre-created the target namespace.
        """
        import httpx

        body: dict[str, Any] = {}
        if ns_config:
            body["description"] = ns_config.description
            body["isolation_mode"] = ns_config.isolation_mode
            if preserve_id_config and ns_config.id_config:
                body["id_config"] = ns_config.id_config
            # None means the archive predates these manifest fields — omit
            # them so the PUT leaves an existing namespace's config untouched
            # instead of resetting it to platform defaults. An explicit value
            # (including an empty allowlist) round-trips.
            if ns_config.allowed_external_refs is not None:
                body["allowed_external_refs"] = ns_config.allowed_external_refs
            if ns_config.deletion_mode is not None:
                body["deletion_mode"] = ns_config.deletion_mode

        url = f"{self._registry_url}/api/registry/namespaces/{namespace}"
        headers = {
            "X-API-Key": self._registry_api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(url, json=body, headers=headers)
            if (
                resp.status_code == 400
                and "confirm_enable_deletion" in resp.text
                and "deletion_mode" in body
            ):
                # An existing retain-mode namespace cannot be flipped to full
                # without explicit confirmation — that guard is deliberate and
                # a restore must not bypass it silently. Apply the rest of the
                # config and surface the skipped field loudly instead of
                # aborting the data restore over a config nuance.
                skipped = body.pop("deletion_mode")
                warning = (
                    f"deletion_mode '{skipped}' NOT applied to existing "
                    f"namespace '{namespace}' — flipping retain to full "
                    "requires a manual PUT with confirm_enable_deletion=true"
                )
                logger.warning(warning)
                self._emit("phase_namespace", f"WARNING: {warning}")
                resp = await client.put(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                raise RestoreEngineError(
                    f"Failed to upsert namespace '{namespace}': "
                    f"{resp.status_code} — {resp.text}"
                )
        logger.info("Upserted namespace %s from manifest config", namespace)

    async def _insert_batch(
        self,
        collection: Any,
        batch: list[dict[str, Any]],
        entity_type: str,
    ) -> None:
        """Bulk insert a batch, handling partial failures."""
        from pymongo.errors import BulkWriteError

        try:
            await collection.insert_many(batch, ordered=False)
        except BulkWriteError as exc:
            n_errors = len(exc.details.get("writeErrors", []))
            n_ok = len(batch) - n_errors
            logger.error(
                "Bulk insert for %s: %d succeeded, %d failed",
                entity_type, n_ok, n_errors,
            )
            # For v1.0 restore into empty namespace, any failure is unexpected
            raise RestoreEngineError(
                f"Bulk insert failed for {entity_type}: "
                f"{n_errors} errors out of {len(batch)} documents. "
                f"First error: {exc.details['writeErrors'][0].get('errmsg', 'unknown')}"
            ) from exc

    async def _restore_blobs(
        self,
        reader: ArchiveReader,
        namespace: str,
        *,
        only: set[str] | None = None,
    ) -> None:
        """Upload file blobs from the archive to MinIO.

        ``only`` narrows the upload to specific file ids — a merge uploads
        bytes for the files it inserted and leaves the ones the target already
        had alone.
        """
        assert self._storage is not None
        blob_ids = reader.list_blobs()
        if only is not None:
            blob_ids = [b for b in blob_ids if b in only]
        if not blob_ids:
            return

        self._emit("phase_blobs", f"Uploading {len(blob_ids)} file blobs", percent=92)

        for i, file_id in enumerate(blob_ids):
            data = reader.read_blob(file_id)
            if data is None:
                logger.warning("Blob %s listed but not readable in archive", file_id)
                continue
            # Use application/octet-stream as default; the File metadata
            # in MongoDB has the real content_type.
            await self._storage.upload(
                storage_key=file_id,
                content=data,
                content_type="application/octet-stream",
            )
            if (i + 1) % 100 == 0:
                self._emit(
                    "phase_blobs",
                    f"Uploaded {i + 1}/{len(blob_ids)} blobs",
                    percent=92 + (i + 1) / len(blob_ids) * 6,
                    current=i + 1,
                    total=len(blob_ids),
                )

        logger.info("Restored %d blobs for namespace %s", len(blob_ids), namespace)

    def _emit(
        self,
        phase: str,
        message: str,
        *,
        percent: float | None = None,
        current: int | None = None,
        total: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._progress(
            ProgressEvent(
                phase=phase,
                message=message,
                percent=percent,
                current=current,
                total=total,
                details=details or {},
            )
        )

    @staticmethod
    def _pct(done: int, total: int) -> float:
        if total == 0:
            return 10.0
        # 10-90% for entity inserts, 90-98% for blobs, 98-100% for finalize
        return min(round(10 + done / total * 80, 1), 90.0)

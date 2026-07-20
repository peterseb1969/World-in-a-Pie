"""Direct MongoDB backup/restore engine (CASE-23 redesign).

Replaces the toolkit-based HTTP-fan-out engine with direct motor cursor
reads for backup and bulk inserts for restore. Runs inside document-store,
which shares the MongoDB instance with all other services.

The engine emits :class:`~wip_toolkit.models.ProgressEvent` via a callback,
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
from wip_toolkit.archive import ArchiveReader, ArchiveWriter
from wip_toolkit.models import (
    EntityCounts,
    Manifest,
    NamespaceConfig,
    NamespaceEntry,
    ProgressEvent,
)

from .file_storage_client import FileStorageClient
from .merge_plan import MERGE_ENTITY_SPECS, EntityPlan, MergePlanner
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

# Merge writes in the same order for the same reason a restore does: it is
# dependency order. Terms need their terminology, documents need their
# template, and registry entries come last so nothing claims an identity
# before the entity holding it exists.
MERGE_ENTITY_ORDER = list(BACKUP_ENTITY_ORDER)


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
    via :class:`~wip_toolkit.archive.ArchiveWriter`.
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
        include_inactive: bool = False,
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
            per_ns_counts[ns] = await self._pre_count(ns, include_inactive, skip_documents)
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

                    query = self._build_query(ns, include_inactive)

                    phase_name = f"phase_{entity_type}"
                    expected = per_ns_counts[ns].get(entity_type, 0)
                    self._emit(
                        phase_name,
                        f"[{ns}] Reading {entity_type} ({expected} expected)",
                        percent=self._pct(processed, total_entities),
                    )

                    count = 0
                    async for doc in collection.find(query):
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
                include_inactive=include_inactive,
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
        self, namespace: str, include_inactive: bool, skip_documents: bool
    ) -> dict[str, int]:
        """Count documents per entity type for progress reporting."""
        query = self._build_query(namespace, include_inactive)
        counts: dict[str, int] = {}
        for entity_type in BACKUP_ENTITY_ORDER:
            if skip_documents and entity_type == "documents":
                counts[entity_type] = 0
                continue
            db_name, coll_name = COLLECTION_MAP[entity_type]
            counts[entity_type] = await self._mongo[db_name][coll_name].count_documents(query)
        return counts

    def _build_query(self, namespace: str, include_inactive: bool) -> dict[str, Any]:
        query: dict[str, Any] = {"namespace": namespace}
        if not include_inactive:
            query["status"] = {"$ne": "deleted"}
        return query

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
    ) -> None:
        self._progress(
            ProgressEvent(
                phase=phase,
                message=message,
                percent=percent,
                current=current,
                total=total,
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
    :class:`~wip_toolkit.archive.ArchiveReader`, bulk-inserts into all
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

                    for entity in reader.read_entities(entity_type, namespace=src):
                        entity.pop("_id", None)  # Strip MongoDB internal ID
                        batch.append(entity)

                        if len(batch) >= batch_size:
                            await self._insert_batch(collection, batch, entity_type)
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
                        count += len(batch)
                        processed += len(batch)

                    logger.info("Restored %d %s into namespace %s", count, entity_type, tgt)

                    # Structural gate between templates and documents: the
                    # reporting tables the restored templates imply must be
                    # creatable and correctly shaped BEFORE any document
                    # moves. Catches broken bookkeeping / mis-shaped tables
                    # at the first template instead of after a full restore.
                    if entity_type == "templates":
                        await self._reporting_phase_structure(tgt)

                # Rebuild the Registry's uniqueness gate for the entries just
                # restored. Runs after registry_entries (last in restore_order)
                # so every entry a claim points at exists.
                await self._recreate_claims(tgt, batch_size=batch_size)

                # Count parity after the namespace's data is in: expected vs
                # actual rows, bounded wait. Mismatch completes WITH a
                # warning — never a hard fail (operator ruling).
                await self._reporting_phase_counts(tgt, skip_documents=skip_documents)

            # Blobs are flat (namespace-agnostic, globally-unique file_ids) — restore
            # the whole archive's blob set once after all namespaces are in.
            if not skip_files and self._storage:
                await self._restore_blobs(reader, targets[0][1])

        self._emit("complete", "Restore complete", percent=100)

    # Clash policies. Documents take on_clash; the schema entities take their
    # own on_schema_clash, because merging data into a namespace whose schema
    # diverged from the archive is a migration someone should look at — the
    # platform's own create-as-upsert write semantics deliberately do NOT make
    # `upsert` the default for a restore action.
    MERGE_CLASH_POLICIES = ("skip", "overwrite")
    MERGE_SCHEMA_CLASH_POLICIES = ("fail", "skip", "upsert")
    SCHEMA_ENTITY_TYPES = ("terminologies", "terms", "templates")

    async def run_merge(
        self,
        archive_path: Path,
        target_namespace: str,
        *,
        on_clash: str = "skip",
        on_schema_clash: str = "fail",
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
        dry_run: bool = False,
    ) -> None:
        """Merge an archive into an existing, possibly non-empty namespace.

        Same install, same namespace name, IDs preserved: the archive is a
        delta source, not a replacement. Entities the target lacks are
        inserted; entities it already holds are resolved by policy.

        The plan is built before anything is written, which makes ``dry_run``
        exact rather than indicative — it reports the same classification the
        real run acts on. A dry run still *fails* on everything a real run
        would refuse (identity conflicts, schema divergence under
        ``on_schema_clash=fail``); surfacing those is what it is for.
        """
        if on_clash not in self.MERGE_CLASH_POLICIES:
            raise RestoreEngineError(
                f"Invalid on_clash '{on_clash}' — must be one of "
                f"{', '.join(self.MERGE_CLASH_POLICIES)}"
            )
        if on_schema_clash not in self.MERGE_SCHEMA_CLASH_POLICIES:
            raise RestoreEngineError(
                f"Invalid on_schema_clash '{on_schema_clash}' — must be one of "
                f"{', '.join(self.MERGE_SCHEMA_CLASH_POLICIES)}"
            )

        with ArchiveReader(archive_path) as reader:
            manifest = reader.read_manifest()
            targets = self._resolve_targets(reader, manifest, target_namespace)

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

                plans, doc_groups = self._build_merge_plan_inputs(
                    reader, src, skip_documents=skip_documents, skip_files=skip_files
                )
                planner = MergePlanner(self._mongo, COLLECTION_MAP)
                built: dict[str, EntityPlan] = {}
                for entity_type, entities in plans.items():
                    built[entity_type] = await planner.plan(entity_type, entities, tgt)

                self._emit_merge_plan(
                    tgt, built, on_clash=on_clash,
                    on_schema_clash=on_schema_clash, dry_run=dry_run,
                )
                self._enforce_merge_gates(tgt, built, on_schema_clash=on_schema_clash)

                if dry_run:
                    continue

                await self._apply_merge(
                    tgt, built, doc_groups,
                    on_clash=on_clash, on_schema_clash=on_schema_clash,
                    batch_size=batch_size,
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
                )
                return

            # Only blobs for files the merge actually inserted: a file the
            # target already had keeps its bytes, and re-uploading them would
            # be work at best and an orphan object at worst.
            if not skip_files and self._storage and inserted_file_ids:
                await self._restore_blobs(reader, targets[0][1], only=inserted_file_ids)

        self._emit("complete", "Merge complete", percent=100)

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

    def _merge_policy_for(
        self, entity_type: str, *, on_clash: str, on_schema_clash: str
    ) -> str:
        if entity_type in self.SCHEMA_ENTITY_TYPES:
            return on_schema_clash
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
        on_schema_clash: str,
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
            policy = self._merge_policy_for(
                entity_type, on_clash=on_clash, on_schema_clash=on_schema_clash
            )
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

    def _enforce_merge_gates(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        *,
        on_schema_clash: str,
    ) -> None:
        """Refuse the merge on anything policy does not cover."""
        conflicts = [
            (entity_type, conflict)
            for entity_type, plan in plans.items()
            for conflict in plan.conflicts
        ]
        if conflicts:
            detail = "; ".join(
                f"{entity_type}: {conflict.reason}"
                for entity_type, conflict in conflicts[:5]
            )
            raise RestoreEngineError(
                f"Merge into '{namespace}' refused — {len(conflicts)} identity "
                f"conflict(s) between archive and target. {detail}. Merge "
                "preserves IDs and cannot reconcile identities that disagree; "
                "this archive needs the ID-reminting (new-namespace) mode."
            )

        if on_schema_clash != "fail":
            return
        divergent = [
            (entity_type, clash)
            for entity_type in self.SCHEMA_ENTITY_TYPES
            if entity_type in plans
            for clash in plans[entity_type].differing_clashes
        ]
        if divergent:
            detail = "; ".join(
                f"{entity_type} "
                f"{self._describe_entity(entity_type, clash.entity)} differs in "
                f"{', '.join(sorted(clash.diff))}"
                for entity_type, clash in divergent[:5]
            )
            raise RestoreEngineError(
                f"Merge into '{namespace}' refused — {len(divergent)} schema "
                f"entit(ies) differ between archive and target. {detail}. "
                "Choose on_schema_clash=skip to keep the target's schema, or "
                "on_schema_clash=upsert to take the archive's."
            )

    async def _apply_merge(
        self,
        namespace: str,
        plans: dict[str, EntityPlan],
        doc_groups: dict[str, list[dict[str, Any]]],
        *,
        on_clash: str,
        on_schema_clash: str,
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

            if entity_type in self.SCHEMA_ENTITY_TYPES and on_schema_clash == "upsert":
                await self._upsert_schema_clashes(namespace, entity_type, plan)
            elif entity_type == "documents" and on_clash == "overwrite":
                await self._overwrite_documents(namespace, plan, doc_groups)

        entries_plan = plans.get("registry_entries")
        if entries_plan and entries_plan.to_insert:
            await self._recreate_claims(
                namespace,
                batch_size=batch_size,
                entry_ids=[
                    e["entry_id"] for e in entries_plan.to_insert if e.get("entry_id")
                ],
            )

    async def _upsert_schema_clashes(
        self, namespace: str, entity_type: str, plan: EntityPlan
    ) -> None:
        """Take the archive's version of a diverged schema entity.

        Templates are versioned, so the archive's definition lands as a NEW
        version of the target's template — the platform's own create-as-upsert
        shape, and the reason nothing is overwritten or lost. Terminologies and
        terms have no version axis: for them "the archive wins" can only mean
        updating the target row in place, which is what an operator asking for
        upsert is asking for.
        """
        clashes = plan.differing_clashes
        if not clashes:
            return

        db_name, coll_name = COLLECTION_MAP[entity_type]
        collection = self._mongo[db_name][coll_name]

        if entity_type == "templates":
            heads = await self._template_head_versions(
                namespace, [c.target.get("template_id") for c in clashes]
            )
            new_rows: list[dict[str, Any]] = []
            for clash in clashes:
                template_id = clash.target.get("template_id")
                next_version = heads.get(template_id, clash.target.get("version", 1)) + 1
                heads[template_id] = next_version
                row = dict(clash.entity)
                row["template_id"] = template_id
                row["version"] = next_version
                new_rows.append(row)
            await self._insert_batch(collection, new_rows, entity_type)
            self._emit(
                "phase_templates",
                f"[{namespace}] upserted {len(new_rows)} template(s) as new "
                "versions from the archive",
            )
            return

        updated = 0
        for clash in clashes:
            payload = {
                key: value
                for key, value in clash.entity.items()
                if key not in ("_id", "created_at", "created_by")
            }
            await collection.update_one(
                {
                    "namespace": namespace,
                    **{
                        field_name: clash.target.get(field_name)
                        for field_name in MERGE_ENTITY_SPECS[entity_type].id_fields
                    },
                },
                {"$set": payload},
            )
            updated += 1
        self._emit(
            f"phase_{entity_type}",
            f"[{namespace}] updated {updated} {entity_type} in place from the "
            "archive (no version history exists for this entity type)",
        )

    async def _template_head_versions(
        self, namespace: str, template_ids: list[str | None]
    ) -> dict[str, int]:
        """Highest stored version per template, for appending new ones."""
        wanted = [t for t in template_ids if t]
        heads: dict[str, int] = {}
        if not wanted:
            return heads
        db_name, coll_name = COLLECTION_MAP["templates"]
        cursor = self._mongo[db_name][coll_name].find(
            {"namespace": namespace, "template_id": {"$in": wanted}},
            {"template_id": 1, "version": 1},
        )
        async for row in cursor:
            template_id = row.get("template_id")
            version = row.get("version", 1)
            if version > heads.get(template_id, 0):
                heads[template_id] = version
        return heads

    async def _overwrite_documents(
        self,
        namespace: str,
        plan: EntityPlan,
        doc_groups: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Let the archive win, going forward.

        The archive's LATEST version of a clashing identity is appended as one
        new version on top of the target's head, adopting the target's
        document_id. The target's history is preserved and the archive's is not
        spliced in: interleaving two independent version chains has no defined
        order and would corrupt the (document_id, version) contract.

        A ``versioned: false`` template has no such contract to protect — its
        lifecycle IS overwrite-in-place — so there the single row is replaced.
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
        new_rows: list[dict[str, Any]] = []
        for clash in clashes:
            source_rows = doc_groups.get(
                clash.entity.get("document_id", ""), [clash.entity]
            )
            latest = max(source_rows, key=lambda r: r.get("version", 1))
            target_head = max(clash.targets, key=lambda r: r.get("version", 1))

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

        self._emit(
            "phase_documents",
            f"[{namespace}] overwrote {appended + replaced} clashing document(s) "
            f"({appended} appended as a new version, {replaced} replaced in "
            "place on versioned:false templates)",
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

    def _resolve_targets(
        self,
        reader: ArchiveReader,
        manifest: Any,
        target_namespace: str,
    ) -> list[tuple[str, str]]:
        """Validate the archive's shape and pair each source namespace with
        its target. Shared by every mode that reads an archive.

        Both modes here write each namespace to itself. A different target is
        rejected rather than honoured: the archived entities, registry entries,
        and composite keys all embed the source namespace, so writing them
        elsewhere would produce records still carrying the source namespace
        after checking only the target. Re-namespacing means new IDs and
        rewritten references — the new-namespace mode's job.
        """
        # Belt behind the endpoint's synchronous 400: a pre-v3 archive is
        # flat, so every namespaces/<ns>/<entity>.jsonl read would find
        # nothing and the job would complete "successfully" having written
        # zero entities. Fail loud for any caller that bypasses the endpoint.
        if not manifest.format_version.startswith("3"):
            raise RestoreEngineError(
                f"Archive is format v{manifest.format_version} — the restore "
                "engine reads the v3 layout. Convert it first: "
                "python -m wip_toolkit convert-archive <src> <dst>"
            )
        # A manifest may *claim* 3.x yet carry no namespaces/ subtree
        # (hand-assembled or truncated zip). namespace_prefixes() can be
        # non-empty from the manifest alone, so check the actual layout —
        # otherwise the same silent zero-entity run happens.
        if not reader.list_namespaces():
            raise RestoreEngineError(
                "Archive manifest claims v3 but the zip has no namespaces/ "
                "tree — malformed archive, nothing to restore"
            )

        source_namespaces = manifest.namespace_prefixes() or reader.list_namespaces()
        if not source_namespaces:
            raise RestoreEngineError("Archive contains no namespaces to restore")

        if target_namespace:
            if len(source_namespaces) > 1:
                raise RestoreEngineError(
                    "target_namespace override is not supported for a "
                    "multi-namespace archive; each namespace restores "
                    "to itself."
                )
            if target_namespace != source_namespaces[0]:
                raise RestoreEngineError(
                    f"target_namespace '{target_namespace}' differs from "
                    f"the archive's namespace '{source_namespaces[0]}' — "
                    "an ID-preserving restore cannot re-namespace data. "
                    "Restore to the archive's own namespace, or use the "
                    "new-namespace (remap) restore mode once available."
                )
        return [(ns, ns) for ns in source_namespaces]

    async def _recreate_claims(
        self,
        namespace: str,
        *,
        batch_size: int,
        entry_ids: list[str] | None = None,
    ) -> None:
        """Rebuild composite-key claims for a namespace's restored entries.

        A claim is derived state — (namespace, entity_type, hash) → owning
        entry — so archives never carry it, but a restored namespace whose
        entries have no claims has lost its uniqueness gate: the next
        registration of an already-taken composite key sails through and mints
        a second entity for one identity. This phase reconstructs one claim per
        entry primary key plus one per embedded synonym, mirroring the
        Registry's own backfill.

        Synonyms carry their own namespace/entity_type (a synonym may live in a
        different namespace than the entry it points at), so claims are keyed
        from the synonym's fields, not the owner's.

        A duplicate is one of two things, and the phase tells them apart before
        it says anything: the key is already claimed by *this* entry (nothing
        to do — the rebuild is idempotent) or by a *different* one, which means
        the incumbent keeps the key and the restored entry sharing it is not
        gate-protected. Only the second warrants a warning, and neither fails a
        restore whose data is already committed.

        ``entry_ids`` narrows the rebuild to specific entries — a merge claims
        only what it inserted, since everything already in the namespace has
        its claims. Omitted, every entry in the namespace is (re)claimed.
        """
        entries_db, entries_coll_name = COLLECTION_MAP["registry_entries"]
        entries = self._mongo[entries_db][entries_coll_name]
        claims_db, claims_coll_name = CLAIMS_COLLECTION
        claims = self._mongo[claims_db][claims_coll_name]

        self._emit(
            "phase_claims",
            f"[{namespace}] rebuilding composite-key claims",
        )

        now = datetime.now(UTC)
        batch: list[dict[str, Any]] = []
        claimed = 0
        already_owned = 0
        taken_by_other = 0

        async def flush() -> tuple[int, int, int]:
            """Insert a batch unordered, classifying any duplicates."""
            from pymongo.errors import BulkWriteError

            if not batch:
                return (0, 0, 0)
            try:
                result = await claims.insert_many(batch, ordered=False)
                return (len(result.inserted_ids), 0, 0)
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
                    rejected = write_error.get("op") or batch[write_error["index"]]
                    incumbent = await claims.find_one({
                        "namespace": rejected["namespace"],
                        "entity_type": rejected["entity_type"],
                        "composite_key_hash": rejected["composite_key_hash"],
                    })
                    if incumbent and incumbent.get("owner_entry_id") == rejected["owner_entry_id"]:
                        mine += 1
                return (
                    len(batch) - len(duplicates),
                    mine,
                    len(duplicates) - mine,
                )

        entry_query: dict[str, Any] = {"namespace": namespace}
        if entry_ids is not None:
            entry_query["entry_id"] = {"$in": entry_ids}
        cursor = entries.find(
            entry_query,
            {
                "entry_id": 1,
                "namespace": 1,
                "entity_type": 1,
                "primary_composite_key_hash": 1,
                "synonyms": 1,
            },
        )
        async for entry in cursor:
            pairs = [
                (
                    entry.get("namespace"),
                    entry.get("entity_type"),
                    entry.get("primary_composite_key_hash"),
                    "primary",
                )
            ]
            pairs += [
                (
                    syn.get("namespace"),
                    syn.get("entity_type"),
                    syn.get("composite_key_hash"),
                    "synonym",
                )
                for syn in entry.get("synonyms", [])
            ]
            for ns, entity_type, key_hash, kind in pairs:
                # An empty hash means "no dedup for this entity" (legacy
                # template entries, identity-less documents). The claims
                # unique index exempts it and so does this rebuild.
                if not key_hash or not ns or not entity_type:
                    continue
                batch.append({
                    "namespace": ns,
                    "entity_type": entity_type,
                    "composite_key_hash": key_hash,
                    "owner_entry_id": entry.get("entry_id"),
                    "kind": kind,
                    "state": "confirmed",
                    "created_at": now,
                })

            if len(batch) >= batch_size:
                ok, mine, theirs = await flush()
                claimed += ok
                already_owned += mine
                taken_by_other += theirs
                batch = []

        ok, mine, theirs = await flush()
        claimed += ok
        already_owned += mine
        taken_by_other += theirs

        logger.info(
            "Rebuilt %d composite-key claims for namespace %s "
            "(%d already held, %d owned by other entries)",
            claimed, namespace, already_owned, taken_by_other,
        )
        if taken_by_other:
            self._emit(
                "warning",
                f"[{namespace}] {taken_by_other} composite key(s) were already "
                "claimed by other entries and were left with their existing "
                "owner — the restored entries sharing those keys are not "
                "gate-protected. Review with the Registry's claim reconcile.",
            )
        else:
            self._emit(
                "phase_claims",
                f"[{namespace}] rebuilt {claimed} composite-key claim(s)",
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
            f"document restore. Issues: {'; '.join(issues[:5]) or 'unknown'}"
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
        self, namespace: str, ns_config: NamespaceConfig | None
    ) -> None:
        """Upsert the namespace via Registry HTTP PUT."""
        import httpx

        body: dict[str, Any] = {}
        if ns_config:
            body["description"] = ns_config.description
            body["isolation_mode"] = ns_config.isolation_mode
            if ns_config.id_config:
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
    ) -> None:
        self._progress(
            ProgressEvent(
                phase=phase,
                message=message,
                percent=percent,
                current=current,
                total=total,
            )
        )

    @staticmethod
    def _pct(done: int, total: int) -> float:
        if total == 0:
            return 10.0
        # 10-90% for entity inserts, 90-98% for blobs, 98-100% for finalize
        return min(round(10 + done / total * 80, 1), 90.0)

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

            # Belt behind the endpoint's synchronous 400: a pre-v3 archive is
            # flat, so every namespaces/<ns>/<entity>.jsonl read below would
            # find nothing and the job would complete "successfully" having
            # restored zero entities into a freshly created namespace. Fail
            # loud instead, for any caller that bypasses the endpoint.
            if not manifest.format_version.startswith("3"):
                raise RestoreEngineError(
                    f"Archive is format v{manifest.format_version} — the restore "
                    "engine reads the v3 layout. Convert it first: "
                    "python -m wip_toolkit convert-archive <src> <dst>"
                )
            # A manifest may *claim* 3.x yet carry no namespaces/ subtree
            # (hand-assembled or truncated zip). namespace_prefixes() can be
            # non-empty from the manifest alone, so check the actual layout —
            # otherwise the same silent zero-entity restore happens.
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
                    # Redirecting an ID-preserving restore is unsound: the
                    # archived entities, registry entries, and composite keys
                    # all embed the source namespace, so the insert would
                    # write records still carrying the source namespace after
                    # empty-checking only the target (CASE-548). Re-namespacing
                    # requires the remap mode (new IDs, rewritten references).
                    raise RestoreEngineError(
                        f"target_namespace '{target_namespace}' differs from "
                        f"the archive's namespace '{source_namespaces[0]}' — "
                        "an ID-preserving restore cannot re-namespace data. "
                        "Restore to the archive's own namespace, or use the "
                        "new-namespace (remap) restore mode once available."
                    )
            targets = [(ns, ns) for ns in source_namespaces]

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

    async def _restore_blobs(self, reader: ArchiveReader, namespace: str) -> None:
        """Upload file blobs from the archive to MinIO."""
        assert self._storage is not None
        blob_ids = reader.list_blobs()
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

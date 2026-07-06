"""Direct MongoDB backup/restore engine (CASE-23 redesign).

Replaces the toolkit-based HTTP-fan-out engine with direct motor cursor
reads for backup and bulk inserts for restore. Runs inside document-store,
which shares the MongoDB instance with all other services.

The engine emits :class:`~wip_toolkit.models.ProgressEvent` via a callback,
making it compatible with the existing ``start_async_job`` / SSE machinery
in :mod:`backup_service`.
"""

from __future__ import annotations

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

    async def run_restore(
        self,
        archive_path: Path,
        target_namespace: str,
        *,
        skip_documents: bool = False,
        skip_files: bool = False,
        batch_size: int = 500,
    ) -> None:
        """Run the full restore pipeline over every namespace in the archive.

        Identity-only restore (CASE-542): each namespace restores to itself and
        its target must be empty. A *single*-namespace archive may still be
        redirected to an explicit ``target_namespace`` (the pre-existing
        single-namespace behaviour); a multi-namespace archive rejects a target
        override — cross-namespace remap (Registry re-mint) is out of scope.
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

            if len(source_namespaces) == 1 and target_namespace:
                targets = [(source_namespaces[0], target_namespace)]
            else:
                if target_namespace:
                    raise RestoreEngineError(
                        "target_namespace override is only supported for a "
                        "single-namespace archive; a multi-namespace archive "
                        "restores each namespace to itself."
                    )
                targets = [(ns, ns) for ns in source_namespaces]

            self._emit(
                "start",
                f"Starting restore of {len(targets)} namespace(s)",
                percent=0,
            )

            # Phase 1: validate ALL targets empty before writing anything
            self._emit("phase_validate", "Checking target namespaces are empty", percent=2)
            for _src, tgt in targets:
                await self._check_namespace_empty(tgt)

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

            # Blobs are flat (namespace-agnostic, globally-unique file_ids) — restore
            # the whole archive's blob set once after all namespaces are in.
            if not skip_files and self._storage:
                await self._restore_blobs(reader, targets[0][1])

        self._emit("complete", "Restore complete", percent=100)

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

        url = f"{self._registry_url}/api/registry/namespaces/{namespace}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.put(
                url,
                json=body,
                headers={
                    "X-API-Key": self._registry_api_key,
                    "Content-Type": "application/json",
                },
            )
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

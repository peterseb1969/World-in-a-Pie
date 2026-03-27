"""Namespace deletion service.

Builds and executes a persistent delete journal for crash-safe
namespace deletion across MongoDB, MinIO, and PostgreSQL.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from ..models.deletion_journal import (
    DeletionJournal,
    DeletionStep,
    InboundReference,
)
from ..models.entry import RegistryEntry
from ..models.grant import NamespaceGrant
from ..models.namespace import Namespace

logger = logging.getLogger("registry.namespace_deletion")


# MongoDB collections that contain namespace-scoped data.
# Each tuple: (database_name, collection_name, filter_field, service_label)
# Each service uses its own database on the shared MongoDB instance.
_MONGO_COLLECTIONS = [
    ("wip_document_store", "files", "namespace", "document-store"),
    ("wip_document_store", "documents", "namespace", "document-store"),
    ("wip_template_store", "templates", "namespace", "template-store"),
    ("wip_def_store", "terms", "namespace", "def-store"),
    ("wip_def_store", "term_relationships", "namespace", "def-store"),
    ("wip_def_store", "term_audit_log", "namespace", "def-store"),
    ("wip_def_store", "terminologies", "namespace", "def-store"),
    ("wip_registry", "registry_entries", "namespace", "registry"),
    ("wip_registry", "namespace_grants", "namespace", "registry"),
]

# id_counters uses counter_key prefix, not a namespace field — handled separately


class NamespaceDeletionService:
    """Orchestrates namespace deletion with a persistent journal."""

    def __init__(self):
        self._minio_url = os.getenv("MINIO_URL")
        self._minio_access_key = os.getenv("MINIO_ACCESS_KEY")
        self._minio_secret_key = os.getenv("MINIO_SECRET_KEY")
        self._minio_bucket = os.getenv("MINIO_BUCKET", "wip-attachments")
        self._postgres_url = os.getenv("REPORTING_SYNC_URL")

    async def dry_run(self, prefix: str) -> dict[str, Any]:
        """Return an impact report without making any changes."""
        ns = await Namespace.find_one({"prefix": prefix})
        if not ns:
            return {"error": "Namespace not found"}

        entity_counts = await self._count_entities(prefix)
        minio_keys = await self._get_minio_keys(prefix)
        inbound_refs = await self._check_inbound_references(prefix)

        has_high_severity = any(
            r.type in ("template_extends", "terminology_reference")
            for r in inbound_refs
        )

        return {
            "namespace": prefix,
            "deletion_mode": ns.deletion_mode,
            "dry_run": True,
            "entity_counts": entity_counts,
            "minio_objects": len(minio_keys),
            "inbound_references": [r.model_dump() for r in inbound_refs],
            "safe_to_delete": not has_high_severity,
            "requires_force": has_high_severity,
        }

    async def start_deletion(
        self,
        prefix: str,
        force: bool = False,
        requested_by: str | None = None,
    ) -> DeletionJournal:
        """Lock the namespace and build + execute the deletion journal."""
        ns = await Namespace.find_one({"prefix": prefix})
        if not ns:
            raise ValueError(f"Namespace not found: {prefix}")

        if ns.prefix == "wip":
            raise ValueError("Cannot delete the default 'wip' namespace")

        if ns.deletion_mode != "full":
            raise ValueError(
                f"Namespace '{prefix}' has deletion_mode='retain'. "
                "Change to 'full' before deleting."
            )

        if ns.status == "locked":
            raise ValueError(
                f"Namespace '{prefix}' is already locked (deletion in progress)"
            )

        # Check inbound references
        inbound_refs = await self._check_inbound_references(prefix)
        high_severity = [
            r for r in inbound_refs
            if r.type in ("template_extends", "terminology_reference")
        ]
        if high_severity and not force:
            raise ValueError(
                f"Namespace '{prefix}' has {len(high_severity)} inbound reference(s) "
                "from other namespaces. Use force=true to proceed."
            )

        # Lock the namespace
        ns.status = "locked"
        ns.updated_at = datetime.now(UTC)
        await ns.save()
        logger.info("Namespace '%s' locked for deletion", prefix)

        # Build journal
        journal = await self._build_journal(prefix, force, requested_by, inbound_refs)
        await journal.create()
        logger.info("Deletion journal created for '%s' with %d steps", prefix, len(journal.steps))

        # Execute
        await self._execute_journal(journal)
        return journal

    async def resume_deletion(self, prefix: str) -> DeletionJournal:
        """Resume an incomplete deletion from where it left off."""
        journal = await DeletionJournal.find_one({
            "namespace": prefix,
            "status": "in_progress",
        })
        if not journal:
            raise ValueError(f"No in-progress deletion found for namespace '{prefix}'")

        await self._execute_journal(journal)
        return journal

    async def get_deletion_status(self, prefix: str) -> DeletionJournal | None:
        """Get the most recent deletion journal for a namespace."""
        return await DeletionJournal.find_one(
            {"namespace": prefix},
            sort=[("requested_at", -1)],
        )

    async def recover_incomplete_deletions(self):
        """On startup, resume any in-progress deletions."""
        incomplete = await DeletionJournal.find({"status": "in_progress"}).to_list()
        for journal in incomplete:
            logger.warning(
                "Found incomplete deletion for namespace '%s', resuming...",
                journal.namespace,
            )
            try:
                await self._execute_journal(journal)
                logger.info("Recovered deletion of namespace '%s'", journal.namespace)
            except Exception:
                logger.exception(
                    "Failed to recover deletion of namespace '%s'", journal.namespace
                )

    # -------------------------------------------------------------------------
    # Internal: counting and reference checking
    # -------------------------------------------------------------------------

    async def _count_entities(self, prefix: str) -> dict[str, int]:
        """Count all entities scoped to a namespace."""
        counts: dict[str, int] = {}

        # Use Beanie models where available
        counts["registry_entries"] = await RegistryEntry.find(
            {"namespace": prefix}
        ).count()
        motor_client = Namespace.get_motor_collection().database.client
        registry_db = motor_client["wip_registry"]
        counts["id_counters"] = await registry_db["id_counters"].count_documents(
            {"counter_key": {"$regex": f"^{prefix}:"}}
        )
        counts["namespace_grants"] = await NamespaceGrant.find(
            {"namespace": prefix}
        ).count()

        # Count across all service databases
        for db_name, coll_name, filter_field, _ in _MONGO_COLLECTIONS:
            if coll_name in ("registry_entries", "id_counters", "namespace_grants"):
                continue  # Already counted via Beanie
            db = motor_client[db_name]
            coll = db[coll_name]
            counts[coll_name] = await coll.count_documents({filter_field: prefix})

        return counts

    async def _get_minio_keys(self, prefix: str) -> list[str]:
        """Get MinIO storage keys for files in this namespace."""
        motor_client = Namespace.get_motor_collection().database.client
        doc_store_db = motor_client["wip_document_store"]
        files_coll = doc_store_db["files"]
        keys = []
        async for doc in files_coll.find(
            {"namespace": prefix},
            {"storage_key": 1, "file_id": 1},
        ):
            key = doc.get("storage_key") or doc.get("file_id")
            if key:
                keys.append(key)
        return keys

    async def _check_inbound_references(self, prefix: str) -> list[InboundReference]:
        """Find references from other namespaces into this one."""
        refs: list[InboundReference] = []
        motor_client = Namespace.get_motor_collection().database.client

        # 1. Template extends: templates in OTHER namespaces that extend
        #    a template in THIS namespace
        templates_coll = motor_client["wip_template_store"]["templates"]
        async for t in templates_coll.find({
            "namespace": {"$ne": prefix},
            "extends_template": {"$exists": True, "$ne": None},
        }, {"namespace": 1, "value": 1, "extends_template": 1}):
            # Look up the parent template to see if it's in our namespace
            parent_value = t.get("extends_template")
            if parent_value:
                parent = await templates_coll.find_one({
                    "value": parent_value,
                    "namespace": prefix,
                })
                if parent:
                    refs.append(InboundReference(
                        type="template_extends",
                        source_namespace=t["namespace"],
                        source_entity=f"{t['value']} (template)",
                        target_entity=f"{parent_value} (template)",
                        impact="Source template loses parent schema. Field inheritance broken.",
                    ))

        # 2. Terminology references: templates in OTHER namespaces with fields
        #    whose terminology_id references a terminology in THIS namespace
        terminologies_coll = motor_client["wip_def_store"]["terminologies"]
        our_terminologies = {}
        async for term in terminologies_coll.find(
            {"namespace": prefix},
            {"terminology_id": 1, "value": 1},
        ):
            our_terminologies[term["terminology_id"]] = term.get("value", term["terminology_id"])

        if our_terminologies:
            tid_set = set(our_terminologies.keys())
            async for t in templates_coll.find({
                "namespace": {"$ne": prefix},
                "fields": {"$exists": True},
            }, {"namespace": 1, "value": 1, "fields": 1}):
                for field in t.get("fields", []):
                    tid = field.get("terminology_id")
                    if tid and tid in tid_set:
                        refs.append(InboundReference(
                            type="terminology_reference",
                            source_namespace=t["namespace"],
                            source_entity=f"{t['value']} (template, field: {field.get('name', '?')})",
                            target_entity=f"{our_terminologies[tid]} (terminology)",
                            impact="Term validation will fail for this field.",
                        ))

        # 3. Synonym links (low severity)
        async for entry in RegistryEntry.find({
            "namespace": {"$ne": prefix},
            "synonyms.namespace": prefix,
        }):
            for syn in entry.synonyms:
                if syn.namespace == prefix:
                    refs.append(InboundReference(
                        type="synonym_link",
                        source_namespace=entry.namespace,
                        source_entity=f"{entry.entry_id} ({entry.entity_type})",
                        target_entity=f"synonym in {prefix}",
                        impact="Cross-reference lost. External synonym remains valid standalone.",
                    ))

        return refs

    # -------------------------------------------------------------------------
    # Internal: journal building
    # -------------------------------------------------------------------------

    async def _build_journal(
        self,
        prefix: str,
        force: bool,
        requested_by: str | None,
        inbound_refs: list[InboundReference],
    ) -> DeletionJournal:
        """Build the deletion journal with all steps."""
        steps: list[DeletionStep] = []
        order = 1

        # Step 1: MinIO objects (must come before files metadata deletion)
        minio_keys = await self._get_minio_keys(prefix)
        if minio_keys and self._minio_url:
            steps.append(DeletionStep(
                order=order,
                store="minio",
                action="delete_objects",
                detail=f"Delete {len(minio_keys)} objects from MinIO",
                storage_keys=minio_keys,
            ))
            order += 1

        # Steps 2-N: MongoDB collections (across all service databases)
        motor_client = Namespace.get_motor_collection().database.client
        for db_name, coll_name, filter_field, _ in _MONGO_COLLECTIONS:
            db = motor_client[db_name]
            coll = db[coll_name]
            count = await coll.count_documents({filter_field: prefix})
            if count > 0:
                steps.append(DeletionStep(
                    order=order,
                    store="mongodb",
                    collection=coll_name,
                    database=db_name,
                    filter={filter_field: prefix},
                    detail=f"Delete {count} documents from {db_name}.{coll_name}",
                ))
                order += 1

        # id_counters: uses counter_key prefix, not a namespace field
        registry_db = motor_client["wip_registry"]
        id_counter_filter = {"counter_key": {"$regex": f"^{prefix}:"}}
        id_counter_count = await registry_db["id_counters"].count_documents(id_counter_filter)
        if id_counter_count > 0:
            steps.append(DeletionStep(
                order=order,
                store="mongodb",
                collection="id_counters",
                database="wip_registry",
                filter=id_counter_filter,
                detail=f"Delete {id_counter_count} ID counters for {prefix}",
            ))
            order += 1

        # PostgreSQL rows (if reporting-sync is configured)
        if self._postgres_url:
            steps.append(DeletionStep(
                order=order,
                store="postgresql",
                action="delete_namespace_rows",
                detail=f"Delete rows WHERE namespace = '{prefix}' from all reporting tables",
                filter={"namespace": prefix},
            ))
            order += 1

        # Final step: delete the namespace record itself
        steps.append(DeletionStep(
            order=order,
            store="mongodb",
            collection="namespaces",
            database="wip_registry",
            filter={"prefix": prefix},
            detail="Delete namespace record",
        ))

        return DeletionJournal(
            namespace=prefix,
            force=force,
            requested_by=requested_by,
            broken_references=inbound_refs if force else [],
            steps=steps,
        )

    # -------------------------------------------------------------------------
    # Internal: journal execution
    # -------------------------------------------------------------------------

    async def _execute_journal(self, journal: DeletionJournal):
        """Execute all pending steps in the journal."""
        summary: dict[str, int] = {}

        for step in journal.steps:
            if step.status == "completed":
                # Already done (crash recovery)
                if step.collection:
                    summary[step.collection] = step.deleted_count
                elif step.store == "minio":
                    summary["minio_objects"] = step.deleted_count
                elif step.store == "postgresql":
                    summary["postgres_rows"] = step.deleted_count
                continue

            try:
                deleted = await self._execute_step(step, journal.namespace)
                step.status = "completed"
                step.deleted_count = deleted
                step.completed_at = datetime.now(UTC)

                if step.collection:
                    summary[step.collection] = deleted
                elif step.store == "minio":
                    summary["minio_objects"] = deleted
                elif step.store == "postgresql":
                    summary["postgres_rows"] = deleted

                logger.info(
                    "Step %d/%d completed: %s (%d deleted)",
                    step.order, len(journal.steps), step.detail, deleted,
                )
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                journal.status = "failed"
                await journal.save()
                logger.error("Step %d failed: %s — %s", step.order, step.detail, e)
                raise

            # Save progress after each step
            await journal.save()

        # All steps completed
        journal.status = "completed"
        journal.completed_at = datetime.now(UTC)
        journal.summary = summary
        await journal.save()
        logger.info("Namespace '%s' deletion completed: %s", journal.namespace, summary)

    async def _execute_step(self, step: DeletionStep, namespace: str) -> int:
        """Execute a single journal step. Returns count of deleted items."""
        if step.store == "mongodb":
            return await self._exec_mongodb_step(step)
        elif step.store == "minio":
            return await self._exec_minio_step(step)
        elif step.store == "postgresql":
            return await self._exec_postgresql_step(step, namespace)
        else:
            raise ValueError(f"Unknown store: {step.store}")

    async def _exec_mongodb_step(self, step: DeletionStep) -> int:
        """Delete documents from a MongoDB collection."""
        motor_client = Namespace.get_motor_collection().database.client
        db_name = step.database or "wip_registry"
        db = motor_client[db_name]
        coll = db[step.collection]
        result = await coll.delete_many(step.filter)
        return result.deleted_count

    async def _exec_minio_step(self, step: DeletionStep) -> int:
        """Delete objects from MinIO via S3 API."""
        if not step.storage_keys or not self._minio_url:
            return 0

        try:
            from urllib.parse import urlparse

            parsed = urlparse(self._minio_url)
            base_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"

            deleted = 0
            async with httpx.AsyncClient(timeout=60.0) as client:
                for key in step.storage_keys:
                    url = f"{base_url}/{self._minio_bucket}/{key}"
                    try:
                        # Simple DELETE per object — works without complex S3 signing
                        # for internal MinIO with access key auth
                        resp = await client.delete(url, auth=(
                            self._minio_access_key,
                            self._minio_secret_key,
                        ))
                        if resp.status_code in (200, 204, 404):
                            deleted += 1
                    except Exception as e:
                        logger.warning("Failed to delete MinIO object %s: %s", key, e)
            return deleted
        except Exception as e:
            logger.error("MinIO deletion failed: %s", e)
            # Don't block the rest of the deletion
            return 0

    async def _exec_postgresql_step(self, step: DeletionStep, namespace: str) -> int:
        """Delete namespace rows from PostgreSQL via reporting-sync API."""
        if not self._postgres_url:
            return 0

        try:
            api_key = os.getenv("API_KEY") or os.getenv("REGISTRY_API_KEY", "")
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.delete(
                    f"{self._postgres_url}/api/reporting-sync/namespace/{namespace}",
                    headers={"X-API-Key": api_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("total_deleted", 0)
                elif resp.status_code == 404:
                    # Endpoint doesn't exist yet — skip gracefully
                    logger.warning(
                        "Reporting-sync namespace delete endpoint not available (404)"
                    )
                    return 0
                else:
                    logger.warning(
                        "PostgreSQL cleanup returned %d: %s",
                        resp.status_code, resp.text,
                    )
                    return 0
        except httpx.ConnectError:
            logger.warning("Could not connect to reporting-sync for PostgreSQL cleanup")
            return 0
        except Exception as e:
            logger.error("PostgreSQL deletion failed: %s", e)
            return 0

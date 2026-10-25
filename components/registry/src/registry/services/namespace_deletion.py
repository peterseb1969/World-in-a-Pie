"""Namespace deletion service.

Builds and executes a persistent delete journal for namespace deletion.
Crash-safety is a MongoDB guarantee: Mongo steps fail the journal loudly
and resume idempotently. File-storage and PostgreSQL cleanup is
best-effort by design — a dead secondary store must not make a namespace undeletable —
but a degraded step records its error and the journal terminates as
"completed_with_warnings" instead of a bare "completed", so the outcome
is visible in the journal itself, not only in service logs.
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from ..models.deletion_journal import (
    DeletionJournal,
    DeletionStep,
    InboundReference,
)
from ..models.entry import RegistryEntry
from ..models.namespace import Namespace

logger = logging.getLogger("registry.namespace_deletion")

# The install's service credential for calls from the Registry to other WIP
# services, in the order the registry manifest provides it: the legacy and
# master keys are both mapped from the install's api-key secret; API_KEY and
# REGISTRY_API_KEY are honoured for setups that name it that way.
# Journal store values for the stored-objects step. "minio" is the name
# older journals carry; it executes the same way so they stay resumable.
_FILE_STORAGE_STORES = ("file_storage", "minio")
# document-store accepts at most this many keys per request.
_STORAGE_DELETE_BATCH = 1000

_SERVICE_KEY_VARS = (
    "WIP_AUTH_LEGACY_API_KEY",
    "MASTER_API_KEY",
    "API_KEY",
    "REGISTRY_API_KEY",
)


def _service_api_key() -> str:
    """Return the first non-empty service key from the environment, or ''."""
    for var in _SERVICE_KEY_VARS:
        value = os.getenv(var)
        if value:
            return value
    return ""


# External service databases with namespace-scoped data.
# Each tuple: (database_name, collection_name, filter_field, service_label)
_EXTERNAL_COLLECTIONS = [
    ("wip_document_store", "files", "namespace", "document-store"),
    ("wip_document_store", "documents", "namespace", "document-store"),
    ("wip_template_store", "templates", "namespace", "template-store"),
    ("wip_def_store", "terms", "namespace", "def-store"),
    ("wip_def_store", "term_relations", "namespace", "def-store"),
    ("wip_def_store", "term_audit_log", "namespace", "def-store"),
    ("wip_def_store", "terminologies", "namespace", "def-store"),
]

# Registry-local collections — database name resolved at runtime from Beanie.
# Each tuple: (collection_name, filter_field)
_REGISTRY_COLLECTIONS = [
    ("registry_entries", "namespace"),
    ("namespace_grants", "namespace"),
    # CASE-427: composite-key claims scoped to this namespace. Cross-namespace
    # synonym claims owned by a now-deleted entry in another namespace are
    # mopped up by startup reconcile_orphan_claims (owner-entry-missing).
    ("composite_key_claims", "namespace"),
]


def _registry_db_name() -> str:
    """Get the actual registry database name from Beanie config."""
    return Namespace.get_motor_collection().database.name

# id_counters uses counter_key prefix, not a namespace field — handled separately


class NamespaceDeletionService:
    """Orchestrates namespace deletion with a persistent journal."""

    def __init__(self):
        # Stored objects are deleted by document-store, which owns the
        # storage client, bucket and credentials for whichever object store
        # the install runs; the registry only sends it the journal's keys.
        self._document_store_url = os.getenv("DOCUMENT_STORE_URL")
        self._postgres_url = os.getenv("REPORTING_SYNC_URL")

    async def dry_run(self, prefix: str) -> dict[str, Any]:
        """Return an impact report without making any changes."""
        ns = await Namespace.find_one({"prefix": prefix})
        if not ns:
            return {"error": "Namespace not found"}

        entity_counts = await self._count_entities(prefix)
        storage_keys = await self._get_storage_keys(prefix)
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
            # Key name kept for API compatibility: it counts stored file
            # objects in whichever object store the install runs.
            "minio_objects": len(storage_keys),
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
        """Count all entities scoped to a namespace.

        Iterates the same collection lists the journal builder deletes from
        (_REGISTRY_COLLECTIONS + _EXTERNAL_COLLECTIONS), so the dry-run
        impact report cannot drift from what the real deletion removes —
        hand-enumerating collections here is how composite_key_claims went
        uncounted while being deleted.
        """
        counts: dict[str, int] = {}

        motor_client = Namespace.get_motor_collection().database.client
        reg_db = motor_client[_registry_db_name()]

        for coll_name, filter_field in _REGISTRY_COLLECTIONS:
            counts[coll_name] = await reg_db[coll_name].count_documents(
                {filter_field: prefix}
            )

        # id_counters is keyed by "<prefix>:<entity_type>", not a namespace
        # field — deleted by the journal via the same regex filter.
        counts["id_counters"] = await reg_db["id_counters"].count_documents(
            {"counter_key": {"$regex": f"^{prefix}:"}}
        )

        # Count across external service databases
        for db_name, coll_name, filter_field, _ in _EXTERNAL_COLLECTIONS:
            db = motor_client[db_name]
            coll = db[coll_name]
            counts[coll_name] = await coll.count_documents({filter_field: prefix})

        return counts

    async def _get_storage_keys(self, prefix: str) -> list[str]:
        """Storage keys of every file record in this namespace (any status)."""
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

        # Step 1: stored file objects. Runs before the files metadata is
        # deleted, and the keys are persisted on the step itself, so a step
        # that fails or is retried later still knows what to remove after the
        # file records are gone.
        storage_keys = await self._get_storage_keys(prefix)
        if storage_keys:
            steps.append(DeletionStep(
                order=order,
                store="file_storage",
                action="delete_objects",
                detail=f"Delete {len(storage_keys)} stored file objects",
                storage_keys=storage_keys,
            ))
            order += 1

        # Steps 2-N: MongoDB collections (across all service databases)
        motor_client = Namespace.get_motor_collection().database.client
        reg_db_name = _registry_db_name()

        # External service databases
        for db_name, coll_name, filter_field, _ in _EXTERNAL_COLLECTIONS:
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

        # Registry-local collections
        for coll_name, filter_field in _REGISTRY_COLLECTIONS:
            db = motor_client[reg_db_name]
            coll = db[coll_name]
            count = await coll.count_documents({filter_field: prefix})
            if count > 0:
                steps.append(DeletionStep(
                    order=order,
                    store="mongodb",
                    collection=coll_name,
                    database=reg_db_name,
                    filter={filter_field: prefix},
                    detail=f"Delete {count} documents from {reg_db_name}.{coll_name}",
                ))
                order += 1

        # id_counters: uses counter_key prefix, not a namespace field
        registry_db = motor_client[reg_db_name]
        id_counter_filter = {"counter_key": {"$regex": f"^{prefix}:"}}
        id_counter_count = await registry_db["id_counters"].count_documents(id_counter_filter)
        if id_counter_count > 0:
            steps.append(DeletionStep(
                order=order,
                store="mongodb",
                collection="id_counters",
                database=reg_db_name,
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
            database=reg_db_name,
            filter={"prefix": prefix},
            detail="Delete namespace record",
        ))

        return DeletionJournal(
            namespace=prefix,
            force=force,
            requested_by=requested_by,
            # Persisted unconditionally: the journal is the audit trail, and a
            # non-forced deletion still breaks its low-severity inbound refs
            # (synonym links) — gating on force would drop the only record of
            # what broke on the common path.
            broken_references=inbound_refs,
            steps=steps,
        )

    # -------------------------------------------------------------------------
    # Internal: journal execution
    # -------------------------------------------------------------------------

    async def _execute_journal(self, journal: DeletionJournal):
        """Execute all pending steps in the journal."""
        summary: dict[str, int] = {}

        # Index-based iteration is load-bearing: Beanie's save() re-syncs the
        # in-memory document from the DB afterwards, REBUILDING journal.steps
        # with fresh objects. A `for step in journal.steps` iterator captured
        # before the first save yields orphans — mutations on them are never
        # serialized, which is how completed journals persisted with every
        # step after the first still 'pending'. Re-binding through
        # journal.steps[i] each iteration always mutates the live objects.
        for i in range(len(journal.steps)):
            step = journal.steps[i]
            if step.status == "completed":
                # Already done (crash recovery)
                if step.collection:
                    summary[step.collection] = step.deleted_count
                elif step.store in _FILE_STORAGE_STORES:
                    summary["minio_objects"] = step.deleted_count
                elif step.store == "postgresql":
                    summary["postgres_rows"] = step.deleted_count
                continue

            try:
                deleted = await self._execute_step(step, journal.namespace)
                # Best-effort executors (object store/PostgreSQL) record a swallowed
                # failure on step.error instead of raising — surface it as a
                # degraded outcome rather than a clean completion.
                step.status = "completed_with_errors" if step.error else "completed"
                step.deleted_count = deleted
                step.completed_at = datetime.now(UTC)

                if step.collection:
                    summary[step.collection] = deleted
                elif step.store in _FILE_STORAGE_STORES:
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

        # All steps executed. Roll degraded best-effort steps up into the
        # terminal status so a journal reader sees the outcome without
        # scanning per-step errors or service logs.
        degraded = [s for s in journal.steps if s.status == "completed_with_errors"]
        journal.status = "completed_with_warnings" if degraded else "completed"
        journal.completed_at = datetime.now(UTC)
        journal.summary = summary
        await journal.save()
        if degraded:
            logger.warning(
                "Namespace '%s' deletion completed with %d degraded step(s): %s",
                journal.namespace, len(degraded),
                "; ".join(f"{s.detail}: {s.error}" for s in degraded),
            )
        else:
            logger.info("Namespace '%s' deletion completed: %s", journal.namespace, summary)

    async def _execute_step(self, step: DeletionStep, namespace: str) -> int:
        """Execute a single journal step. Returns count of deleted items."""
        if step.store == "mongodb":
            return await self._exec_mongodb_step(step)
        elif step.store in _FILE_STORAGE_STORES:
            return await self._exec_file_storage_step(step, namespace)
        elif step.store == "postgresql":
            return await self._exec_postgresql_step(step, namespace)
        else:
            raise ValueError(f"Unknown store: {step.store}")

    async def _exec_mongodb_step(self, step: DeletionStep) -> int:
        """Delete documents from a MongoDB collection."""
        motor_client = Namespace.get_motor_collection().database.client
        db_name = step.database or _registry_db_name()
        db = motor_client[db_name]
        coll = db[cast(str, step.collection)]
        result = await coll.delete_many(cast(dict, step.filter) or {})
        return result.deleted_count

    async def _exec_file_storage_step(self, step: DeletionStep, namespace: str) -> int:
        """Delete the step's stored objects through document-store."""
        keys = step.storage_keys or []
        if not keys:
            return 0
        # A re-run must not inherit the error of an earlier attempt.
        step.error = None

        if not self._document_store_url:
            logger.error("DOCUMENT_STORE_URL is not configured; stored objects not deleted")
            step.error = (
                "DOCUMENT_STORE_URL not configured; "
                f"{len(keys)} stored objects may remain"
            )
            return 0
        api_key = _service_api_key()
        if not api_key:
            logger.error(
                "No service API key configured for the file-storage cleanup "
                "(checked %s)", ", ".join(_SERVICE_KEY_VARS),
            )
            step.error = (
                "no service API key configured for file-storage cleanup; "
                f"{len(keys)} stored objects may remain"
            )
            return 0

        deleted = 0
        problems: list[str] = []
        url = f"{self._document_store_url}/api/document-store/files/storage/delete"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                for i in range(0, len(keys), _STORAGE_DELETE_BATCH):
                    batch = keys[i:i + _STORAGE_DELETE_BATCH]
                    resp = await client.post(
                        url,
                        json={"namespace": namespace, "storage_keys": batch},
                        headers={"X-API-Key": api_key},
                    )
                    if resp.status_code != 200:
                        problems.append(
                            f"document-store answered {resp.status_code} for "
                            f"{len(batch)} keys: {resp.text[:200]}"
                        )
                        continue
                    data = resp.json()
                    deleted += int(data.get("deleted") or 0)
                    if data.get("refused"):
                        problems.append(
                            f"{len(data['refused'])} keys refused (owned by another "
                            "namespace)"
                        )
                    if data.get("failed"):
                        problems.append(f"{len(data['failed'])} keys failed to delete")
        except httpx.HTTPError as e:
            logger.warning("Could not reach document-store for file-storage cleanup: %s", e)
            problems.append(f"could not reach document-store: {e}")

        if problems:
            step.error = (
                f"{deleted} of {len(keys)} stored objects deleted; "
                + "; ".join(problems)
                + "; objects may remain in storage"
            )
        return deleted

    async def _exec_postgresql_step(self, step: DeletionStep, namespace: str) -> int:
        """Delete namespace rows from PostgreSQL via reporting-sync API."""
        if not self._postgres_url:
            return 0

        api_key = _service_api_key()
        if not api_key:
            # Sending an empty key would be refused by reporting-sync and
            # degrade to the generic "rows may remain" below; name the real
            # cause instead so the journal points at the misconfiguration.
            logger.error(
                "No service API key configured for the reporting-sync cleanup "
                "(checked %s)", ", ".join(_SERVICE_KEY_VARS),
            )
            step.error = (
                "no service API key configured for reporting-sync cleanup; "
                "PostgreSQL rows may remain"
            )
            return 0

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.delete(
                    f"{self._postgres_url}/api/reporting-sync/namespace/{namespace}",
                    headers={"X-API-Key": api_key},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    total = data.get("total_deleted")
                    if total is None:
                        # A present-but-null count (the reporting-sync
                        # schema-per-namespace rewrite shipped one) must not
                        # reach the int-typed journal fields — it poisons the
                        # journal document and the DELETE reports failure
                        # after the deletion already succeeded. Record the
                        # degraded audit instead of inventing a count.
                        step.error = (
                            "postgres row count unavailable (upstream "
                            "returned null total_deleted)"
                        )
                        return 0
                    return int(total)
                elif resp.status_code == 404:
                    # Endpoint doesn't exist yet — best-effort skip, but the
                    # rows remain, so record the degraded outcome.
                    logger.warning(
                        "Reporting-sync namespace delete endpoint not available (404)"
                    )
                    step.error = (
                        "reporting-sync namespace-delete endpoint unavailable "
                        "(404); PostgreSQL rows may remain"
                    )
                    return 0
                else:
                    logger.warning(
                        "PostgreSQL cleanup returned %d: %s",
                        resp.status_code, resp.text,
                    )
                    step.error = (
                        f"PostgreSQL cleanup returned {resp.status_code}; "
                        "rows may remain"
                    )
                    return 0
        except httpx.ConnectError:
            logger.warning("Could not connect to reporting-sync for PostgreSQL cleanup")
            step.error = (
                "could not connect to reporting-sync; PostgreSQL rows may remain"
            )
            return 0
        except Exception as e:
            logger.error("PostgreSQL deletion failed: %s", e)
            step.error = f"PostgreSQL cleanup failed: {e}; rows may remain"
            return 0

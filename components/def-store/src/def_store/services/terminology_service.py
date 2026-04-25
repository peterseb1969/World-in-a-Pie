"""Terminology service for business logic."""

import asyncio
import logging
from datetime import UTC, datetime

from pymongo.errors import BulkWriteError, DuplicateKeyError

# Import identity helper from wip-auth
# This returns the authenticated identity, not the client-provided value
from ..api.auth import get_identity_string
from ..models.api_models import (
    BulkResultItem,
    CreateTerminologyRequest,
    CreateTermRequest,
    DeprecateTermRequest,
    TerminologyResponse,
    TermResponse,
    UpdateTerminologyRequest,
    UpdateTermRequest,
)
from ..models.audit_log import TermAuditLog
from ..models.term import Term
from ..models.term_relation import TermRelation
from ..models.terminology import Terminology, TerminologyMetadata
from .nats_client import (
    EventType as NatsEventType,
)
from .nats_client import (
    publish_term_event,
    publish_term_events_bulk,
    publish_term_relation_event,
    publish_terminology_event,
)
from .registry_client import RegistryError, get_registry_client

logger = logging.getLogger(__name__)


class TerminologyService:
    """Service for managing terminologies and terms."""

    # =========================================================================
    # TERMINOLOGY OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_terminology(
        request: CreateTerminologyRequest,
        namespace: str
    ) -> TerminologyResponse:
        """
        Create a new terminology.

        1. Register with Registry to get ID
        2. Create terminology document in MongoDB

        Args:
            request: Creation request
            namespace: Namespace for the terminology (default: wip)

        Returns:
            Created terminology

        Raises:
            ValueError: If value already exists
            RegistryError: If Registry communication fails
        """
        # Check if value already exists within namespace
        existing = await Terminology.find_one({"namespace": namespace, "value": request.value})
        if existing:
            raise ValueError(f"Terminology with value '{request.value}' already exists in namespace '{namespace}'")

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Register with Registry to get ID (or use pre-assigned ID for restore)
        client = get_registry_client()
        terminology_id = await client.register_terminology(
            value=request.value,
            label=request.label,
            created_by=actor,
            namespace=namespace,
            entry_id=request.terminology_id,
        )

        # mutable implies extensible
        extensible = request.extensible or request.mutable

        # Create terminology document
        terminology = Terminology(
            namespace=namespace,
            terminology_id=terminology_id,
            value=request.value,
            label=request.label,
            description=request.description,
            case_sensitive=request.case_sensitive,
            allow_multiple=request.allow_multiple,
            extensible=extensible,
            mutable=request.mutable,
            metadata=request.metadata or TerminologyMetadata(),
            created_by=actor,
        )
        try:
            await terminology.insert()
        except DuplicateKeyError as e:
            raise ValueError(
                f"Terminology ID '{terminology_id}' already exists (collision across namespaces)"
            ) from e

        # Register auto-synonym for human-readable resolution
        # On failure, roll back the MongoDB document and re-raise
        try:
            await client.register_auto_synonym(
                target_id=terminology_id,
                namespace=namespace,
                entity_type="terminologies",
                composite_key={
                    "ns": namespace,
                    "type": "terminology",
                    "value": request.value,
                },
                created_by=actor,
            )
        except RegistryError:
            logger.error(
                "Auto-synonym registration failed for terminology %s — rolling back",
                terminology_id,
            )
            await terminology.delete()
            raise

        # Create audit log entry for terminology creation
        await TerminologyService._create_audit_log(
            term_id=terminology_id,
            terminology_id=terminology_id,
            action="created",
            changed_by=actor,
            new_values={
                "value": request.value,
                "label": request.label,
                "description": request.description,
            },
            namespace=namespace
        )

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_CREATED,
            TerminologyService._terminology_to_event_dict(terminology),
            changed_by=actor,
        )

        return TerminologyService._to_terminology_response(terminology)

    @staticmethod
    async def get_terminology(
        terminology_id: str | None = None,
        value: str | None = None,
        namespace: str | None = None
    ) -> TerminologyResponse | None:
        """
        Get a terminology by ID or value.

        Args:
            terminology_id: Terminology ID
            value: Terminology value (e.g., 'DOC_STATUS')
            namespace: Namespace to search in (if None, searches globally by ID)

        Returns:
            Terminology if found, None otherwise
        """
        if terminology_id:
            # ID lookups can be global (for cross-namespace refs in open mode)
            query = {"terminology_id": terminology_id}
            if namespace:
                query["namespace"] = namespace
            terminology = await Terminology.find_one(query)
        elif value:
            query = {"value": value}
            if namespace is not None:
                query["namespace"] = namespace
            terminology = await Terminology.find_one(query)
        else:
            return None

        if terminology:
            return TerminologyService._to_terminology_response(terminology)
        return None

    @staticmethod
    async def list_terminologies(
        status: str | None = None,
        value: str | None = None,
        page: int = 1,
        page_size: int = 50,
        ns_filter: dict | None = None,
    ) -> tuple[list[TerminologyResponse], int]:
        """
        List terminologies with pagination.

        Args:
            status: Filter by status (active, inactive)
            value: Filter by exact value match
            page: Page number (1-indexed)
            page_size: Items per page
            ns_filter: Namespace filter dict from resolve_namespace_filter()

        Returns:
            Tuple of (terminologies, total_count)
        """
        query: dict = {}
        if ns_filter:
            query.update(ns_filter)
        if status:
            query["status"] = status
        if value:
            query["value"] = value

        total = await Terminology.find(query).count()
        skip = (page - 1) * page_size

        terminologies = await Terminology.find(query) \
            .sort("label") \
            .skip(skip) \
            .limit(page_size) \
            .to_list()

        return (
            [TerminologyService._to_terminology_response(t) for t in terminologies],
            total
        )

    @staticmethod
    async def update_terminology(
        terminology_id: str,
        request: UpdateTerminologyRequest
    ) -> TerminologyResponse | None:
        """
        Update a terminology.

        If value changes, adds a synonym in the Registry.

        Args:
            terminology_id: Terminology to update
            request: Update request

        Returns:
            Updated terminology, or None if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return None

        # Track if value is changing
        old_value = terminology.value
        value_changed = request.value and request.value != old_value

        # If value changes, add auto-synonym in Registry for new value
        if value_changed:
            # Check new value doesn't exist within the same namespace
            existing = await Terminology.find_one({"namespace": terminology.namespace, "value": request.value})
            if existing:
                raise ValueError(f"Terminology with value '{request.value}' already exists in namespace '{terminology.namespace}'")

            # Register auto-synonym for the new value (old auto-synonym persists)
            client = get_registry_client()
            await client.register_auto_synonym(
                target_id=terminology_id,
                namespace=terminology.namespace,
                entity_type="terminologies",
                composite_key={
                    "ns": terminology.namespace,
                    "type": "terminology",
                    "value": request.value,
                },
                created_by=get_identity_string(),
            )

        # Track changes for audit log
        changed_fields = []
        previous_values = {}
        new_values = {}

        def _track(field_name, old_val, new_val):
            if new_val is not None and new_val != old_val:
                changed_fields.append(field_name)
                previous_values[field_name] = old_val
                new_values[field_name] = new_val

        _track("value", terminology.value, request.value)
        _track("label", terminology.label, request.label)
        _track("description", terminology.description, request.description)
        _track("case_sensitive", terminology.case_sensitive, request.case_sensitive)
        _track("allow_multiple", terminology.allow_multiple, request.allow_multiple)
        _track("extensible", terminology.extensible, request.extensible)
        _track("mutable", terminology.mutable, request.mutable)

        # Reject mutable changes if terms exist
        if request.mutable is not None and request.mutable != terminology.mutable:
            if terminology.term_count > 0:
                raise ValueError(
                    "Cannot change mutable flag on terminology with existing terms"
                )

        # Apply updates
        if request.value is not None:
            terminology.value = request.value
        if request.label is not None:
            terminology.label = request.label
        if request.description is not None:
            terminology.description = request.description
        if request.case_sensitive is not None:
            terminology.case_sensitive = request.case_sensitive
        if request.allow_multiple is not None:
            terminology.allow_multiple = request.allow_multiple
        if request.extensible is not None:
            terminology.extensible = request.extensible
        if request.mutable is not None:
            terminology.mutable = request.mutable
            # mutable implies extensible
            if request.mutable:
                terminology.extensible = True
        if request.metadata is not None:
            terminology.metadata = request.metadata

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        terminology.updated_at = datetime.now(UTC)
        terminology.updated_by = actor
        await terminology.save()

        # Create audit log entry if there were changes
        if changed_fields:
            await TerminologyService._create_audit_log(
                term_id=terminology_id,
                terminology_id=terminology_id,
                action="updated",
                changed_by=actor,
                changed_fields=changed_fields,
                previous_values=previous_values,
                new_values=new_values,
                namespace=terminology.namespace
            )

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_UPDATED,
            TerminologyService._terminology_to_event_dict(terminology),
            changed_by=actor,
        )

        return TerminologyService._to_terminology_response(terminology)

    @staticmethod
    async def delete_terminology(
        terminology_id: str,
        updated_by: str | None = None,  # Deprecated: uses authenticated identity
        hard_delete: bool = False,
    ) -> bool:
        """
        Delete a terminology. Hard-deletes if mutable OR if hard_delete=True
        and namespace deletion_mode is 'full'. Soft-deletes otherwise.

        Also deletes/deactivates all terms and relations in the terminology.

        Args:
            terminology_id: Terminology to delete
            updated_by: Deprecated - uses authenticated identity
            hard_delete: Force hard-delete (requires namespace deletion_mode='full')

        Returns:
            True if deleted, False if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return False

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Determine if we should hard-delete
        should_hard_delete = terminology.mutable
        if hard_delete and not should_hard_delete:
            # Check namespace deletion_mode
            client = get_registry_client()
            deletion_mode = await client.get_namespace_deletion_mode(terminology.namespace)
            if deletion_mode != "full":
                raise ValueError(
                    f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')"
                )
            should_hard_delete = True

        if should_hard_delete:
            # HARD DELETE: remove terminology, all terms, and all relations

            # 1. Get all term IDs in this terminology
            term_ids = [
                t.term_id
                for t in await Term.find({"terminology_id": terminology_id}).to_list()
            ]

            # 2. Delete all relations involving these terms
            if term_ids:
                await TermRelation.find({
                    "$or": [
                        {"source_term_id": {"$in": term_ids}},
                        {"target_term_id": {"$in": term_ids}}
                    ]
                }).delete()

            # 3. Hard-delete all terms
            await Term.find({"terminology_id": terminology_id}).delete()

            # 4. Capture event dict before deleting
            event_dict = TerminologyService._terminology_to_event_dict(terminology)
            event_dict["hard_delete"] = True

            # 5. Hard-delete the terminology document
            await terminology.delete()

            # 6. Hard-delete Registry entries for terminology and its terms
            client = get_registry_client()
            try:
                await client.hard_delete_entry(terminology_id, updated_by=actor)
                for tid in term_ids:
                    await client.hard_delete_entry(tid, updated_by=actor)
            except Exception as e:
                logger.warning(f"Failed to hard-delete Registry entries for terminology {terminology_id}: {e}")
        else:
            # SOFT DELETE: deactivate terminology and all terms (existing behavior)
            terminology.status = "inactive"
            terminology.updated_at = datetime.now(UTC)
            terminology.updated_by = actor
            await terminology.save()

            await Term.find({"terminology_id": terminology_id}).update_many({
                "$set": {
                    "status": "inactive",
                    "updated_at": datetime.now(UTC),
                    "updated_by": actor
                }
            })

            event_dict = TerminologyService._terminology_to_event_dict(terminology)

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_DELETED,
            event_dict,
            changed_by=actor,
        )

        return True

    @staticmethod
    async def restore_terminology(
        terminology_id: str,
        restore_terms: bool = True
    ) -> TerminologyResponse | None:
        """
        Restore a soft-deleted terminology (set status back to active).

        Optionally reactivates all terms that were deactivated with it.

        Args:
            terminology_id: Terminology to restore
            restore_terms: If True, also reactivate inactive terms

        Returns:
            Restored terminology, or None if not found
        """
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            return None

        if terminology.status == "active":
            return TerminologyService._to_terminology_response(terminology)

        actor = get_identity_string()
        now = datetime.now(UTC)

        # Reactivate terminology
        terminology.status = "active"
        terminology.updated_at = now
        terminology.updated_by = actor
        await terminology.save()

        # Reactivate terms
        if restore_terms:
            await Term.find({
                "terminology_id": terminology_id,
                "status": "inactive"
            }).update_many({
                "$set": {
                    "status": "active",
                    "updated_at": now,
                    "updated_by": actor
                }
            })

            # Recalculate term count
            terminology.term_count = await Term.find({
                "terminology_id": terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        # Publish NATS event
        await publish_terminology_event(
            NatsEventType.TERMINOLOGY_RESTORED,
            TerminologyService._terminology_to_event_dict(terminology),
            changed_by=actor,
        )

        return TerminologyService._to_terminology_response(terminology)

    # =========================================================================
    # TERM OPERATIONS
    # =========================================================================

    @staticmethod
    async def create_term(
        terminology_id: str,
        request: CreateTermRequest,
    ) -> TermResponse:
        """
        Create a new term in a terminology.

        Namespace is inherited from the parent terminology.

        Args:
            terminology_id: Parent terminology ID
            request: Creation request

        Returns:
            Created term

        Raises:
            ValueError: If terminology not found or value exists
        """
        # Verify terminology exists
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            raise ValueError(f"Terminology '{terminology_id}' not found")

        namespace = terminology.namespace

        # Check value uniqueness within terminology and namespace
        existing = await Term.find_one({
            "namespace": namespace,
            "terminology_id": terminology_id,
            "value": request.value
        })
        if existing:
            raise ValueError(
                f"Term with value '{request.value}' already exists in terminology"
            )

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Default label to value if not provided
        label = request.label or request.value

        # Register with Registry to get ID (or use pre-assigned ID for restore)
        client = get_registry_client()
        term_id = await client.register_term(
            terminology_id=terminology_id,
            value=request.value,
            created_by=actor,
            namespace=namespace,
            entry_id=request.term_id,
        )

        # Create term document
        term = Term(
            namespace=namespace,
            term_id=term_id,
            terminology_id=terminology_id,
            terminology_value=terminology.value,
            value=request.value,
            aliases=request.aliases,
            label=label,
            description=request.description,
            sort_order=request.sort_order,
            parent_term_id=request.parent_term_id,
            translations=request.translations,
            metadata=request.metadata,
            created_by=actor,
        )
        try:
            await term.insert()
        except DuplicateKeyError as e:
            raise ValueError(
                f"Term ID '{term_id}' already exists (collision across namespaces)"
            ) from e

        # Register auto-synonym for human-readable resolution
        # Uses "TERMINOLOGY_VALUE:TERM_VALUE" colon notation for resolution
        # On failure, roll back the MongoDB document and re-raise
        try:
            await client.register_auto_synonym(
                target_id=term_id,
                namespace=namespace,
                entity_type="terms",
                composite_key={
                    "ns": namespace,
                    "type": "term",
                    "terminology": terminology.value,
                    "value": request.value,
                },
                created_by=actor,
            )
        except RegistryError:
            logger.error(
                "Auto-synonym registration failed for term %s — rolling back",
                term_id,
            )
            await term.delete()
            raise

        # Create audit log entry
        await TerminologyService._create_audit_log(
            term_id=term_id,
            terminology_id=terminology_id,
            action="created",
            changed_by=actor,
            new_values={
                "value": request.value,
                "aliases": request.aliases,
                "label": label,
            },
            namespace=namespace
        )

        # Update terminology term count
        terminology.term_count += 1
        terminology.updated_at = datetime.now(UTC)
        await terminology.save()

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_CREATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=actor,
        )

        # Invalidate relation type cache if this is the system terminology
        if terminology.value == "_ONTOLOGY_RELATIONSHIP_TYPES":
            from .ontology_service import OntologyService
            OntologyService.invalidate_relation_type_cache()

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def create_terms_bulk(
        terminology_id: str,
        terms: list[CreateTermRequest],
        created_by: str | None = None,  # Deprecated: uses authenticated identity
        skip_duplicates: bool = True,
        update_existing: bool = False,
        batch_size: int = 1000,
        registry_batch_size: int = 100,
    ) -> list[BulkResultItem]:
        """
        Create multiple terms in a terminology using batch operations.

        Uses bulk MongoDB operations (insert_many) instead of per-term inserts
        for significantly better performance on large imports.

        Namespace is inherited from the parent terminology.

        All operations are chunked to handle large imports (100k+ terms).

        Args:
            terminology_id: Parent terminology ID
            terms: Terms to create
            created_by: Deprecated - uses authenticated identity
            skip_duplicates: If True, skip terms whose value already exists
            update_existing: If True, placeholder for future update logic
            batch_size: Number of terms to process per MongoDB batch (default 1000)
            registry_batch_size: Number of terms per registry HTTP call (default 100)

        Returns:
            List of operation results
        """
        if not terms:
            return []

        total_terms = len(terms)
        logger.info(f"Starting bulk import of {total_terms} terms to {terminology_id}")

        # Phase A: Verify terminology exists (1 query)
        terminology = await Terminology.find_one({"terminology_id": terminology_id})
        if not terminology:
            raise ValueError(f"Terminology '{terminology_id}' not found")

        namespace = terminology.namespace

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()
        now = datetime.now(UTC)

        # Initialize results array
        results: list[BulkResultItem | None] = [None] * len(terms)
        total_created = 0
        client = get_registry_client()
        num_batches = (total_terms + batch_size - 1) // batch_size

        # Process in batches to avoid MongoDB/HTTP limits
        for batch_num, batch_start in enumerate(range(0, len(terms), batch_size), 1):
            batch_end = min(batch_start + batch_size, len(terms))
            batch_terms = terms[batch_start:batch_end]
            logger.info(
                f"Processing batch {batch_num}/{num_batches}: "
                f"terms {batch_start+1}-{batch_end} of {total_terms}"
            )

            # Phase B: Registry call for this batch (with sub-batching)
            logger.debug(f"Registering {len(batch_terms)} terms with registry...")
            batch_registry_results = await client.register_terms_bulk(
                terminology_id=terminology_id,
                terms=[
                    {"value": t.value, "entry_id": t.term_id}
                    if hasattr(t, "term_id") and t.term_id
                    else {"value": t.value}
                    for t in batch_terms
                ],
                created_by=actor,
                registry_batch_size=registry_batch_size,
                namespace=namespace,
            )
            logger.debug(f"Registry registration complete for batch {batch_num}")

            # Phase C: Duplicate check for this batch
            batch_ids = [
                r["registry_id"] for r in batch_registry_results
                if r.get("status") != "error" and r.get("registry_id")
            ]
            batch_values = [t.value for t in batch_terms]

            existing_by_id = {}
            existing_by_value = {}
            if batch_ids:
                existing_terms = await Term.find(
                    {"term_id": {"$in": batch_ids}}
                ).to_list()
                existing_by_id = {t.term_id: t for t in existing_terms}
            if batch_values:
                value_matches = await Term.find({
                    "namespace": namespace,
                    "terminology_id": terminology_id,
                    "value": {"$in": batch_values}
                }).to_list()
                existing_by_value = {t.value: t for t in value_matches}

            # Phase D: Partition this batch into create/skip/error
            terms_to_insert: list[Term] = []
            insert_indices: list[int] = []  # maps insert position -> global index

            for i, (term_req, reg_result) in enumerate(zip(batch_terms, batch_registry_results, strict=False)):
                global_idx = batch_start + i

                if reg_result.get("status") == "error":
                    results[global_idx] = BulkResultItem(
                        index=global_idx,
                        status="error",
                        value=term_req.value,
                        error=reg_result.get("error")
                    )
                    continue

                term_id = reg_result["registry_id"]

                # Check duplicates by term_id or by value within terminology
                existing = existing_by_id.get(term_id) or existing_by_value.get(term_req.value)
                if existing:
                    if skip_duplicates or update_existing:
                        results[global_idx] = BulkResultItem(
                            index=global_idx,
                            status="skipped" if skip_duplicates else "updated",
                            id=existing.term_id,
                            value=term_req.value,
                            error="Already exists" if skip_duplicates else None
                        )
                    else:
                        results[global_idx] = BulkResultItem(
                            index=global_idx,
                            status="error",
                            value=term_req.value,
                            error=f"Term with value '{term_req.value}' already exists"
                        )
                    continue

                # Default label to value if not provided
                label = term_req.label or term_req.value

                # Build Term document for batch insert
                term = Term(
                    namespace=namespace,
                    term_id=term_id,
                    terminology_id=terminology_id,
                    terminology_value=terminology.value,
                    value=term_req.value,
                    aliases=term_req.aliases,
                    label=label,
                    description=term_req.description,
                    sort_order=term_req.sort_order,
                    parent_term_id=term_req.parent_term_id,
                    translations=term_req.translations,
                    metadata=term_req.metadata,
                    created_by=actor,
                )
                terms_to_insert.append(term)
                insert_indices.append(global_idx)

            # Phase E: Batch insert terms for this batch
            batch_created = 0
            if terms_to_insert:
                try:
                    await Term.insert_many(terms_to_insert, ordered=False)
                    # All succeeded
                    for pos, idx in enumerate(insert_indices):
                        term = terms_to_insert[pos]
                        results[idx] = BulkResultItem(
                            index=idx,
                            status="created",
                            id=term.term_id,
                            value=term.value,
                        )
                        batch_created += 1
                except BulkWriteError as bwe:
                    # Some inserts may have failed (e.g. race condition duplicates)
                    failed_indices = {
                        err["index"] for err in bwe.details.get("writeErrors", [])
                    }
                    error_messages = {
                        err["index"]: err.get("errmsg", "Insert failed")
                        for err in bwe.details.get("writeErrors", [])
                    }
                    for pos, idx in enumerate(insert_indices):
                        term = terms_to_insert[pos]
                        if pos in failed_indices:
                            results[idx] = BulkResultItem(
                                index=idx,
                                status="error",
                                value=term.value,
                                error=error_messages.get(pos, "Insert failed"),
                            )
                        else:
                            results[idx] = BulkResultItem(
                                index=idx,
                                status="created",
                                id=term.term_id,
                                value=term.value,
                            )
                            batch_created += 1

            # Phase F: Batch insert audit logs for this batch
            audit_entries = [
                TermAuditLog(
                    namespace=namespace,
                    term_id=terms_to_insert[pos].term_id,
                    terminology_id=terminology_id,
                    action="created",
                    changed_by=actor,
                    changed_at=now,
                    new_values={
                        "value": terms_to_insert[pos].value,
                        "aliases": terms_to_insert[pos].aliases,
                        "label": terms_to_insert[pos].label,
                    },
                )
                for pos, idx in enumerate(insert_indices)
                if results[idx] is not None and results[idx].status == "created"
            ]
            if audit_entries:
                await TermAuditLog.insert_many(audit_entries)

            # Phase F2: Publish NATS events for created terms
            created_term_dicts = [
                TerminologyService._term_to_event_dict(terms_to_insert[pos])
                for pos, idx in enumerate(insert_indices)
                if results[idx] is not None and results[idx].status == "created"
            ]
            if created_term_dicts:
                await publish_term_events_bulk(
                    NatsEventType.TERM_CREATED,
                    created_term_dicts,
                    changed_by=actor,
                )

            # Phase F3: Register auto-synonyms for created terms
            synonym_items = [
                {
                    "target_id": terms_to_insert[pos].term_id,
                    "namespace": namespace,
                    "entity_type": "terms",
                    "composite_key": {
                        "ns": namespace,
                        "type": "term",
                        "terminology": terminology.value,
                        "value": terms_to_insert[pos].value,
                    },
                    "created_by": actor,
                }
                for pos, idx in enumerate(insert_indices)
                if results[idx] is not None and results[idx].status == "created"
            ]
            if synonym_items:
                await client.register_auto_synonyms_bulk(synonym_items)

            total_created += batch_created
            logger.info(
                f"Batch {batch_num}/{num_batches} complete: "
                f"{batch_created} created, {total_created} total so far"
            )

            # Pause between batches to allow memory cleanup and prevent resource exhaustion
            # This is especially important on memory-constrained environments
            if batch_end < len(terms):
                await asyncio.sleep(0.1)  # 100ms pause between MongoDB batches

        # Phase G: Update terminology term count (1 save at the end)
        if total_created > 0:
            terminology.term_count += total_created
            terminology.updated_at = now
            await terminology.save()

        # Invalidate relation type cache if this is the system terminology
        if total_created > 0 and terminology.value == "_ONTOLOGY_RELATIONSHIP_TYPES":
            from .ontology_service import OntologyService
            OntologyService.invalidate_relation_type_cache()

        logger.info(
            f"Bulk import complete: {total_created} terms created out of {total_terms} submitted"
        )
        return results

    @staticmethod
    async def get_term(
        term_id: str | None = None,
    ) -> TermResponse | None:
        """
        Get a term by ID.

        Args:
            term_id: Term ID

        Returns:
            Term if found, None otherwise
        """
        if not term_id:
            return None

        term = await Term.find_one({"term_id": term_id})
        if term:
            return TerminologyService._to_term_response(term)
        return None

    @staticmethod
    async def list_terms(
        terminology_id: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
        search: str | None = None,
        ns_filter: dict | None = None,
    ) -> tuple[list[TermResponse], int]:
        """
        List terms in a terminology with pagination.

        Args:
            terminology_id: Terminology to list terms from
            status: Filter by status
            page: Page number (1-based)
            page_size: Number of items per page
            search: Search string for value or aliases
            ns_filter: Namespace filter dict from resolve_namespace_filter()

        Returns:
            Tuple of (list of terms, total count)
        """
        query: dict = {"terminology_id": terminology_id}
        if ns_filter:
            query.update(ns_filter)
        if status:
            query["status"] = status

        # Add search filter if provided
        if search:
            query["$or"] = [
                {"term_id": {"$regex": search, "$options": "i"}},
                {"value": {"$regex": search, "$options": "i"}},
                {"label": {"$regex": search, "$options": "i"}},
                {"aliases": {"$regex": search, "$options": "i"}}
            ]

        # Get total count
        total = await Term.find(query).count()

        # Get paginated results
        skip = (page - 1) * page_size
        terms = await Term.find(query) \
            .sort([("sort_order", 1), ("value", 1)]) \
            .skip(skip) \
            .limit(page_size) \
            .to_list()

        return [TerminologyService._to_term_response(t) for t in terms], total

    @staticmethod
    async def update_term(
        term_id: str,
        request: UpdateTermRequest
    ) -> TermResponse | None:
        """
        Update a term.

        Creates an audit log entry for all changes.
        """
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return None

        # Track changes for audit log
        changed_fields = []
        previous_values = {}
        new_values = {}

        # Check value uniqueness if value is changing
        if request.value is not None and request.value != term.value:
            existing = await Term.find_one({
                "namespace": term.namespace,
                "terminology_id": term.terminology_id,
                "value": request.value
            })
            if existing:
                raise ValueError(f"Term with value '{request.value}' already exists")

        # Apply updates and track changes
        if request.value is not None and request.value != term.value:
            changed_fields.append("value")
            previous_values["value"] = term.value
            new_values["value"] = request.value
            term.value = request.value

        if request.aliases is not None and request.aliases != term.aliases:
            changed_fields.append("aliases")
            previous_values["aliases"] = term.aliases
            new_values["aliases"] = request.aliases
            term.aliases = request.aliases

        if request.label is not None and request.label != term.label:
            changed_fields.append("label")
            previous_values["label"] = term.label
            new_values["label"] = request.label
            term.label = request.label

        if request.description is not None and request.description != term.description:
            changed_fields.append("description")
            previous_values["description"] = term.description
            new_values["description"] = request.description
            term.description = request.description

        if request.sort_order is not None and request.sort_order != term.sort_order:
            changed_fields.append("sort_order")
            previous_values["sort_order"] = term.sort_order
            new_values["sort_order"] = request.sort_order
            term.sort_order = request.sort_order

        if request.parent_term_id is not None and request.parent_term_id != term.parent_term_id:
            changed_fields.append("parent_term_id")
            previous_values["parent_term_id"] = term.parent_term_id
            new_values["parent_term_id"] = request.parent_term_id
            term.parent_term_id = request.parent_term_id

        if request.translations is not None:
            changed_fields.append("translations")
            previous_values["translations"] = [t.model_dump() for t in term.translations]
            new_values["translations"] = [t.model_dump() for t in request.translations]
            term.translations = request.translations

        if request.metadata is not None:
            changed_fields.append("metadata")
            previous_values["metadata"] = term.metadata.copy()
            term.metadata.update(request.metadata)
            new_values["metadata"] = term.metadata

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        term.updated_at = datetime.now(UTC)
        term.updated_by = actor
        await term.save()

        # Create audit log entry if there were changes
        if changed_fields:
            await TerminologyService._create_audit_log(
                term_id=term_id,
                terminology_id=term.terminology_id,
                action="updated",
                changed_by=actor,
                changed_fields=changed_fields,
                previous_values=previous_values,
                new_values=new_values,
                namespace=term.namespace
            )

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_UPDATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=actor,
        )

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def deprecate_term(
        term_id: str,
        request: DeprecateTermRequest
    ) -> TermResponse | None:
        """
        Deprecate a term (mark as deprecated but keep for historical data).
        """
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return None

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        term.status = "deprecated"
        term.deprecated_reason = request.reason
        term.replaced_by_term_id = request.replaced_by_term_id
        term.updated_at = datetime.now(UTC)
        term.updated_by = actor
        await term.save()

        # Update terminology term count
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_DEPRECATED,
            TerminologyService._term_to_event_dict(term),
            changed_by=actor,
        )

        return TerminologyService._to_term_response(term)

    @staticmethod
    async def delete_term(
        term_id: str,
        updated_by: str | None = None,  # Deprecated: uses authenticated identity
        hard_delete: bool = False,
    ) -> bool:
        """Delete a term. Hard-deletes if terminology is mutable OR if hard_delete=True
        and namespace deletion_mode is 'full'. Soft-deletes otherwise."""
        term = await Term.find_one({"term_id": term_id})
        if not term:
            return False

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Check if parent terminology is mutable
        terminology = await Terminology.find_one({"terminology_id": term.terminology_id})
        should_hard_delete = bool(terminology and terminology.mutable)

        if hard_delete and not should_hard_delete:
            # Check namespace deletion_mode
            client = get_registry_client()
            deletion_mode = await client.get_namespace_deletion_mode(term.namespace)
            if deletion_mode != "full":
                raise ValueError(
                    f"Hard-delete requires namespace deletion_mode='full' (currently '{deletion_mode}')"
                )
            should_hard_delete = True

        if should_hard_delete:
            # HARD DELETE: remove term and cascade relations

            # 1. Find and delete relations involving this term
            relations = await TermRelation.find({
                "$or": [
                    {"source_term_id": term_id},
                    {"target_term_id": term_id}
                ]
            }).to_list()

            if relations:
                # Publish relation.deleted events before removing
                for rel in relations:
                    await publish_term_relation_event(
                        NatsEventType.TERM_RELATION_DELETED,
                        {
                            "namespace": rel.namespace,
                            "source_term_id": rel.source_term_id,
                            "target_term_id": rel.target_term_id,
                            "relation_type": rel.relation_type,
                            "hard_delete": True,
                        },
                        changed_by=actor,
                    )

                # Delete relations from MongoDB
                await TermRelation.find({
                    "$or": [
                        {"source_term_id": term_id},
                        {"target_term_id": term_id}
                    ]
                }).delete()

            # 2. Capture event dict before deleting the document
            event_dict = TerminologyService._term_to_event_dict(term)
            event_dict["hard_delete"] = True

            # 3. Hard-delete the term document
            await term.delete()

            # 4. Hard-delete Registry entry
            client = get_registry_client()
            try:
                await client.hard_delete_entry(term_id, updated_by=actor)
            except Exception as e:
                logger.warning(f"Failed to hard-delete Registry entry for term {term_id}: {e}")
        else:
            # SOFT DELETE: set status to inactive (existing behavior)
            term.status = "inactive"
            term.updated_at = datetime.now(UTC)
            term.updated_by = actor
            await term.save()
            event_dict = TerminologyService._term_to_event_dict(term)

        # Update terminology term count
        if terminology:
            terminology.term_count = await Term.find({
                "terminology_id": term.terminology_id,
                "status": "active"
            }).count()
            await terminology.save()

        # Publish NATS event
        await publish_term_event(
            NatsEventType.TERM_DELETED,
            event_dict,
            changed_by=actor,
        )

        return True

    # =========================================================================
    # VALIDATION
    # =========================================================================

    @staticmethod
    async def validate_value(
        terminology_id: str | None = None,
        terminology_value: str | None = None,
        value: str = ""
    ) -> tuple[bool, Term | None, str | None, Term | None]:
        """
        Validate a value against a terminology.

        Matches against value OR aliases.

        Args:
            terminology_id: Terminology ID
            terminology_value: Terminology value (alternative to ID)
            value: Value to validate

        Returns:
            Tuple of (is_valid, matched_term, matched_via, suggestion)
            matched_via is 'value' or 'alias' if matched
        """
        # Find terminology
        if terminology_id:
            terminology = await Terminology.find_one({"terminology_id": terminology_id})
        elif terminology_value:
            terminology = await Terminology.find_one({"value": terminology_value})
        else:
            return (False, None, None, None)

        if not terminology:
            return (False, None, None, None)

        # Prepare value for comparison
        compare_value = value if terminology.case_sensitive else value.lower()

        # Get all active terms in this terminology
        terms = await Term.find({
            "terminology_id": terminology.terminology_id,
            "status": "active"
        }).to_list()

        # Try to find match by value or alias
        for term in terms:
            if terminology.case_sensitive:
                # Check value
                if term.value == value:
                    return (True, term, "value", None)
                # Check aliases
                if value in term.aliases:
                    return (True, term, "alias", None)
            else:
                # Case-insensitive matching
                if term.value.lower() == compare_value:
                    return (True, term, "value", None)
                if any(alias.lower() == compare_value for alias in term.aliases):
                    return (True, term, "alias", None)

        # No exact match - try to find suggestion
        # Simple approach: find terms that start with the value
        suggestion = None
        if len(value) >= 2:
            for t in terms:
                t_value = t.value if terminology.case_sensitive else t.value.lower()
                if t_value.startswith(compare_value):
                    suggestion = t
                    break

        return (False, None, None, suggestion)

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _to_terminology_response(t: Terminology) -> TerminologyResponse:
        """Convert Terminology document to response model."""
        return TerminologyResponse(
            terminology_id=t.terminology_id,
            namespace=t.namespace,
            value=t.value,
            label=t.label,
            description=t.description,
            case_sensitive=t.case_sensitive,
            allow_multiple=t.allow_multiple,
            extensible=t.extensible,
            mutable=t.mutable,
            metadata=t.metadata,
            status=t.status,
            term_count=t.term_count,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    def _to_term_response(t: Term) -> TermResponse:
        """Convert Term document to response model."""
        return TermResponse(
            term_id=t.term_id,
            namespace=t.namespace,
            terminology_id=t.terminology_id,
            terminology_value=t.terminology_value,
            value=t.value,
            aliases=t.aliases,
            label=t.label or t.value,
            description=t.description,
            sort_order=t.sort_order,
            parent_term_id=t.parent_term_id,
            translations=t.translations,
            metadata=t.metadata,
            status=t.status,
            deprecated_reason=t.deprecated_reason,
            replaced_by_term_id=t.replaced_by_term_id,
            created_at=t.created_at,
            created_by=t.created_by,
            updated_at=t.updated_at,
            updated_by=t.updated_by,
        )

    @staticmethod
    def _terminology_to_event_dict(t: Terminology) -> dict:
        """Convert Terminology document to a dict for NATS event payload."""
        return {
            "terminology_id": t.terminology_id,
            "namespace": t.namespace,
            "value": t.value,
            "label": t.label,
            "description": t.description,
            "case_sensitive": t.case_sensitive,
            "allow_multiple": t.allow_multiple,
            "extensible": t.extensible,
            "mutable": t.mutable,
            "status": t.status,
            "term_count": t.term_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "created_by": t.created_by,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "updated_by": t.updated_by,
        }

    @staticmethod
    def _term_to_event_dict(t: Term) -> dict:
        """Convert Term document to a dict for NATS event payload."""
        return {
            "term_id": t.term_id,
            "namespace": t.namespace,
            "terminology_id": t.terminology_id,
            "terminology_value": t.terminology_value,
            "value": t.value,
            "aliases": t.aliases,
            "label": t.label or t.value,
            "description": t.description,
            "sort_order": t.sort_order,
            "parent_term_id": t.parent_term_id,
            "status": t.status,
            "deprecated_reason": t.deprecated_reason,
            "replaced_by_term_id": t.replaced_by_term_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "created_by": t.created_by,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "updated_by": t.updated_by,
        }

    @staticmethod
    async def _create_audit_log(
        term_id: str,
        terminology_id: str,
        action: str,
        namespace: str,
        changed_by: str | None = None,
        changed_fields: list[str] | None = None,
        previous_values: dict | None = None,
        new_values: dict | None = None,
        comment: str | None = None,
    ):
        """Create an audit log entry for a term change."""
        audit_entry = TermAuditLog(
            namespace=namespace,
            term_id=term_id,
            terminology_id=terminology_id,
            action=action,
            changed_by=changed_by,
            changed_fields=changed_fields or [],
            previous_values=previous_values or {},
            new_values=new_values or {},
            comment=comment
        )
        await audit_entry.insert()

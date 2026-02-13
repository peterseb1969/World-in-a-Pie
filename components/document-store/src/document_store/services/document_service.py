"""Document service for CRUD operations and upsert logic."""

import time
from datetime import datetime, timezone
from typing import Any, Optional
import math

from ..models.document import Document, DocumentStatus, DocumentMetadata
from ..models.api_models import (
    DocumentCreateRequest,
    DocumentResponse,
    DocumentCreateResponse,
    DocumentListResponse,
    DocumentVersionSummary,
    DocumentVersionResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    BulkCreateRequest,
    BulkCreateResponse,
    BulkCreateResult,
    ValidationResponse,
    ValidationError,
)
from .registry_client import get_registry_client, RegistryError
from .validation_service import ValidationService
from .nats_client import publish_document_event, EventType, is_nats_enabled
from .file_storage_client import is_file_storage_enabled
from .reference_validator import get_reference_validator, ReferenceValidationError

# Import identity helper from wip-auth
# This returns the authenticated identity, not the client-provided value
from ..api.auth import get_identity_string


class DocumentService:
    """
    Service for document CRUD operations and upsert logic.

    Implements the identity-based upsert pattern:
    - If no active document with same identity_hash exists: create new document
    - If active document exists: deactivate old, create new version

    Includes timing instrumentation for performance analysis.
    """

    # Class-level timing statistics for document creation
    _creation_timing: dict[str, list[float]] = {}
    _creation_count: int = 0

    @classmethod
    def get_creation_timing_stats(cls) -> dict[str, Any]:
        """Get aggregated timing statistics for document creation."""
        if cls._creation_count == 0:
            return {"creation_count": 0, "stages": {}}

        stats = {
            "creation_count": cls._creation_count,
            "stages": {}
        }

        for stage, times in cls._creation_timing.items():
            if times:
                sorted_times = sorted(times)
                n = len(sorted_times)
                stats["stages"][stage] = {
                    "count": n,
                    "avg_ms": sum(times) / n,
                    "min_ms": sorted_times[0],
                    "max_ms": sorted_times[-1],
                    "p50_ms": sorted_times[n // 2],
                    "p95_ms": sorted_times[int(n * 0.95)] if n >= 20 else sorted_times[-1],
                    "p99_ms": sorted_times[int(n * 0.99)] if n >= 100 else sorted_times[-1],
                }

        return stats

    @classmethod
    def reset_creation_timing_stats(cls):
        """Reset creation timing statistics."""
        cls._creation_timing = {}
        cls._creation_count = 0

    @classmethod
    def _record_creation_timing(cls, timing: dict[str, float]):
        """Record timing from a document creation."""
        cls._creation_count += 1
        for stage, ms in timing.items():
            if stage not in cls._creation_timing:
                cls._creation_timing[stage] = []
            cls._creation_timing[stage].append(ms)

    def __init__(self):
        self.validation_service = ValidationService()

    async def create_document(
        self,
        request: DocumentCreateRequest,
        pool_id: str = "wip-documents",
        template_pool_id: str = "wip-templates"
    ) -> tuple[DocumentCreateResponse, Optional[str]]:
        """
        Create or update a document.

        Implements upsert logic based on identity hash:
        - Validates document against template
        - Computes identity hash
        - Creates new document or new version

        Args:
            request: Document creation request
            pool_id: Pool ID for the document (default: wip-documents)
            template_pool_id: Pool ID of the template (default: wip-templates)

        Returns:
            Tuple of (response, error_message)
        """
        timing = {}
        total_start = time.perf_counter()

        # Validate document
        start = time.perf_counter()
        validation_result = await self.validation_service.validate(
            request.template_id,
            request.data
        )
        timing["1_validation"] = (time.perf_counter() - start) * 1000

        if not validation_result.valid:
            # Return validation errors
            return None, self._format_validation_errors(validation_result.errors)

        # Validate cross-namespace references (isolation mode check)
        try:
            validator = get_reference_validator()
            await validator.validate_document_references(
                document_pool_id=pool_id,
                template_pool_id=template_pool_id,
                term_references=validation_result.term_references,
                file_references=validation_result.file_references,
            )
        except ReferenceValidationError as e:
            return None, f"Cross-namespace reference violation: {e.violations}"

        # Check for existing active document with same identity within pool
        start = time.perf_counter()
        identity_hash = validation_result.identity_hash
        existing = await self._find_active_by_identity(identity_hash, pool_id=pool_id)
        timing["2_find_existing"] = (time.perf_counter() - start) * 1000

        if existing:
            # Upsert: deactivate old, create new version
            start = time.perf_counter()
            result = await self._create_new_version(
                request, existing, validation_result, pool_id=pool_id, template_pool_id=template_pool_id
            )
            timing["3_create_version"] = (time.perf_counter() - start) * 1000
        else:
            # Create new document
            start = time.perf_counter()
            result = await self._create_new_document(
                request, validation_result, pool_id=pool_id, template_pool_id=template_pool_id,
                synonyms=request.synonyms
            )
            timing["3_create_new"] = (time.perf_counter() - start) * 1000

        timing["total"] = (time.perf_counter() - total_start) * 1000
        self._record_creation_timing(timing)

        return result

    async def _create_new_document(
        self,
        request: DocumentCreateRequest,
        validation_result: Any,
        pool_id: str = "wip-documents",
        template_pool_id: str = "wip-templates",
        synonyms: Optional[list[dict]] = None
    ) -> tuple[DocumentCreateResponse, Optional[str]]:
        """Create a brand new document."""
        try:
            # Get authenticated identity (not client-provided)
            actor = get_identity_string()

            # Generate document ID from Registry
            start = time.perf_counter()
            registry = get_registry_client()
            document_id = await registry.generate_document_id(
                identity_hash=validation_result.identity_hash,
                template_id=request.template_id,
                created_by=actor,
                pool_id=pool_id
            )
            registry_ms = (time.perf_counter() - start) * 1000

            # Register synonyms if provided
            if synonyms:
                try:
                    await registry.add_synonyms(
                        entry_id=document_id,
                        pool_id=pool_id,
                        synonyms=synonyms
                    )
                except RegistryError as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Failed to register synonyms for {document_id}: {e}"
                    )

            # Create document
            now = datetime.now(timezone.utc)
            metadata = DocumentMetadata(
                warnings=validation_result.warnings,
                custom=request.metadata or {}
            )

            document = Document(
                pool_id=pool_id,
                document_id=document_id,
                template_id=request.template_id,
                template_pool_id=template_pool_id,
                template_version=validation_result.template_version,
                template_code=validation_result.template_code,
                identity_hash=validation_result.identity_hash,
                version=1,
                data=request.data,
                term_references=validation_result.term_references,
                references=validation_result.references,
                file_references=validation_result.file_references,
                status=DocumentStatus.ACTIVE,
                created_at=now,
                created_by=actor,
                updated_at=now,
                updated_by=actor,
                metadata=metadata
            )

            start = time.perf_counter()
            await document.insert()
            mongo_ms = (time.perf_counter() - start) * 1000

            # Record detailed timing
            self._record_creation_timing({
                "3a_registry_id": registry_ms,
                "3b_mongo_insert": mongo_ms,
            })

            # Publish document created event
            await publish_document_event(
                EventType.DOCUMENT_CREATED,
                self._document_to_event_payload(document),
                changed_by=actor
            )

            # Update file reference counts (increment for new document)
            await self._update_file_reference_counts(
                validation_result.file_references, delta=1
            )

            return DocumentCreateResponse(
                document_id=document_id,
                template_id=request.template_id,
                template_code=validation_result.template_code,
                identity_hash=validation_result.identity_hash,
                version=1,
                is_new=True,
                previous_version=None,
                warnings=validation_result.warnings
            ), None

        except RegistryError as e:
            return None, f"Failed to generate document ID: {str(e)}"

    def _data_has_changed(
        self,
        existing: Document,
        new_data: dict[str, Any],
        new_term_references: list[dict[str, Any]],
        new_references: list[dict[str, Any]],
        new_file_references: list[dict[str, Any]] = None
    ) -> bool:
        """
        Check if document data has changed.

        Compares the data, term_references, references, and file_references
        to determine if a new version should be created.
        """
        import json

        # Compare data (use JSON serialization for consistent comparison)
        existing_data_json = json.dumps(existing.data, sort_keys=True, default=str)
        new_data_json = json.dumps(new_data, sort_keys=True, default=str)

        if existing_data_json != new_data_json:
            return True

        # Compare term_references
        existing_term_refs_json = json.dumps(existing.term_references, sort_keys=True, default=str)
        new_term_refs_json = json.dumps(new_term_references, sort_keys=True, default=str)

        if existing_term_refs_json != new_term_refs_json:
            return True

        # Compare references
        existing_refs_json = json.dumps(existing.references, sort_keys=True, default=str)
        new_refs_json = json.dumps(new_references, sort_keys=True, default=str)

        if existing_refs_json != new_refs_json:
            return True

        # Compare file_references
        if new_file_references is not None:
            existing_file_refs_json = json.dumps(existing.file_references, sort_keys=True, default=str)
            new_file_refs_json = json.dumps(new_file_references, sort_keys=True, default=str)

            if existing_file_refs_json != new_file_refs_json:
                return True

        return False

    async def _create_new_version(
        self,
        request: DocumentCreateRequest,
        existing: Document,
        validation_result: Any,
        pool_id: str = "wip-documents",
        template_pool_id: str = "wip-templates"
    ) -> tuple[DocumentCreateResponse, Optional[str]]:
        """Create a new version of an existing document."""
        # Check if data has actually changed
        if not self._data_has_changed(
            existing,
            request.data,
            validation_result.term_references,
            validation_result.references,
            validation_result.file_references
        ):
            # No change - return existing document info without creating new version
            return DocumentCreateResponse(
                document_id=existing.document_id,
                template_id=existing.template_id,
                template_code=existing.template_code,
                identity_hash=existing.identity_hash,
                version=existing.version,
                is_new=False,
                previous_version=None,  # No previous version because nothing changed
                warnings=validation_result.warnings
            ), None

        try:
            # Get authenticated identity (not client-provided)
            actor = get_identity_string()

            # Generate new document ID from Registry
            registry = get_registry_client()
            document_id = await registry.generate_document_id(
                identity_hash=validation_result.identity_hash,
                template_id=request.template_id,
                created_by=actor,
                pool_id=pool_id
            )

            # Deactivate old version
            existing.status = DocumentStatus.INACTIVE
            existing.updated_at = datetime.now(timezone.utc)
            existing.updated_by = actor
            await existing.save()

            # Create new version
            now = datetime.now(timezone.utc)
            new_version = existing.version + 1
            metadata = DocumentMetadata(
                warnings=validation_result.warnings,
                custom=request.metadata or {}
            )

            document = Document(
                pool_id=pool_id,
                document_id=document_id,
                template_id=request.template_id,
                template_pool_id=template_pool_id,
                template_version=validation_result.template_version,
                template_code=validation_result.template_code,
                identity_hash=validation_result.identity_hash,
                version=new_version,
                data=request.data,
                term_references=validation_result.term_references,
                references=validation_result.references,
                file_references=validation_result.file_references,
                status=DocumentStatus.ACTIVE,
                created_at=now,
                created_by=actor,
                updated_at=now,
                updated_by=actor,
                metadata=metadata
            )

            await document.insert()

            # Publish document updated event
            await publish_document_event(
                EventType.DOCUMENT_UPDATED,
                self._document_to_event_payload(document),
                changed_by=actor
            )

            # Update file reference counts
            # Decrement for old version (now inactive)
            await self._update_file_reference_counts(
                existing.file_references, delta=-1
            )
            # Increment for new version
            await self._update_file_reference_counts(
                validation_result.file_references, delta=1
            )

            return DocumentCreateResponse(
                document_id=document_id,
                template_id=request.template_id,
                template_code=validation_result.template_code,
                identity_hash=validation_result.identity_hash,
                version=new_version,
                is_new=False,
                previous_version=existing.version,
                warnings=validation_result.warnings
            ), None

        except RegistryError as e:
            return None, f"Failed to generate document ID: {str(e)}"

    async def _find_active_by_identity(
        self,
        identity_hash: str,
        pool_id: str = "wip-documents"
    ) -> Optional[Document]:
        """Find the active document with the given identity hash within pool."""
        return await Document.find_one({
            "pool_id": pool_id,
            "identity_hash": identity_hash,
            "status": DocumentStatus.ACTIVE.value
        })

    def _format_validation_errors(
        self,
        errors: list[dict[str, Any]]
    ) -> str:
        """Format validation errors as a string."""
        messages = [e.get("message", "Validation error") for e in errors]
        return "; ".join(messages)

    def _document_to_event_payload(self, document: Document) -> dict[str, Any]:
        """Convert Document to event payload for NATS publishing."""
        return {
            "document_id": document.document_id,
            "template_id": document.template_id,
            "template_version": document.template_version,
            "template_code": document.template_code,
            "identity_hash": document.identity_hash,
            "version": document.version,
            "data": document.data,
            "term_references": document.term_references,
            "references": document.references,
            "file_references": document.file_references,
            "status": document.status.value if hasattr(document.status, 'value') else document.status,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "created_by": document.created_by,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            "updated_by": document.updated_by,
        }

    async def get_document(
        self,
        document_id: str
    ) -> Optional[DocumentResponse]:
        """Get a document by ID."""
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return None
        return await self._to_response(document)

    async def get_document_by_identity(
        self,
        identity_hash: str,
        include_inactive: bool = False
    ) -> Optional[DocumentResponse]:
        """Get a document by identity hash."""
        query = {"identity_hash": identity_hash}
        if not include_inactive:
            query["status"] = DocumentStatus.ACTIVE.value

        document = await Document.find_one(query)
        if not document:
            return None
        return await self._to_response(document)

    async def list_documents(
        self,
        template_id: Optional[str] = None,
        template_code: Optional[str] = None,
        status: Optional[DocumentStatus] = None,
        page: int = 1,
        page_size: int = 20,
        pool_id: str = "wip-documents"
    ) -> DocumentListResponse:
        """List documents with pagination."""
        query = {"pool_id": pool_id}
        if template_id:
            query["template_id"] = template_id
        if template_code:
            query["template_code"] = template_code
        if status:
            query["status"] = status.value

        # Count total
        total = await Document.find(query).count()

        # Fetch page
        skip = (page - 1) * page_size
        documents = await Document.find(query).skip(skip).limit(page_size).to_list()

        # Convert to responses (async)
        items = [await self._to_response(d) for d in documents]

        return DocumentListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if total > 0 else 1
        )

    async def get_document_versions(
        self,
        document_id: str
    ) -> Optional[DocumentVersionResponse]:
        """Get all versions of a document."""
        # Find the document first to get identity_hash
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return None

        # Find all versions with same identity_hash
        versions = await Document.find(
            {"identity_hash": document.identity_hash}
        ).sort([("version", -1)]).to_list()

        return DocumentVersionResponse(
            identity_hash=document.identity_hash,
            current_version=max(v.version for v in versions),
            versions=[
                DocumentVersionSummary(
                    document_id=v.document_id,
                    version=v.version,
                    status=v.status,
                    created_at=v.created_at,
                    created_by=v.created_by
                )
                for v in versions
            ]
        )

    async def get_document_version(
        self,
        document_id: str,
        version: int
    ) -> Optional[DocumentResponse]:
        """Get a specific version of a document."""
        # Find the document first to get identity_hash
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return None

        # Find specific version
        version_doc = await Document.find_one({
            "identity_hash": document.identity_hash,
            "version": version
        })

        if not version_doc:
            return None

        return await self._to_response(version_doc)

    async def get_latest_document(
        self,
        document_id: str
    ) -> Optional[DocumentResponse]:
        """
        Get the latest version of a document.

        Given any document_id (even an old version), returns the latest version.
        This is useful when you have a reference to an old version but want
        the current data.

        Args:
            document_id: Any document ID from the version chain

        Returns:
            The latest version of the document, or None if not found
        """
        # Find the document first to get identity_hash
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return None

        # Find the latest version with same identity_hash
        latest = await Document.find(
            {"identity_hash": document.identity_hash}
        ).sort([("version", -1)]).limit(1).to_list()

        if not latest:
            return None

        return await self._to_response(latest[0])

    async def delete_document(
        self,
        document_id: str,
        deleted_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> bool:
        """Soft-delete a document (set status to inactive)."""
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return False

        if document.status == DocumentStatus.INACTIVE:
            return True  # Already inactive

        # Use authenticated identity (not client-provided)
        actor = get_identity_string()

        document.status = DocumentStatus.INACTIVE
        document.updated_at = datetime.now(timezone.utc)
        document.updated_by = actor
        await document.save()

        # Publish document deleted event
        await publish_document_event(
            EventType.DOCUMENT_DELETED,
            self._document_to_event_payload(document),
            changed_by=actor
        )

        # Update file reference counts (decrement for deleted document)
        await self._update_file_reference_counts(
            document.file_references, delta=-1
        )

        return True

    async def archive_document(
        self,
        document_id: str,
        archived_by: Optional[str] = None  # Deprecated: uses authenticated identity
    ) -> bool:
        """Archive a document."""
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return False

        # Use authenticated identity (not client-provided)
        actor = get_identity_string()

        document.status = DocumentStatus.ARCHIVED
        document.updated_at = datetime.now(timezone.utc)
        document.updated_by = actor
        await document.save()

        # Publish document archived event
        await publish_document_event(
            EventType.DOCUMENT_ARCHIVED,
            self._document_to_event_payload(document),
            changed_by=actor
        )

        # Update file reference counts (decrement for archived document)
        await self._update_file_reference_counts(
            document.file_references, delta=-1
        )

        return True

    async def query_documents(
        self,
        request: DocumentQueryRequest
    ) -> DocumentQueryResponse:
        """Query documents with complex filters."""
        query = self._build_query(request)

        # Count total
        total = await Document.find(query).count()

        # Build sort
        sort_direction = 1 if request.sort_order == "asc" else -1
        sort_field = request.sort_by

        # Fetch page
        skip = (request.page - 1) * request.page_size
        documents = await Document.find(query)\
            .sort([(sort_field, sort_direction)])\
            .skip(skip)\
            .limit(request.page_size)\
            .to_list()

        # Convert to responses (async)
        items = [await self._to_response(d) for d in documents]

        return DocumentQueryResponse(
            items=items,
            total=total,
            page=request.page,
            page_size=request.page_size,
            pages=math.ceil(total / request.page_size) if total > 0 else 1,
            query=request
        )

    def _build_query(self, request: DocumentQueryRequest) -> dict[str, Any]:
        """Build MongoDB query from request."""
        query = {}

        if request.template_id:
            query["template_id"] = request.template_id

        if request.status:
            query["status"] = request.status.value

        # Apply filters
        for filter_item in request.filters:
            field = filter_item.field
            operator = filter_item.operator
            value = filter_item.value

            if operator == "eq":
                query[field] = value
            elif operator == "ne":
                query[field] = {"$ne": value}
            elif operator == "gt":
                query[field] = {"$gt": value}
            elif operator == "gte":
                query[field] = {"$gte": value}
            elif operator == "lt":
                query[field] = {"$lt": value}
            elif operator == "lte":
                query[field] = {"$lte": value}
            elif operator == "in":
                query[field] = {"$in": value}
            elif operator == "nin":
                query[field] = {"$nin": value}
            elif operator == "exists":
                query[field] = {"$exists": value}
            elif operator == "regex":
                query[field] = {"$regex": value}

        return query

    async def bulk_create(
        self,
        request: BulkCreateRequest
    ) -> BulkCreateResponse:
        """
        Create multiple documents with optimized bulk operations.

        Optimizations:
        - All validations run first (using cached templates/terminologies)
        - Single bulk Registry call for all document IDs
        - Batch MongoDB operations for existing document checks
        """
        timing = {}
        total_start = time.perf_counter()

        results: list[BulkCreateResult] = []
        created = 0
        updated = 0
        unchanged = 0
        failed = 0

        # Stage 1: Validate all documents
        start = time.perf_counter()
        validation_results = []
        valid_indices = []  # Indices of valid documents

        for i, item in enumerate(request.items):
            try:
                validation_result = await self.validation_service.validate(
                    item.template_id,
                    item.data
                )
                if validation_result.valid:
                    validation_results.append((i, item, validation_result))
                    valid_indices.append(i)
                else:
                    failed += 1
                    results.append(BulkCreateResult(
                        index=i,
                        status="error",
                        error=self._format_validation_errors(validation_result.errors)
                    ))
                    if not request.continue_on_error:
                        # Fill in remaining as skipped
                        for j in range(i + 1, len(request.items)):
                            results.append(BulkCreateResult(
                                index=j, status="skipped", error="Stopped due to previous error"
                            ))
                        timing["1_validation"] = (time.perf_counter() - start) * 1000
                        timing["total"] = (time.perf_counter() - total_start) * 1000
                        self._record_creation_timing(timing)
                        return BulkCreateResponse(
                            total=len(request.items),
                            created=0, updated=0, unchanged=0, failed=failed,
                            results=sorted(results, key=lambda r: r.index)
                        )
            except Exception as e:
                failed += 1
                results.append(BulkCreateResult(
                    index=i, status="error", error=str(e)
                ))
                if not request.continue_on_error:
                    for j in range(i + 1, len(request.items)):
                        results.append(BulkCreateResult(
                            index=j, status="skipped", error="Stopped due to previous error"
                        ))
                    timing["1_validation"] = (time.perf_counter() - start) * 1000
                    timing["total"] = (time.perf_counter() - total_start) * 1000
                    self._record_creation_timing(timing)
                    return BulkCreateResponse(
                        total=len(request.items),
                        created=0, updated=0, unchanged=0, failed=failed,
                        results=sorted(results, key=lambda r: r.index)
                    )

        timing["1_validation"] = (time.perf_counter() - start) * 1000

        if not validation_results:
            timing["total"] = (time.perf_counter() - total_start) * 1000
            self._record_creation_timing(timing)
            return BulkCreateResponse(
                total=len(request.items),
                created=0, updated=0, unchanged=0, failed=failed,
                results=sorted(results, key=lambda r: r.index)
            )

        # Stage 2: Batch check for existing documents
        start = time.perf_counter()
        identity_hashes = [vr[2].identity_hash for vr in validation_results]
        existing_docs = await Document.find({
            "identity_hash": {"$in": identity_hashes},
            "status": DocumentStatus.ACTIVE.value
        }).to_list()
        existing_by_hash = {doc.identity_hash: doc for doc in existing_docs}
        timing["2_find_existing"] = (time.perf_counter() - start) * 1000

        # Get authenticated identity (not client-provided)
        actor = get_identity_string()

        # Stage 3: Bulk request IDs from Registry
        start = time.perf_counter()
        registry = get_registry_client()
        registry_items = [
            {
                "identity_hash": vr[2].identity_hash,
                "template_id": vr[1].template_id,
                "version": (existing_by_hash[vr[2].identity_hash].version + 1
                           if vr[2].identity_hash in existing_by_hash else 1)
            }
            for vr in validation_results
        ]

        try:
            registry_results = await registry.generate_document_ids_bulk(
                registry_items,
                created_by=actor
            )
        except RegistryError as e:
            # All valid documents fail due to Registry error
            for i, item, _ in validation_results:
                failed += 1
                results.append(BulkCreateResult(
                    index=i, status="error", error=f"Registry error: {str(e)}"
                ))
            timing["3_registry_bulk"] = (time.perf_counter() - start) * 1000
            timing["total"] = (time.perf_counter() - total_start) * 1000
            self._record_creation_timing(timing)
            return BulkCreateResponse(
                total=len(request.items),
                created=0, updated=0, unchanged=0, failed=failed,
                results=sorted(results, key=lambda r: r.index)
            )

        timing["3_registry_bulk"] = (time.perf_counter() - start) * 1000

        # Stage 4: Create all documents
        start = time.perf_counter()
        now = datetime.now(timezone.utc)

        for idx, ((i, item, validation_result), registry_result) in enumerate(
            zip(validation_results, registry_results)
        ):
            try:
                if registry_result.get("status") == "error":
                    failed += 1
                    results.append(BulkCreateResult(
                        index=i, status="error",
                        error=registry_result.get("error", "Failed to generate ID")
                    ))
                    continue

                document_id = registry_result.get("registry_id")
                identity_hash = validation_result.identity_hash
                existing = existing_by_hash.get(identity_hash)

                if existing:
                    # Check if data has actually changed
                    if not self._data_has_changed(
                        existing,
                        item.data,
                        validation_result.term_references,
                        validation_result.references,
                        validation_result.file_references
                    ):
                        # No change - return existing document info without creating new version
                        unchanged += 1
                        results.append(BulkCreateResult(
                            index=i,
                            status="unchanged",
                            document_id=existing.document_id,
                            identity_hash=identity_hash,
                            version=existing.version,
                            is_new=False,
                            warnings=validation_result.warnings
                        ))
                        continue

                    # Deactivate old version
                    existing.status = DocumentStatus.INACTIVE
                    existing.updated_at = now
                    existing.updated_by = actor
                    await existing.save()
                    new_version = existing.version + 1
                    is_new = False
                else:
                    new_version = 1
                    is_new = True

                # Create document
                metadata = DocumentMetadata(
                    warnings=validation_result.warnings,
                    custom=item.metadata or {}
                )
                document = Document(
                    document_id=document_id,
                    template_id=item.template_id,
                    template_version=validation_result.template_version,
                    template_code=validation_result.template_code,
                    identity_hash=identity_hash,
                    version=new_version,
                    data=item.data,
                    term_references=validation_result.term_references,
                    references=validation_result.references,
                    file_references=validation_result.file_references,
                    status=DocumentStatus.ACTIVE,
                    created_at=now,
                    created_by=actor,
                    updated_at=now,
                    updated_by=actor,
                    metadata=metadata
                )
                await document.insert()

                # Publish event
                event_type = EventType.DOCUMENT_CREATED if is_new else EventType.DOCUMENT_UPDATED
                await publish_document_event(
                    event_type,
                    self._document_to_event_payload(document),
                    changed_by=actor
                )

                # Update file reference counts
                if not is_new and existing:
                    # Decrement for old version
                    await self._update_file_reference_counts(
                        existing.file_references, delta=-1
                    )
                # Increment for new version
                await self._update_file_reference_counts(
                    validation_result.file_references, delta=1
                )

                if is_new:
                    created += 1
                    status = "created"
                else:
                    updated += 1
                    status = "updated"

                results.append(BulkCreateResult(
                    index=i,
                    status=status,
                    document_id=document_id,
                    identity_hash=identity_hash,
                    version=new_version,
                    is_new=is_new,
                    warnings=validation_result.warnings
                ))

            except Exception as e:
                failed += 1
                results.append(BulkCreateResult(
                    index=i, status="error", error=str(e)
                ))
                if not request.continue_on_error:
                    # Mark remaining as skipped
                    for remaining_idx in range(idx + 1, len(validation_results)):
                        remaining_i = validation_results[remaining_idx][0]
                        results.append(BulkCreateResult(
                            index=remaining_i, status="skipped",
                            error="Stopped due to previous error"
                        ))
                    break

        timing["4_create_documents"] = (time.perf_counter() - start) * 1000
        timing["total"] = (time.perf_counter() - total_start) * 1000
        self._record_creation_timing(timing)

        return BulkCreateResponse(
            total=len(request.items),
            created=created,
            updated=updated,
            unchanged=unchanged,
            failed=failed,
            results=sorted(results, key=lambda r: r.index)
        )

    async def validate_document(
        self,
        template_id: str,
        data: dict[str, Any]
    ) -> ValidationResponse:
        """Validate document without saving."""
        result = await self.validation_service.validate(template_id, data)

        return ValidationResponse(
            valid=result.valid,
            errors=[
                ValidationError(
                    field=e.get("field"),
                    code=e.get("code", "error"),
                    message=e.get("message", "Validation error"),
                    details=e.get("details")
                )
                for e in result.errors
            ],
            warnings=result.warnings,
            identity_hash=result.identity_hash,
            template_version=result.template_version,
            term_references=result.term_references,
            references=result.references,
            file_references=result.file_references
        )

    async def _to_response(self, document: Document) -> DocumentResponse:
        """Convert Document to DocumentResponse with latest version info."""
        # Find the latest version for this identity
        latest = await Document.find(
            {"identity_hash": document.identity_hash}
        ).sort([("version", -1)]).limit(1).to_list()

        if latest:
            latest_doc = latest[0]
            is_latest = document.version == latest_doc.version
            latest_version = latest_doc.version
            latest_document_id = latest_doc.document_id
        else:
            # This shouldn't happen, but handle gracefully
            is_latest = True
            latest_version = document.version
            latest_document_id = document.document_id

        return DocumentResponse(
            document_id=document.document_id,
            template_id=document.template_id,
            template_version=document.template_version,
            template_code=document.template_code,
            identity_hash=document.identity_hash,
            version=document.version,
            data=document.data,
            term_references=document.term_references,
            references=document.references,
            file_references=document.file_references,
            status=document.status,
            created_at=document.created_at,
            created_by=document.created_by,
            updated_at=document.updated_at,
            updated_by=document.updated_by,
            metadata=document.metadata,
            is_latest_version=is_latest,
            latest_version=latest_version,
            latest_document_id=latest_document_id
        )

    async def _update_file_reference_counts(
        self,
        file_references: list[dict[str, Any]],
        delta: int
    ):
        """
        Update reference counts for files.

        Args:
            file_references: List of file reference dicts with file_id
            delta: Change in reference count (+1 for add, -1 for remove)
        """
        if not file_references or not is_file_storage_enabled():
            return

        from .file_service import get_file_service

        file_service = get_file_service()
        for ref in file_references:
            file_id = ref.get("file_id")
            if file_id:
                try:
                    await file_service.update_reference_count(file_id, delta)
                except Exception as e:
                    # Log but don't fail - reference count is best-effort
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Failed to update reference count for file {file_id}: {e}"
                    )


# Singleton instance
_service: Optional[DocumentService] = None


def get_document_service() -> DocumentService:
    """Get the singleton document service instance."""
    global _service
    if _service is None:
        _service = DocumentService()
    return _service

"""Document service for CRUD operations and upsert logic."""

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


class DocumentService:
    """
    Service for document CRUD operations and upsert logic.

    Implements the identity-based upsert pattern:
    - If no active document with same identity_hash exists: create new document
    - If active document exists: deactivate old, create new version
    """

    def __init__(self):
        self.validation_service = ValidationService()

    async def create_document(
        self,
        request: DocumentCreateRequest
    ) -> tuple[DocumentCreateResponse, Optional[str]]:
        """
        Create or update a document.

        Implements upsert logic based on identity hash:
        - Validates document against template
        - Computes identity hash
        - Creates new document or new version

        Args:
            request: Document creation request

        Returns:
            Tuple of (response, error_message)
        """
        # Validate document
        validation_result = await self.validation_service.validate(
            request.template_id,
            request.data
        )

        if not validation_result.valid:
            # Return validation errors
            return None, self._format_validation_errors(validation_result.errors)

        # Check for existing active document with same identity
        identity_hash = validation_result.identity_hash
        existing = await self._find_active_by_identity(identity_hash)

        if existing:
            # Upsert: deactivate old, create new version
            return await self._create_new_version(
                request, existing, validation_result
            )
        else:
            # Create new document
            return await self._create_new_document(
                request, validation_result
            )

    async def _create_new_document(
        self,
        request: DocumentCreateRequest,
        validation_result: Any
    ) -> tuple[DocumentCreateResponse, Optional[str]]:
        """Create a brand new document."""
        try:
            # Generate document ID from Registry
            registry = get_registry_client()
            document_id = await registry.generate_document_id(
                identity_hash=validation_result.identity_hash,
                template_id=request.template_id,
                created_by=request.created_by
            )

            # Create document
            now = datetime.now(timezone.utc)
            metadata = DocumentMetadata(
                warnings=validation_result.warnings,
                custom=request.metadata or {}
            )

            document = Document(
                document_id=document_id,
                template_id=request.template_id,
                template_version=validation_result.template_version,
                identity_hash=validation_result.identity_hash,
                version=1,
                data=request.data,
                status=DocumentStatus.ACTIVE,
                created_at=now,
                created_by=request.created_by,
                updated_at=now,
                updated_by=request.created_by,
                metadata=metadata
            )

            await document.insert()

            return DocumentCreateResponse(
                document_id=document_id,
                template_id=request.template_id,
                identity_hash=validation_result.identity_hash,
                version=1,
                is_new=True,
                previous_version=None,
                warnings=validation_result.warnings
            ), None

        except RegistryError as e:
            return None, f"Failed to generate document ID: {str(e)}"

    async def _create_new_version(
        self,
        request: DocumentCreateRequest,
        existing: Document,
        validation_result: Any
    ) -> tuple[DocumentCreateResponse, Optional[str]]:
        """Create a new version of an existing document."""
        try:
            # Generate new document ID from Registry
            registry = get_registry_client()
            document_id = await registry.generate_document_id(
                identity_hash=validation_result.identity_hash,
                template_id=request.template_id,
                created_by=request.created_by
            )

            # Deactivate old version
            existing.status = DocumentStatus.INACTIVE
            existing.updated_at = datetime.now(timezone.utc)
            existing.updated_by = request.created_by
            await existing.save()

            # Create new version
            now = datetime.now(timezone.utc)
            new_version = existing.version + 1
            metadata = DocumentMetadata(
                warnings=validation_result.warnings,
                custom=request.metadata or {}
            )

            document = Document(
                document_id=document_id,
                template_id=request.template_id,
                template_version=validation_result.template_version,
                identity_hash=validation_result.identity_hash,
                version=new_version,
                data=request.data,
                status=DocumentStatus.ACTIVE,
                created_at=now,
                created_by=request.created_by,
                updated_at=now,
                updated_by=request.created_by,
                metadata=metadata
            )

            await document.insert()

            return DocumentCreateResponse(
                document_id=document_id,
                template_id=request.template_id,
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
        identity_hash: str
    ) -> Optional[Document]:
        """Find the active document with the given identity hash."""
        return await Document.find_one({
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

    async def get_document(
        self,
        document_id: str
    ) -> Optional[DocumentResponse]:
        """Get a document by ID."""
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return None
        return self._to_response(document)

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
        return self._to_response(document)

    async def list_documents(
        self,
        template_id: Optional[str] = None,
        status: Optional[DocumentStatus] = None,
        page: int = 1,
        page_size: int = 20
    ) -> DocumentListResponse:
        """List documents with pagination."""
        query = {}
        if template_id:
            query["template_id"] = template_id
        if status:
            query["status"] = status.value

        # Count total
        total = await Document.find(query).count()

        # Fetch page
        skip = (page - 1) * page_size
        documents = await Document.find(query).skip(skip).limit(page_size).to_list()

        return DocumentListResponse(
            items=[self._to_response(d) for d in documents],
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

        return self._to_response(version_doc)

    async def delete_document(
        self,
        document_id: str,
        deleted_by: Optional[str] = None
    ) -> bool:
        """Soft-delete a document (set status to inactive)."""
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return False

        if document.status == DocumentStatus.INACTIVE:
            return True  # Already inactive

        document.status = DocumentStatus.INACTIVE
        document.updated_at = datetime.now(timezone.utc)
        document.updated_by = deleted_by
        await document.save()

        return True

    async def archive_document(
        self,
        document_id: str,
        archived_by: Optional[str] = None
    ) -> bool:
        """Archive a document."""
        document = await Document.find_one({"document_id": document_id})
        if not document:
            return False

        document.status = DocumentStatus.ARCHIVED
        document.updated_at = datetime.now(timezone.utc)
        document.updated_by = archived_by
        await document.save()

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

        return DocumentQueryResponse(
            items=[self._to_response(d) for d in documents],
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
        """Create multiple documents."""
        results = []
        created = 0
        updated = 0
        failed = 0

        for i, item in enumerate(request.items):
            try:
                response, error = await self.create_document(item)

                if error:
                    failed += 1
                    results.append(BulkCreateResult(
                        index=i,
                        status="error",
                        error=error
                    ))
                    if not request.continue_on_error:
                        break
                else:
                    if response.is_new:
                        created += 1
                        status = "created"
                    else:
                        updated += 1
                        status = "updated"

                    results.append(BulkCreateResult(
                        index=i,
                        status=status,
                        document_id=response.document_id,
                        identity_hash=response.identity_hash,
                        version=response.version,
                        is_new=response.is_new,
                        warnings=response.warnings
                    ))

            except Exception as e:
                failed += 1
                results.append(BulkCreateResult(
                    index=i,
                    status="error",
                    error=str(e)
                ))
                if not request.continue_on_error:
                    break

        return BulkCreateResponse(
            total=len(request.items),
            created=created,
            updated=updated,
            failed=failed,
            results=results
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
            template_version=result.template_version
        )

    def _to_response(self, document: Document) -> DocumentResponse:
        """Convert Document to DocumentResponse."""
        return DocumentResponse(
            document_id=document.document_id,
            template_id=document.template_id,
            template_version=document.template_version,
            identity_hash=document.identity_hash,
            version=document.version,
            data=document.data,
            status=document.status,
            created_at=document.created_at,
            created_by=document.created_by,
            updated_at=document.updated_at,
            updated_by=document.updated_by,
            metadata=document.metadata
        )


# Singleton instance
_service: Optional[DocumentService] = None


def get_document_service() -> DocumentService:
    """Get the singleton document service instance."""
    global _service
    if _service is None:
        _service = DocumentService()
    return _service

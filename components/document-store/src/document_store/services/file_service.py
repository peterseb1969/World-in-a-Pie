"""File service for managing file entities and storage operations."""

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from ..models.file import File, FileStatus, FileMetadata, FileReference
from ..models.api_models import (
    FileResponse,
    FileListResponse,
    FileDownloadResponse,
    FileUploadMetadata,
    UpdateFileMetadataRequest,
    FileBulkDeleteRequest,
    FileBulkDeleteResponse,
    FileBulkResult,
    FileIntegrityIssue,
    FileIntegrityResponse,
)
from .registry_client import get_registry_client, RegistryError
from .file_storage_client import (
    get_file_storage_client,
    FileStorageError,
    is_file_storage_enabled,
)

# Import identity helper from wip-auth
from ..api.auth import get_identity_string


class FileServiceError(Exception):
    """Error in file service operations."""
    pass


class FileService:
    """
    Service for file entity management.

    Handles:
    - File upload (Registry ID generation + MinIO storage + MongoDB metadata)
    - File retrieval and pre-signed download URLs
    - File deletion (soft-delete)
    - Reference count tracking
    - Orphan detection
    - Validation against field constraints
    """

    async def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str,
        metadata: Optional[FileUploadMetadata] = None,
    ) -> FileResponse:
        """
        Upload a file to storage.

        1. Generate file ID from Registry (FILE-XXXXXX)
        2. Compute checksum
        3. Upload to MinIO
        4. Create File document in MongoDB

        Args:
            content: File content as bytes
            filename: Original filename
            content_type: MIME type
            metadata: Optional metadata (description, tags, category, etc.)

        Returns:
            FileResponse with file details

        Raises:
            FileServiceError: If upload fails
        """
        if not is_file_storage_enabled():
            raise FileServiceError("File storage is not enabled")

        # Get authenticated identity
        actor = get_identity_string()

        # Compute checksum
        checksum = hashlib.sha256(content).hexdigest()

        # Generate file ID from Registry
        try:
            registry = get_registry_client()
            file_id = await self._generate_file_id(registry, checksum, actor)
        except RegistryError as e:
            raise FileServiceError(f"Failed to generate file ID: {e}")

        # Upload to MinIO (storage_key = file_id)
        try:
            storage = get_file_storage_client()
            await storage.upload(
                storage_key=file_id,
                content=content,
                content_type=content_type,
                metadata={
                    "filename": filename,
                    "checksum": checksum,
                    "uploaded_by": actor or "unknown",
                }
            )
        except FileStorageError as e:
            raise FileServiceError(f"Failed to upload file to storage: {e}")

        # Create File document in MongoDB
        now = datetime.now(timezone.utc)
        file_metadata = FileMetadata(
            description=metadata.description if metadata else None,
            tags=metadata.tags if metadata else [],
            category=metadata.category if metadata else None,
            custom=metadata.custom if metadata else {},
        )

        file_doc = File(
            file_id=file_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum=checksum,
            storage_key=file_id,
            metadata=file_metadata,
            status=FileStatus.ORPHAN,  # Orphan until referenced
            reference_count=0,
            allowed_templates=metadata.allowed_templates if metadata else None,
            uploaded_at=now,
            uploaded_by=actor,
        )

        await file_doc.insert()

        return self._to_response(file_doc)

    async def _generate_file_id(
        self,
        registry,
        checksum: str,
        created_by: Optional[str]
    ) -> str:
        """Generate a file ID from the Registry."""
        # Use checksum + UUID to ensure uniqueness (same file can be uploaded multiple times)
        async with httpx.AsyncClient(timeout=registry.timeout) as client:
            response = await client.post(
                f"{registry.base_url}/api/registry/entries/register",
                headers=registry._get_headers(),
                json=[{
                    "namespace": "wip-files",
                    "composite_key": {
                        "checksum": checksum,
                        "upload_uuid": str(uuid.uuid4()),
                    },
                    "created_by": created_by,
                    "metadata": {"type": "file"}
                }]
            )

            if response.status_code != 200:
                raise RegistryError(
                    f"Failed to generate file ID: {response.status_code} - {response.text}"
                )

            data = response.json()
            result = data["results"][0]

            if result["status"] == "error":
                raise RegistryError(f"Registration error: {result.get('error')}")

            return result["registry_id"]

    async def get_file(self, file_id: str) -> Optional[FileResponse]:
        """
        Get file metadata by ID.

        Args:
            file_id: File ID (FILE-XXXXXX)

        Returns:
            FileResponse or None if not found
        """
        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return None
        return self._to_response(file_doc)

    async def get_download_url(
        self,
        file_id: str,
        expires_in: int = 3600
    ) -> Optional[FileDownloadResponse]:
        """
        Generate a pre-signed download URL for a file.

        Args:
            file_id: File ID (FILE-XXXXXX)
            expires_in: URL expiration time in seconds (default: 1 hour)

        Returns:
            FileDownloadResponse with pre-signed URL, or None if not found
        """
        if not is_file_storage_enabled():
            raise FileServiceError("File storage is not enabled")

        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return None

        if file_doc.status == FileStatus.INACTIVE:
            raise FileServiceError("File has been deleted")

        try:
            storage = get_file_storage_client()
            url = await storage.generate_download_url(
                storage_key=file_doc.storage_key,
                expires_in=expires_in,
                filename=file_doc.filename,
            )
        except FileStorageError as e:
            raise FileServiceError(f"Failed to generate download URL: {e}")

        return FileDownloadResponse(
            file_id=file_doc.file_id,
            filename=file_doc.filename,
            content_type=file_doc.content_type,
            size_bytes=file_doc.size_bytes,
            download_url=url,
            expires_in=expires_in,
        )

    async def download_file(self, file_id: str) -> Optional[tuple[bytes, File]]:
        """
        Download file content.

        Args:
            file_id: File ID (FILE-XXXXXX)

        Returns:
            Tuple of (content bytes, File document), or None if not found
        """
        if not is_file_storage_enabled():
            raise FileServiceError("File storage is not enabled")

        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return None

        if file_doc.status == FileStatus.INACTIVE:
            raise FileServiceError("File has been deleted")

        try:
            storage = get_file_storage_client()
            content = await storage.download(file_doc.storage_key)
            return content, file_doc
        except FileStorageError as e:
            raise FileServiceError(f"Failed to download file: {e}")

    async def delete_file(
        self,
        file_id: str,
        force: bool = False
    ) -> bool:
        """
        Soft-delete a file.

        Args:
            file_id: File ID (FILE-XXXXXX)
            force: If True, delete even if file is referenced by documents

        Returns:
            True if deleted, False if not found

        Raises:
            FileServiceError: If file is referenced and force=False
        """
        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return False

        if file_doc.status == FileStatus.INACTIVE:
            return True  # Already deleted

        if file_doc.reference_count > 0 and not force:
            raise FileServiceError(
                f"File is referenced by {file_doc.reference_count} document(s). "
                "Use force=true to delete anyway."
            )

        actor = get_identity_string()

        file_doc.status = FileStatus.INACTIVE
        file_doc.updated_at = datetime.now(timezone.utc)
        file_doc.updated_by = actor
        await file_doc.save()

        return True

    async def hard_delete_file(self, file_id: str) -> bool:
        """
        Permanently delete a file from storage and database.

        Only use for cleanup operations. Requires file to be in INACTIVE status.

        Args:
            file_id: File ID (FILE-XXXXXX)

        Returns:
            True if deleted, False if not found
        """
        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return False

        if file_doc.status != FileStatus.INACTIVE:
            raise FileServiceError("Only inactive files can be hard-deleted")

        # Delete from MinIO
        if is_file_storage_enabled():
            try:
                storage = get_file_storage_client()
                await storage.delete(file_doc.storage_key)
            except FileStorageError:
                pass  # Ignore storage errors (file may already be gone)

        # Delete from MongoDB
        await file_doc.delete()

        return True

    async def update_metadata(
        self,
        file_id: str,
        request: UpdateFileMetadataRequest
    ) -> Optional[FileResponse]:
        """
        Update file metadata.

        Args:
            file_id: File ID (FILE-XXXXXX)
            request: Metadata updates

        Returns:
            Updated FileResponse, or None if not found
        """
        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return None

        if file_doc.status == FileStatus.INACTIVE:
            raise FileServiceError("Cannot update deleted file")

        actor = get_identity_string()

        # Update metadata fields if provided
        if request.description is not None:
            file_doc.metadata.description = request.description
        if request.tags is not None:
            file_doc.metadata.tags = request.tags
        if request.category is not None:
            file_doc.metadata.category = request.category
        if request.custom is not None:
            file_doc.metadata.custom.update(request.custom)
        if request.allowed_templates is not None:
            file_doc.allowed_templates = request.allowed_templates

        file_doc.updated_at = datetime.now(timezone.utc)
        file_doc.updated_by = actor
        await file_doc.save()

        return self._to_response(file_doc)

    async def list_files(
        self,
        status: Optional[FileStatus] = None,
        content_type: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
        uploaded_by: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> FileListResponse:
        """
        List files with pagination and filters.

        Args:
            status: Filter by status
            content_type: Filter by MIME type (prefix match, e.g., "image/")
            category: Filter by category
            tags: Filter by tags (all must match)
            uploaded_by: Filter by uploader
            page: Page number (1-indexed)
            page_size: Items per page

        Returns:
            FileListResponse with paginated results
        """
        query = {}

        if status:
            query["status"] = status.value
        if content_type:
            if content_type.endswith("/*"):
                # Match MIME type prefix (e.g., "image/*")
                prefix = content_type[:-2]
                query["content_type"] = {"$regex": f"^{prefix}/"}
            else:
                query["content_type"] = content_type
        if category:
            query["metadata.category"] = category
        if tags:
            query["metadata.tags"] = {"$all": tags}
        if uploaded_by:
            query["uploaded_by"] = uploaded_by

        # Count total
        total = await File.find(query).count()

        # Fetch page
        skip = (page - 1) * page_size
        files = await File.find(query).skip(skip).limit(page_size).sort(
            [("uploaded_at", -1)]
        ).to_list()

        return FileListResponse(
            items=[self._to_response(f) for f in files],
            total=total,
            page=page,
            page_size=page_size,
            pages=math.ceil(total / page_size) if total > 0 else 1,
        )

    async def update_reference_count(
        self,
        file_id: str,
        delta: int
    ) -> Optional[File]:
        """
        Update file reference count.

        Called when documents add/remove file references.

        Args:
            file_id: File ID (FILE-XXXXXX)
            delta: Change in reference count (+1 for add, -1 for remove)

        Returns:
            Updated File document, or None if not found
        """
        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return None

        file_doc.reference_count = max(0, file_doc.reference_count + delta)

        # Update status based on reference count
        if file_doc.status != FileStatus.INACTIVE:
            if file_doc.reference_count > 0:
                file_doc.status = FileStatus.ACTIVE
            else:
                file_doc.status = FileStatus.ORPHAN

        file_doc.updated_at = datetime.now(timezone.utc)
        await file_doc.save()

        return file_doc

    async def find_orphans(
        self,
        older_than_hours: int = 0,
        limit: int = 100
    ) -> list[FileResponse]:
        """
        Find orphan files (uploaded but not referenced).

        Args:
            older_than_hours: Only return orphans older than this (0 = all orphans)
            limit: Maximum number to return

        Returns:
            List of orphan FileResponses
        """
        query = {
            "status": FileStatus.ORPHAN.value,
            "reference_count": 0,
        }

        # Only add time filter if older_than_hours > 0
        if older_than_hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
            query["uploaded_at"] = {"$lt": cutoff}

        orphans = await File.find(query).limit(limit).to_list()

        return [self._to_response(f) for f in orphans]

    async def validate_file_for_field(
        self,
        file_id: str,
        allowed_types: list[str],
        max_size_mb: float,
        allowed_templates: Optional[list[str]] = None
    ) -> tuple[bool, Optional[str], Optional[FileReference]]:
        """
        Validate that a file meets field constraints.

        Args:
            file_id: File ID to validate
            allowed_types: Allowed MIME type patterns (e.g., ["image/*", "application/pdf"])
            max_size_mb: Maximum file size in MB
            allowed_templates: Template codes that can use this file (from field config)

        Returns:
            Tuple of (is_valid, error_message, file_reference)
        """
        file_doc = await File.find_one({"file_id": file_id})
        if not file_doc:
            return False, f"File not found: {file_id}", None

        if file_doc.status == FileStatus.INACTIVE:
            return False, f"File has been deleted: {file_id}", None

        # Check size
        max_size_bytes = int(max_size_mb * 1024 * 1024)
        if file_doc.size_bytes > max_size_bytes:
            return False, f"File size ({file_doc.size_bytes} bytes) exceeds maximum ({max_size_bytes} bytes)", None

        # Check content type
        if not self._matches_type_pattern(file_doc.content_type, allowed_types):
            return False, f"File type '{file_doc.content_type}' not allowed. Allowed: {allowed_types}", None

        # Check template restriction (from file's allowed_templates)
        if file_doc.allowed_templates is not None and allowed_templates:
            # File has restrictions - check if any of the allowed_templates are in file's list
            if not any(t in file_doc.allowed_templates for t in allowed_templates):
                return False, f"File not allowed for templates: {allowed_templates}", None

        # Create file reference
        file_ref = FileReference(
            field_path="",  # Will be set by caller
            file_id=file_doc.file_id,
            filename=file_doc.filename,
            content_type=file_doc.content_type,
            size_bytes=file_doc.size_bytes,
            description=file_doc.metadata.description,
        )

        return True, None, file_ref

    def _matches_type_pattern(self, content_type: str, patterns: list[str]) -> bool:
        """Check if content_type matches any of the allowed patterns."""
        for pattern in patterns:
            if pattern == "*/*":
                return True
            if pattern.endswith("/*"):
                # Wildcard pattern (e.g., "image/*")
                prefix = pattern[:-2]
                if content_type.startswith(prefix + "/"):
                    return True
            elif pattern == content_type:
                return True
        return False

    async def bulk_delete(
        self,
        request: FileBulkDeleteRequest
    ) -> FileBulkDeleteResponse:
        """
        Delete multiple files.

        Args:
            request: Bulk delete request with file IDs

        Returns:
            FileBulkDeleteResponse with results
        """
        results = []
        deleted = 0
        failed = 0

        for i, file_id in enumerate(request.file_ids):
            try:
                success = await self.delete_file(file_id)
                if success:
                    deleted += 1
                    results.append(FileBulkResult(
                        index=i,
                        status="success",
                        file_id=file_id,
                    ))
                else:
                    failed += 1
                    results.append(FileBulkResult(
                        index=i,
                        status="error",
                        file_id=file_id,
                        error="File not found",
                    ))
            except FileServiceError as e:
                failed += 1
                results.append(FileBulkResult(
                    index=i,
                    status="error",
                    file_id=file_id,
                    error=str(e),
                ))

        return FileBulkDeleteResponse(
            total=len(request.file_ids),
            deleted=deleted,
            failed=failed,
            results=results,
        )

    async def check_integrity(self) -> FileIntegrityResponse:
        """
        Check file integrity (orphans, missing storage, broken references).

        Returns:
            FileIntegrityResponse with issues found
        """
        issues = []
        summary = {
            "orphan_file": 0,
            "missing_storage": 0,
            "inactive_referenced": 0,
        }

        # Check for orphan files (status=orphan, older than 24 hours)
        orphans = await self.find_orphans(older_than_hours=24, limit=100)
        for orphan in orphans:
            summary["orphan_file"] += 1
            issues.append(FileIntegrityIssue(
                type="orphan_file",
                severity="warning",
                file_id=orphan.file_id,
                message=f"Orphan file not referenced by any document: {orphan.filename}",
            ))

        # Check for files with storage issues (if storage is enabled)
        if is_file_storage_enabled():
            storage = get_file_storage_client()
            active_files = await File.find({
                "status": {"$ne": FileStatus.INACTIVE.value}
            }).limit(1000).to_list()

            for file_doc in active_files:
                try:
                    exists = await storage.exists(file_doc.storage_key)
                    if not exists:
                        summary["missing_storage"] += 1
                        issues.append(FileIntegrityIssue(
                            type="missing_storage",
                            severity="error",
                            file_id=file_doc.file_id,
                            message=f"File missing from storage: {file_doc.filename}",
                        ))
                except FileStorageError:
                    pass  # Storage check failed - could be transient

        # Check for inactive files still being referenced
        inactive_referenced = await File.find({
            "status": FileStatus.INACTIVE.value,
            "reference_count": {"$gt": 0},
        }).limit(100).to_list()

        for file_doc in inactive_referenced:
            summary["inactive_referenced"] += 1
            issues.append(FileIntegrityIssue(
                type="broken_reference",
                severity="error",
                file_id=file_doc.file_id,
                message=f"Deleted file still referenced by {file_doc.reference_count} document(s)",
            ))

        # Determine overall status
        if any(i.severity == "error" for i in issues):
            status = "error"
        elif issues:
            status = "warning"
        else:
            status = "healthy"

        return FileIntegrityResponse(
            status=status,
            checked_at=datetime.now(timezone.utc),
            summary=summary,
            issues=issues,
        )

    async def get_by_checksum(self, checksum: str) -> list[FileResponse]:
        """
        Find files by checksum (for duplicate detection).

        Args:
            checksum: SHA-256 checksum

        Returns:
            List of files with matching checksum
        """
        files = await File.find({"checksum": checksum}).to_list()
        return [self._to_response(f) for f in files]

    def _to_response(self, file_doc: File) -> FileResponse:
        """Convert File document to FileResponse."""
        return FileResponse(
            file_id=file_doc.file_id,
            filename=file_doc.filename,
            content_type=file_doc.content_type,
            size_bytes=file_doc.size_bytes,
            checksum=file_doc.checksum,
            storage_key=file_doc.storage_key,
            metadata=file_doc.metadata,
            status=file_doc.status,
            reference_count=file_doc.reference_count,
            allowed_templates=file_doc.allowed_templates,
            uploaded_at=file_doc.uploaded_at,
            uploaded_by=file_doc.uploaded_by,
            updated_at=file_doc.updated_at,
            updated_by=file_doc.updated_by,
        )


# Singleton instance
_service: Optional[FileService] = None


def get_file_service() -> FileService:
    """Get the singleton file service instance."""
    global _service
    if _service is None:
        _service = FileService()
    return _service

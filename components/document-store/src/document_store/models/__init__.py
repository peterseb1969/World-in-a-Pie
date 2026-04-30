"""Models for the Document Store service."""

from .api_models import (
    ArchiveItem,
    BulkResponse,
    # Bulk operation models
    BulkResultItem,
    DeleteItem,
    # Document models
    DocumentCreateRequest,
    DocumentListResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    DocumentResponse,
    DocumentVersionResponse,
    FileDownloadResponse,
    FileIntegrityIssue,
    FileIntegrityResponse,
    FileListResponse,
    FileResponse,
    # File models
    FileUploadMetadata,
    UpdateFileItem,
    UpdateFileMetadataRequest,
    ValidationError,
    ValidationRequest,
    ValidationResponse,
)
from .document import Document, DocumentMetadata, DocumentStatus
from .file import File, FileMetadata, FileReference, FileStatus

__all__ = [
    "ArchiveItem",
    "BulkResponse",
    # Bulk operation models
    "BulkResultItem",
    "DeleteItem",
    # Document models
    "Document",
    "DocumentCreateRequest",
    "DocumentListResponse",
    "DocumentMetadata",
    "DocumentQueryRequest",
    "DocumentQueryResponse",
    "DocumentResponse",
    "DocumentStatus",
    "DocumentVersionResponse",
    # File models
    "File",
    "FileDownloadResponse",
    "FileIntegrityIssue",
    "FileIntegrityResponse",
    "FileListResponse",
    "FileMetadata",
    "FileReference",
    "FileResponse",
    "FileStatus",
    "FileUploadMetadata",
    "UpdateFileItem",
    "UpdateFileMetadataRequest",
    "ValidationError",
    "ValidationRequest",
    "ValidationResponse",
]

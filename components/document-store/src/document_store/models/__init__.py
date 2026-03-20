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
    # Bulk operation models
    "BulkResultItem",
    "BulkResponse",
    "DeleteItem",
    "ArchiveItem",
    "UpdateFileItem",
    # Document models
    "Document",
    "DocumentMetadata",
    "DocumentStatus",
    "DocumentCreateRequest",
    "DocumentResponse",
    "DocumentListResponse",
    "DocumentVersionResponse",
    "DocumentQueryRequest",
    "DocumentQueryResponse",
    "ValidationRequest",
    "ValidationResponse",
    "ValidationError",
    # File models
    "File",
    "FileMetadata",
    "FileStatus",
    "FileReference",
    "FileUploadMetadata",
    "UpdateFileMetadataRequest",
    "FileResponse",
    "FileListResponse",
    "FileDownloadResponse",
    "FileIntegrityIssue",
    "FileIntegrityResponse",
]

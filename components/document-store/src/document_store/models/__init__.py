"""Models for the Document Store service."""

from .document import Document, DocumentMetadata, DocumentStatus
from .file import File, FileMetadata, FileStatus, FileReference
from .api_models import (
    # Bulk operation models
    BulkResultItem,
    BulkResponse,
    DeleteItem,
    ArchiveItem,
    UpdateFileItem,
    # Document models
    DocumentCreateRequest,
    DocumentResponse,
    DocumentListResponse,
    DocumentVersionResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    ValidationRequest,
    ValidationResponse,
    ValidationError,
    # File models
    FileUploadMetadata,
    UpdateFileMetadataRequest,
    FileResponse,
    FileListResponse,
    FileDownloadResponse,
    FileIntegrityIssue,
    FileIntegrityResponse,
)

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

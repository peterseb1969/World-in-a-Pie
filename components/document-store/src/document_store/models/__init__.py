"""Models for the Document Store service."""

from .document import Document, DocumentMetadata, DocumentStatus
from .file import File, FileMetadata, FileStatus, FileReference
from .api_models import (
    # Document models
    DocumentCreateRequest,
    DocumentResponse,
    DocumentListResponse,
    DocumentVersionResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    BulkCreateRequest,
    BulkCreateResponse,
    BulkCreateResult,
    ValidationRequest,
    ValidationResponse,
    ValidationError,
    # File models
    FileUploadMetadata,
    UpdateFileMetadataRequest,
    FileResponse,
    FileListResponse,
    FileDownloadResponse,
    FileBulkResult,
    FileBulkDeleteRequest,
    FileBulkDeleteResponse,
    FileIntegrityIssue,
    FileIntegrityResponse,
)

__all__ = [
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
    "BulkCreateRequest",
    "BulkCreateResponse",
    "BulkCreateResult",
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
    "FileBulkResult",
    "FileBulkDeleteRequest",
    "FileBulkDeleteResponse",
    "FileIntegrityIssue",
    "FileIntegrityResponse",
]

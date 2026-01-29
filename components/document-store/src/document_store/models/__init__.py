"""Models for the Document Store service."""

from .document import Document, DocumentMetadata, DocumentStatus
from .api_models import (
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
)

__all__ = [
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
]

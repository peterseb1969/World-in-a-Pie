"""Services for the Document Store."""

from .def_store_client import (
    DefStoreClient,
    DefStoreError,
    configure_def_store_client,
    get_def_store_client,
)
from .document_service import DocumentService
from .file_service import (
    FileService,
    FileServiceError,
    get_file_service,
)
from .file_storage_client import (
    FileStorageClient,
    FileStorageError,
    configure_file_storage_client,
    get_file_storage_client,
    is_file_storage_enabled,
)
from .identity_service import IdentityService
from .registry_client import (
    RegistryClient,
    RegistryError,
    configure_registry_client,
    get_registry_client,
)
from .template_store_client import (
    TemplateStoreClient,
    TemplateStoreError,
    configure_template_store_client,
    get_template_store_client,
)
from .validation_service import ValidationService

__all__ = [
    "DefStoreClient",
    "DefStoreError",
    "DocumentService",
    "FileService",
    "FileServiceError",
    "FileStorageClient",
    "FileStorageError",
    "IdentityService",
    "RegistryClient",
    "RegistryError",
    "TemplateStoreClient",
    "TemplateStoreError",
    "ValidationService",
    "configure_def_store_client",
    "configure_file_storage_client",
    "configure_registry_client",
    "configure_template_store_client",
    "get_def_store_client",
    "get_file_service",
    "get_file_storage_client",
    "get_registry_client",
    "get_template_store_client",
    "is_file_storage_enabled",
]

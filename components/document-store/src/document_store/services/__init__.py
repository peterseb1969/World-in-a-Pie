"""Services for the Document Store."""

from .registry_client import (
    RegistryClient,
    RegistryError,
    get_registry_client,
    configure_registry_client,
)
from .template_store_client import (
    TemplateStoreClient,
    TemplateStoreError,
    get_template_store_client,
    configure_template_store_client,
)
from .def_store_client import (
    DefStoreClient,
    DefStoreError,
    get_def_store_client,
    configure_def_store_client,
)
from .file_storage_client import (
    FileStorageClient,
    FileStorageError,
    get_file_storage_client,
    configure_file_storage_client,
    is_file_storage_enabled,
)
from .file_service import (
    FileService,
    FileServiceError,
    get_file_service,
)
from .identity_service import IdentityService
from .validation_service import ValidationService
from .document_service import DocumentService

__all__ = [
    "RegistryClient",
    "RegistryError",
    "get_registry_client",
    "configure_registry_client",
    "TemplateStoreClient",
    "TemplateStoreError",
    "get_template_store_client",
    "configure_template_store_client",
    "DefStoreClient",
    "DefStoreError",
    "get_def_store_client",
    "configure_def_store_client",
    "FileStorageClient",
    "FileStorageError",
    "get_file_storage_client",
    "configure_file_storage_client",
    "is_file_storage_enabled",
    "FileService",
    "FileServiceError",
    "get_file_service",
    "IdentityService",
    "ValidationService",
    "DocumentService",
]

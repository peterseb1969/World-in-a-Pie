"""API routers for the Registry service."""

from fastapi import APIRouter

from .api_keys import router as api_keys_router
from .entries import router as entries_router
from .grants import my_router as my_router
from .grants import router as grants_router
from .namespace_deletion import router as deletion_router
from .namespaces import router as namespaces_router
from .reservations import router as reservations_router
from .search import router as search_router
from .synonyms import router as synonyms_router
from .system_backup import router as system_backup_router

# Create main API router
api_router = APIRouter(prefix="/api/registry")

# Include sub-routers — deletion router BEFORE namespaces to avoid route conflicts
api_router.include_router(deletion_router, prefix="/namespaces", tags=["Namespace Deletion"])
api_router.include_router(namespaces_router, prefix="/namespaces", tags=["Namespaces"])
api_router.include_router(grants_router, prefix="/namespaces", tags=["Grants"])
api_router.include_router(entries_router, prefix="/entries", tags=["Entries"])
api_router.include_router(synonyms_router, prefix="/synonyms", tags=["Synonyms"])
api_router.include_router(search_router, prefix="/search", tags=["Search"])
api_router.include_router(my_router, prefix="/my", tags=["My Permissions"])
api_router.include_router(api_keys_router, prefix="/api-keys", tags=["API Keys"])
api_router.include_router(reservations_router, prefix="/reservations", tags=["Reservations"])
api_router.include_router(system_backup_router, tags=["System Backup"])

__all__ = ["api_router"]
